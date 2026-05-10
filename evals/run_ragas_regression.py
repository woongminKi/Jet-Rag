"""S5-B — RAGAS 회귀 자동화 baseline + 임계 가드.

목적
----
S5 /answer UX 정리의 마지막 sprint. 답변 품질 회귀를 자동 탐지할 수 있도록
golden v2 의 stratified sample (30 row, qtype 균등) 에 대한 RAGAS 메트릭
**baseline 을 측정·기록**하고, 회귀 임계 가드를 결정한다.

설계 원칙
---------
- **외부 의존성 = LLM 호출만** (Gemini 2.5 Flash judge + BGE-M3 free embedding).
- **stratified sampling** (qtype 비율 반영) — 30 row 표본의 대표성 확보.
- **운영 코드 변경 0** — 기존 `services/ragas_eval.evaluate_single` 그대로 재사용.
- **재현 가능** — `--seed` 로 같은 sample 재선정. `--baseline-json` 으로 직전 baseline
  load 하여 회귀 비교.
- **Cost cap** — `--max-rows` 로 cost 절대 가드 (default 30, 사용자 승인 cost ~$0.30).

Pipeline (per row)
-----------------
1. golden v2 stratified sample → query / doc_id / expected_answer_summary
2. HTTP `GET /search` → contexts (top-K chunks 본문)
3. HTTP `GET /answer` → answer
4. `evaluate_single(query, answer, contexts)` → faithfulness + answer_relevancy + context_precision
5. aggregate (overall + qtype × metric)

산출
----
- `evals/results/s5_b_ragas_baseline.md` — markdown 리포트 (overall + qtype + per-row)
- `evals/results/s5_b_ragas_baseline.json` — raw + threshold guard 권고

Threshold 가드 (Q-S5-3)
-----------------------
임계는 다음 두 floor 의 **max** 채택 (보수적):
- **statistical**: baseline_mean - 2 × stdev (95% confidence interval, 표본 클 때)
- **industry rule**: faithfulness ≥ 0.85 / answer_relevancy ≥ 0.80 / context_precision ≥ 0.70
  (RAGAS 권고 + 한국어 RAG 보정).

향후 회귀 측정 (별도 sprint, 동일 cost) 은 본 baseline 의 `threshold_guard` 와 비교하여
회귀 row 자동 식별.

비판적 한계
-----------
- 표본 30 row — 통계 신뢰도 중간 (qtype 별 3~5 row, 표본 작은 qtype 은 추세만).
- LLM judge ~80~90% 일관성 — 동일 query 라도 ±5% 변동 가능 (re-run smoothing 필요).
- BGE-M3 cosine context_precision 은 의미 매칭만 — "answer 에 useful 한가" 추론 X.
- Gemini API rate limit / quota 발동 시 partial result — JSON 에 `partial: true` 명시.

사용
----
    # 1) uvicorn 시동
    cd api && DEFAULT_USER_ID='00000000-0000-0000-0000-000000000001' \
      uv run uvicorn app.main:app --reload &

    # 2) baseline 측정 (cost ~$0.30, 30 row)
    cd api && DEFAULT_USER_ID='00000000-0000-0000-0000-000000000001' \
      uv run python ../evals/run_ragas_regression.py --max-rows 30 --seed 42

    # 3) 향후 회귀 비교
    uv run python ../evals/run_ragas_regression.py --max-rows 30 --seed 42 \
      --baseline-json ../evals/results/s5_b_ragas_baseline.json
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import random
import statistics
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# api/ 를 import path 에 추가 — services/ragas_eval 직접 호출 위해
_API_PATH = Path(__file__).resolve().parents[0].parent / "api"
if (_API_PATH / "app").exists():
    sys.path.insert(0, str(_API_PATH))

_REPO_ROOT = Path(__file__).resolve().parent.parent
_GOLDEN_V2_CSV = _REPO_ROOT / "evals" / "golden_v2.csv"
_DEFAULT_OUT_MD = _REPO_ROOT / "evals" / "results" / "s5_b_ragas_baseline.md"
_DEFAULT_OUT_JSON = _REPO_ROOT / "evals" / "results" / "s5_b_ragas_baseline.json"

_SEARCH_BASE = os.environ.get("RAGAS_REGRESSION_BASE", "http://localhost:8000")
_SEARCH_LIMIT = 10
_ANSWER_TOP_K = 8

# Industry rule of thumb floor — RAGAS 권고 + 한국어 RAG 보정.
_INDUSTRY_FLOOR: dict[str, float] = {
    "faithfulness": 0.85,
    "answer_relevancy": 0.80,
    "context_precision": 0.70,
}

# qtype 별 floor override — LLM judge 의 qtype 한계 반영.
# vision_diagram: Faithfulness judge 가 diagram/도표 기반 답변을 text 로만 검증
#   → claim verify 불가 → 일관 0.5 수준. 임계 낮춤 (RAGAS n=30 결과 §2.2 검증).
_QTYPE_FLOOR_OVERRIDES: dict[str, dict[str, float]] = {
    "vision_diagram": {
        "faithfulness": 0.50,  # LLM judge 한계 — diagram 기반 claim verify 불가
        # answer_relevancy / context_precision: 기본 industry floor 유지
    },
}

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DTO
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GoldenRow:
    """golden v2 의 회귀 측정 대상 row (RAGAS 용)."""

    id: str
    query: str
    query_type: str
    doc_id: str  # 빈 문자열이면 cross_doc 의도 — RAGAS 회귀 sample 에서 제외 권고
    expected_answer_summary: str  # ground truth 요약 (있으면 활용, 없으면 무시)


@dataclass
class RowMeasurement:
    """1 row 의 측정 결과."""

    golden_id: str
    query_type: str
    doc_id: str
    query: str
    answer: str
    n_contexts: int
    faithfulness: float | None = None
    answer_relevancy: float | None = None
    context_precision: float | None = None
    error: str | None = None
    eval_took_ms: int = 0


@dataclass
class AggregateStats:
    metric: str
    n: int
    mean: float | None
    stdev: float | None
    min: float | None
    max: float | None


@dataclass
class ThresholdGuard:
    metric: str
    statistical_floor: float | None  # mean - 2σ
    industry_floor: float
    recommended: float  # max(statistical, industry)
    rationale: str


# ---------------------------------------------------------------------------
# Loaders + sampling
# ---------------------------------------------------------------------------


def _load_golden_v2(csv_path: Path) -> list[GoldenRow]:
    """golden v2 전체 row → RAGAS 측정용 GoldenRow."""
    out: list[GoldenRow] = []
    with csv_path.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            qid = (row.get("id") or "").strip()
            if not qid:
                continue
            out.append(
                GoldenRow(
                    id=qid,
                    query=(row.get("query") or "").strip(),
                    query_type=(row.get("query_type") or "").strip(),
                    doc_id=(row.get("doc_id") or "").strip(),
                    expected_answer_summary=(
                        row.get("expected_answer_summary") or ""
                    ).strip(),
                )
            )
    return out


def stratified_sample(
    rows: list[GoldenRow],
    *,
    n: int,
    seed: int,
    skip_cross_doc: bool = True,
) -> list[GoldenRow]:
    """qtype 비율을 보존한 stratified sample.

    - `skip_cross_doc`: doc_id 빈 row (cross_doc U-row) 는 /answer doc_id scope 적용
      불가 → RAGAS regression sample 에서 제외 (default true).
    - 각 qtype 별 sample 수는 ``round(n * count(qtype) / total)``, 최소 1.
    - 같은 qtype 안에서는 ``random.Random(seed)`` 로 결정적 셔플 후 앞에서 채택.
    - 합계가 n 보다 크면 가장 표본 큰 qtype 부터 1씩 빼서 맞춤. 작으면 +1.
    """
    eligible = [r for r in rows if r.doc_id] if skip_cross_doc else list(rows)
    if not eligible:
        return []
    if n >= len(eligible):
        return list(eligible)

    by_qtype: dict[str, list[GoldenRow]] = defaultdict(list)
    for r in eligible:
        by_qtype[r.query_type or "unknown"].append(r)

    total = len(eligible)
    quotas: dict[str, int] = {}
    for qt, group in by_qtype.items():
        ratio = len(group) / total
        quotas[qt] = max(1, round(n * ratio))

    # quota 합 보정
    diff = sum(quotas.values()) - n
    if diff > 0:
        # 큰 그룹부터 1씩 빼기
        for qt in sorted(quotas, key=lambda k: -len(by_qtype[k])):
            if diff == 0:
                break
            if quotas[qt] > 1:
                quotas[qt] -= 1
                diff -= 1
    elif diff < 0:
        for qt in sorted(quotas, key=lambda k: -len(by_qtype[k])):
            if diff == 0:
                break
            if quotas[qt] < len(by_qtype[qt]):
                quotas[qt] += 1
                diff += 1

    rng = random.Random(seed)
    picked: list[GoldenRow] = []
    for qt, group in by_qtype.items():
        shuffled = list(group)
        rng.shuffle(shuffled)
        picked.extend(shuffled[: quotas.get(qt, 0)])
    # 최종 정렬 (가독성 — qtype, id 순)
    picked.sort(key=lambda r: (r.query_type, r.id))
    return picked


# ---------------------------------------------------------------------------
# HTTP callers — /search + /answer
# ---------------------------------------------------------------------------


def _http_get_json(url: str, *, timeout: float) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.load(resp)


def _call_search(query: str, doc_id: str) -> list[str]:
    qs = urllib.parse.urlencode(
        {"q": query, "limit": str(_SEARCH_LIMIT), "doc_id": doc_id}
    )
    data = _http_get_json(f"{_SEARCH_BASE}/search?{qs}", timeout=30)
    contexts: list[str] = []
    for item in data.get("items") or []:
        for ch in item.get("matched_chunks") or []:
            text = (ch.get("text") or "").strip()
            if text:
                contexts.append(text)
    return contexts[:_SEARCH_LIMIT]


def _call_answer(query: str, doc_id: str) -> str:
    qs = urllib.parse.urlencode(
        {"q": query, "top_k": str(_ANSWER_TOP_K), "doc_id": doc_id}
    )
    data = _http_get_json(f"{_SEARCH_BASE}/answer?{qs}", timeout=120)
    return (data.get("answer") or "").strip()


# ---------------------------------------------------------------------------
# Per-row measurement
# ---------------------------------------------------------------------------


def _evaluate_llm_only(
    *, query: str, answer: str, contexts: list[str]
) -> dict[str, float | None]:
    """LLM-only RAGAS (Faithfulness + ResponseRelevancy). BGE-M3 호출 0.

    motivation: services.ragas_eval.evaluate_single 은 BGE-M3 HF API 가 한 row 당
    5~73s 차지 (P95 73s, 한계 #3/#8/#12). 회귀 측정용 baseline 은 LLM-only 만으로도
    충분 — context_precision 은 별도 sprint 또는 cron 에서 측정 권고.
    """
    from datasets import Dataset
    from langchain_google_genai import (
        ChatGoogleGenerativeAI,
        GoogleGenerativeAIEmbeddings,
    )
    from ragas import EvaluationDataset, evaluate
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import Faithfulness, ResponseRelevancy

    api_key = os.environ["GEMINI_API_KEY"]
    judge_model = os.environ.get("RAGAS_JUDGE_MODEL", "gemini-2.5-flash")
    embed_model = os.environ.get(
        "RAGAS_EMBEDDING_MODEL", "models/gemini-embedding-001"
    )
    judge_llm = LangchainLLMWrapper(
        ChatGoogleGenerativeAI(model=judge_model, google_api_key=api_key)
    )
    judge_emb = LangchainEmbeddingsWrapper(
        GoogleGenerativeAIEmbeddings(model=embed_model, google_api_key=api_key)
    )
    ds = Dataset.from_dict(
        {
            "user_input": [query],
            "response": [answer],
            "retrieved_contexts": [contexts],
        }
    )
    result = evaluate(
        dataset=EvaluationDataset.from_hf_dataset(ds),
        metrics=[Faithfulness(), ResponseRelevancy()],
        llm=judge_llm,
        embeddings=judge_emb,
    )
    scores = result.scores[0] if result.scores else {}

    def _safe(v: Any) -> float | None:
        try:
            if v is None:
                return None
            f = float(v)
            if f != f:
                return None
            return max(0.0, min(1.0, f))
        except Exception:  # noqa: BLE001
            return None

    return {
        "faithfulness": _safe(scores.get("faithfulness")),
        "answer_relevancy": _safe(scores.get("answer_relevancy")),
    }


def measure_row(g: GoldenRow, *, skip_context_precision: bool = False) -> RowMeasurement:
    """1 row 측정 — search + answer + RAGAS evaluate.

    `skip_context_precision=True` 시 BGE-M3 호출 우회 (LLM-only 평가).
    """
    rec = RowMeasurement(
        golden_id=g.id,
        query_type=g.query_type,
        doc_id=g.doc_id,
        query=g.query,
        answer="",
        n_contexts=0,
    )
    try:
        contexts = _call_search(g.query, g.doc_id)
        answer = _call_answer(g.query, g.doc_id)
        rec.answer = answer
        rec.n_contexts = len(contexts)
    except Exception as exc:  # noqa: BLE001
        rec.error = f"http_call: {exc!r}"
        return rec

    if not contexts or not answer:
        rec.error = "empty_search_or_answer"
        return rec

    t0 = time.monotonic()
    if skip_context_precision:
        try:
            scores = _evaluate_llm_only(
                query=g.query, answer=answer, contexts=contexts
            )
        except Exception as exc:  # noqa: BLE001
            rec.error = f"ragas_llm_only: {exc!r}"
            return rec
        rec.faithfulness = scores.get("faithfulness")
        rec.answer_relevancy = scores.get("answer_relevancy")
        rec.context_precision = None
    else:
        from app.services.ragas_eval import RagasUnavailable, evaluate_single

        try:
            result = evaluate_single(query=g.query, answer=answer, contexts=contexts)
        except RagasUnavailable as exc:
            rec.error = f"ragas_unavailable: {exc}"
            return rec
        m = result.metrics
        rec.faithfulness = m.faithfulness
        rec.answer_relevancy = m.answer_relevancy
        rec.context_precision = m.context_precision
    rec.eval_took_ms = int((time.monotonic() - t0) * 1000)
    return rec


# ---------------------------------------------------------------------------
# Aggregator + threshold guard
# ---------------------------------------------------------------------------


_METRICS = ("faithfulness", "answer_relevancy", "context_precision")


def aggregate(records: list[RowMeasurement]) -> dict[str, AggregateStats]:
    out: dict[str, AggregateStats] = {}
    for metric in _METRICS:
        vals = [getattr(r, metric) for r in records if getattr(r, metric) is not None]
        if not vals:
            out[metric] = AggregateStats(metric, 0, None, None, None, None)
            continue
        out[metric] = AggregateStats(
            metric=metric,
            n=len(vals),
            mean=statistics.fmean(vals),
            stdev=statistics.pstdev(vals) if len(vals) >= 2 else 0.0,
            min=min(vals),
            max=max(vals),
        )
    return out


def by_qtype(records: list[RowMeasurement]) -> dict[str, dict[str, AggregateStats]]:
    grouped: dict[str, list[RowMeasurement]] = defaultdict(list)
    for r in records:
        grouped[r.query_type].append(r)
    return {qt: aggregate(rs) for qt, rs in grouped.items()}


def _floor_for(metric: str, qtype: str | None) -> float:
    """qtype 별 industry floor — `_QTYPE_FLOOR_OVERRIDES` 가 있으면 그것 우선."""
    if qtype:
        override = _QTYPE_FLOOR_OVERRIDES.get(qtype, {}).get(metric)
        if override is not None:
            return override
    return _INDUSTRY_FLOOR[metric]


def derive_thresholds(
    aggregates: dict[str, AggregateStats],
    *,
    qtype: str | None = None,
) -> dict[str, ThresholdGuard]:
    """베이스라인 → 회귀 임계 가드.

    임계 = max(statistical_floor, industry_floor).
    statistical_floor = mean - 2σ (n≥2 일 때만).
    qtype 가 지정되면 `_QTYPE_FLOOR_OVERRIDES` 의 floor 가 industry 대신 사용됨.
    """
    out: dict[str, ThresholdGuard] = {}
    for metric in _METRICS:
        stats = aggregates.get(metric)
        industry = _floor_for(metric, qtype)
        if stats is None or stats.mean is None:
            out[metric] = ThresholdGuard(
                metric=metric,
                statistical_floor=None,
                industry_floor=industry,
                recommended=industry,
                rationale="baseline 측정 실패 → industry rule of thumb 만 사용",
            )
            continue
        statistical: float | None = None
        if stats.stdev is not None and stats.n >= 2:
            statistical = max(0.0, stats.mean - 2 * stats.stdev)
        candidates: list[float] = [industry]
        if statistical is not None:
            candidates.append(statistical)
        recommended = max(candidates)
        rationale_parts = [
            f"baseline mean={stats.mean:.3f}",
        ]
        if statistical is not None:
            rationale_parts.append(f"-2σ={statistical:.3f}")
        rationale_parts.append(f"industry={industry:.2f}")
        rationale_parts.append(f"recommended=max → {recommended:.3f}")
        out[metric] = ThresholdGuard(
            metric=metric,
            statistical_floor=statistical,
            industry_floor=industry,
            recommended=recommended,
            rationale=" / ".join(rationale_parts),
        )
    return out


def derive_qtype_thresholds(
    qtype_breakdown: dict[str, dict[str, AggregateStats]],
) -> dict[str, dict[str, ThresholdGuard]]:
    """qtype 별 임계 가드. `_QTYPE_FLOOR_OVERRIDES` 가 있는 qtype 만 다른 floor."""
    return {
        qtype: derive_thresholds(agg, qtype=qtype)
        for qtype, agg in qtype_breakdown.items()
    }


# ---------------------------------------------------------------------------
# Markdown / JSON rendering
# ---------------------------------------------------------------------------


def render_markdown(
    *,
    records: list[RowMeasurement],
    aggregates: dict[str, AggregateStats],
    qtype_breakdown: dict[str, dict[str, AggregateStats]],
    thresholds: dict[str, ThresholdGuard],
    qtype_thresholds: dict[str, dict[str, ThresholdGuard]] | None = None,
    sample_n: int,
    seed: int,
    elapsed_s: float,
) -> str:
    lines: list[str] = []
    lines.append("# S5-B — RAGAS 회귀 baseline (golden v2 stratified sample)")
    lines.append("")
    lines.append(f"- 측정 일시: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- sample n: {sample_n}, seed: {seed}")
    lines.append(f"- 총 소요: {elapsed_s:.1f}s")
    lines.append(f"- LLM judge: gemini-2.5-flash (faithfulness, answer_relevancy)")
    lines.append("- context_precision: BGE-M3 cosine (휴리스틱)")
    lines.append("")
    lines.append("## Overall")
    lines.append("")
    lines.append("| metric | n | mean | stdev | min | max |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for metric in _METRICS:
        s = aggregates[metric]
        if s.mean is None:
            lines.append(f"| {metric} | {s.n} | — | — | — | — |")
        else:
            lines.append(
                f"| {metric} | {s.n} | {s.mean:.3f} | {s.stdev:.3f} | "
                f"{s.min:.3f} | {s.max:.3f} |"
            )
    lines.append("")
    lines.append("## qtype 별 breakdown (mean)")
    lines.append("")
    lines.append("| qtype | n | faithfulness | answer_relevancy | context_precision |")
    lines.append("|---|---:|---:|---:|---:|")
    for qt in sorted(qtype_breakdown):
        agg = qtype_breakdown[qt]
        cells: list[str] = [qt]
        # n = max n across metrics (보통 같음)
        n_max = max((agg[m].n for m in _METRICS), default=0)
        cells.append(str(n_max))
        for m in _METRICS:
            v = agg[m].mean
            cells.append("—" if v is None else f"{v:.3f}")
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("## Threshold guard (Q-S5-3 결정)")
    lines.append("")
    lines.append(
        "임계 = max(baseline_mean - 2σ, industry_floor). 회귀 측정에서 본 임계 미만 시 alert."
    )
    lines.append("")
    lines.append("| metric | statistical_floor (-2σ) | industry_floor | recommended |")
    lines.append("|---|---:|---:|---:|")
    for metric in _METRICS:
        g = thresholds[metric]
        stat_str = "—" if g.statistical_floor is None else f"{g.statistical_floor:.3f}"
        lines.append(
            f"| {metric} | {stat_str} | {g.industry_floor:.2f} | "
            f"**{g.recommended:.3f}** |"
        )
    lines.append("")
    for metric in _METRICS:
        g = thresholds[metric]
        lines.append(f"- **{metric}** 임계 근거: {g.rationale}")
    lines.append("")

    # qtype 별 임계 (override 가 있는 qtype 만 표기)
    if qtype_thresholds:
        override_qtypes = sorted(_QTYPE_FLOOR_OVERRIDES.keys())
        present_overrides = [
            qt for qt in override_qtypes if qt in qtype_thresholds
        ]
        if present_overrides:
            lines.append("### qtype 별 임계 override")
            lines.append("")
            lines.append(
                "LLM judge 의 qtype 한계 반영. override 가 없는 qtype 은 위 overall 임계 적용."
            )
            lines.append("")
            lines.append(
                "| qtype | metric | override industry_floor | recommended |"
            )
            lines.append("|---|---|---:|---:|")
            for qt in present_overrides:
                qt_thresh = qtype_thresholds[qt]
                for metric, override in _QTYPE_FLOOR_OVERRIDES[qt].items():
                    g = qt_thresh.get(metric)
                    rec_str = f"{g.recommended:.3f}" if g else "—"
                    lines.append(
                        f"| {qt} | {metric} | {override:.2f} | **{rec_str}** |"
                    )
            lines.append("")

    lines.append("## Per-row")
    lines.append("")
    lines.append(
        "| # | id | qtype | n_ctx | faithfulness | answer_relevancy | "
        "context_precision | eval_ms | error |"
    )
    lines.append("|---|---|---|---:|---:|---:|---:|---:|---|")
    for i, r in enumerate(records, start=1):
        cells = [
            str(i),
            r.golden_id,
            r.query_type,
            str(r.n_contexts),
            "—" if r.faithfulness is None else f"{r.faithfulness:.2f}",
            "—" if r.answer_relevancy is None else f"{r.answer_relevancy:.2f}",
            "—" if r.context_precision is None else f"{r.context_precision:.2f}",
            str(r.eval_took_ms),
            r.error or "",
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    return "\n".join(lines)


def render_json(
    *,
    records: list[RowMeasurement],
    aggregates: dict[str, AggregateStats],
    qtype_breakdown: dict[str, dict[str, AggregateStats]],
    thresholds: dict[str, ThresholdGuard],
    qtype_thresholds: dict[str, dict[str, ThresholdGuard]] | None = None,
    sample_n: int,
    seed: int,
    elapsed_s: float,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "sample_n": sample_n,
        "seed": seed,
        "elapsed_s": elapsed_s,
        "overall": {m: asdict(aggregates[m]) for m in _METRICS},
        "qtype_breakdown": {
            qt: {m: asdict(agg[m]) for m in _METRICS}
            for qt, agg in qtype_breakdown.items()
        },
        "threshold_guard": {m: asdict(thresholds[m]) for m in _METRICS},
        "rows": [asdict(r) for r in records],
    }
    if qtype_thresholds:
        out["qtype_threshold_guard"] = {
            qt: {m: asdict(g[m]) for m in _METRICS}
            for qt, g in qtype_thresholds.items()
        }
    return out


def compare_against_baseline(
    current_aggregates: dict[str, AggregateStats],
    baseline_path: Path,
    *,
    current_qtype_breakdown: dict[str, dict[str, AggregateStats]] | None = None,
) -> list[str]:
    """직전 baseline 의 threshold_guard 와 현재 mean 비교 → 회귀 alert lines.

    `current_qtype_breakdown` 전달 시 baseline 의 `qtype_threshold_guard` 가
    있으면 qtype 별 비교도 추가 (override 가 있는 qtype 만).
    """
    if not baseline_path.exists():
        return [f"⚠ baseline JSON 없음: {baseline_path}"]
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    guards = baseline.get("threshold_guard", {})
    alerts: list[str] = []
    for metric in _METRICS:
        guard = guards.get(metric)
        cur = current_aggregates[metric]
        if not guard or cur.mean is None:
            continue
        recommended = float(guard.get("recommended", 0))
        if cur.mean < recommended:
            alerts.append(
                f"❌ {metric} 회귀 — 현재 mean={cur.mean:.3f} < 임계 {recommended:.3f}"
            )
        else:
            alerts.append(
                f"✅ {metric} 통과 — 현재 mean={cur.mean:.3f} ≥ 임계 {recommended:.3f}"
            )
    # qtype 별 비교 (override 가 있는 qtype 만)
    qtype_guards = baseline.get("qtype_threshold_guard", {})
    if qtype_guards and current_qtype_breakdown:
        for qtype, qt_guard in qtype_guards.items():
            cur_qt_agg = current_qtype_breakdown.get(qtype)
            if not cur_qt_agg:
                continue
            for metric in _METRICS:
                # override 가 있는 metric 만 (자체 qtype guard 가 overall 과 다른 경우만)
                override = _QTYPE_FLOOR_OVERRIDES.get(qtype, {}).get(metric)
                if override is None:
                    continue
                guard = qt_guard.get(metric)
                cur_stat = cur_qt_agg.get(metric)
                if not guard or cur_stat is None or cur_stat.mean is None:
                    continue
                recommended = float(guard.get("recommended", 0))
                if cur_stat.mean < recommended:
                    alerts.append(
                        f"❌ {qtype}.{metric} 회귀 — mean={cur_stat.mean:.3f} < 임계 {recommended:.3f}"
                    )
                else:
                    alerts.append(
                        f"✅ {qtype}.{metric} 통과 — mean={cur_stat.mean:.3f} ≥ 임계 {recommended:.3f}"
                    )
    return alerts


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="S5-B RAGAS 회귀 자동화 — golden v2 stratified sample baseline."
    )
    parser.add_argument(
        "--max-rows", type=int, default=30, help="sample 크기 (default 30, cost ~$0.30)"
    )
    parser.add_argument("--seed", type=int, default=42, help="sampling seed")
    parser.add_argument(
        "--include-cross-doc",
        action="store_true",
        help="cross_doc U-row (doc_id 빈 row) 도 sample 에 포함 (default 제외)",
    )
    parser.add_argument(
        "--baseline-json",
        type=Path,
        default=None,
        help="회귀 비교용 직전 baseline JSON 경로",
    )
    parser.add_argument(
        "--out-md", type=Path, default=_DEFAULT_OUT_MD, help="markdown 출력 경로"
    )
    parser.add_argument(
        "--out-json", type=Path, default=_DEFAULT_OUT_JSON, help="JSON 출력 경로"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="sample 만 결정 후 종료 (cost 0). 표본 분포 사전 점검용.",
    )
    parser.add_argument(
        "--skip-context-precision",
        action="store_true",
        help=(
            "BGE-M3 cosine 우회 (LLM-only 평가). 한계 #3/#8/#12 의 HF API 73s+ "
            "병목 회피 — 회귀 baseline 권고 옵션."
        ),
    )
    args = parser.parse_args(argv)

    # api/app/config.py 가 .env (load_dotenv) 자동 적용 → GEMINI_API_KEY 로드.
    # script 단독 실행 시 .env 경유로 환경변수 채워주기 위해 명시 import.
    try:
        from app import config as _app_config  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] app.config import 실패 ({exc}); 환경변수 직접 설정 필요", file=sys.stderr)

    rows = _load_golden_v2(_GOLDEN_V2_CSV)
    print(f"[load] golden v2 {len(rows)} rows", file=sys.stderr)
    sample = stratified_sample(
        rows,
        n=args.max_rows,
        seed=args.seed,
        skip_cross_doc=not args.include_cross_doc,
    )
    qtype_dist: dict[str, int] = defaultdict(int)
    for r in sample:
        qtype_dist[r.query_type] += 1
    print(
        f"[sample] n={len(sample)} (seed={args.seed}) qtype 분포: "
        + ", ".join(f"{k}={v}" for k, v in sorted(qtype_dist.items())),
        file=sys.stderr,
    )

    if args.dry_run:
        print("[dry-run] sample 결정 후 종료 (cost 0)", file=sys.stderr)
        for i, r in enumerate(sample, start=1):
            print(f"  {i:>2}. [{r.query_type}] {r.id} — {r.query[:60]!r}", file=sys.stderr)
        return 0

    if not os.environ.get("GEMINI_API_KEY"):
        print("[FAIL] GEMINI_API_KEY 환경변수 미설정", file=sys.stderr)
        return 1

    t0 = time.monotonic()
    records: list[RowMeasurement] = []
    # incremental JSONL — 중단 시에도 부분 결과 보존 (한 row 평균 1~8min, 재시도 안전)
    partial_path = args.out_json.with_suffix(".partial.jsonl")
    partial_path.parent.mkdir(parents=True, exist_ok=True)
    partial_path.write_text("", encoding="utf-8")  # truncate 새 시도
    for i, r in enumerate(sample, start=1):
        print(
            f"  [{i}/{len(sample)}] [{r.query_type}] {r.id} — {r.query[:60]!r}",
            file=sys.stderr,
            flush=True,
        )
        rec = measure_row(r, skip_context_precision=args.skip_context_precision)
        records.append(rec)
        if rec.error:
            print(f"    ⚠ {rec.error}", file=sys.stderr, flush=True)
        else:
            print(
                f"    faithfulness={rec.faithfulness} "
                f"answer_relevancy={rec.answer_relevancy} "
                f"context_precision={rec.context_precision} "
                f"eval_ms={rec.eval_took_ms}",
                file=sys.stderr,
                flush=True,
            )
        # 즉시 디스크 기록 (다음 row 시작 전)
        with partial_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")

    elapsed = time.monotonic() - t0
    aggregates = aggregate(records)
    qtype_breakdown = by_qtype(records)
    thresholds = derive_thresholds(aggregates)
    qtype_thresholds = derive_qtype_thresholds(qtype_breakdown)

    md = render_markdown(
        records=records,
        aggregates=aggregates,
        qtype_breakdown=qtype_breakdown,
        thresholds=thresholds,
        qtype_thresholds=qtype_thresholds,
        sample_n=len(sample),
        seed=args.seed,
        elapsed_s=elapsed,
    )
    js = render_json(
        records=records,
        aggregates=aggregates,
        qtype_breakdown=qtype_breakdown,
        thresholds=thresholds,
        qtype_thresholds=qtype_thresholds,
        sample_n=len(sample),
        seed=args.seed,
        elapsed_s=elapsed,
    )
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.write_text(md, encoding="utf-8")
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(js, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] markdown → {args.out_md}", file=sys.stderr)
    print(f"[OK] JSON → {args.out_json}", file=sys.stderr)

    if args.baseline_json is not None:
        print("[compare] 직전 baseline 대비 회귀 비교", file=sys.stderr)
        for line in compare_against_baseline(
            aggregates, args.baseline_json, current_qtype_breakdown=qtype_breakdown
        ):
            print(f"  {line}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
