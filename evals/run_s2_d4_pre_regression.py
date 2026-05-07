"""S2 D4-pre 회귀 측정 — needs_vision skip 의 골든셋 영향 검증.

master plan §6 S2 D4-pre — D2/D3 운영 정책 변경 전 골든셋 recall 보호 검증 가드.
가설: ~36% 페이지 vision skip → 골든셋 recall 5/6 (83.3%) 유지 (vision_diagram +
table_lookup 의 정답 페이지는 모두 needs_vision=True).

옵션 C (Hybrid) 진행 — mock 시뮬레이션 + DB 정답 chunk page cross-check.

(1) Mock 시뮬레이션
  - 어제 D3 측정 CSV (`evals/results/vision_need_score_d3.csv`) 의 needs_vision 결정
  - 골든셋 v1 의 vision_diagram + table_lookup row 의 source_hint (p.X) 와 cross-check
  - source_hint 정답 페이지가 needs_vision=False 면 = 회귀 위험 row

(2) DB 정답 chunk page cross-check (--use-db 시 활성)
  - 골든셋 row 의 relevant_chunks chunk_idx → chunks 테이블 조회 → page 추출
  - 그 page 가 D3 mock 의 needs_vision=False 페이지면 잠재 회귀 chunk
  - 단순 page-level coverage — vision-derived 인지 일반 text 인지 구분 불가

(3) 실제 retrieval (--measure-retrieval 시 활성, 실 search() 호출)
  - 현재 DB chunks (S2 D1 ship 이전 상태) 로 baseline R@10 측정
  - 추후 reingest 후 동일 측정으로 회귀 ±X pp 산출 (본 스크립트 -1회 실행만으로는 비교 불가)

산출:
  - markdown report (stdout 또는 --output)
  - per-row CSV (--output 동일 경로 .csv)

실행:
  cd api && uv run python ../evals/run_s2_d4_pre_regression.py \\
      --output ../evals/results/s2_d4_pre_regression.md
  --use-db        # DB 조회 활성 (Supabase 연결 필요)
  --measure-retrieval  # 실제 search() 호출 (DB 필요, baseline 측정용)
"""

from __future__ import annotations

import argparse
import csv
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# api/ 를 import path 에 추가 — DB / search() 직접 호출 위해
_API_PATH = Path(__file__).resolve().parents[0].parent / "api"
if (_API_PATH / "app").exists():
    sys.path.insert(0, str(_API_PATH))

_REPO_ROOT = Path(__file__).resolve().parent.parent
_D3_CSV = _REPO_ROOT / "evals" / "results" / "vision_need_score_d3.csv"
_GOLDEN_CSV = _REPO_ROOT / "evals" / "golden_v1.csv"

# 골든셋 v1 의 vision_diagram + table_lookup row 만 측정 대상
_TARGET_QUERY_TYPES = {"vision_diagram", "table_lookup"}

# doc title → D3 CSV 의 doc 컬럼 매핑 (.pdf 확장자 포함)
# golden 의 expected_doc_title 에 맞춰 partial match 로 cross-check.


# ---------------------------------------------------------------------------
# DTO
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class D3PageDecision:
    """D3 측정 CSV 의 페이지 단위 결정값."""

    doc: str  # D3 CSV 의 doc 컬럼 (.pdf 확장자 포함)
    page: int  # 1-based
    needs_vision: bool
    triggers: tuple[str, ...]  # signal_kinds 컬럼 (`|` 분리)


@dataclass(frozen=True)
class GoldenRow:
    """골든셋 v1 의 측정 대상 row."""

    id: str
    query: str
    query_type: str  # vision_diagram | table_lookup
    doc_id: str  # UUID 또는 빈 (U-row)
    expected_doc_title: str
    relevant_chunks: tuple[int, ...]  # chunk_idx
    source_hint: str  # "p.40" 같은 자유 텍스트


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _load_d3_decisions(csv_path: Path) -> list[D3PageDecision]:
    """D3 측정 CSV 로드 — `evals/results/vision_need_score_d3.csv`."""
    out: list[D3PageDecision] = []
    with csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            triggers_raw = (row.get("signal_kinds") or "").strip()
            triggers = (
                tuple(t for t in triggers_raw.split("|") if t)
                if triggers_raw
                else ()
            )
            out.append(
                D3PageDecision(
                    doc=row["doc"].strip(),
                    page=int(row["page"]),
                    needs_vision=row["needs_vision"].strip() == "True",
                    triggers=triggers,
                )
            )
    return out


def _load_golden_targets(csv_path: Path) -> list[GoldenRow]:
    """골든셋 v1 의 vision_diagram + table_lookup row 만 추출."""
    out: list[GoldenRow] = []
    with csv_path.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            qtype = (row.get("query_type") or "").strip()
            if qtype not in _TARGET_QUERY_TYPES:
                continue
            relv_str = (row.get("relevant_chunks") or "").strip()
            relv = tuple(
                int(x.strip()) for x in relv_str.split(",") if x.strip().isdigit()
            )
            out.append(
                GoldenRow(
                    id=row["id"].strip(),
                    query=row["query"].strip(),
                    query_type=qtype,
                    doc_id=(row.get("doc_id") or "").strip(),
                    expected_doc_title=(row.get("expected_doc_title") or "").strip(),
                    relevant_chunks=relv,
                    source_hint=(row.get("source_hint") or "").strip(),
                )
            )
    return out


# ---------------------------------------------------------------------------
# (1) Mock 시뮬레이션 — D3 needs_vision 분포
# ---------------------------------------------------------------------------


def _doc_skip_summary(decisions: list[D3PageDecision]) -> list[dict]:
    """doc 별 needs_vision skip 비율 (= False 페이지 비율)."""
    by_doc: dict[str, list[D3PageDecision]] = defaultdict(list)
    for d in decisions:
        by_doc[d.doc].append(d)
    rows: list[dict] = []
    for doc, pages in by_doc.items():
        total = len(pages)
        skipped = sum(1 for p in pages if not p.needs_vision)
        called = total - skipped
        rows.append(
            {
                "doc": doc,
                "total_pages": total,
                "needs_vision_called": called,
                "needs_vision_skipped": skipped,
                "skip_rate": skipped / total if total else 0.0,
            }
        )
    rows.sort(key=lambda r: r["doc"])
    return rows


def _overall_skip_rate(rows: list[dict]) -> tuple[int, int, int, float]:
    """(total, called, skipped, skip_rate) 전체 합산."""
    total = sum(r["total_pages"] for r in rows)
    called = sum(r["needs_vision_called"] for r in rows)
    skipped = sum(r["needs_vision_skipped"] for r in rows)
    rate = skipped / total if total else 0.0
    return total, called, skipped, rate


# ---------------------------------------------------------------------------
# (2) Golden cross-check — source_hint(p.X) cross-check
# ---------------------------------------------------------------------------


def _parse_source_hint_page(hint: str) -> int | None:
    """`p.40` / `p.6 근처` / `p.40` 등에서 페이지 번호 추출.

    실패 시 None — golden row 의 source_hint 가 page 명시 안 한 경우 mock 검증 불가.
    """
    if not hint:
        return None
    import re

    m = re.search(r"p\.\s*(\d+)", hint, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


def _match_d3_doc(
    doc_title: str, decisions_by_doc: dict[str, list[D3PageDecision]]
) -> str | None:
    """golden 의 expected_doc_title 을 D3 CSV 의 doc 컬럼 (.pdf 확장자) 와 매칭.

    title 첫 12자 partial match — title 끝이 잘려있는 경우 (golden CSV 가 truncate) 대응.
    """
    if not doc_title:
        return None
    norm_title = unicodedata.normalize("NFC", doc_title)
    # exact + extension 시도
    candidates = [f"{norm_title}.pdf", norm_title]
    for cand in candidates:
        if cand in decisions_by_doc:
            return cand
    # partial — golden title 의 prefix 가 D3 doc 에 포함
    head = norm_title[:12]
    for d3_doc in decisions_by_doc:
        if head and head in unicodedata.normalize("NFC", d3_doc):
            return d3_doc
    return None


@dataclass(frozen=True)
class HintCrossCheck:
    """source_hint cross-check 결과."""

    golden_id: str
    query_type: str
    expected_doc_title: str
    matched_d3_doc: str | None
    hint_page: int | None
    needs_vision_at_hint: bool | None  # None = 측정 불가 (page 미상 또는 doc 미매칭)
    triggers: tuple[str, ...]
    note: str  # "page 미상" / "doc 미매칭" / OK


def _cross_check_hints(
    golden_rows: list[GoldenRow], decisions: list[D3PageDecision]
) -> list[HintCrossCheck]:
    """source_hint 의 page 가 needs_vision=True/False 였는지 cross-check.

    - hint_page 추출 실패 → note="page 미상" / needs_vision_at_hint=None
    - doc 매칭 실패 (예: pptx/hwpx/없는 doc) → note="doc 미매칭" / needs_vision_at_hint=None
    - 정상 → needs_vision_at_hint 결정값 + triggers
    """
    by_doc: dict[str, list[D3PageDecision]] = defaultdict(list)
    for d in decisions:
        by_doc[d.doc].append(d)

    results: list[HintCrossCheck] = []
    for g in golden_rows:
        d3_doc = _match_d3_doc(g.expected_doc_title, by_doc)
        hint_page = _parse_source_hint_page(g.source_hint)
        if d3_doc is None:
            results.append(
                HintCrossCheck(
                    golden_id=g.id,
                    query_type=g.query_type,
                    expected_doc_title=g.expected_doc_title,
                    matched_d3_doc=None,
                    hint_page=hint_page,
                    needs_vision_at_hint=None,
                    triggers=(),
                    note="doc 미매칭",
                )
            )
            continue
        if hint_page is None:
            results.append(
                HintCrossCheck(
                    golden_id=g.id,
                    query_type=g.query_type,
                    expected_doc_title=g.expected_doc_title,
                    matched_d3_doc=d3_doc,
                    hint_page=None,
                    needs_vision_at_hint=None,
                    triggers=(),
                    note="page 미상",
                )
            )
            continue
        # page 매칭
        page_decision = next(
            (p for p in by_doc[d3_doc] if p.page == hint_page), None
        )
        if page_decision is None:
            results.append(
                HintCrossCheck(
                    golden_id=g.id,
                    query_type=g.query_type,
                    expected_doc_title=g.expected_doc_title,
                    matched_d3_doc=d3_doc,
                    hint_page=hint_page,
                    needs_vision_at_hint=None,
                    triggers=(),
                    note=f"page {hint_page} D3 CSV 부재",
                )
            )
            continue
        results.append(
            HintCrossCheck(
                golden_id=g.id,
                query_type=g.query_type,
                expected_doc_title=g.expected_doc_title,
                matched_d3_doc=d3_doc,
                hint_page=hint_page,
                needs_vision_at_hint=page_decision.needs_vision,
                triggers=page_decision.triggers,
                note="OK",
            )
        )
    return results


# ---------------------------------------------------------------------------
# (3) DB 정답 chunk page cross-check (--use-db)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChunkPageCheck:
    """정답 chunk 의 page 가 needs_vision=True/False 인지."""

    golden_id: str
    chunk_idx: int
    chunk_page: int | None
    matched_d3_doc: str | None
    needs_vision_at_page: bool | None
    triggers: tuple[str, ...]
    note: str


def _fetch_chunk_pages(
    golden_rows: list[GoldenRow],
) -> dict[tuple[str, int], int | None]:
    """chunks 테이블에서 (doc_id, chunk_idx) → page 매핑 fetch.

    DB 연결 필요 — `app.db.get_supabase_client()`.
    """
    from app.db import get_supabase_client  # noqa: E402

    client = get_supabase_client()

    out: dict[tuple[str, int], int | None] = {}
    for g in golden_rows:
        if not g.doc_id:
            continue  # U-row (doc_id 없음) skip
        for chunk_idx in g.relevant_chunks:
            key = (g.doc_id, chunk_idx)
            if key in out:
                continue
            resp = (
                client.table("chunks")
                .select("page")
                .eq("doc_id", g.doc_id)
                .eq("chunk_idx", chunk_idx)
                .limit(1)
                .execute()
            )
            rows = resp.data or []
            out[key] = rows[0].get("page") if rows else None
    return out


def _doc_id_to_d3_title(
    golden_rows: list[GoldenRow],
) -> dict[str, str]:
    """golden row 의 doc_id → expected_doc_title 매핑 (DB 조회 결과 page → D3 doc 매칭용)."""
    out: dict[str, str] = {}
    for g in golden_rows:
        if g.doc_id and g.expected_doc_title:
            out[g.doc_id] = g.expected_doc_title
    return out


def _check_chunk_pages(
    golden_rows: list[GoldenRow],
    chunk_page_map: dict[tuple[str, int], int | None],
    decisions: list[D3PageDecision],
) -> list[ChunkPageCheck]:
    """정답 chunk 의 page 의 needs_vision 결정값 lookup."""
    by_doc: dict[str, list[D3PageDecision]] = defaultdict(list)
    for d in decisions:
        by_doc[d.doc].append(d)
    title_map = _doc_id_to_d3_title(golden_rows)

    results: list[ChunkPageCheck] = []
    for g in golden_rows:
        if not g.doc_id:
            continue
        d3_doc = _match_d3_doc(g.expected_doc_title, by_doc)
        for chunk_idx in g.relevant_chunks:
            page = chunk_page_map.get((g.doc_id, chunk_idx))
            if page is None:
                results.append(
                    ChunkPageCheck(
                        golden_id=g.id,
                        chunk_idx=chunk_idx,
                        chunk_page=None,
                        matched_d3_doc=d3_doc,
                        needs_vision_at_page=None,
                        triggers=(),
                        note="chunk page DB 부재",
                    )
                )
                continue
            if d3_doc is None:
                results.append(
                    ChunkPageCheck(
                        golden_id=g.id,
                        chunk_idx=chunk_idx,
                        chunk_page=page,
                        matched_d3_doc=None,
                        needs_vision_at_page=None,
                        triggers=(),
                        note="doc 미매칭",
                    )
                )
                continue
            page_decision = next(
                (p for p in by_doc[d3_doc] if p.page == page), None
            )
            if page_decision is None:
                results.append(
                    ChunkPageCheck(
                        golden_id=g.id,
                        chunk_idx=chunk_idx,
                        chunk_page=page,
                        matched_d3_doc=d3_doc,
                        needs_vision_at_page=None,
                        triggers=(),
                        note=f"page {page} D3 CSV 부재",
                    )
                )
                continue
            results.append(
                ChunkPageCheck(
                    golden_id=g.id,
                    chunk_idx=chunk_idx,
                    chunk_page=page,
                    matched_d3_doc=d3_doc,
                    needs_vision_at_page=page_decision.needs_vision,
                    triggers=page_decision.triggers,
                    note="OK",
                )
            )
    return results


# ---------------------------------------------------------------------------
# (4) 실 retrieval (--measure-retrieval) — baseline 측정용
# ---------------------------------------------------------------------------


def _measure_baseline_retrieval(
    golden_rows: list[GoldenRow], k: int = 10
) -> list[dict]:
    """현재 DB chunks 상태에서 R@10 측정 (baseline). 추후 reingest 후 비교 용도."""
    from app.routers.search import search  # noqa: E402
    from app.services.retrieval_metrics import recall_at_k  # noqa: E402

    results: list[dict] = []
    for g in golden_rows:
        if not g.doc_id:
            results.append(
                {
                    "id": g.id,
                    "query_type": g.query_type,
                    "doc_id": "",
                    "recall_at_10": None,
                    "predicted_top10": [],
                    "note": "doc_id 없음 (U-row, retrieval 평가 skip)",
                }
            )
            continue
        try:
            resp = search(
                q=unicodedata.normalize("NFC", g.query),
                limit=50,
                offset=0,
                tags=None,
                doc_type=None,
                from_date=None,
                to_date=None,
                doc_id=g.doc_id,
                mode="hybrid",
            )
            data = resp.model_dump()
            items = data.get("items", [])
            chunks: list[int] = []
            for it in items:
                if it.get("doc_id") == g.doc_id:
                    matched = sorted(
                        it.get("matched_chunks", []),
                        key=lambda c: c.get("rrf_score") or 0.0,
                        reverse=True,
                    )
                    chunks = [c["chunk_idx"] for c in matched]
                    break
            relv_set = set(g.relevant_chunks)
            recall = recall_at_k(chunks, relv_set, k=k)
            results.append(
                {
                    "id": g.id,
                    "query_type": g.query_type,
                    "doc_id": g.doc_id,
                    "recall_at_10": recall,
                    "predicted_top10": chunks[:k],
                    "note": "OK",
                }
            )
        except Exception as exc:  # noqa: BLE001
            results.append(
                {
                    "id": g.id,
                    "query_type": g.query_type,
                    "doc_id": g.doc_id,
                    "recall_at_10": None,
                    "predicted_top10": [],
                    "note": f"ERROR: {exc}",
                }
            )
    return results


# ---------------------------------------------------------------------------
# Markdown 출력
# ---------------------------------------------------------------------------


def _format_markdown(
    *,
    skip_summary: list[dict],
    overall: tuple[int, int, int, float],
    hint_checks: list[HintCrossCheck],
    chunk_checks: list[ChunkPageCheck] | None,
    retrieval: list[dict] | None,
) -> str:
    lines: list[str] = []
    lines.append("# S2 D4-pre 회귀 측정 — needs_vision skip 의 골든셋 영향")
    lines.append("")
    lines.append(
        "옵션 C (Hybrid) — D3 측정 CSV 기반 mock + (옵션) DB 정답 chunk page cross-check"
    )
    lines.append("")

    # (1) doc 별 skip 비율
    total, called, skipped, rate = overall
    lines.append("## §1 needs_vision skip / called 분포 (D3 측정 결과 기반)")
    lines.append("")
    lines.append(f"- **합계**: {total} pages → called {called} / skipped {skipped} (skip {rate * 100:.1f}%)")
    lines.append("")
    lines.append("| doc | total | called | skipped | skip_rate |")
    lines.append("|---|---:|---:|---:|---:|")
    for r in skip_summary:
        lines.append(
            f"| {r['doc']} | {r['total_pages']} | {r['needs_vision_called']} | "
            f"{r['needs_vision_skipped']} | {r['skip_rate'] * 100:.1f}% |"
        )
    lines.append("")

    # (2) source_hint cross-check
    lines.append("## §2 골든셋 source_hint cross-check (vision_diagram + table_lookup)")
    lines.append("")
    lines.append("`source_hint` 의 page 가 D3 mock 의 needs_vision=False 면 = 회귀 위험 row.")
    lines.append("")
    lines.append("| id | query_type | doc title | hint page | needs_vision | triggers | note |")
    lines.append("|---|---|---|---:|:---:|---|---|")
    risk_rows = []
    safe_rows = []
    unknown_rows = []
    for h in hint_checks:
        nv_str = (
            "True"
            if h.needs_vision_at_hint is True
            else ("False" if h.needs_vision_at_hint is False else "—")
        )
        page_str = str(h.hint_page) if h.hint_page else "—"
        trig_str = "|".join(h.triggers) if h.triggers else "—"
        title_short = h.expected_doc_title[:30]
        lines.append(
            f"| {h.golden_id} | {h.query_type} | {title_short} | {page_str} | "
            f"{nv_str} | {trig_str} | {h.note} |"
        )
        if h.needs_vision_at_hint is False:
            risk_rows.append(h)
        elif h.needs_vision_at_hint is True:
            safe_rows.append(h)
        else:
            unknown_rows.append(h)
    lines.append("")
    lines.append(
        f"- **회귀 위험**: {len(risk_rows)}건 (hint page 의 needs_vision=False)"
    )
    lines.append(f"- **안전**: {len(safe_rows)}건 (hint page 의 needs_vision=True)")
    lines.append(f"- **불명**: {len(unknown_rows)}건 (page 미상 / doc 미매칭)")
    lines.append("")
    if risk_rows:
        lines.append("### 회귀 위험 row 상세")
        lines.append("")
        for h in risk_rows:
            lines.append(
                f"- **{h.golden_id}** ({h.query_type}, {h.expected_doc_title} p.{h.hint_page}): "
                f"needs_vision=False → vision skip 시 정답 페이지 OCR/caption 누락 위험"
            )
        lines.append("")

    # (3) DB 정답 chunk page cross-check (옵션)
    if chunk_checks is not None:
        lines.append("## §3 DB 정답 chunk page cross-check (--use-db)")
        lines.append("")
        lines.append(
            "정답 chunk 의 `page` 컬럼이 needs_vision=False 면 = "
            "vision-derived 였을 경우 회귀 가능 (일반 text 추출은 skip 영향 0)."
        )
        lines.append("")
        lines.append(
            "| golden_id | chunk_idx | chunk_page | needs_vision | triggers | note |"
        )
        lines.append("|---|---:|---:|:---:|---|---|")
        risky_chunks = []
        safe_chunks = []
        unknown_chunks = []
        for c in chunk_checks:
            nv_str = (
                "True"
                if c.needs_vision_at_page is True
                else ("False" if c.needs_vision_at_page is False else "—")
            )
            page_str = str(c.chunk_page) if c.chunk_page else "—"
            trig_str = "|".join(c.triggers) if c.triggers else "—"
            lines.append(
                f"| {c.golden_id} | {c.chunk_idx} | {page_str} | {nv_str} | "
                f"{trig_str} | {c.note} |"
            )
            if c.needs_vision_at_page is False:
                risky_chunks.append(c)
            elif c.needs_vision_at_page is True:
                safe_chunks.append(c)
            else:
                unknown_chunks.append(c)
        lines.append("")
        total_chunks = len(chunk_checks)
        lines.append(
            f"- chunk-level 회귀 위험: {len(risky_chunks)}/{total_chunks} "
            f"({100 * len(risky_chunks) / total_chunks:.1f}%)"
            if total_chunks
            else "- chunk-level 측정 0건"
        )
        lines.append(f"- 안전 chunk: {len(safe_chunks)}")
        lines.append(f"- 불명: {len(unknown_chunks)}")
        lines.append("")

    # (4) baseline retrieval (옵션)
    if retrieval is not None:
        lines.append("## §4 baseline retrieval R@10 (--measure-retrieval)")
        lines.append("")
        lines.append("현재 DB chunks 상태 (S2 D1 ship 이전 적재) — 추후 reingest 후 동일 측정으로 회귀 ±X pp 산출.")
        lines.append("")
        lines.append("| id | query_type | recall_at_10 | predicted_top10 | note |")
        lines.append("|---|---|---:|---|---|")
        successful = [r for r in retrieval if r.get("recall_at_10") is not None]
        for r in retrieval:
            recall_str = (
                f"{r['recall_at_10']:.3f}"
                if r.get("recall_at_10") is not None
                else "—"
            )
            top10 = ",".join(map(str, r.get("predicted_top10", [])[:5]))
            lines.append(
                f"| {r['id']} | {r['query_type']} | {recall_str} | {top10} | {r.get('note', '')} |"
            )
        lines.append("")
        if successful:
            avg_r10 = sum(r["recall_at_10"] for r in successful) / len(successful)
            lines.append(f"- 평균 R@10: {avg_r10:.4f} (n={len(successful)})")
            lines.append("")

    # (5) 결정
    lines.append("## §5 결정")
    lines.append("")
    risk_count = sum(1 for h in hint_checks if h.needs_vision_at_hint is False)
    if chunk_checks is not None:
        risky_chunk_ratio = sum(
            1 for c in chunk_checks if c.needs_vision_at_page is False
        ) / max(1, len(chunk_checks))
    else:
        risky_chunk_ratio = None

    # 회귀 위험 row 와 baseline retrieval 의 cross-check —
    # 정답 chunk 가 현재 (vision 호출된) baseline 에서 top-10 에 retrieve 되면
    # 그 chunk 는 일반 text extraction 산출일 가능성 높음 → vision skip 영향 적음.
    risk_already_retrievable: list[str] = []
    if retrieval is not None:
        retrieval_by_id = {r["id"]: r for r in retrieval}
        for h in hint_checks:
            if h.needs_vision_at_hint is False:
                r = retrieval_by_id.get(h.golden_id)
                if r and r.get("recall_at_10") and r["recall_at_10"] >= 0.5:
                    risk_already_retrievable.append(h.golden_id)

    lines.append(f"- source_hint 회귀 위험 row: **{risk_count}건**")
    if risky_chunk_ratio is not None:
        lines.append(f"- DB chunk-level 회귀 위험 비율: **{risky_chunk_ratio * 100:.1f}%**")
    if retrieval is not None:
        lines.append(
            f"- 회귀 위험 row 중 baseline R@10 ≥ 0.5: **{len(risk_already_retrievable)}건** "
            "(정답 chunk 가 일반 text extraction 산출 → vision skip 영향 ≈ 0)"
        )
    lines.append("")
    lines.append("### 권고")
    lines.append("")
    real_risk = risk_count - len(risk_already_retrievable)
    if real_risk <= 0:
        lines.append(
            "- ✅ S2 D2 진입 가능 — 표면 회귀 위험 row 의 정답 chunk 가 현재 baseline 에서 "
            "이미 top-10 retrieve. 정답 chunk 가 vision-derived 가 아닌 일반 text 추출 산출이라 "
            "vision skip 영향 ≈ 0."
        )
    elif real_risk == 1:
        lines.append(
            "- ⚠️  경계선 — 실 회귀 위험 1건. 옵션 A (실 reingest + 골든셋 R@10) 권고 후 D2 진입 결정."
        )
    else:
        lines.append(
            "- ❌ S1.5 v3 (table 휴리스틱 v3 / multi-line table) 또는 임계 정정 우선. "
            f"실 회귀 위험 {real_risk}건 — D2 직진 시 recall 보장 불가."
        )
    lines.append("")
    return "\n".join(lines)


def _write_csv(rows: list[dict], path: Path) -> None:
    """rows → CSV (rows 가 비어있으면 skip)."""
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def _hint_checks_to_rows(hint_checks: list[HintCrossCheck]) -> list[dict]:
    return [
        {
            "golden_id": h.golden_id,
            "query_type": h.query_type,
            "expected_doc_title": h.expected_doc_title,
            "matched_d3_doc": h.matched_d3_doc or "",
            "hint_page": h.hint_page if h.hint_page is not None else "",
            "needs_vision_at_hint": (
                ""
                if h.needs_vision_at_hint is None
                else str(h.needs_vision_at_hint)
            ),
            "triggers": "|".join(h.triggers),
            "note": h.note,
        }
        for h in hint_checks
    ]


def _chunk_checks_to_rows(checks: list[ChunkPageCheck]) -> list[dict]:
    return [
        {
            "golden_id": c.golden_id,
            "chunk_idx": c.chunk_idx,
            "chunk_page": c.chunk_page if c.chunk_page is not None else "",
            "matched_d3_doc": c.matched_d3_doc or "",
            "needs_vision_at_page": (
                ""
                if c.needs_vision_at_page is None
                else str(c.needs_vision_at_page)
            ),
            "triggers": "|".join(c.triggers),
            "note": c.note,
        }
        for c in checks
    ]


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="S2 D4-pre 회귀 측정 — needs_vision skip 의 골든셋 영향"
    )
    parser.add_argument("--output", "-o", help="markdown 출력 경로")
    parser.add_argument(
        "--use-db",
        action="store_true",
        help="DB 조회 활성 (정답 chunk page cross-check)",
    )
    parser.add_argument(
        "--measure-retrieval",
        action="store_true",
        help="실제 search() 호출 baseline R@10 측정 (DB 필요)",
    )
    parser.add_argument(
        "--d3-csv",
        type=str,
        default=str(_D3_CSV),
        help=f"D3 측정 CSV 경로 (default: {_D3_CSV})",
    )
    parser.add_argument(
        "--golden-csv",
        type=str,
        default=str(_GOLDEN_CSV),
        help=f"골든셋 CSV 경로 (default: {_GOLDEN_CSV})",
    )
    args = parser.parse_args()

    d3_path = Path(args.d3_csv)
    golden_path = Path(args.golden_csv)
    if not d3_path.exists():
        print(f"[ERROR] D3 CSV 없음: {d3_path}", file=sys.stderr)
        return 1
    if not golden_path.exists():
        print(f"[ERROR] 골든셋 CSV 없음: {golden_path}", file=sys.stderr)
        return 1

    decisions = _load_d3_decisions(d3_path)
    golden_rows = _load_golden_targets(golden_path)
    print(
        f"[OK] D3 페이지 {len(decisions)}건 / 골든셋 vision_diagram+table_lookup {len(golden_rows)}건 로드",
        file=sys.stderr,
    )

    skip_summary = _doc_skip_summary(decisions)
    overall = _overall_skip_rate(skip_summary)
    hint_checks = _cross_check_hints(golden_rows, decisions)

    chunk_checks: list[ChunkPageCheck] | None = None
    if args.use_db or args.measure_retrieval:
        try:
            chunk_page_map = _fetch_chunk_pages(golden_rows)
            chunk_checks = _check_chunk_pages(
                golden_rows, chunk_page_map, decisions
            )
            print(
                f"[OK] DB chunk page lookup {len(chunk_page_map)}건",
                file=sys.stderr,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] DB 조회 실패: {exc}", file=sys.stderr)
            chunk_checks = None

    retrieval: list[dict] | None = None
    if args.measure_retrieval:
        try:
            retrieval = _measure_baseline_retrieval(golden_rows)
            print(
                f"[OK] baseline retrieval 측정 {len(retrieval)}건",
                file=sys.stderr,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] retrieval 측정 실패: {exc}", file=sys.stderr)
            retrieval = None

    md = _format_markdown(
        skip_summary=skip_summary,
        overall=overall,
        hint_checks=hint_checks,
        chunk_checks=chunk_checks,
        retrieval=retrieval,
    )

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(md, encoding="utf-8")
        print(f"[OK] markdown → {out_path}", file=sys.stderr)
        # CSV 동봉
        hint_csv = out_path.with_name(out_path.stem + "_hints.csv")
        _write_csv(_hint_checks_to_rows(hint_checks), hint_csv)
        print(f"[OK] CSV → {hint_csv}", file=sys.stderr)
        if chunk_checks is not None:
            chunk_csv = out_path.with_name(out_path.stem + "_chunks.csv")
            _write_csv(_chunk_checks_to_rows(chunk_checks), chunk_csv)
            print(f"[OK] CSV → {chunk_csv}", file=sys.stderr)
        if retrieval is not None:
            ret_csv = out_path.with_name(out_path.stem + "_retrieval.csv")
            _write_csv(
                [
                    {
                        "id": r["id"],
                        "query_type": r["query_type"],
                        "doc_id": r["doc_id"],
                        "recall_at_10": (
                            r["recall_at_10"] if r["recall_at_10"] is not None else ""
                        ),
                        "predicted_top10": ",".join(
                            map(str, r.get("predicted_top10", []))
                        ),
                        "note": r["note"],
                    }
                    for r in retrieval
                ],
                ret_csv,
            )
            print(f"[OK] CSV → {ret_csv}", file=sys.stderr)
    else:
        print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
