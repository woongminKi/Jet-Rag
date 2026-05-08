"""S4-A D4 — 골든셋 v2 R@10 3축 breakdown 측정 (planner v0.1 §S4-A D4).

목적
----
S4-A D1+D2+D3 Phase 1 ship 직후 — vision prompt v2 + chunk.text 합성 + golden v2
가 모두 갖춰진 시점. 검색 path 변경 0 인 baseline 측정으로 다음을 확보:

1. **golden v2 (157 row) baseline** — qtype 9 / doc_type 5+ / caption_dependent 2
   3축 breakdown 의 R@10 / nDCG@10 / MRR / top-1
2. **caption_dependent gap** — true(18) vs false(139) 의 metric gap.
   D5 prompt v2 reingest 의 expected gain 추정 baseline.
3. **D5 후 회귀 비교 기준** — 동일 도구 재실행으로 prompt v1↔v2 직접 비교.

D4 시점 한계 (정직히 명시)
-------------------------
- chunks 의 적재 시점 prompt_version 이 chunks 테이블에 기록 안 됨 → D4 시점에는
  prompt v1↔v2 직접 비교 불가. (D5 reingest 후 동일 도구 재실행으로 비교)
- caption_dependent=true 표본 18건 — 통계 신뢰도 낮음, 추세만 확인.
- baseline = RRF-only (S3 D5 combo a 와 동일 ENV) — reranker / MMR 효과는 S3 D5 결과
  참조. D4 는 데이터 차원 (qtype × caption × doc_type) 의 baseline 분리 측정.

측정 metric 4종 (cell 별)
------------------------
1. R@10 (graded — relevant 1.0, acceptable 0.5)
2. nDCG@10 (graded)
3. MRR (graded — relevant 우선)
4. top-1 적중률
+ 보조: latency_ms (informational), reranker_path (RRF-only 검증 — "disabled" 기대)

3축 breakdown
-------------
- **qtype** (9종): exact_fact / fuzzy_memory / table_lookup / vision_diagram / ...
- **doc_type** (5+1): pdf / hwpx / hwp / pptx / docx / "" (cross_doc U-row)
- **caption_dependent** (2): true / false

설계 원칙
---------
- **운영 코드 변경 0** — ENV 토글 (`JETRAG_RERANKER_ENABLED=false` +
  `JETRAG_MMR_DISABLE=1`) 만으로 RRF-only 강제.
- **외부 API 호출 0** — vision/Gemini/HF reranker 호출 없음. 옵션 C 진입 가능.
- **acceptable_chunks 전달** — `recall_at_k` / `ndcg_at_k` / `mrr` 모두 acceptable
  인자 전달 (G-A-021 류 false negative 방지, S3 D5 패턴 답습).

산출
----
- ``evals/results/s4_a_d4_results.md`` — overall + 3축 breakdown + caption gap
- ``evals/results/s4_a_d4_raw.json`` — raw cell 데이터 (157 row × N field)

실행
----
    cd api && uv run python ../evals/run_s4_a_d4_breakdown_eval.py
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
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# api/ 를 import path 에 추가 — search() 직접 호출 위해
_API_PATH = Path(__file__).resolve().parents[0].parent / "api"
if (_API_PATH / "app").exists():
    sys.path.insert(0, str(_API_PATH))

_REPO_ROOT = Path(__file__).resolve().parent.parent
_GOLDEN_V2_CSV = _REPO_ROOT / "evals" / "golden_v2.csv"
_DEFAULT_OUT_MD = _REPO_ROOT / "evals" / "results" / "s4_a_d4_results.md"
_DEFAULT_OUT_JSON = _REPO_ROOT / "evals" / "results" / "s4_a_d4_raw.json"

# search 응답 top-K — 한 doc 에 매칭 청크 최대 50건.
_SEARCH_LIMIT = 50

# RRF-only baseline ENV — S3 D5 combo `a` 와 동일.
_BASELINE_ENV: dict[str, str] = {
    "JETRAG_RERANKER_ENABLED": "false",
    "JETRAG_MMR_DISABLE": "1",
}


# ---------------------------------------------------------------------------
# DTO
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GoldenV2Row:
    """골든셋 v2 의 측정 대상 row.

    v1 12 컬럼 + S4-A D3 추가 2 컬럼 (`doc_type` / `caption_dependent`).
    """

    id: str
    query: str
    query_type: str
    doc_id: str  # UUID 또는 빈 문자열 (U-row, cross_doc 의도 가능)
    expected_doc_title: str
    relevant_chunks: tuple[int, ...]
    acceptable_chunks: tuple[int, ...]
    doc_type: str  # pdf / hwpx / hwp / pptx / docx / "" (U-row)
    caption_dependent: bool


@dataclass
class CellResult:
    """1 cell = (golden_row, RRF-only baseline) 의 측정값."""

    golden_id: str
    query_type: str
    doc_type: str
    caption_dependent: bool
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


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _load_golden_v2(csv_path: Path) -> list[GoldenV2Row]:
    """골든셋 v2 전체 row 로드 — utf-8-sig 로 BOM 제거."""
    out: list[GoldenV2Row] = []
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
            cap_dep_raw = (row.get("caption_dependent") or "").strip().lower()
            out.append(
                GoldenV2Row(
                    id=qid,
                    query=(row.get("query") or "").strip(),
                    query_type=(row.get("query_type") or "").strip(),
                    doc_id=(row.get("doc_id") or "").strip(),
                    expected_doc_title=(row.get("expected_doc_title") or "").strip(),
                    relevant_chunks=relv,
                    acceptable_chunks=accept,
                    doc_type=(row.get("doc_type") or "").strip(),
                    caption_dependent=cap_dep_raw == "true",
                )
            )
    return out


# ---------------------------------------------------------------------------
# ENV apply / restore — RRF-only 강제
# ---------------------------------------------------------------------------


def _apply_baseline_env() -> dict[str, str | None]:
    """`_BASELINE_ENV` 를 ``os.environ`` 에 적용. 이전 값 반환 (restore 용)."""
    saved: dict[str, str | None] = {}
    for k, v in _BASELINE_ENV.items():
        saved[k] = os.environ.get(k)
        os.environ[k] = v
    return saved


def _restore_env(saved: dict[str, str | None]) -> None:
    for k, prev in saved.items():
        if prev is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = prev


# ---------------------------------------------------------------------------
# 1 cell 측정
# ---------------------------------------------------------------------------


def _measure_one_cell(g: GoldenV2Row) -> CellResult:
    """search() 호출 1회 → CellResult.

    relevant_chunks 비어있는 row 도 호출 — latency / reranker_path 는 측정 가능.
    chunk-level metric 만 None.
    """
    from app.routers.search import search  # noqa: E402
    from app.services.retrieval_metrics import (  # noqa: E402
        mrr as mrr_fn,
        ndcg_at_k,
        recall_at_k,
    )

    cell = CellResult(
        golden_id=g.id,
        query_type=g.query_type,
        doc_type=g.doc_type,
        caption_dependent=g.caption_dependent,
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

    target_items = _pick_target_items(items, g)
    if not target_items:
        cell.note = "doc 매칭 fail"
        return cell

    # Phase 2-A — multi-doc cross_doc U-row 한정으로 다중 item 의 matched_chunks 합산.
    # Single-doc / single-item 일 때는 기존 path 와 동치 (하위 호환).
    merged: list[dict[str, Any]] = []
    for it in target_items:
        merged.extend(it.get("matched_chunks") or [])
    matched = sorted(
        merged,
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


def _pick_target_items(
    items: list[dict[str, Any]], g: GoldenV2Row
) -> list[dict[str, Any]]:
    """search 응답 items 중 golden row 의 expected doc 와 매칭되는 item 들 선택.

    Phase 2-A 보강 — multi-doc cross_doc U-row 의 R@10 폭락 fix.

    매칭 규칙
    --------
    - doc_id 명시 row → ``it.doc_id == g.doc_id`` 단일 item (single-doc, 1건).
    - U-row (doc_id 비어있음) + ``|`` separator 없는 expected_doc_title →
      title 12자 prefix 매칭 1건 + RRF top-1 fallback (하위 호환, single-doc 동치).
    - U-row + ``|`` separator 있는 expected_doc_title → 각 sub-title 12자 prefix
      매칭 item 합산 (multi-doc, 1+ 건). cross_doc 정답 chunk 가 다중 doc 에
      흩어진 경우 모든 doc 의 matched_chunks 를 RRF desc 합산해 R@10 측정.

    합산 대상 item 은 RRF score 중복 없이 search 응답 그대로. predicted_top10 의
    chunk_idx 는 chunks 테이블이 (doc_id, chunk_idx) unique 라도 cross_doc U-row
    가 라벨러가 chunk_idx 만 명시한 schema (relevant=`15,0` 등) 라 doc 무관 비교.
    중복 chunk_idx 가 다른 doc 에서 등장 시 RRF score 큰 쪽이 먼저 정렬됨.
    """
    if g.doc_id:
        for it in items:
            if it.get("doc_id") == g.doc_id:
                return [it]
        return []
    if not g.expected_doc_title:
        return [items[0]] if items else []

    sub_titles = [
        s.strip()
        for s in g.expected_doc_title.split("|")
        if s.strip()
    ]
    if len(sub_titles) <= 1:
        # single-doc U-row — 기존 12자 prefix 매칭 + top-1 fallback
        title_norm = unicodedata.normalize(
            "NFC", g.expected_doc_title
        ).lower()
        head = title_norm[:12]
        for it in items:
            item_title = unicodedata.normalize(
                "NFC", it.get("doc_title") or ""
            ).lower()
            if head and head in item_title:
                return [it]
        return [items[0]] if items else []

    # multi-doc cross_doc U-row — 각 sub-title 별 첫 매칭 item 합산
    matched: list[dict[str, Any]] = []
    seen_doc_ids: set[str] = set()
    for sub in sub_titles:
        sub_norm = unicodedata.normalize("NFC", sub).lower()
        head = sub_norm[:12]
        if not head:
            continue
        for it in items:
            doc_id = it.get("doc_id")
            if doc_id and doc_id in seen_doc_ids:
                continue
            item_title = unicodedata.normalize(
                "NFC", it.get("doc_title") or ""
            ).lower()
            if head in item_title:
                matched.append(it)
                if doc_id:
                    seen_doc_ids.add(doc_id)
                break
    return matched


def _pick_target_item(
    items: list[dict[str, Any]], g: GoldenV2Row
) -> dict[str, Any] | None:
    """``_pick_target_items`` single-result 래퍼 — 하위 호환 + 단위 테스트 호환.

    multi-doc cross_doc 매칭은 ``_pick_target_items`` 직접 호출. 본 함수는
    single-item path 만 반환 (기존 단위 테스트 / API 호환).
    """
    res = _pick_target_items(items, g)
    return res[0] if res else None


# ---------------------------------------------------------------------------
# 전체 측정
# ---------------------------------------------------------------------------


def _measure_all(rows: list[GoldenV2Row]) -> list[CellResult]:
    """RRF-only baseline ENV 적용 → row 별 측정 → CellResult list 반환.

    측정 후 ENV 복원.
    """
    saved = _apply_baseline_env()
    cells: list[CellResult] = []
    try:
        for idx, g in enumerate(rows, start=1):
            cell = _measure_one_cell(g)
            cells.append(cell)
            if idx % 25 == 0:
                print(
                    f"  [D4] {idx}/{len(rows)} done — "
                    f"latest qtype={cell.query_type} caption={cell.caption_dependent}",
                    file=sys.stderr,
                )
    finally:
        _restore_env(saved)
    return cells


# ---------------------------------------------------------------------------
# Aggregation — 3축 breakdown
# ---------------------------------------------------------------------------


@dataclass
class GroupSummary:
    """그룹 (qtype / doc_type / caption_dependent) 의 metric 집계."""

    label: str
    n_rows: int
    n_chunk_evaluable: int
    avg_recall_at_10: float
    avg_ndcg_at_10: float
    avg_mrr: float
    top1_rate: float
    p95_latency_ms: float
    avg_latency_ms: float
    doc_match_fail: int
    error_count: int


def _aggregate_group(label: str, cells: list[CellResult]) -> GroupSummary:
    """1 그룹의 cells → GroupSummary."""
    n = len(cells)
    chunk_evals = [c for c in cells if c.recall_at_10 is not None]
    n_eval = len(chunk_evals)

    avg_r10 = (
        sum(c.recall_at_10 for c in chunk_evals) / n_eval if n_eval else 0.0
    )
    avg_ndcg = (
        sum(c.ndcg_at_10 for c in chunk_evals) / n_eval if n_eval else 0.0
    )
    avg_mrr = (
        sum(c.mrr for c in chunk_evals) / n_eval if n_eval else 0.0
    )
    top1_rate = (
        sum(1 for c in chunk_evals if c.top1_hit) / n_eval if n_eval else 0.0
    )

    latencies = sorted(c.latency_ms for c in cells if c.latency_ms > 0)
    p95 = _percentile(latencies, 95.0)
    avg_lat = sum(latencies) / len(latencies) if latencies else 0.0

    err = sum(1 for c in cells if c.note.startswith("ERROR"))
    doc_fail = sum(1 for c in cells if c.note == "doc 매칭 fail")

    return GroupSummary(
        label=label,
        n_rows=n,
        n_chunk_evaluable=n_eval,
        avg_recall_at_10=avg_r10,
        avg_ndcg_at_10=avg_ndcg,
        avg_mrr=avg_mrr,
        top1_rate=top1_rate,
        p95_latency_ms=p95,
        avg_latency_ms=avg_lat,
        doc_match_fail=doc_fail,
        error_count=err,
    )


def _percentile(values: list[float], pct: float) -> float:
    """단순 percentile — values 정렬됨 가정. statistics.quantiles 의 n=1 ValueError 회피."""
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    k = (len(values) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(values) - 1)
    frac = k - lo
    return values[lo] * (1 - frac) + values[hi] * frac


def _group_by(
    cells: list[CellResult], key_fn
) -> dict[Any, list[CellResult]]:
    """key_fn 으로 cells 그룹핑."""
    out: dict[Any, list[CellResult]] = defaultdict(list)
    for c in cells:
        out[key_fn(c)].append(c)
    return out


# ---------------------------------------------------------------------------
# Markdown 출력
# ---------------------------------------------------------------------------


def _format_markdown(
    *,
    overall: GroupSummary,
    by_qtype: list[GroupSummary],
    by_doc_type: list[GroupSummary],
    by_caption: list[GroupSummary],
    by_qtype_caption: list[GroupSummary],
    n_golden: int,
) -> str:
    lines: list[str] = []
    lines.append("# S4-A D4 — 골든셋 v2 R@10 3축 breakdown 측정")
    lines.append("")
    lines.append(f"- 골든셋 v2: **{n_golden} row** (`evals/golden_v2.csv`)")
    lines.append(
        "- 측정 모드: **RRF-only baseline** "
        "(`JETRAG_RERANKER_ENABLED=false` + `JETRAG_MMR_DISABLE=1`) — "
        "S3 D5 combo `a` 와 동일 ENV"
    )
    lines.append("- 외부 API 호출 0 (vision/Gemini/HF reranker 비활성)")
    lines.append("- 운영 코드 변경 0 — ENV 토글 + 측정 도구만")
    lines.append("")
    lines.append("## §0 D4 시점 한계 (정직히 명시)")
    lines.append("")
    lines.append(
        "- **prompt v1↔v2 직접 비교 불가** — chunks 의 적재 prompt_version 이 "
        "chunks 테이블에 기록 안 됨. D5 reingest 후 동일 도구 재실행으로 비교 가능."
    )
    lines.append(
        "- **caption_dependent=true 표본 18건** — 통계 신뢰도 낮음, 추세만 확인."
    )
    lines.append(
        "- **baseline = RRF-only** — reranker / MMR 효과는 S3 D5 결과 참조. "
        "D4 는 데이터 차원 (qtype × caption × doc_type) 의 baseline 분리 측정."
    )
    lines.append("")

    # §1 — overall
    lines.append("## §1 Overall (157 row baseline)")
    lines.append("")
    lines.append(
        "| n / n_eval | R@10 | nDCG@10 | MRR | top-1 | "
        "P95 lat (ms) | doc 매칭 fail | err |"
    )
    lines.append("|---:|---:|---:|---:|---:|---:|---:|---:|")
    lines.append(
        f"| {overall.n_rows}/{overall.n_chunk_evaluable} | "
        f"{overall.avg_recall_at_10:.4f} | {overall.avg_ndcg_at_10:.4f} | "
        f"{overall.avg_mrr:.4f} | {overall.top1_rate:.4f} | "
        f"{overall.p95_latency_ms:.1f} | {overall.doc_match_fail} | "
        f"{overall.error_count} |"
    )
    lines.append("")

    # §2 — qtype breakdown
    lines.append("## §2 qtype 9종 breakdown")
    lines.append("")
    lines.append(
        "| qtype | n / n_eval | R@10 | nDCG@10 | MRR | top-1 | "
        "P95 lat | doc fail |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for s in by_qtype:
        lines.append(
            f"| {s.label} | {s.n_rows}/{s.n_chunk_evaluable} | "
            f"{s.avg_recall_at_10:.4f} | {s.avg_ndcg_at_10:.4f} | "
            f"{s.avg_mrr:.4f} | {s.top1_rate:.4f} | "
            f"{s.p95_latency_ms:.1f} | {s.doc_match_fail} |"
        )
    lines.append("")

    # §3 — doc_type breakdown
    lines.append("## §3 doc_type breakdown (5+1)")
    lines.append("")
    lines.append(
        "| doc_type | n / n_eval | R@10 | nDCG@10 | MRR | top-1 | doc fail |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for s in by_doc_type:
        label_disp = s.label if s.label else "(empty / U-row)"
        lines.append(
            f"| {label_disp} | {s.n_rows}/{s.n_chunk_evaluable} | "
            f"{s.avg_recall_at_10:.4f} | {s.avg_ndcg_at_10:.4f} | "
            f"{s.avg_mrr:.4f} | {s.top1_rate:.4f} | {s.doc_match_fail} |"
        )
    lines.append("")

    # §4 — caption_dependent gap
    lines.append("## §4 caption_dependent gap (D5 reingest 의 expected gain 추정)")
    lines.append("")
    lines.append(
        "| caption_dependent | n / n_eval | R@10 | nDCG@10 | MRR | top-1 |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|")
    cap_summary: dict[str, GroupSummary] = {s.label: s for s in by_caption}
    for label in ("true", "false"):
        s = cap_summary.get(label)
        if s is None:
            continue
        lines.append(
            f"| {label} | {s.n_rows}/{s.n_chunk_evaluable} | "
            f"{s.avg_recall_at_10:.4f} | {s.avg_ndcg_at_10:.4f} | "
            f"{s.avg_mrr:.4f} | {s.top1_rate:.4f} |"
        )
    lines.append("")
    if "true" in cap_summary and "false" in cap_summary:
        gap_r10 = (
            cap_summary["false"].avg_recall_at_10
            - cap_summary["true"].avg_recall_at_10
        )
        gap_top1 = (
            cap_summary["false"].top1_rate - cap_summary["true"].top1_rate
        )
        lines.append(
            f"- **R@10 gap (false − true)**: {gap_r10:+.4f} — 양수 시 caption_dependent "
            "row 가 baseline 에서 손해 → D5 prompt v2 reingest 의 expected gain ceiling."
        )
        lines.append(
            f"- **top-1 gap (false − true)**: {gap_top1:+.4f}"
        )
    lines.append("")

    # §5 — qtype × caption_dependent cross-tab
    lines.append("## §5 qtype × caption_dependent cross-tab")
    lines.append("")
    lines.append(
        "| qtype | caption | n / n_eval | R@10 | top-1 |"
    )
    lines.append("|---|:---:|---:|---:|---:|")
    for s in by_qtype_caption:
        # label 형식: "{qtype}|{cap}"
        qt, cap_str = s.label.split("|", 1)
        lines.append(
            f"| {qt} | {cap_str} | {s.n_rows}/{s.n_chunk_evaluable} | "
            f"{s.avg_recall_at_10:.4f} | {s.top1_rate:.4f} |"
        )
    lines.append("")

    # §6 — DoD KPI 판정
    lines.append("## §6 DoD KPI 판정 (golden v2 baseline)")
    lines.append("")
    lines.append("| KPI | overall | 임계 | 판정 |")
    lines.append("|---|---:|---:|:---:|")
    for name, value, threshold in [
        ("R@10 ≥ 0.75", overall.avg_recall_at_10, 0.75),
        ("top-1 ≥ 0.80", overall.top1_rate, 0.80),
        ("top-1 ≥ 0.95", overall.top1_rate, 0.95),
    ]:
        verdict = "충족" if value >= threshold else "미달"
        lines.append(
            f"| {name} | {value:.4f} | {threshold:.2f} | {verdict} |"
        )
    lines.append("")

    # §7 — 자동 추출 이슈
    lines.append("## §7 자동 추출 이슈")
    lines.append("")
    issues: list[str] = []
    if overall.doc_match_fail > 0:
        issues.append(
            f"- doc 매칭 fail **{overall.doc_match_fail}건** — "
            "expected_doc_title partial match 실패 (golden v2 title 정정 또는 search 응답 추적)."
        )
    if overall.error_count > 0:
        issues.append(
            f"- search() ERROR **{overall.error_count}건** — "
            "raw json `note` 컬럼으로 분류 (network/DB/HF)."
        )
    # qtype 별 R@10 이 overall 의 -0.20 미만 → 약한 qtype 표시
    for s in by_qtype:
        if (
            s.n_chunk_evaluable >= 3
            and s.avg_recall_at_10 < overall.avg_recall_at_10 - 0.20
        ):
            issues.append(
                f"- qtype `{s.label}` 약함 — R@10 {s.avg_recall_at_10:.4f} "
                f"(overall 대비 {s.avg_recall_at_10 - overall.avg_recall_at_10:+.4f})"
            )
    if not issues:
        issues.append("- 자동 추출 이슈 없음")
    lines.extend(issues)
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# raw JSON dump
# ---------------------------------------------------------------------------


def _serialize_cells(cells: list[CellResult]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in cells:
        out.append(
            {
                "golden_id": c.golden_id,
                "query_type": c.query_type,
                "doc_type": c.doc_type,
                "caption_dependent": c.caption_dependent,
                "doc_id": c.doc_id,
                "recall_at_10": c.recall_at_10,
                "ndcg_at_10": c.ndcg_at_10,
                "mrr": c.mrr,
                "top1_hit": c.top1_hit,
                "latency_ms": round(c.latency_ms, 2),
                "reranker_path": c.reranker_path,
                "predicted_top10": c.predicted_top10,
                "note": c.note,
            }
        )
    return out


def _summary_to_dict(s: GroupSummary) -> dict[str, Any]:
    return {
        "label": s.label,
        "n_rows": s.n_rows,
        "n_chunk_evaluable": s.n_chunk_evaluable,
        "avg_recall_at_10": s.avg_recall_at_10,
        "avg_ndcg_at_10": s.avg_ndcg_at_10,
        "avg_mrr": s.avg_mrr,
        "top1_rate": s.top1_rate,
        "p95_latency_ms": s.p95_latency_ms,
        "avg_latency_ms": s.avg_latency_ms,
        "doc_match_fail": s.doc_match_fail,
        "error_count": s.error_count,
    }


# ---------------------------------------------------------------------------
# Aggregation entry — overall + 3축 + cross-tab
# ---------------------------------------------------------------------------


def aggregate_all(
    cells: list[CellResult],
) -> tuple[
    GroupSummary,
    list[GroupSummary],
    list[GroupSummary],
    list[GroupSummary],
    list[GroupSummary],
]:
    """cells → (overall, by_qtype, by_doc_type, by_caption, by_qtype_caption).

    각 list 는 R@10 내림차순 정렬 (cross-tab 은 qtype 내 caption 순).
    """
    overall = _aggregate_group("overall", cells)

    by_qtype_groups = _group_by(cells, lambda c: c.query_type)
    by_qtype = [
        _aggregate_group(qt, group) for qt, group in by_qtype_groups.items()
    ]
    by_qtype.sort(key=lambda s: -s.avg_recall_at_10)

    by_dt_groups = _group_by(cells, lambda c: c.doc_type)
    by_doc_type = [_aggregate_group(dt, g) for dt, g in by_dt_groups.items()]
    by_doc_type.sort(key=lambda s: -s.n_rows)

    by_cap_groups = _group_by(
        cells, lambda c: "true" if c.caption_dependent else "false"
    )
    by_caption = [
        _aggregate_group(label, g) for label, g in by_cap_groups.items()
    ]

    by_qt_cap_groups = _group_by(
        cells,
        lambda c: f"{c.query_type}|{'true' if c.caption_dependent else 'false'}",
    )
    by_qtype_caption = [
        _aggregate_group(label, g) for label, g in by_qt_cap_groups.items()
    ]
    by_qtype_caption.sort(key=lambda s: s.label)

    return overall, by_qtype, by_doc_type, by_caption, by_qtype_caption


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="S4-A D4 — 골든셋 v2 R@10 3축 breakdown 측정 (RRF-only baseline)"
    )
    p.add_argument(
        "--out",
        default=str(_DEFAULT_OUT_MD),
        help="markdown 결과 출력 경로 (default: evals/results/s4_a_d4_results.md)",
    )
    p.add_argument(
        "--out-json",
        default=str(_DEFAULT_OUT_JSON),
        help="raw cell json 출력 경로 (default: evals/results/s4_a_d4_raw.json)",
    )
    p.add_argument(
        "--golden-csv",
        default=str(_GOLDEN_V2_CSV),
        help=f"golden v2 CSV (default: {_GOLDEN_V2_CSV})",
    )
    p.add_argument(
        "--limit-rows",
        type=int,
        default=0,
        help="측정 row 제한 (디버그용, 0=전체)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    golden_path = Path(args.golden_csv)
    if not golden_path.exists():
        print(f"[ERROR] golden csv 없음: {golden_path}", file=sys.stderr)
        return 1
    rows = _load_golden_v2(golden_path)
    if args.limit_rows > 0:
        rows = rows[: args.limit_rows]
        print(
            f"[WARN] --limit-rows={args.limit_rows} 적용 — production 측정 아님",
            file=sys.stderr,
        )
    print(f"[INFO] 골든셋 v2 row 수: {len(rows)}", file=sys.stderr)

    t0 = time.monotonic()
    cells = _measure_all(rows)
    elapsed = time.monotonic() - t0

    overall, by_qt, by_dt, by_cap, by_qt_cap = aggregate_all(cells)
    print(
        f"[INFO] 측정 완료 — {elapsed:.1f}s, "
        f"R@10={overall.avg_recall_at_10:.4f}, "
        f"top-1={overall.top1_rate:.4f}, "
        f"P95={overall.p95_latency_ms:.1f}ms",
        file=sys.stderr,
    )

    md = _format_markdown(
        overall=overall,
        by_qtype=by_qt,
        by_doc_type=by_dt,
        by_caption=by_cap,
        by_qtype_caption=by_qt_cap,
        n_golden=len(rows),
    )
    out_md = Path(args.out)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(md, encoding="utf-8")
    print(f"[OK] markdown report: {out_md}", file=sys.stderr)

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    raw = {
        "n_golden": len(rows),
        "elapsed_sec": round(elapsed, 2),
        "baseline_env": _BASELINE_ENV,
        "overall": _summary_to_dict(overall),
        "by_qtype": [_summary_to_dict(s) for s in by_qt],
        "by_doc_type": [_summary_to_dict(s) for s in by_dt],
        "by_caption_dependent": [_summary_to_dict(s) for s in by_cap],
        "by_qtype_caption": [_summary_to_dict(s) for s in by_qt_cap],
        "cells": _serialize_cells(cells),
    }
    out_json.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[OK] raw json: {out_json}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
