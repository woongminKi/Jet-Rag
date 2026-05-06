"""W26 S1 D5 — D2-D 모델 변경 (gemini-2.5-flash → gemini-2.0-flash) 회귀 측정.

목적
----
Phase 1 S0 D2-D ship (factory.py 의 default 모델 매핑 변경) 의 회귀 영향을
사용자 작성 골든셋 v0.6 (33 entry, 9 query_type) 으로 정량 측정.

측정 시나리오
------------
- 시나리오 A — 현재 default `gemini-2.0-flash` (D2-D 후)
- 시나리오 B — 이전 default `gemini-2.5-flash` (D2-D 전, ENV override)

본질적 한계 (정직 인정)
----------------------
- v0.6 user 골든셋은 chunk_idx ground truth 없음 → 검색 retrieval 메트릭 (R@10/MRR/nDCG)
  직접 측정 불가. 본 스크립트는 **답변 단계 비교만** 측정한다.
- 모델 변경은 LLM 만 영향 (BGE-M3 embedding + RPC + reranker 는 LLM 미사용).
  검색 metric 회귀는 정의상 **항상 0** — chunk RRF 결과는 두 시나리오 모두 동일.
- doc 매칭률 (expected_doc_title vs sources[*].doc_title) 은 본 스크립트가 측정.
  단, 모델과 무관 (sources 는 LLM 호출 전 검색에서 결정).
- 답변 품질 자동 측정은 한국어 휴리스틱 (must_include 키워드 매칭) — RAGAS judge 비용 회피.
  RAGAS judge 추가 측정은 결과 보고 후 사용자 결정.

측정 메트릭 (휴리스틱)
--------------------
- `must_include_hit` — 답변에 must_include 의 키워드가 들어갔는가 (Recall, OR 매칭)
- `expected_doc_hit` — sources 의 doc_title 중 expected_doc_title 매칭이 있는가
- `answer_len` — 답변 글자수 (정보량 proxy)
- `latency_ms` — 답변 응답 시간
- `out_of_scope_correct` — negative=true 인 query 에 "찾지 못했습니다" 응답했는가

ENV
---
- 시나리오 A: 그대로 실행 (factory default = 2.0-flash)
- 시나리오 B: 본 스크립트 안에서 `JETRAG_LLM_MODEL_ANSWER=gemini-2.5-flash` 토글

비용 추정
---------
- 33 query × 2 시나리오 = 66 LLM 호출
- 2.0-flash: input $0.10 + output $0.40 / 1M tokens → ~$0.0003/query
- 2.5-flash: input $0.30 + output $2.50 / 1M tokens → ~$0.001/query
- 합계 < $0.1 (가드레일 통과)

사용
----
    cd api && uv run python ../evals/run_v06_user_answer.py \
        --output ../evals/results_v0.6_comparison.md
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any

# api/ 를 import path 에 추가 — answer router + factory 직접 호출 위해.
_API_PATH = Path(__file__).resolve().parents[1] / "api"
sys.path.insert(0, str(_API_PATH))

from app.adapters.factory import get_gemini_pricing, get_llm_provider  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.routers.answer import _build_messages, _gather_chunks  # noqa: E402

_GOLDEN_CSV = Path(__file__).parent / "golden_v0.6_user.csv"
_TOP_K = 5  # /answer default 와 동일 (LLM 에 전달할 chunks)
_TEMPERATURE = 0.2  # answer 라우터 default

# 측정 시나리오 — (라벨, JETRAG_LLM_MODEL_ANSWER override)
_SCENARIOS: list[tuple[str, str | None]] = [
    ("2.0-flash", None),  # ENV 미설정 → factory default (D2-D 후)
    ("2.5-flash", "gemini-2.5-flash"),  # ENV override (D2-D 전 시나리오 재현)
]


def _load_golden(path: Path) -> list[dict[str, Any]]:
    """v0.6 user 골든셋 로드. negative='true' 는 out_of_scope 케이스."""
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            r["must_include"] = [
                t.strip() for t in (r.get("must_include") or "").split(";") if t.strip()
            ]
            r["expected_doc_titles"] = [
                t.strip() for t in (r.get("expected_doc_title") or "").split("|") if t.strip()
            ]
            r["negative"] = (r.get("negative") or "").strip().lower() == "true"
            rows.append(r)
    return rows


def _measure_one(
    qa: dict[str, Any], user_id: str
) -> dict[str, Any]:
    """단일 query 측정 — _gather_chunks + factory(answer) + LLM complete.

    answer 라우터의 lru_cache `_get_llm()` 우회 — factory 직접 호출로 ENV 매번 읽음.
    """
    query = unicodedata.normalize("NFC", qa["query"].strip())
    t0 = time.monotonic()

    # 검색 (LLM 무관 — 두 시나리오 동일 결과)
    try:
        chunks, query_parsed = _gather_chunks(
            query=query, doc_id=None, top_k=_TOP_K, user_id=user_id
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "query": query,
            "error": f"search_failed: {exc}",
            "took_ms": int((time.monotonic() - t0) * 1000),
        }

    # 검색 결과 0 → /answer 엔드포인트 패턴: LLM 호출 회피
    if not chunks:
        answer_text = "제공된 자료에서 해당 정보를 찾지 못했습니다."
        model_used = "(no_llm_call)"
        llm_ms = 0
    else:
        messages = _build_messages(query, chunks)
        # 핵심 — factory 직접 호출. ENV (JETRAG_LLM_MODEL_ANSWER) 매번 새로 읽음.
        llm = get_llm_provider("answer")
        model_used = getattr(llm, "model", "?")
        t_llm = time.monotonic()
        try:
            answer_text = llm.complete(messages, temperature=_TEMPERATURE).strip()
        except Exception as exc:  # noqa: BLE001
            return {
                "query": query,
                "error": f"llm_failed: {exc}",
                "model_used": model_used,
                "took_ms": int((time.monotonic() - t0) * 1000),
            }
        llm_ms = int((time.monotonic() - t_llm) * 1000)

    took_ms = int((time.monotonic() - t0) * 1000)
    return {
        "query": query,
        "answer": answer_text,
        "answer_len": len(answer_text),
        "model_used": model_used,
        "took_ms": took_ms,
        "llm_ms": llm_ms,
        "n_chunks": len(chunks),
        "source_doc_titles": [c.get("doc_title") for c in chunks if c.get("doc_title")],
    }


def _evaluate_metrics(
    qa: dict[str, Any], measurement: dict[str, Any]
) -> dict[str, Any]:
    """측정 결과 + golden GT → 휴리스틱 메트릭."""
    if "error" in measurement:
        return {**measurement, "must_include_hit": None, "expected_doc_hit": None}

    answer = measurement.get("answer", "")
    answer_norm = unicodedata.normalize("NFC", answer).lower()

    # must_include — 키워드 OR 매칭 (한국어 substring)
    must_keys = qa.get("must_include") or []
    if must_keys:
        hits = sum(1 for k in must_keys if k.lower() in answer_norm)
        must_include_hit = hits / len(must_keys)
    else:
        must_include_hit = None  # negative 케이스 등

    # expected_doc — sources 의 doc_title 중 expected 매칭 (substring 매칭)
    expected_titles = qa.get("expected_doc_titles") or []
    source_titles = measurement.get("source_doc_titles") or []
    if expected_titles:
        expected_doc_hit = any(
            any(et.lower() in (st or "").lower() for st in source_titles)
            for et in expected_titles
        )
    else:
        expected_doc_hit = None

    # out_of_scope — negative=true 시 "찾지 못했습니다" 답변 expected
    out_of_scope_correct: bool | None = None
    if qa.get("negative"):
        out_of_scope_correct = (
            "찾지 못" in answer or "없습니다" in answer or "no_llm_call" in measurement.get("model_used", "")
        )

    return {
        **measurement,
        "must_include_hit": must_include_hit,
        "expected_doc_hit": expected_doc_hit,
        "out_of_scope_correct": out_of_scope_correct,
    }


def _run_scenario(
    label: str,
    model_override: str | None,
    golden: list[dict[str, Any]],
    user_id: str,
) -> list[dict[str, Any]]:
    """시나리오 1회 실행. ENV override 토글 후 33 query 순차 측정."""
    if model_override:
        os.environ["JETRAG_LLM_MODEL_ANSWER"] = model_override
        print(f"\n[scenario {label}] ENV JETRAG_LLM_MODEL_ANSWER={model_override}", file=sys.stderr)
    else:
        os.environ.pop("JETRAG_LLM_MODEL_ANSWER", None)
        print(f"\n[scenario {label}] ENV JETRAG_LLM_MODEL_ANSWER 제거 (factory default)", file=sys.stderr)

    results: list[dict[str, Any]] = []
    for i, qa in enumerate(golden, start=1):
        m = _measure_one(qa, user_id)
        e = _evaluate_metrics(qa, m)
        e["query_type"] = qa.get("query_type", "")
        results.append(e)
        if "error" in e:
            print(f"  [{i}/{len(golden)}] ERR {e['query'][:40]!r} — {e['error'][:60]}", file=sys.stderr)
        else:
            print(
                f"  [{i}/{len(golden)}] {e.get('model_used', '?')} "
                f"must={e.get('must_include_hit')} "
                f"doc={e.get('expected_doc_hit')} "
                f"oos={e.get('out_of_scope_correct')} "
                f"{e['took_ms']}ms",
                file=sys.stderr,
            )
    return results


def _aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    """시나리오 results → 평균 메트릭."""
    successful = [r for r in results if "error" not in r]
    if not successful:
        return {"n_total": len(results), "n_success": 0}

    must_vals = [r["must_include_hit"] for r in successful if r["must_include_hit"] is not None]
    doc_vals = [r["expected_doc_hit"] for r in successful if r["expected_doc_hit"] is not None]
    oos_vals = [r["out_of_scope_correct"] for r in successful if r["out_of_scope_correct"] is not None]
    lat_vals = [r["took_ms"] for r in successful]
    llm_lat_vals = [r["llm_ms"] for r in successful if r.get("llm_ms")]
    ans_len_vals = [r["answer_len"] for r in successful]

    return {
        "n_total": len(results),
        "n_success": len(successful),
        "must_include_recall": statistics.mean(must_vals) if must_vals else 0.0,
        "must_include_n": len(must_vals),
        "expected_doc_hit_rate": (sum(doc_vals) / len(doc_vals)) if doc_vals else 0.0,
        "expected_doc_n": len(doc_vals),
        "out_of_scope_correct_rate": (sum(oos_vals) / len(oos_vals)) if oos_vals else 0.0,
        "out_of_scope_n": len(oos_vals),
        "latency_avg_ms": statistics.mean(lat_vals) if lat_vals else 0.0,
        "latency_p95_ms": sorted(lat_vals)[int(len(lat_vals) * 0.95)] if lat_vals else 0,
        "llm_latency_avg_ms": statistics.mean(llm_lat_vals) if llm_lat_vals else 0.0,
        "answer_len_avg": statistics.mean(ans_len_vals) if ans_len_vals else 0.0,
    }


def _format_comparison_md(
    agg_a: dict[str, Any], agg_b: dict[str, Any], golden_n: int
) -> str:
    """비교 표 markdown — A=2.0-flash (현재 default), B=2.5-flash (이전 default)."""
    pricing_a = get_gemini_pricing("gemini-2.0-flash")
    pricing_b = get_gemini_pricing("gemini-2.5-flash")
    # 단가 ratio (input + output 평균 가정 — 실 토큰 수 로깅은 vision_usage_log 만 — answer 는 미로깅)
    cost_ratio_input = pricing_b["input"] / pricing_a["input"]
    cost_ratio_output = pricing_b["output"] / pricing_a["output"]

    lines: list[str] = []
    lines.append("# v0.6 user 골든셋 — gemini-2.0-flash vs gemini-2.5-flash 비교")
    lines.append("")
    lines.append(f"- 측정 일시: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 골든셋: `evals/golden_v0.6_user.csv` ({golden_n} entry, 9 query_type)")
    lines.append(f"- 시나리오 A: **gemini-2.0-flash** (Phase 1 S0 D2-D 후 default)")
    lines.append(f"- 시나리오 B: **gemini-2.5-flash** (D2-D 전 default, ENV override 재현)")
    lines.append(f"- top_k: {_TOP_K}, temperature: {_TEMPERATURE}")
    lines.append(f"- 측정 방식: in-process (answer 라우터 _gather_chunks + factory 직접 호출, lru_cache 우회)")
    lines.append("")
    lines.append("## 지표 비교")
    lines.append("")
    lines.append("| 지표 | 2.5-flash (B) | 2.0-flash (A) | 차이 (A-B) | 판정 |")
    lines.append("|---|---:|---:|---:|---|")

    def _row(label: str, key: str, percent: bool = True, higher_better: bool = True) -> str:
        v_b = agg_b.get(key) or 0.0
        v_a = agg_a.get(key) or 0.0
        diff = v_a - v_b
        if percent:
            diff_pp = diff * 100
            verdict = _verdict_pp(diff_pp, higher_better)
            return (
                f"| {label} | {v_b * 100:.1f}% | {v_a * 100:.1f}% | "
                f"{'+' if diff_pp >= 0 else ''}{diff_pp:.1f}pp | {verdict} |"
            )
        # ms 또는 글자수 — higher_better 반대 (latency 는 작을수록 좋음)
        verdict = _verdict_pp(diff if higher_better else -diff, higher_better=True)
        return (
            f"| {label} | {v_b:.0f} | {v_a:.0f} | "
            f"{'+' if diff >= 0 else ''}{diff:.0f} | {verdict} |"
        )

    lines.append(_row("must_include 키워드 hit (recall)", "must_include_recall"))
    lines.append(_row("expected_doc hit (doc-level)", "expected_doc_hit_rate"))
    lines.append(_row("out_of_scope 정확도 (negative)", "out_of_scope_correct_rate"))
    lines.append(_row("평균 답변 길이 (글자)", "answer_len_avg", percent=False, higher_better=True))
    lines.append(_row("평균 latency (ms)", "latency_avg_ms", percent=False, higher_better=False))
    lines.append(_row("평균 LLM-only latency (ms)", "llm_latency_avg_ms", percent=False, higher_better=False))
    lines.append("")

    lines.append("## 비용 (단가 기반 예상)")
    lines.append("")
    lines.append("| 항목 | 2.0-flash | 2.5-flash | ratio (B/A) |")
    lines.append("|---|---:|---:|---:|")
    lines.append(
        f"| input $/1M tokens | ${pricing_a['input']:.3f} | ${pricing_b['input']:.3f} | "
        f"{cost_ratio_input:.1f}× |"
    )
    lines.append(
        f"| output $/1M tokens | ${pricing_a['output']:.3f} | ${pricing_b['output']:.3f} | "
        f"{cost_ratio_output:.1f}× |"
    )
    lines.append("")
    lines.append("## 검색 메트릭 (R@10 / MRR / nDCG)")
    lines.append("")
    lines.append(
        "- v0.6 user 골든셋은 chunk_idx ground truth 가 없어 직접 측정 불가."
    )
    lines.append(
        "- 모델 변경은 LLM 만 영향 (BGE-M3 embedding + RPC + reranker 는 LLM 미사용) → "
        "정의상 검색 메트릭 회귀는 0. v0.5 auto 골든셋 (chunk_idx GT 보유) 별도 측정 권고."
    )
    lines.append("")
    lines.append("## 판정 기준 (사용자 명세)")
    lines.append("")
    lines.append("- must_include_recall, expected_doc_hit, out_of_scope_correct 의 ΔA-B 평균:")

    deltas = []
    for key in ("must_include_recall", "expected_doc_hit_rate", "out_of_scope_correct_rate"):
        a_val = agg_a.get(key) or 0.0
        b_val = agg_b.get(key) or 0.0
        deltas.append((a_val - b_val) * 100)
    avg_delta = statistics.mean(deltas) if deltas else 0.0
    lines.append(f"  - 평균 Δ = {avg_delta:+.2f}pp")

    if avg_delta >= -2.0:
        verdict = "**OK — 회귀 미미** (사용자 기준 ≥ -2pp)"
        recommendation = "현재 default (`gemini-2.0-flash`) 유지."
    elif avg_delta >= -5.0:
        verdict = "**부분 회귀** (-2 ~ -5pp)"
        recommendation = "purpose 별 ENV override 으로 일부만 2.5 회복 권고 (예: answer 만)."
    else:
        verdict = "**전체 회복 권고** (≥ -5pp 회귀)"
        recommendation = "factory 의 answer 모델 매핑을 다시 2.5-flash 로."

    lines.append(f"- 판정: {verdict}")
    lines.append(f"- 권고: {recommendation}")
    lines.append("")
    lines.append("## 비판적 한계")
    lines.append("")
    lines.append(
        "- N=33 → CI ±10pp 수준 (낮음). v0.5 auto 100+ 로 보강 시 신뢰도↑ (S1 D2 자동 골든셋)."
    )
    lines.append(
        "- LLM 비결정성 (temperature=0.2) — 1회 측정만, 반복 측정은 비용↑ 회피."
    )
    lines.append(
        "- 휴리스틱 must_include 매칭은 단순 substring — 동의어/표현 변이 false negative 가능."
    )
    lines.append(
        "- RAGAS judge (faithfulness/answer_relevancy) 는 본 측정에서 미포함. 비용 +$0.5 예상 → 사용자 결정 후 추가."
    )
    return "\n".join(lines)


def _verdict_pp(diff: float, higher_better: bool = True) -> str:
    """차이 → 한 글자 판정."""
    threshold_ok = 2.0 if higher_better else -2.0
    threshold_minor = -5.0 if higher_better else 5.0
    if higher_better:
        if diff >= threshold_ok:
            return "OK"
        if diff >= threshold_minor:
            return "minor"
        return "회귀"
    # smaller is better
    if diff <= 2.0:
        return "OK"
    if diff <= 5.0:
        return "minor"
    return "회귀"


def main() -> int:
    parser = argparse.ArgumentParser(description="v0.6 user 골든셋 모델 변경 회귀 측정")
    parser.add_argument(
        "--golden", type=Path, default=_GOLDEN_CSV,
        help=f"골든셋 CSV (default {_GOLDEN_CSV.name})",
    )
    parser.add_argument(
        "--output", "-o", type=Path, default=None,
        help="비교 표 markdown 출력 경로",
    )
    parser.add_argument(
        "--results-json", type=Path, default=None,
        help="시나리오별 raw 결과 JSON 출력 경로",
    )
    parser.add_argument(
        "--scenario", choices=["A", "B", "both"], default="both",
        help="A=2.0-flash, B=2.5-flash, both=둘 다",
    )
    args = parser.parse_args()

    if not args.golden.exists():
        print(f"[FAIL] 골든셋 미발견: {args.golden}", file=sys.stderr)
        return 1

    golden = _load_golden(args.golden)
    print(f"[OK] 골든셋 로드: {args.golden.name} ({len(golden)} entry)", file=sys.stderr)

    settings = get_settings()
    user_id = str(settings.default_user_id)

    all_results: dict[str, list[dict[str, Any]]] = {}

    if args.scenario in ("A", "both"):
        all_results["2.0-flash"] = _run_scenario("2.0-flash", None, golden, user_id)
    if args.scenario in ("B", "both"):
        all_results["2.5-flash"] = _run_scenario("2.5-flash", "gemini-2.5-flash", golden, user_id)

    # raw JSON 저장 (선택)
    if args.results_json:
        args.results_json.write_text(
            json.dumps(all_results, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[OK] raw JSON 저장: {args.results_json}", file=sys.stderr)

    # 집계 + 비교 표 (둘 다 측정 시만)
    if args.scenario == "both":
        agg_a = _aggregate(all_results["2.0-flash"])
        agg_b = _aggregate(all_results["2.5-flash"])
        print("\n[집계 — 2.0-flash]", json.dumps(agg_a, ensure_ascii=False, indent=2), file=sys.stderr)
        print("\n[집계 — 2.5-flash]", json.dumps(agg_b, ensure_ascii=False, indent=2), file=sys.stderr)

        md = _format_comparison_md(agg_a, agg_b, len(golden))
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(md, encoding="utf-8")
            print(f"\n[OK] 비교 표 저장: {args.output}", file=sys.stderr)
        else:
            print("\n" + md)
    else:
        # 단일 시나리오 — 집계만 출력
        for label, results in all_results.items():
            agg = _aggregate(results)
            print(f"\n[집계 — {label}]", json.dumps(agg, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
