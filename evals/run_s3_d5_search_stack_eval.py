"""S3 D5 — 검색 스택 정량 측정 sprint (planner v0.1 §S3 D5).

목적
----
S3 D1~D4 의 search 스택 변화 (intent_router / confidence + meta fast path /
query decomposer / reranker cap+cache+degrade+MMR) 를 골든셋 v1 (158 row) ×
3 조합 매트릭스로 7 metric 측정해 KPI DoD 충족/미달을 정량 판정한다.

3 조합 매트릭스
---------------
- (a) RRF-only (baseline)
    ENV: ``JETRAG_RERANKER_ENABLED=false``
- (b) RRF + reranker (cap20, MMR off)
    ENV: ``JETRAG_RERANKER_ENABLED=true`` ``JETRAG_RERANKER_CANDIDATE_CAP=20``
         ``JETRAG_MMR_DISABLE=1``
- (c) RRF + reranker + MMR (cap20, λ=0.7)
    ENV: ``JETRAG_RERANKER_ENABLED=true`` ``JETRAG_RERANKER_CANDIDATE_CAP=20``
         ``JETRAG_MMR_DISABLE=0`` ``JETRAG_MMR_LAMBDA=0.7``

측정 metric 7종
---------------
1. R@10 (graded — relevant 1.0, acceptable 0.5)  — `recall_at_k`
2. nDCG@10 (graded)                              — `ndcg_at_k`
3. MRR (graded — relevant 우선)                  — `mrr`
4. top-1 적중률 (정답 chunk 가 top-1 인 비율)   — 본 모듈 `_top1_hit`
5. P95 latency (row 별 wall-clock ms)
6. cache hit rate (reranker_path == "cached" 비율, 조합 b/c 한정 의미)
7. degrade frequency (reranker_path == "degraded" 비율)

설계 원칙
---------
- **운영 코드 변경 0** — ENV 토글로만 분기. ``api/`` 하위 수정 0.
- **외부 vision API 호출 0** — D3 paid_decomposition 은 default OFF 유지.
- **mock-reranker** — ``--mock-reranker`` 시 ``get_reranker_provider`` monkey
  patch. deterministic score (chunk_id sha1 기반) 로 cache hit 정상 작동.
  HF cold start latency 불확정성 회피 — CI / 회귀 비교 용도.
- **acceptable_chunks 전달** — D5 phase 1 §6.3 도구 fix 답습.
  ``recall_at_k`` / ``ndcg_at_k`` / ``mrr`` 모두 acceptable 인자 전달.
- **R@10 v2 와 동일한 chunk 추출 로직** — `run_s2_d4_pre_regression.py`
  ``_measure_baseline_retrieval`` 재사용 패턴. matched_chunks 를 rrf_score
  내림차순 정렬 후 chunk_idx 추출.

산출
----
- ``evals/results/s3_d5_results.md`` — 3 조합 × 7 metric 표 + cross-doc sub-report
- ``evals/results/s3_d5_raw.json`` — raw cell 데이터 (158 row × 3 combo × N field)

실행
----
    cd api && uv run --with pytest python ../evals/run_s3_d5_search_stack_eval.py \\
        --combo all --mock-reranker --out ../evals/results/s3_d5_results.md

전제
----
- DB 적재 완료 (D5 phase 1 sample-report reingest 포함 baseline)
- DEFAULT_USER_ID env 설정
- ``JETRAG_PAID_DECOMPOSITION_ENABLED=false`` (default — D5-ext 까지 OFF)
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import statistics
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# api/ 를 import path 에 추가 — search() 직접 호출 위해
_API_PATH = Path(__file__).resolve().parents[0].parent / "api"
if (_API_PATH / "app").exists():
    sys.path.insert(0, str(_API_PATH))

_REPO_ROOT = Path(__file__).resolve().parent.parent
_GOLDEN_CSV = _REPO_ROOT / "evals" / "golden_v1.csv"
_DEFAULT_OUT_MD = _REPO_ROOT / "evals" / "results" / "s3_d5_results.md"
_DEFAULT_OUT_JSON = _REPO_ROOT / "evals" / "results" / "s3_d5_raw.json"

# 검색 응답 top-K — search 함수의 limit 인자.
# 한 doc 에 매칭 청크 최대 50건 가져온다 (cross-doc / single-doc 모두 동일 의도).
_SEARCH_LIMIT = 50
# top-1 / cross-doc distinct 측정에 사용하는 top-K.
_TOPK_TOP1 = 1
_TOPK_CROSS_DOC = 5
_CROSS_DOC_DISTINCT_THRESHOLD = 3

# metric 키 화이트리스트 — argparse `--metrics` 검증.
_ALLOWED_METRICS = {
    "r10",
    "ndcg",
    "mrr",
    "top1",
    "latency",
    "cache",
    "degrade",
}

# 조합 라벨 → ENV dict.
# (b)/(c) 는 reranker on. (c) 는 추가로 MMR_DISABLE=0 + LAMBDA=0.7.
_COMBO_ENV: dict[str, dict[str, str]] = {
    "a": {
        "JETRAG_RERANKER_ENABLED": "false",
        # MMR 도 off — RRF-only baseline 의 의도.
        "JETRAG_MMR_DISABLE": "1",
    },
    "b": {
        "JETRAG_RERANKER_ENABLED": "true",
        "JETRAG_RERANKER_CANDIDATE_CAP": "20",
        "JETRAG_MMR_DISABLE": "1",
    },
    "c": {
        "JETRAG_RERANKER_ENABLED": "true",
        "JETRAG_RERANKER_CANDIDATE_CAP": "20",
        "JETRAG_MMR_DISABLE": "0",
        "JETRAG_MMR_LAMBDA": "0.7",
    },
}

_COMBO_LABEL: dict[str, str] = {
    "a": "RRF-only",
    "b": "RRF+reranker(cap20)",
    "c": "RRF+reranker+MMR(λ=0.7)",
}


# ---------------------------------------------------------------------------
# DTO
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GoldenRow:
    """골든셋 v1 의 측정 대상 row.

    `relevant_chunks` 가 비어있는 row 는 chunk-level metric 측정 불가
    (recall/ndcg/mrr/top1) — latency / cache / degrade 만 의미 있음.
    """

    id: str
    query: str
    query_type: str
    doc_id: str  # UUID 또는 빈 문자열 (U-row)
    expected_doc_title: str
    relevant_chunks: tuple[int, ...]
    acceptable_chunks: tuple[int, ...]


@dataclass
class CellResult:
    """1 cell = (combo, golden_row) 의 측정값."""

    combo: str
    golden_id: str
    query_type: str
    doc_id: str
    # chunk-level metric — relevant_chunks 비어있으면 None
    recall_at_10: float | None = None
    ndcg_at_10: float | None = None
    mrr: float | None = None
    top1_hit: bool | None = None
    # 항상 측정
    latency_ms: float = 0.0
    reranker_path: str = "disabled"
    note: str = ""
    predicted_top10: list[int] = field(default_factory=list)
    # cross-doc sub-report 용 — top-5 의 distinct doc_id 수
    distinct_doc_top5: int = 0


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _load_golden_rows(csv_path: Path) -> list[GoldenRow]:
    """골든셋 v1 전체 row 로드 — utf-8-sig 로 BOM 제거."""
    out: list[GoldenRow] = []
    with csv_path.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            qid = (row.get("id") or "").strip()
            if not qid:
                continue
            relv_str = (row.get("relevant_chunks") or "").strip()
            relv = tuple(
                int(x.strip()) for x in relv_str.split(",") if x.strip().isdigit()
            )
            accept_str = (row.get("acceptable_chunks") or "").strip()
            accept = tuple(
                int(x.strip()) for x in accept_str.split(",") if x.strip().isdigit()
            )
            out.append(
                GoldenRow(
                    id=qid,
                    query=(row.get("query") or "").strip(),
                    query_type=(row.get("query_type") or "").strip(),
                    doc_id=(row.get("doc_id") or "").strip(),
                    expected_doc_title=(row.get("expected_doc_title") or "").strip(),
                    relevant_chunks=relv,
                    acceptable_chunks=accept,
                )
            )
    return out


# ---------------------------------------------------------------------------
# mock reranker — deterministic, 외부 호출 0
# ---------------------------------------------------------------------------


class _MockRerankerProvider:
    """deterministic mock — chunk_id 의 sha1 기반 score (0.0~1.0).

    실제 BGE-reranker 의 의미 매칭은 흉내내지 못하지만, 코드 path
    (cache lookup → invoke → store → cover guard skip → MMR) 는 모두 통과.
    cache hit 률 / degrade 률 / latency 측정 의미는 동일하게 보존.
    """

    def rerank(self, query: str, pairs: list[tuple[str, str]]) -> list[float]:
        # query 와 chunk_id 결합 sha1 → 0.0~1.0 normalize.
        # query 가중을 줘 같은 chunk 라도 query 별 다른 score 산출.
        query_norm = (query or "").lower().strip()
        scores: list[float] = []
        for cid, _text in pairs:
            digest = hashlib.sha1(
                f"{query_norm}|{cid}".encode("utf-8")
            ).hexdigest()
            # 16진수 8자 → 32bit int → [0, 1) float
            value = int(digest[:8], 16) / 0x1_0000_0000
            scores.append(value)
        return scores


def _patch_mock_reranker() -> None:
    """``app.adapters.impl.bge_reranker_hf.get_reranker_provider`` 를 mock 으로 교체.

    HF 호출 0. cache store / lookup 은 정상 작동 (key 가 chunk_ids 기반이라
    동일 input → 동일 output).
    """
    from app.adapters.impl import bge_reranker_hf

    mock = _MockRerankerProvider()
    bge_reranker_hf.get_reranker_provider = lambda: mock  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# combo ENV apply / restore
# ---------------------------------------------------------------------------


def _apply_combo_env(combo: str) -> dict[str, str | None]:
    """``_COMBO_ENV[combo]`` 를 ``os.environ`` 에 적용. 이전 값 반환 (restore 용).

    이전 값이 없던 키는 None 으로 마킹 — restore 시 ``del os.environ[key]``.
    """
    env = _COMBO_ENV[combo]
    saved: dict[str, str | None] = {}
    for k, v in env.items():
        saved[k] = os.environ.get(k)
        os.environ[k] = v
    return saved


def _restore_env(saved: dict[str, str | None]) -> None:
    """``_apply_combo_env`` 의 inverse — 이전 ENV 값 복원."""
    for k, prev in saved.items():
        if prev is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = prev


# ---------------------------------------------------------------------------
# 측정 — 한 cell 1회 search() 호출 + metric 계산
# ---------------------------------------------------------------------------


def _measure_one_cell(combo: str, g: GoldenRow) -> CellResult:
    """search() 호출 1회 → CellResult.

    relevant_chunks 비어있는 row 도 호출 — latency / reranker_path /
    distinct_doc_top5 는 측정 가능. chunk-level metric 만 None.
    """
    from app.routers.search import search  # noqa: E402
    from app.services.retrieval_metrics import (  # noqa: E402
        mrr as mrr_fn,
        ndcg_at_k,
        recall_at_k,
    )

    cell = CellResult(
        combo=combo,
        golden_id=g.id,
        query_type=g.query_type,
        doc_id=g.doc_id,
    )

    if not g.query:
        cell.note = "query 비어있음"
        return cell

    t_start = time.monotonic()
    try:
        resp = search(
            q=unicodedata.normalize("NFC", g.query),
            limit=_SEARCH_LIMIT,
            offset=0,
            tags=None,
            doc_type=None,
            from_date=None,
            to_date=None,
            doc_id=(g.doc_id or None),
            mode="hybrid",
            response=None,
        )
    except Exception as exc:  # noqa: BLE001
        cell.latency_ms = (time.monotonic() - t_start) * 1000.0
        cell.note = f"ERROR: {exc.__class__.__name__}: {exc}"
        return cell
    cell.latency_ms = (time.monotonic() - t_start) * 1000.0

    data = resp.model_dump()
    items: list[dict[str, Any]] = data.get("items") or []
    qp = data.get("query_parsed") or {}
    cell.reranker_path = qp.get("reranker_path") or "disabled"

    # cross-doc distinct doc_id top-5
    top5_doc_ids = [it.get("doc_id") for it in items[:_TOPK_CROSS_DOC]]
    cell.distinct_doc_top5 = len({d for d in top5_doc_ids if d})

    # chunk-level metric — doc_id 매칭된 item 의 matched_chunks 추출.
    # doc_id 비어있는 U-row 도 expected_doc_title 매칭 시도 (partial title).
    target_item = _pick_target_item(items, g)
    if target_item is None:
        cell.note = "doc 매칭 fail"
        return cell

    matched = sorted(
        target_item.get("matched_chunks") or [],
        key=lambda c: (c.get("rrf_score") or 0.0),
        reverse=True,
    )
    chunks_top: list[int] = [c["chunk_idx"] for c in matched]
    cell.predicted_top10 = chunks_top[:10]

    if g.relevant_chunks or g.acceptable_chunks:
        relv = set(g.relevant_chunks)
        accept = set(g.acceptable_chunks)
        cell.recall_at_10 = recall_at_k(
            chunks_top, relv, k=10, acceptable_chunks=accept
        )
        cell.ndcg_at_10 = ndcg_at_k(
            chunks_top, relv, k=10, acceptable_chunks=accept
        )
        cell.mrr = mrr_fn(chunks_top, relv, k=10, acceptable_chunks=accept)
        cell.top1_hit = bool(chunks_top) and (
            chunks_top[0] in relv or chunks_top[0] in accept
        )
    else:
        cell.note = cell.note or "정답 chunks 없음 (latency 만 측정)"

    return cell


def _pick_target_item(
    items: list[dict[str, Any]], g: GoldenRow
) -> dict[str, Any] | None:
    """search 응답 items 중 golden row 의 expected doc 와 매칭되는 item 1건 선택.

    - doc_id 명시 row → ``it.doc_id == g.doc_id``
    - U-row (doc_id 비어있음) → ``expected_doc_title`` partial match 후
      RRF top-1 (items 는 이미 정렬됨)
    """
    if g.doc_id:
        for it in items:
            if it.get("doc_id") == g.doc_id:
                return it
        return None
    # U-row — title partial match
    if not g.expected_doc_title:
        return items[0] if items else None
    title_norm = unicodedata.normalize(
        "NFC", g.expected_doc_title
    ).lower()
    head = title_norm[:12]
    for it in items:
        item_title = unicodedata.normalize(
            "NFC", it.get("doc_title") or ""
        ).lower()
        if head and head in item_title:
            return it
    # fallback — top-1
    return items[0] if items else None


# ---------------------------------------------------------------------------
# 1 combo 전체 측정
# ---------------------------------------------------------------------------


def _measure_combo(
    combo: str, rows: list[GoldenRow], reset_cache: bool = True
) -> list[CellResult]:
    """combo ENV 적용 → row 별 측정 → CellResult list 반환.

    측정 전 reranker_cache 초기화 (이전 combo 의 cache 가 다음 combo hit rate
    부풀리는 것 방지). 측정 후 ENV 복원.
    """
    from app.services import reranker_cache

    saved = _apply_combo_env(combo)
    if reset_cache:
        reranker_cache._reset_for_test()

    cells: list[CellResult] = []
    try:
        for idx, g in enumerate(rows, start=1):
            cell = _measure_one_cell(combo, g)
            cells.append(cell)
            if idx % 20 == 0:
                print(
                    f"  [{combo}] {idx}/{len(rows)} done — "
                    f"latest path={cell.reranker_path}",
                    file=sys.stderr,
                )
    finally:
        _restore_env(saved)

    return cells


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


@dataclass
class ComboSummary:
    """1 combo 의 7 metric 집계."""

    combo: str
    label: str
    n_rows: int
    n_chunk_evaluable: int  # relevant or acceptable 있는 row 수
    avg_recall_at_10: float
    avg_ndcg_at_10: float
    avg_mrr: float
    top1_rate: float
    p95_latency_ms: float
    avg_latency_ms: float
    cache_hit_rate: float
    degrade_rate: float
    invoke_rate: float
    disabled_rate: float
    error_count: int
    doc_match_fail: int


def _aggregate(combo: str, cells: list[CellResult]) -> ComboSummary:
    n = len(cells)
    chunk_evals = [c for c in cells if c.recall_at_10 is not None]
    n_eval = len(chunk_evals)

    avg_r10 = (
        sum(c.recall_at_10 for c in chunk_evals) / n_eval if n_eval else 0.0
    )
    avg_ndcg = (
        sum(c.ndcg_at_10 for c in chunk_evals) / n_eval if n_eval else 0.0
    )
    avg_mrr = sum(c.mrr for c in chunk_evals) / n_eval if n_eval else 0.0
    top1_hits = [c for c in chunk_evals if c.top1_hit]
    top1_rate = len(top1_hits) / n_eval if n_eval else 0.0

    latencies = sorted(c.latency_ms for c in cells if c.latency_ms > 0)
    p95 = _percentile(latencies, 95.0)
    avg_lat = sum(latencies) / len(latencies) if latencies else 0.0

    paths = [c.reranker_path for c in cells]
    cache_rate = paths.count("cached") / n if n else 0.0
    degrade_rate = paths.count("degraded") / n if n else 0.0
    invoke_rate = paths.count("invoked") / n if n else 0.0
    disabled_rate = paths.count("disabled") / n if n else 0.0

    err = sum(1 for c in cells if c.note.startswith("ERROR"))
    doc_fail = sum(1 for c in cells if c.note == "doc 매칭 fail")

    return ComboSummary(
        combo=combo,
        label=_COMBO_LABEL[combo],
        n_rows=n,
        n_chunk_evaluable=n_eval,
        avg_recall_at_10=avg_r10,
        avg_ndcg_at_10=avg_ndcg,
        avg_mrr=avg_mrr,
        top1_rate=top1_rate,
        p95_latency_ms=p95,
        avg_latency_ms=avg_lat,
        cache_hit_rate=cache_rate,
        degrade_rate=degrade_rate,
        invoke_rate=invoke_rate,
        disabled_rate=disabled_rate,
        error_count=err,
        doc_match_fail=doc_fail,
    )


def _percentile(values: list[float], pct: float) -> float:
    """단순 percentile — values 가 정렬되어 있다고 가정. statistics 모듈 회피.

    `statistics.quantiles` 는 n=1 시 ValueError 라 ad-hoc 구현.
    """
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    k = (len(values) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(values) - 1)
    frac = k - lo
    return values[lo] * (1 - frac) + values[hi] * frac


# ---------------------------------------------------------------------------
# cross-doc only sub-report
# ---------------------------------------------------------------------------


def _cross_doc_sub_summary(
    combo: str, cells: list[CellResult]
) -> dict[str, Any]:
    """cells 중 distinct_doc_top5 ≥ 3 인 row 만 추려 metric 재계산.

    "cross-doc 의도 의심 row" subset — top-5 가 3개 이상의 doc 에서 매칭되면
    cross-doc query 인 가능성 높다 (intent_router 의 룰 외 보조 휴리스틱).
    """
    subset = [
        c
        for c in cells
        if c.distinct_doc_top5 >= _CROSS_DOC_DISTINCT_THRESHOLD
    ]
    n = len(subset)
    chunk_evals = [c for c in subset if c.recall_at_10 is not None]
    n_eval = len(chunk_evals)
    return {
        "combo": combo,
        "label": _COMBO_LABEL[combo],
        "n_subset": n,
        "n_chunk_evaluable": n_eval,
        "avg_recall_at_10": (
            sum(c.recall_at_10 for c in chunk_evals) / n_eval
            if n_eval
            else 0.0
        ),
        "avg_ndcg_at_10": (
            sum(c.ndcg_at_10 for c in chunk_evals) / n_eval
            if n_eval
            else 0.0
        ),
        "avg_mrr": (
            sum(c.mrr for c in chunk_evals) / n_eval if n_eval else 0.0
        ),
        "top1_rate": (
            sum(1 for c in chunk_evals if c.top1_hit) / n_eval
            if n_eval
            else 0.0
        ),
    }


# ---------------------------------------------------------------------------
# Markdown 출력
# ---------------------------------------------------------------------------


def _format_markdown(
    *,
    summaries: list[ComboSummary],
    cross_doc: list[dict[str, Any]] | None,
    n_golden: int,
    mock_reranker: bool,
    cells_by_combo: dict[str, list[CellResult]],
) -> str:
    lines: list[str] = []
    lines.append("# S3 D5 — 검색 스택 정량 측정 결과")
    lines.append("")
    lines.append(
        f"- 골든셋 v1: **{n_golden} row** "
        f"(`evals/golden_v1.csv`)"
    )
    lines.append(
        f"- 측정 모드: {'mock-reranker' if mock_reranker else '실 BGE-reranker'} "
        "(외부 vision API 호출 0, paid_decomposition OFF)"
    )
    lines.append(
        "- 운영 코드 변경 0 — ENV 토글로만 분기 (`api/` 하위 수정 없음)"
    )
    lines.append("")

    # §1 — 3 조합 × 7 metric 표
    lines.append("## §1 3 조합 × 7 metric 매트릭스")
    lines.append("")
    lines.append(
        "| combo | label | n / n_eval | R@10 | nDCG@10 | MRR | top-1 | "
        "P95 lat (ms) | cache hit | degrade | err |"
    )
    lines.append(
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"
    )
    for s in summaries:
        lines.append(
            f"| {s.combo} | {s.label} | {s.n_rows}/{s.n_chunk_evaluable} | "
            f"{s.avg_recall_at_10:.4f} | {s.avg_ndcg_at_10:.4f} | "
            f"{s.avg_mrr:.4f} | {s.top1_rate:.4f} | "
            f"{s.p95_latency_ms:.1f} | {s.cache_hit_rate:.3f} | "
            f"{s.degrade_rate:.3f} | {s.error_count} |"
        )
    lines.append("")

    # path 분포 보강 표
    lines.append("### §1.1 reranker_path 분포 (per combo)")
    lines.append("")
    lines.append(
        "| combo | invoked | cached | degraded | disabled | doc 매칭 fail |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|")
    for s in summaries:
        lines.append(
            f"| {s.combo} | {s.invoke_rate:.3f} | {s.cache_hit_rate:.3f} | "
            f"{s.degrade_rate:.3f} | {s.disabled_rate:.3f} | "
            f"{s.doc_match_fail} |"
        )
    lines.append("")

    # latency 보강
    lines.append("### §1.2 latency (ms) — avg / P95")
    lines.append("")
    lines.append("| combo | avg | P95 |")
    lines.append("|---|---:|---:|")
    for s in summaries:
        lines.append(
            f"| {s.combo} | {s.avg_latency_ms:.1f} | {s.p95_latency_ms:.1f} |"
        )
    lines.append("")

    # §2 — DoD 충족/미달 판정
    lines.append("## §2 DoD KPI 판정 (planner v0.1 §S3 D5)")
    lines.append("")
    best = _pick_best(summaries)
    lines.append(f"- **선정 baseline**: combo `{best.combo}` ({best.label})")
    lines.append("")
    dod_rows = [
        ("top-1 ≥ 0.95", best.top1_rate, 0.95),
        ("R@10 ≥ 0.75", best.avg_recall_at_10, 0.75),
        ("정확도 (top-1) ≥ 0.80", best.top1_rate, 0.80),
    ]
    lines.append("| KPI | 측정값 | 임계 | 판정 |")
    lines.append("|---|---:|---:|:---:|")
    for name, value, threshold in dod_rows:
        verdict = "충족" if value >= threshold else "미달"
        lines.append(
            f"| {name} | {value:.4f} | {threshold:.2f} | {verdict} |"
        )
    lines.append("")

    # cross-doc distinct
    cross_eligible_total = sum(
        1
        for c in cells_by_combo.get(best.combo, [])
        if c.distinct_doc_top5 >= _CROSS_DOC_DISTINCT_THRESHOLD
    )
    cross_total = len(cells_by_combo.get(best.combo, []))
    lines.append(
        f"- cross-doc top-5 distinct doc_id ≥ 3 row: "
        f"**{cross_eligible_total} / {cross_total}** "
        f"({(cross_eligible_total / cross_total * 100) if cross_total else 0:.1f}%)"
    )
    lines.append("")

    # P95 비교
    lines.append("- **P95 latency 조합 비교**")
    for s in summaries:
        lines.append(
            f"  - combo `{s.combo}` ({s.label}): {s.p95_latency_ms:.1f} ms"
        )
    lines.append("")

    # §3 — cross-doc only sub-report
    if cross_doc:
        lines.append("## §3 cross-doc only sub-report (top-5 distinct doc_id ≥ 3)")
        lines.append("")
        lines.append(
            "| combo | label | n_subset | n_eval | R@10 | nDCG@10 | MRR | top-1 |"
        )
        lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
        for cd in cross_doc:
            lines.append(
                f"| {cd['combo']} | {cd['label']} | {cd['n_subset']} | "
                f"{cd['n_chunk_evaluable']} | {cd['avg_recall_at_10']:.4f} | "
                f"{cd['avg_ndcg_at_10']:.4f} | {cd['avg_mrr']:.4f} | "
                f"{cd['top1_rate']:.4f} |"
            )
        lines.append("")

    # §4 — 발견 이슈 (자동 추출)
    lines.append("## §4 자동 추출 이슈")
    lines.append("")
    issues: list[str] = []
    for s in summaries:
        if s.doc_match_fail > 0:
            issues.append(
                f"- combo `{s.combo}`: doc 매칭 fail **{s.doc_match_fail}건** "
                "(expected_doc_title partial match 실패 — golden CSV title 정정 필요)"
            )
        if s.error_count > 0:
            issues.append(
                f"- combo `{s.combo}`: search() ERROR **{s.error_count}건** "
                "(network / DB / HF 분류 필요 — raw json `note` 컬럼 참조)"
            )
        if s.degrade_rate > 0.0:
            issues.append(
                f"- combo `{s.combo}`: degrade rate **{s.degrade_rate:.3f}** "
                "(reranker 월간 cap 임계 80% 도달 — 측정 중 degrade path 진입)"
            )
        if s.top1_rate < 0.80 and s.n_chunk_evaluable > 0:
            issues.append(
                f"- combo `{s.combo}`: top-1 rate **{s.top1_rate:.3f}** "
                "(DoD 정확도 0.80 미달 — query_type 별 분포 추가 분석 필요)"
            )
    if not issues:
        issues.append("- 자동 추출 이슈 없음")
    lines.extend(issues)
    lines.append("")

    return "\n".join(lines)


def _pick_best(summaries: list[ComboSummary]) -> ComboSummary:
    """top-1 rate 최대인 combo 선정. tie 시 R@10 → P95 latency 낮은 순.

    DoD 핵심 KPI 가 top-1 (정확도) 라 그 기준 최적화.
    """
    return max(
        summaries,
        key=lambda s: (
            s.top1_rate,
            s.avg_recall_at_10,
            -s.p95_latency_ms,
        ),
    )


# ---------------------------------------------------------------------------
# raw JSON dump
# ---------------------------------------------------------------------------


def _serialize_cells(cells: list[CellResult]) -> list[dict[str, Any]]:
    """CellResult list → dict list (json 직렬화 가능)."""
    out: list[dict[str, Any]] = []
    for c in cells:
        out.append(
            {
                "combo": c.combo,
                "golden_id": c.golden_id,
                "query_type": c.query_type,
                "doc_id": c.doc_id,
                "recall_at_10": c.recall_at_10,
                "ndcg_at_10": c.ndcg_at_10,
                "mrr": c.mrr,
                "top1_hit": c.top1_hit,
                "latency_ms": round(c.latency_ms, 2),
                "reranker_path": c.reranker_path,
                "distinct_doc_top5": c.distinct_doc_top5,
                "predicted_top10": c.predicted_top10,
                "note": c.note,
            }
        )
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="S3 D5 — 검색 스택 정량 측정 (3 조합 × 158 row × 7 metric)"
    )
    p.add_argument(
        "--combo",
        choices=["a", "b", "c", "all"],
        default="all",
        help="측정 조합 — a/b/c/all (default all)",
    )
    p.add_argument(
        "--metrics",
        default="r10,ndcg,mrr,top1,latency,cache,degrade",
        help=(
            "출력 metric 화이트리스트 (CSV) — "
            "r10,ndcg,mrr,top1,latency,cache,degrade. "
            "현재 본 스크립트는 모든 metric 을 산출하며 본 옵션은 라벨링용."
        ),
    )
    p.add_argument(
        "--cross-doc-only",
        action="store_true",
        help="cross-doc only sub-report 단독 출력 (top-5 distinct doc_id ≥ 3)",
    )
    p.add_argument(
        "--out",
        default=str(_DEFAULT_OUT_MD),
        help="markdown 결과 출력 경로 (default: evals/results/s3_d5_results.md)",
    )
    p.add_argument(
        "--out-json",
        default=str(_DEFAULT_OUT_JSON),
        help="raw cell json 출력 경로 (default: evals/results/s3_d5_raw.json)",
    )
    p.add_argument(
        "--mock-reranker",
        action="store_true",
        help="BGE-reranker HF 호출을 deterministic mock 으로 교체 (CI / 회귀 비교 용도)",
    )
    p.add_argument(
        "--limit-rows",
        type=int,
        default=0,
        help=(
            "측정 row 수 제한 (디버그용, 0=전체). "
            "production 측정에서는 절대 사용 금지."
        ),
    )
    return p.parse_args(argv)


def _validate_metrics(raw: str) -> list[str]:
    parts = [m.strip() for m in raw.split(",") if m.strip()]
    invalid = [m for m in parts if m not in _ALLOWED_METRICS]
    if invalid:
        raise SystemExit(
            f"--metrics 에 알 수 없는 키: {invalid}. "
            f"허용: {sorted(_ALLOWED_METRICS)}"
        )
    return parts


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _validate_metrics(args.metrics)

    if args.mock_reranker:
        _patch_mock_reranker()
        print("[INFO] mock-reranker 활성 — HF 호출 0", file=sys.stderr)

    rows = _load_golden_rows(_GOLDEN_CSV)
    if args.limit_rows > 0:
        rows = rows[: args.limit_rows]
        print(
            f"[WARN] --limit-rows={args.limit_rows} 적용 — production 측정 아님",
            file=sys.stderr,
        )
    print(f"[INFO] 골든셋 v1 row 수: {len(rows)}", file=sys.stderr)

    combos = ["a", "b", "c"] if args.combo == "all" else [args.combo]

    cells_by_combo: dict[str, list[CellResult]] = {}
    summaries: list[ComboSummary] = []
    cross_doc_summaries: list[dict[str, Any]] = []

    for combo in combos:
        print(
            f"[INFO] === combo {combo} ({_COMBO_LABEL[combo]}) 측정 시작 ===",
            file=sys.stderr,
        )
        t_combo = time.monotonic()
        cells = _measure_combo(combo, rows)
        elapsed = time.monotonic() - t_combo
        cells_by_combo[combo] = cells
        summary = _aggregate(combo, cells)
        summaries.append(summary)
        cross_doc_summaries.append(_cross_doc_sub_summary(combo, cells))
        print(
            f"[INFO] combo {combo} 완료 — {elapsed:.1f}s, "
            f"R@10={summary.avg_recall_at_10:.4f}, "
            f"top-1={summary.top1_rate:.4f}, "
            f"P95={summary.p95_latency_ms:.1f}ms",
            file=sys.stderr,
        )

    md = _format_markdown(
        summaries=summaries,
        cross_doc=cross_doc_summaries,
        n_golden=len(rows),
        mock_reranker=args.mock_reranker,
        cells_by_combo=cells_by_combo,
    )

    out_md = Path(args.out)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(md, encoding="utf-8")
    print(f"[OK] markdown report: {out_md}", file=sys.stderr)

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    raw = {
        "n_golden": len(rows),
        "mock_reranker": args.mock_reranker,
        "combos": {
            combo: {
                "label": _COMBO_LABEL[combo],
                "env": _COMBO_ENV[combo],
                "summary": _summary_to_dict(s),
                "cells": _serialize_cells(cells_by_combo[combo]),
            }
            for combo, s in zip(combos, summaries)
        },
        "cross_doc_subset": cross_doc_summaries,
    }
    out_json.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] raw json: {out_json}", file=sys.stderr)

    return 0


def _summary_to_dict(s: ComboSummary) -> dict[str, Any]:
    return {
        "combo": s.combo,
        "label": s.label,
        "n_rows": s.n_rows,
        "n_chunk_evaluable": s.n_chunk_evaluable,
        "avg_recall_at_10": s.avg_recall_at_10,
        "avg_ndcg_at_10": s.avg_ndcg_at_10,
        "avg_mrr": s.avg_mrr,
        "top1_rate": s.top1_rate,
        "p95_latency_ms": s.p95_latency_ms,
        "avg_latency_ms": s.avg_latency_ms,
        "cache_hit_rate": s.cache_hit_rate,
        "degrade_rate": s.degrade_rate,
        "invoke_rate": s.invoke_rate,
        "disabled_rate": s.disabled_rate,
        "error_count": s.error_count,
        "doc_match_fail": s.doc_match_fail,
    }


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
