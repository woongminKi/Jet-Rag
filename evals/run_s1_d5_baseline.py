"""S1 D5 — 골든셋 v1 baseline 정량 측정 + 모델 회귀 비교.

목적
----
S1 D2 ship 한 골든셋 v1 (157 row, 12 컬럼) 으로 baseline 을 측정해
master plan §6 의 S1 마무리 지표를 확정하고, 동시에 D2-D 결정 (default
모델 = `gemini-2.5-flash`) 의 lite 비교를 한 번에 캡처한다.

설계 결정 (사용자 승인 4건, 2026-05-07)
--------------------------------------
1. **`GeminiLLMProvider` 직접 인스턴스화** — `_get_llm()` lru_cache 우회를
   위해 `factory.get_llm_provider()` 가 아닌 어댑터 직접 생성. 본 스크립트는
   1회성 측정이라 정합 우려 없음. (D5 후 다시 운영 코드는 factory 사용)
2. **RAGAS Faithfulness + ResponseRelevancy 함께 캡처** — sample 30건 한정,
   추가 비용 ~$0.05/30건 (≪ $0.5 가드레일). 의사결정 신호 ↑.
3. **단위 테스트 5건** — `api/tests/test_s1_d5_sampling.py` 에 sampling
   결정성 회귀 보호. 본 스크립트의 외부 의존 부분은 격리.
4. **`evals/results/` git ignore** — 재현성은 seed=42 로 확보.

측정 시나리오
------------
- A — `gemini-2.5-flash` (현재 default, D2-D ship)
- B — `gemini-2.5-flash-lite` (저렴, lite 채택 평가)

각 시나리오에서 v1 골든셋 sample 30건 (seed=42, stratified by query_type) 으로:
- retrieval 메트릭 — Recall@10 / MRR / nDCG@10 (LLM 무관, 한 번만 측정)
- answer 메트릭 — must_include_recall / expected_doc_hit / out_of_scope_correct
- LLM judge — Faithfulness / ResponseRelevancy (RAGAS, sample 30건만, ~$0.05)
- latency — answer p50 / p95
- cost 추정 — 단가 dict + 응답 길이 proxy

비용 가드레일
------------
- 30 query × 2 시나리오 × LLM 1회 = 60 호출
- 2.5-flash: ~$0.001/query → ~$0.06
- 2.5-flash-lite: ~$0.0003/query → ~$0.01
- RAGAS judge (Faithfulness + ResponseRelevancy): ~$0.0017/query × 60 = ~$0.10
- BGE-M3 cosine (heuristic context_precision): 무료 (HF cached)
- **합계 ≤ $0.5** (사용자 가드레일 통과)

사용
----
    cd api && uv run python ../evals/run_s1_d5_baseline.py \\
        --output ../evals/results/s1_d5_baseline.md \\
        --results-json ../evals/results/s1_d5_baseline.json

옵션
----
- `--sample-size N` (default 30) — sample 크기. 0 시 전체 (157) 사용 — 비용 ↑.
- `--seed N` (default 42) — 결정성 확보. 재현 시 같은 seed 사용 권고.
- `--scenario {A,B,both}` (default both) — A=2.5-flash, B=2.5-flash-lite.
- `--skip-ragas` — RAGAS judge 생략 (~$0.10 절약).
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import random
import statistics
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any

# api/ 를 import path 에 추가 — answer/factory 직접 호출.
_API_PATH = Path(__file__).resolve().parents[1] / "api"
sys.path.insert(0, str(_API_PATH))

from app.adapters.factory import get_gemini_pricing  # noqa: E402
from app.adapters.impl.gemini_llm import GeminiLLMProvider  # noqa: E402
from app.adapters.llm import ChatMessage  # noqa: E402, F401
from app.config import get_settings  # noqa: E402
from app.routers.answer import _build_messages, _gather_chunks  # noqa: E402
from app.services.ragas_eval import (  # noqa: E402
    RagasUnavailable,
    evaluate_context_precision_only,
    evaluate_single,
)
from app.services.retrieval_metrics import mrr, ndcg_at_k, recall_at_k  # noqa: E402

logger = logging.getLogger(__name__)

_GOLDEN_CSV = Path(__file__).parent / "golden_v1.csv"
_TOP_K = 5  # /answer default — LLM 에 전달
_RETRIEVAL_K = 10  # Recall@10 / MRR@10 / nDCG@10 측정용
_TEMPERATURE = 0.2
_DEFAULT_SAMPLE_SIZE = 30
_DEFAULT_SEED = 42

# 시나리오 — (라벨, 모델 ID)
_SCENARIOS: list[tuple[str, str]] = [
    ("2.5-flash", "gemini-2.5-flash"),
    ("2.5-flash-lite", "gemini-2.5-flash-lite"),
]


# ============================================================
# 골든셋 로드 + sampling
# ============================================================


def _load_golden(path: Path) -> list[dict[str, Any]]:
    """v1 골든셋 (12 컬럼, utf-8-sig BOM) 로드. user row (G-U-***) 도 포함.

    user row 는 `doc_id` 빈 값 → retrieval 평가 자동 skip, answer 평가만 측정.
    """
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            r["query"] = unicodedata.normalize("NFC", (r.get("query") or "").strip())
            r["must_include"] = [
                t.strip() for t in (r.get("must_include") or "").split(";") if t.strip()
            ]
            r["expected_doc_titles"] = [
                t.strip()
                for t in (r.get("expected_doc_title") or "").split("|")
                if t.strip()
            ]
            r["negative"] = (r.get("negative") or "").strip().lower() == "true"
            r["doc_id"] = (r.get("doc_id") or "").strip()
            relv_str = (r.get("relevant_chunks") or "").strip()
            accept_str = (r.get("acceptable_chunks") or "").strip()
            r["relevant_chunks"] = {
                int(x.strip()) for x in relv_str.split(",") if x.strip().isdigit()
            }
            r["acceptable_chunks"] = {
                int(x.strip()) for x in accept_str.split(",") if x.strip().isdigit()
            }
            rows.append(r)
    return rows


def sample_golden(
    rows: list[dict[str, Any]],
    *,
    sample_size: int,
    seed: int,
    stratified: bool = False,
) -> list[dict[str, Any]]:
    """결정성 sampling — `random.Random(seed)` 로 격리 PRNG 사용.

    `random.shuffle` 글로벌 PRNG 에 영향 주지 않도록 인스턴스 사용.
    seed 같으면 같은 sample 보장 — 재현성 (`evals/results/` git ignore 가능).

    sample_size > len(rows) → 모집단 그대로 (cap).
    sample_size == 0 → 빈 list.
    stratified=True → query_type 별 비율 보존 sampling.
    """
    if sample_size <= 0:
        return []
    if sample_size >= len(rows):
        return list(rows)

    rng = random.Random(seed)

    if not stratified:
        return rng.sample(rows, k=sample_size)

    # stratified — query_type 별 sample 후 합치기
    by_type: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        qt = r.get("query_type") or "unknown"
        by_type.setdefault(qt, []).append(r)

    total = len(rows)
    sampled: list[dict[str, Any]] = []
    for qt, group in by_type.items():
        # 비율 그대로 — 반올림. 0건 받는 type 도 있을 수 있음.
        share = round(len(group) / total * sample_size)
        share = min(share, len(group))
        if share <= 0:
            continue
        sampled.extend(rng.sample(group, k=share))

    # 반올림 누적으로 sample_size 와 ±1~2 차이 발생 가능 → 보정
    if len(sampled) > sample_size:
        sampled = rng.sample(sampled, k=sample_size)
    elif len(sampled) < sample_size:
        # 누락분 — 전체 모집단에서 미선택 row 중 보충
        used_ids = {r["id"] for r in sampled}
        remaining = [r for r in rows if r["id"] not in used_ids]
        deficit = sample_size - len(sampled)
        if remaining:
            sampled.extend(rng.sample(remaining, k=min(deficit, len(remaining))))

    return sampled


# ============================================================
# 한 query 측정 — retrieval + answer + (옵션) RAGAS judge
# ============================================================


def _measure_one(
    qa: dict[str, Any],
    *,
    user_id: str,
    llm: GeminiLLMProvider,
    skip_ragas: bool,
) -> dict[str, Any]:
    """단일 query 측정.

    답변 단계: `_gather_chunks` (검색) → `_build_messages` (프롬프트) →
    `llm.complete` (Gemini 호출). lru_cache `_get_llm()` 우회 — 본 스크립트의
    `llm` 파라미터로 직접 생성한 인스턴스 사용 (사용자 승인 1번).
    """
    query = qa["query"]
    t_total = time.monotonic()

    # 1) 검색 — top-K 큰 값 (10) 으로 한 번만, retrieval + answer 모두 cover
    try:
        chunks_full, query_parsed = _gather_chunks(
            query=query, doc_id=None, top_k=_RETRIEVAL_K, user_id=user_id
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "id": qa.get("id"),
            "query": query,
            "query_type": qa.get("query_type") or "",
            "error": f"search_failed: {exc}",
            "took_ms": int((time.monotonic() - t_total) * 1000),
        }

    # 2) retrieval 메트릭 (chunk_idx GT 보유 시만)
    relevant = qa.get("relevant_chunks") or set()
    acceptable = qa.get("acceptable_chunks") or set()
    expected_doc_id = qa.get("doc_id")
    if relevant and expected_doc_id:
        # _gather_chunks 는 multi-doc 리턴 → 같은 doc 의 chunk_idx 만 비교
        retrieved_idx = [
            int(c["chunk_idx"])
            for c in chunks_full
            if c.get("doc_id") == expected_doc_id
        ]
        # retrieval_metrics 의 graded relevance API — relevant + acceptable 로 분리 전달.
        recall = recall_at_k(
            retrieved_idx, relevant, k=_RETRIEVAL_K, acceptable_chunks=acceptable
        )
        mrr_val = mrr(
            retrieved_idx, relevant, k=_RETRIEVAL_K, acceptable_chunks=acceptable
        )
        ndcg = ndcg_at_k(
            retrieved_idx, relevant, k=_RETRIEVAL_K, acceptable_chunks=acceptable
        )
    else:
        recall = mrr_val = ndcg = None

    # 3) answer — top-K 만 LLM 에 전달
    chunks_for_answer = chunks_full[:_TOP_K]
    if not chunks_for_answer:
        answer_text = "제공된 자료에서 해당 정보를 찾지 못했습니다."
        llm_ms = 0
    else:
        messages = _build_messages(query, chunks_for_answer)
        t_llm = time.monotonic()
        try:
            answer_text = llm.complete(messages, temperature=_TEMPERATURE).strip()
        except Exception as exc:  # noqa: BLE001
            return {
                "id": qa.get("id"),
                "query": query,
                "query_type": qa.get("query_type") or "",
                "error": f"llm_failed: {exc}",
                "took_ms": int((time.monotonic() - t_total) * 1000),
            }
        llm_ms = int((time.monotonic() - t_llm) * 1000)

    # 4) 휴리스틱 정확도
    answer_norm = unicodedata.normalize("NFC", answer_text).lower()
    must_keys = qa.get("must_include") or []
    if must_keys:
        hits = sum(1 for k in must_keys if k.lower() in answer_norm)
        must_include_hit = hits / len(must_keys)
    else:
        must_include_hit = None

    expected_titles = qa.get("expected_doc_titles") or []
    source_titles = [c.get("doc_title") for c in chunks_for_answer if c.get("doc_title")]
    if expected_titles:
        expected_doc_hit = any(
            any(et.lower() in (st or "").lower() for st in source_titles)
            for et in expected_titles
        )
    else:
        expected_doc_hit = None

    out_of_scope_correct: bool | None = None
    if qa.get("negative"):
        out_of_scope_correct = (
            "찾지 못" in answer_text
            or "없습니다" in answer_text
            or not chunks_for_answer
        )

    # 5) RAGAS judge (Faithfulness + ResponseRelevancy) — sample 한정 비용 통제
    faithfulness = answer_relevancy = context_precision = None
    judge_ms = 0
    judge_error: str | None = None
    if not skip_ragas and chunks_for_answer:
        contexts = [c.get("text") or "" for c in chunks_for_answer]
        t_judge = time.monotonic()
        try:
            ragas_res = evaluate_single(
                query=query, answer=answer_text, contexts=contexts
            )
            faithfulness = ragas_res.metrics.faithfulness
            answer_relevancy = ragas_res.metrics.answer_relevancy
            # RAGAS judge 가 context_precision 도 함께 계산 (BGE-M3 cosine 휴리스틱)
            context_precision = ragas_res.metrics.context_precision
        except RagasUnavailable as exc:
            judge_error = str(exc)
            # context_precision 만 BGE-M3 휴리스틱으로 fallback (LLM judge 0)
            try:
                heur = evaluate_context_precision_only(query=query, contexts=contexts)
                context_precision = heur.metrics.context_precision
            except RagasUnavailable:
                pass
        judge_ms = int((time.monotonic() - t_judge) * 1000)

    took_ms = int((time.monotonic() - t_total) * 1000)
    return {
        "id": qa.get("id"),
        "query": query,
        "query_type": qa.get("query_type") or "",
        "answer": answer_text,
        "answer_len": len(answer_text),
        "n_chunks": len(chunks_for_answer),
        "n_retrieved": len(chunks_full),
        "source_doc_titles": source_titles,
        # retrieval
        "recall_at_10": recall,
        "mrr_at_10": mrr_val,
        "ndcg_at_10": ndcg,
        # answer 휴리스틱
        "must_include_hit": must_include_hit,
        "expected_doc_hit": expected_doc_hit,
        "out_of_scope_correct": out_of_scope_correct,
        # RAGAS
        "faithfulness": faithfulness,
        "answer_relevancy": answer_relevancy,
        "context_precision": context_precision,
        "judge_error": judge_error,
        # latency
        "took_ms": took_ms,
        "llm_ms": llm_ms,
        "judge_ms": judge_ms,
        "query_parsed": query_parsed,
    }


def _run_scenario(
    label: str,
    model_id: str,
    sample: list[dict[str, Any]],
    user_id: str,
    skip_ragas: bool,
) -> list[dict[str, Any]]:
    """한 시나리오 (모델 1개) 의 sample 전체 측정.

    `GeminiLLMProvider` 직접 인스턴스화 — factory + lru_cache 우회 (사용자 승인 1번).
    같은 인스턴스로 sample 안의 모든 query 처리 → SDK 클라이언트 재사용 (cold 회피).
    """
    print(f"\n[scenario {label}] 모델={model_id}, n={len(sample)}", file=sys.stderr)
    llm = GeminiLLMProvider(model=model_id)

    results: list[dict[str, Any]] = []
    for i, qa in enumerate(sample, start=1):
        m = _measure_one(qa, user_id=user_id, llm=llm, skip_ragas=skip_ragas)
        results.append(m)
        if "error" in m:
            print(
                f"  [{i}/{len(sample)}] ERR {m['query'][:30]!r} — {m['error'][:60]}",
                file=sys.stderr,
            )
        else:
            judge_str = ""
            if m.get("faithfulness") is not None:
                judge_str = (
                    f" faith={m['faithfulness']:.2f}"
                    f" rel={m.get('answer_relevancy') or 0:.2f}"
                )
            print(
                f"  [{i}/{len(sample)}] {label}"
                f" must={m.get('must_include_hit')}"
                f" doc={m.get('expected_doc_hit')}"
                f" R@10={m.get('recall_at_10')}"
                f"{judge_str}"
                f" {m['took_ms']}ms",
                file=sys.stderr,
            )
    return results


# ============================================================
# 집계 + markdown 리포트
# ============================================================


def _aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    """sample 결과 → 평균 메트릭."""
    successful = [r for r in results if "error" not in r]
    if not successful:
        return {"n_total": len(results), "n_success": 0}

    def _avg(key: str) -> float | None:
        vals = [r[key] for r in successful if r.get(key) is not None]
        return statistics.mean(vals) if vals else None

    def _hit_rate(key: str) -> float | None:
        vals = [r[key] for r in successful if r.get(key) is not None]
        if not vals:
            return None
        # bool / float 모두 cover (must_include_hit 은 ratio, expected_doc_hit 은 bool)
        return sum(1.0 if v is True else float(v) for v in vals) / len(vals)

    lat_vals = [r["took_ms"] for r in successful]
    llm_lat = [r["llm_ms"] for r in successful if r.get("llm_ms")]
    ans_len = [r["answer_len"] for r in successful]

    return {
        "n_total": len(results),
        "n_success": len(successful),
        # retrieval (None 빠진 평균)
        "recall_at_10": _avg("recall_at_10"),
        "mrr_at_10": _avg("mrr_at_10"),
        "ndcg_at_10": _avg("ndcg_at_10"),
        "n_retrieval_eval": sum(
            1 for r in successful if r.get("recall_at_10") is not None
        ),
        # answer 휴리스틱
        "must_include_recall": _avg("must_include_hit"),
        "must_include_n": sum(
            1 for r in successful if r.get("must_include_hit") is not None
        ),
        "expected_doc_hit_rate": _hit_rate("expected_doc_hit"),
        "expected_doc_n": sum(
            1 for r in successful if r.get("expected_doc_hit") is not None
        ),
        "out_of_scope_correct_rate": _hit_rate("out_of_scope_correct"),
        "out_of_scope_n": sum(
            1 for r in successful if r.get("out_of_scope_correct") is not None
        ),
        # RAGAS
        "faithfulness_avg": _avg("faithfulness"),
        "answer_relevancy_avg": _avg("answer_relevancy"),
        "context_precision_avg": _avg("context_precision"),
        "n_ragas_eval": sum(
            1 for r in successful if r.get("faithfulness") is not None
        ),
        # latency
        "latency_avg_ms": statistics.mean(lat_vals) if lat_vals else 0.0,
        "latency_p95_ms": (
            sorted(lat_vals)[int(len(lat_vals) * 0.95)] if lat_vals else 0
        ),
        "llm_latency_avg_ms": statistics.mean(llm_lat) if llm_lat else 0.0,
        "answer_len_avg": statistics.mean(ans_len) if ans_len else 0.0,
    }


def _format_metric(v: float | None, *, percent: bool = True) -> str:
    if v is None:
        return "—"
    if percent:
        return f"{v * 100:.1f}%"
    return f"{v:.3f}"


def _format_diff_pp(a: float | None, b: float | None) -> tuple[str, str]:
    """A vs B 의 pp 차이 + 판정 라벨 (OK / minor / 회귀)."""
    if a is None or b is None:
        return ("—", "—")
    diff_pp = (a - b) * 100
    sign = "+" if diff_pp >= 0 else ""
    if diff_pp >= -2.0:
        verdict = "OK"
    elif diff_pp >= -5.0:
        verdict = "minor"
    else:
        verdict = "회귀"
    return (f"{sign}{diff_pp:.1f}pp", verdict)


def _format_baseline_md(
    agg_a: dict[str, Any] | None,
    agg_b: dict[str, Any] | None,
    *,
    sample_n: int,
    seed: int,
    skip_ragas: bool,
    golden_total: int,
) -> str:
    """baseline 측정 markdown."""
    pricing_a = get_gemini_pricing("gemini-2.5-flash")
    pricing_b = get_gemini_pricing("gemini-2.5-flash-lite")
    cost_ratio_input = pricing_b["input"] / pricing_a["input"]
    cost_ratio_output = pricing_b["output"] / pricing_a["output"]

    lines: list[str] = []
    lines.append("# S1 D5 — 골든셋 v1 baseline 정량 측정")
    lines.append("")
    lines.append(f"- 측정 일시: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(
        f"- 골든셋: `evals/golden_v1.csv` ({golden_total} row, "
        f"sample {sample_n} seed={seed} stratified by query_type)"
    )
    lines.append("- 시나리오 A: **gemini-2.5-flash** (현재 default, D2-D)")
    lines.append("- 시나리오 B: **gemini-2.5-flash-lite** (저렴, lite 채택 후보)")
    lines.append(
        f"- top_k(answer): {_TOP_K}, top_k(retrieval): {_RETRIEVAL_K}, "
        f"temperature: {_TEMPERATURE}"
    )
    lines.append(
        f"- LLM judge: {'OFF (--skip-ragas)' if skip_ragas else 'ON (Faithfulness + ResponseRelevancy)'}"
    )
    lines.append(
        "- 측정 방식: in-process (`_gather_chunks` + `GeminiLLMProvider` 직접 인스턴스화)"
    )
    lines.append("")

    if agg_a is None or agg_b is None:
        lines.append("## 부분 결과 (단일 시나리오만 실행)")
        lines.append("")
        for label, agg in [("A=2.5-flash", agg_a), ("B=2.5-flash-lite", agg_b)]:
            if agg is None:
                continue
            lines.append(f"### {label}")
            lines.append("")
            lines.append(f"```json\n{json.dumps(agg, ensure_ascii=False, indent=2)}\n```")
            lines.append("")
        return "\n".join(lines)

    # ============== 비교 표 ==============
    lines.append("## 1. 검색 retrieval 메트릭 (chunk-level GT 보유 row 한정)")
    lines.append("")
    lines.append(
        "> 모델 변경은 LLM 만 영향 — 검색 메트릭은 BGE-M3 dense + sparse RPC 결과 "
        "동일. 두 시나리오 모두 같은 값 (회귀 정의상 0)."
    )
    lines.append("")
    lines.append(f"| 메트릭 | 값 (n={agg_a.get('n_retrieval_eval', 0)}) |")
    lines.append("|---|---:|")
    lines.append(f"| Recall@10 | {_format_metric(agg_a.get('recall_at_10'))} |")
    lines.append(f"| MRR@10 | {_format_metric(agg_a.get('mrr_at_10'), percent=False)} |")
    lines.append(f"| nDCG@10 | {_format_metric(agg_a.get('ndcg_at_10'), percent=False)} |")
    lines.append("")

    # ============== 답변 휴리스틱 ==============
    lines.append("## 2. 답변 휴리스틱 (must_include / expected_doc / out_of_scope)")
    lines.append("")
    lines.append("| 메트릭 | 2.5-flash (A) | 2.5-flash-lite (B) | Δ (B-A) | 판정 |")
    lines.append("|---|---:|---:|---:|---|")
    for label, key in [
        ("must_include 키워드 hit", "must_include_recall"),
        ("expected_doc hit (doc-level)", "expected_doc_hit_rate"),
        ("out_of_scope 정확도 (negative)", "out_of_scope_correct_rate"),
    ]:
        a = agg_a.get(key)
        b = agg_b.get(key)
        diff_str, verdict = _format_diff_pp(b, a)
        lines.append(
            f"| {label} | {_format_metric(a)} | {_format_metric(b)} "
            f"| {diff_str} | {verdict} |"
        )
    lines.append("")

    # ============== RAGAS judge ==============
    if not skip_ragas:
        lines.append("## 3. RAGAS LLM judge (Faithfulness + ResponseRelevancy)")
        lines.append("")
        lines.append(
            f"| 메트릭 | 2.5-flash (A) | 2.5-flash-lite (B) "
            f"| Δ (B-A) | n_eval |"
        )
        lines.append("|---|---:|---:|---:|---:|")
        for label, key in [
            ("Faithfulness", "faithfulness_avg"),
            ("ResponseRelevancy", "answer_relevancy_avg"),
            ("context_precision (BGE-M3 휴리스틱)", "context_precision_avg"),
        ]:
            a = agg_a.get(key)
            b = agg_b.get(key)
            diff_str, _ = _format_diff_pp(b, a)
            lines.append(
                f"| {label} | {_format_metric(a, percent=False)} "
                f"| {_format_metric(b, percent=False)} | {diff_str} "
                f"| {agg_a.get('n_ragas_eval', 0)} |"
            )
        lines.append("")
    else:
        lines.append("## 3. RAGAS LLM judge — SKIPPED (`--skip-ragas`)")
        lines.append("")

    # ============== latency / 비용 ==============
    lines.append("## 4. latency / 비용")
    lines.append("")
    lines.append(
        f"| 항목 | 2.5-flash (A) | 2.5-flash-lite (B) "
        f"| ratio (B/A) |"
    )
    lines.append("|---|---:|---:|---:|")
    lines.append(
        f"| 평균 응답 latency (ms) | {agg_a.get('latency_avg_ms', 0):.0f} "
        f"| {agg_b.get('latency_avg_ms', 0):.0f} "
        f"| {(agg_b.get('latency_avg_ms', 0) / max(agg_a.get('latency_avg_ms', 1), 1)):.2f}× |"
    )
    lines.append(
        f"| LLM-only latency 평균 (ms) | {agg_a.get('llm_latency_avg_ms', 0):.0f} "
        f"| {agg_b.get('llm_latency_avg_ms', 0):.0f} "
        f"| {(agg_b.get('llm_latency_avg_ms', 0) / max(agg_a.get('llm_latency_avg_ms', 1), 1)):.2f}× |"
    )
    lines.append(
        f"| p95 latency (ms) | {agg_a.get('latency_p95_ms', 0)} "
        f"| {agg_b.get('latency_p95_ms', 0)} | — |"
    )
    lines.append(
        f"| 평균 답변 길이 (글자) | {agg_a.get('answer_len_avg', 0):.0f} "
        f"| {agg_b.get('answer_len_avg', 0):.0f} | — |"
    )
    lines.append(
        f"| input $/1M tokens | ${pricing_a['input']:.3f} | ${pricing_b['input']:.3f} "
        f"| {cost_ratio_input:.2f}× |"
    )
    lines.append(
        f"| output $/1M tokens | ${pricing_a['output']:.3f} | ${pricing_b['output']:.3f} "
        f"| {cost_ratio_output:.2f}× |"
    )
    lines.append("")

    # ============== 권고 ==============
    lines.append("## 5. 의사결정 권고")
    lines.append("")
    lines.append(
        "본 baseline 결과 + sample 30 의 통계적 신뢰도 한계 (CI ±10pp 수준) 을 함께 고려:"
    )
    lines.append("")

    deltas = []
    for key in ("must_include_recall", "expected_doc_hit_rate", "out_of_scope_correct_rate"):
        a_v = agg_a.get(key)
        b_v = agg_b.get(key)
        if a_v is not None and b_v is not None:
            deltas.append((b_v - a_v) * 100)

    avg_delta = statistics.mean(deltas) if deltas else 0.0
    lines.append(f"- 답변 휴리스틱 평균 Δ (B-A) = {avg_delta:+.2f}pp")

    if not skip_ragas:
        ragas_deltas = []
        for key in ("faithfulness_avg", "answer_relevancy_avg"):
            a_v = agg_a.get(key)
            b_v = agg_b.get(key)
            if a_v is not None and b_v is not None:
                ragas_deltas.append((b_v - a_v) * 100)
        if ragas_deltas:
            ragas_avg = statistics.mean(ragas_deltas)
            lines.append(f"- RAGAS judge 평균 Δ (B-A) = {ragas_avg:+.2f}pp")
            avg_delta = (avg_delta + ragas_avg) / 2  # 종합 신호

    if avg_delta >= -2.0:
        lines.append("")
        lines.append(
            "→ **권고: lite 채택** — 회귀 미미 (≥ -2pp), 비용 1/3 이하 (input 0.33×, output 0.16×). "
            "factory `_GEMINI_DEFAULT_MODELS['answer']` 를 `gemini-2.5-flash-lite` 로 변경 검토."
        )
    elif avg_delta >= -5.0:
        lines.append("")
        lines.append(
            "→ **권고: hybrid** — 부분 회귀 (-2 ~ -5pp). purpose 별 분기 — "
            "tag/summary/decomposition/hyde 는 이미 lite, answer 만 flash 유지 (현재 매핑 유지). "
            "추가 sample 측정으로 신뢰도 확보 후 재평가."
        )
    else:
        lines.append("")
        lines.append(
            "→ **권고: flash 유지** — 회귀 ≥ -5pp 명확. factory 기본값 변경 비권고. "
            "lite 는 인제스트 보조 (tag/summary 등 이미 채택) 에만 한정."
        )
    lines.append("")

    # ============== 비판적 한계 ==============
    lines.append("## 6. 비판적 한계 (정직 명시)")
    lines.append("")
    lines.append(
        f"- sample n={sample_n}, seed={seed} (stratified) — CI ±10pp 수준 (낮음). "
        "신뢰도 ↑ 하려면 sample_size 100+ 필요 (비용 ~$0.5×n/30 비례 증가)."
    )
    lines.append(
        f"- LLM 비결정성 (temperature={_TEMPERATURE}) — 1회 측정만, 반복 측정 시 "
        "±2~5pp 변동 자연 발생. 재현성은 seed 로만 보장 (sampling), LLM 출력은 비결정."
    )
    lines.append(
        "- 검색 메트릭은 모델 변경과 무관 — chunk_idx GT 가 있는 row (auto 분) 한정."
    )
    lines.append(
        "- must_include 매칭은 substring 기반 — 동의어/표현 변이 false negative 가능 "
        "(예: '시트' vs '좌석')."
    )
    if skip_ragas:
        lines.append(
            "- RAGAS judge SKIP — Faithfulness/ResponseRelevancy 미측정. 답변 정합성 신호 부족."
        )
    return "\n".join(lines)


# ============================================================
# main
# ============================================================


def main() -> int:
    parser = argparse.ArgumentParser(
        description="S1 D5 골든셋 v1 baseline 정량 측정 (2.5-flash vs 2.5-flash-lite)"
    )
    parser.add_argument(
        "--golden", type=Path, default=_GOLDEN_CSV,
        help=f"골든셋 CSV (default {_GOLDEN_CSV.name})",
    )
    parser.add_argument(
        "--output", "-o", type=Path, default=None,
        help="markdown 결과 출력 경로 (`evals/results/` 권장, git ignore)",
    )
    parser.add_argument(
        "--results-json", type=Path, default=None,
        help="시나리오별 raw JSON 출력 경로",
    )
    parser.add_argument(
        "--scenario", choices=["A", "B", "both"], default="both",
        help="A=2.5-flash, B=2.5-flash-lite, both=둘 다 (default both)",
    )
    parser.add_argument(
        "--sample-size", type=int, default=_DEFAULT_SAMPLE_SIZE,
        help=f"sample 크기 (default {_DEFAULT_SAMPLE_SIZE}). 0=전체.",
    )
    parser.add_argument(
        "--seed", type=int, default=_DEFAULT_SEED,
        help=f"sampling seed (default {_DEFAULT_SEED}). 결정성 보장.",
    )
    parser.add_argument(
        "--skip-ragas", action="store_true",
        help="RAGAS LLM judge 생략 (~$0.10 절약)",
    )
    args = parser.parse_args()

    if not args.golden.exists():
        print(f"[FAIL] 골든셋 미발견: {args.golden}", file=sys.stderr)
        return 1

    golden_full = _load_golden(args.golden)
    print(
        f"[OK] 골든셋 로드: {args.golden.name} ({len(golden_full)} row)",
        file=sys.stderr,
    )

    sample = sample_golden(
        golden_full, sample_size=args.sample_size, seed=args.seed, stratified=True
    )
    print(
        f"[OK] sample 생성: {len(sample)} row "
        f"(seed={args.seed}, stratified by query_type)",
        file=sys.stderr,
    )

    settings = get_settings()
    user_id = str(settings.default_user_id)

    all_results: dict[str, list[dict[str, Any]]] = {}
    for label, model_id in _SCENARIOS:
        scenario_letter = "A" if label == "2.5-flash" else "B"
        if args.scenario in (scenario_letter, "both"):
            all_results[label] = _run_scenario(
                label, model_id, sample, user_id, args.skip_ragas
            )

    # raw JSON 저장 (선택)
    if args.results_json:
        args.results_json.parent.mkdir(parents=True, exist_ok=True)
        # 일부 필드 (set, 등) JSON 직렬화 안정화
        serializable = {
            label: [_serialize(r) for r in rs] for label, rs in all_results.items()
        }
        args.results_json.write_text(
            json.dumps(serializable, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[OK] raw JSON 저장: {args.results_json}", file=sys.stderr)

    agg_a = _aggregate(all_results.get("2.5-flash", []))
    agg_b = _aggregate(all_results.get("2.5-flash-lite", []))
    if "2.5-flash" in all_results:
        print(
            "\n[집계 — 2.5-flash]",
            json.dumps(agg_a, ensure_ascii=False, indent=2),
            file=sys.stderr,
        )
    if "2.5-flash-lite" in all_results:
        print(
            "\n[집계 — 2.5-flash-lite]",
            json.dumps(agg_b, ensure_ascii=False, indent=2),
            file=sys.stderr,
        )

    md = _format_baseline_md(
        agg_a if "2.5-flash" in all_results else None,
        agg_b if "2.5-flash-lite" in all_results else None,
        sample_n=len(sample),
        seed=args.seed,
        skip_ragas=args.skip_ragas,
        golden_total=len(golden_full),
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(md, encoding="utf-8")
        print(f"\n[OK] markdown 저장: {args.output}", file=sys.stderr)
    else:
        print("\n" + md)

    return 0


def _serialize(row: dict[str, Any]) -> dict[str, Any]:
    """JSON 직렬화 — set 타입 list 로, query_parsed dict 그대로."""
    out: dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, set):
            out[k] = sorted(v)
        else:
            out[k] = v
    return out


if __name__ == "__main__":
    sys.exit(main())
