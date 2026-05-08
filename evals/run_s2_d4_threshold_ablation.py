"""S2 D4 옵션 A — vision_need_score threshold ablation 측정.

master plan §6 S2 D4 옵션 A — D3 ship 임계 (5 신호 OR rule) 의 11 후보 (C0~C5 메인
조합 + A1~A5 단독 ablation) 를 D3 raw signal CSV 기반으로 시뮬레이션 측정.

설계 원칙
- **vision_need_score 모듈 상수 변경 0** (운영 모듈 격리). 후보 임계는 본 스크립트
  내부의 ``Threshold`` dataclass 로 표현하고 ``_or_rule_with_thresholds()`` 동등
  함수로 재계산. monkey-patch 금지.
- D3 raw signal CSV (`evals/results/vision_need_score_d3.csv`) 의 페이지별 raw 신호 5종
  (text_density / table_like_score / image_area_ratio / text_quality / caption_score)
  만 사용. PDF 재파싱 0, 외부 API 0.
- run_s2_d4_pre_regression 의 loader / cross-check / markdown formatter 패턴 재사용.
- §6.2 결정 트리에 따라 권고 후보 자동 산출.

산출
- markdown report: 11 후보 × (overall_skip_rate / per-doc skip / hint_hit_rate /
  데이터센터 p.40 catch / chunk_hit_rate (옵션))
- JSON: 후보별 raw 결과 (machine-readable)
- per-row CSV: hint cross-check + (옵션) chunk cross-check

실행
    cd api && uv run python ../evals/run_s2_d4_threshold_ablation.py \\
        --output ../evals/results/s2_d4_threshold_ablation.md \\
        --json ../evals/results/s2_d4_threshold_ablation.json
    --use-db        # DB 정답 chunk page cross-check 활성 (read-only)
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import unicodedata
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

# api/ 를 import path 에 추가 — DB chunk page lookup 위해
_API_PATH = Path(__file__).resolve().parents[0].parent / "api"
if (_API_PATH / "app").exists():
    sys.path.insert(0, str(_API_PATH))

_REPO_ROOT = Path(__file__).resolve().parent.parent
_D3_CSV = _REPO_ROOT / "evals" / "results" / "vision_need_score_d3.csv"
_GOLDEN_CSV = _REPO_ROOT / "evals" / "golden_v1.csv"
_DEFAULT_OUTPUT_MD = _REPO_ROOT / "evals" / "results" / "s2_d4_threshold_ablation.md"
_DEFAULT_OUTPUT_JSON = _REPO_ROOT / "evals" / "results" / "s2_d4_threshold_ablation.json"

# 골든셋 vision_diagram + table_lookup row 만 측정 대상 (D4-pre 와 동일 정책)
_TARGET_QUERY_TYPES = {"vision_diagram", "table_lookup"}

# 데이터센터 p.40 catch 후보 식별 — golden id + doc title prefix + page 매칭
# G-A-008 = 데이터센터 산업 활성화 지원 사업 안내서 p.40 (table_lookup, 어제 D3 회귀 row)
_DATACENTER_P40_GOLDEN_ID = "G-A-008"
_DATACENTER_P40_DOC_PREFIX = "(붙임2) 2025년 데이터센터"
_DATACENTER_P40_PAGE = 40

# 결정 트리 (§6.2) 의 hit_rate 임계
_DECISION_HIT_RATE_THRESHOLD = 5 / 6  # 83.3%
# 채택 후보 skip rate 가 본 임계 미만이면 사용자 확인 필요 (Q-S2-D4-4)
_LOW_SKIP_RATE_WARN_AT = 0.30


# ---------------------------------------------------------------------------
# Threshold dataclass (P2-1 — magic number → 명시 라벨 + docstring)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Threshold:
    """vision_need_score OR rule 의 5 신호 임계 — ablation 후보 1개 표현.

    필드별 의미는 vision_need_score.py 모듈 상수와 동일. ``trigger_*`` 필드는
    "해당 신호를 OR rule 에 포함시킬지 여부" — 단독 ablation (A1~A5) 에서 한 신호
    제외 1개만 활성화하는 데 사용.

    name: ablation report 에 표기되는 후보 ID (예: ``C0_baseline``, ``A3_image``).
    """

    name: str
    density: float  # text_density 가 본 값 미만이면 trigger
    table: float  # table_like_score 가 본 값 이상이면 trigger
    image: float  # image_area_ratio 가 본 값 이상이면 trigger
    quality: float  # text_quality 가 본 값 이하이면 trigger
    caption: float  # caption_score 가 본 값 이상이면 trigger
    trigger_density: bool = True
    trigger_table: bool = True
    trigger_image: bool = True
    trigger_quality: bool = True
    trigger_caption: bool = True


def _build_candidates() -> list[Threshold]:
    """C0~C5 메인 조합 + A1~A5 단독 ablation 11 후보 생성."""
    main = [
        Threshold(name="C0_baseline", density=1e-3, table=0.30, image=0.30, quality=0.40, caption=0.20),
        Threshold(name="C1_conservative", density=1e-3, table=0.40, image=0.40, quality=0.30, caption=0.30),
        Threshold(name="C2_aggressive", density=1.5e-3, table=0.20, image=0.20, quality=0.50, caption=0.10),
        Threshold(name="C3_caption_aggr", density=1e-3, table=0.30, image=0.30, quality=0.40, caption=0.10),
        Threshold(name="C4_image_aggr", density=1e-3, table=0.30, image=0.20, quality=0.40, caption=0.20),
        Threshold(name="C5_density_aggr", density=2e-3, table=0.30, image=0.30, quality=0.40, caption=0.20),
    ]
    # A1~A5: 한 신호만 활성, 나머지 4 신호 OFF (trigger_* = False)
    # 임계는 baseline 값 사용 (단독 효과 측정).
    ablation = [
        Threshold(
            name="A1_density_only",
            density=1e-3, table=0.30, image=0.30, quality=0.40, caption=0.20,
            trigger_density=True, trigger_table=False, trigger_image=False,
            trigger_quality=False, trigger_caption=False,
        ),
        Threshold(
            name="A2_table_only",
            density=1e-3, table=0.30, image=0.30, quality=0.40, caption=0.20,
            trigger_density=False, trigger_table=True, trigger_image=False,
            trigger_quality=False, trigger_caption=False,
        ),
        Threshold(
            name="A3_image_only",
            density=1e-3, table=0.30, image=0.30, quality=0.40, caption=0.20,
            trigger_density=False, trigger_table=False, trigger_image=True,
            trigger_quality=False, trigger_caption=False,
        ),
        Threshold(
            name="A4_quality_only",
            density=1e-3, table=0.30, image=0.30, quality=0.40, caption=0.20,
            trigger_density=False, trigger_table=False, trigger_image=False,
            trigger_quality=True, trigger_caption=False,
        ),
        Threshold(
            name="A5_caption_only",
            density=1e-3, table=0.30, image=0.30, quality=0.40, caption=0.20,
            trigger_density=False, trigger_table=False, trigger_image=False,
            trigger_quality=False, trigger_caption=True,
        ),
    ]
    return main + ablation


# ---------------------------------------------------------------------------
# DTO — D3 raw signal 페이지 + golden row + 시뮬레이션 결과
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class D3PageSignal:
    """D3 raw signal CSV 페이지 1행 — 5 신호 + page meta."""

    doc: str
    page: int
    page_area_pt2: float
    text_density: float
    table_like_score: float
    image_area_ratio: float
    text_quality: float
    caption_score: float


@dataclass(frozen=True)
class GoldenRow:
    """골든셋 v1 의 측정 대상 row (vision_diagram + table_lookup)."""

    id: str
    query: str
    query_type: str
    doc_id: str
    expected_doc_title: str
    relevant_chunks: tuple[int, ...]
    source_hint: str


@dataclass(frozen=True)
class PageDecision:
    """단일 후보·페이지 시뮬레이션 결과."""

    doc: str
    page: int
    needs_vision: bool
    triggers: tuple[str, ...]


@dataclass(frozen=True)
class HintHit:
    """hint cross-check 결과 — golden row 1건."""

    golden_id: str
    query_type: str
    expected_doc_title: str
    matched_d3_doc: str | None
    hint_page: int | None
    needs_vision_at_hint: bool | None  # None = 측정 불가
    triggers: tuple[str, ...]
    note: str


@dataclass(frozen=True)
class ChunkHit:
    """DB chunk page cross-check 결과 — 정답 chunk 1건."""

    golden_id: str
    chunk_idx: int
    chunk_page: int | None
    matched_d3_doc: str | None
    needs_vision_at_page: bool | None
    triggers: tuple[str, ...]
    note: str


@dataclass(frozen=True)
class CandidateResult:
    """후보 1개의 종합 결과 — markdown / JSON 출력 source."""

    threshold: Threshold
    overall_skip_rate: float
    overall_total_pages: int
    overall_skipped: int
    per_doc_skip: dict[str, dict]  # doc → {total/skipped/rate}
    hint_hits: list[HintHit]
    hint_hit_rate: float  # 측정 가능 row 중 needs_vision=True 비율
    hint_measurable: int  # needs_vision_at_hint != None 의 row 수
    datacenter_p40_caught: bool  # G-A-008 의 hint page (40) needs_vision=True?
    chunk_hits: list[ChunkHit] | None  # --use-db 시만
    chunk_hit_rate: float | None


# ---------------------------------------------------------------------------
# OR rule with substituted thresholds — vision_need_score 모듈 상수 격리
# ---------------------------------------------------------------------------


def _or_rule_with_thresholds(signal: D3PageSignal, t: Threshold) -> dict[str, bool]:
    """D3 OR rule 동등 함수 — 후보 임계로 재계산. 본 스크립트 안 단일 source.

    vision_need_score._or_rule_triggers 의 동등 구현. 단, 본 함수는 monkey-patch 없이
    후보 임계 (Threshold) 와 trigger_* 활성 플래그를 모두 반영. 운영 모듈은 변경 0.
    """
    triggers: dict[str, bool] = {}
    triggers["low_density"] = (
        t.trigger_density
        and signal.page_area_pt2 > 0
        and signal.text_density < t.density
    )
    triggers["table_like"] = (
        t.trigger_table and signal.table_like_score >= t.table
    )
    triggers["image_area"] = (
        t.trigger_image and signal.image_area_ratio >= t.image
    )
    triggers["text_quality_low"] = (
        t.trigger_quality and signal.text_quality <= t.quality
    )
    triggers["caption"] = (
        t.trigger_caption and signal.caption_score >= t.caption
    )
    return triggers


def recompute_with_thresholds(
    signals: list[D3PageSignal], t: Threshold
) -> list[PageDecision]:
    """후보 임계로 모든 페이지의 needs_vision 재계산.

    ablation 스크립트의 핵심 함수 — 운영 모듈 (vision_need_score) 호출 0, 본 함수
    내부의 _or_rule_with_thresholds 로 단일 source.
    """
    out: list[PageDecision] = []
    for s in signals:
        triggers = _or_rule_with_thresholds(s, t)
        active = tuple(k for k, v in triggers.items() if v)
        out.append(
            PageDecision(
                doc=s.doc,
                page=s.page,
                needs_vision=bool(active),
                triggers=active,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Loaders — D3 CSV / 골든셋 (D4-pre 패턴 재사용)
# ---------------------------------------------------------------------------


def _load_d3_signals(csv_path: Path) -> list[D3PageSignal]:
    """D3 raw signal CSV 로드. needs_vision / signal_kinds 컬럼은 무시 (재계산용)."""
    out: list[D3PageSignal] = []
    with csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                out.append(
                    D3PageSignal(
                        doc=row["doc"].strip(),
                        page=int(row["page"]),
                        page_area_pt2=float(row.get("page_area_pt2") or 0.0),
                        text_density=float(row.get("text_density") or 0.0),
                        table_like_score=float(row.get("table_like_score") or 0.0),
                        image_area_ratio=float(row.get("image_area_ratio") or 0.0),
                        text_quality=float(row.get("text_quality") or 1.0),
                        caption_score=float(row.get("caption_score") or 0.0),
                    )
                )
            except (KeyError, ValueError) as exc:
                print(
                    f"[WARN] D3 CSV row skip (parse error: {exc}): {row}",
                    file=sys.stderr,
                )
    return out


def _load_golden_targets(csv_path: Path) -> list[GoldenRow]:
    """골든셋 v1 의 vision_diagram + table_lookup row 만 추출 (D4-pre 동일)."""
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
# doc_skip_summary — per-doc + overall (D4-pre 패턴 재사용)
# ---------------------------------------------------------------------------


def doc_skip_summary(decisions: list[PageDecision]) -> tuple[dict[str, dict], int, int, float]:
    """doc 별 + overall skip rate. (per_doc, total, skipped, rate)."""
    by_doc: dict[str, list[PageDecision]] = defaultdict(list)
    for d in decisions:
        by_doc[d.doc].append(d)
    per_doc: dict[str, dict] = {}
    for doc, pages in by_doc.items():
        total = len(pages)
        skipped = sum(1 for p in pages if not p.needs_vision)
        per_doc[doc] = {
            "total_pages": total,
            "skipped": skipped,
            "called": total - skipped,
            "skip_rate": skipped / total if total else 0.0,
        }
    total_pages = sum(v["total_pages"] for v in per_doc.values())
    total_skipped = sum(v["skipped"] for v in per_doc.values())
    rate = total_skipped / total_pages if total_pages else 0.0
    return per_doc, total_pages, total_skipped, rate


# ---------------------------------------------------------------------------
# hint cross-check (D4-pre 패턴 재사용 — Threshold 별 재계산)
# ---------------------------------------------------------------------------


def _parse_source_hint_page(hint: str) -> int | None:
    """`p.40` / `p.6 근처` 등에서 page 번호 추출 (D4-pre 동일 정책)."""
    if not hint:
        return None
    m = re.search(r"p\.\s*(\d+)", hint, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _match_d3_doc(
    doc_title: str, decisions_by_doc: dict[str, list[PageDecision]]
) -> str | None:
    """golden expected_doc_title → D3 CSV doc 컬럼 매칭 (prefix 12자, D4-pre 동일)."""
    if not doc_title:
        return None
    norm_title = unicodedata.normalize("NFC", doc_title)
    for cand in (f"{norm_title}.pdf", norm_title):
        if cand in decisions_by_doc:
            return cand
    head = norm_title[:12]
    if not head:
        return None
    for d3_doc in decisions_by_doc:
        if head in unicodedata.normalize("NFC", d3_doc):
            return d3_doc
    return None


def cross_check_hints(
    golden_rows: list[GoldenRow], decisions: list[PageDecision]
) -> list[HintHit]:
    """source_hint 의 page 가 본 후보의 needs_vision=True/False 였는지."""
    by_doc: dict[str, list[PageDecision]] = defaultdict(list)
    for d in decisions:
        by_doc[d.doc].append(d)
    out: list[HintHit] = []
    for g in golden_rows:
        d3_doc = _match_d3_doc(g.expected_doc_title, by_doc)
        hint_page = _parse_source_hint_page(g.source_hint)
        if d3_doc is None:
            out.append(HintHit(
                golden_id=g.id, query_type=g.query_type,
                expected_doc_title=g.expected_doc_title,
                matched_d3_doc=None, hint_page=hint_page,
                needs_vision_at_hint=None, triggers=(), note="doc 미매칭",
            ))
            continue
        if hint_page is None:
            out.append(HintHit(
                golden_id=g.id, query_type=g.query_type,
                expected_doc_title=g.expected_doc_title,
                matched_d3_doc=d3_doc, hint_page=None,
                needs_vision_at_hint=None, triggers=(), note="page 미상",
            ))
            continue
        page_decision = next(
            (p for p in by_doc[d3_doc] if p.page == hint_page), None
        )
        if page_decision is None:
            out.append(HintHit(
                golden_id=g.id, query_type=g.query_type,
                expected_doc_title=g.expected_doc_title,
                matched_d3_doc=d3_doc, hint_page=hint_page,
                needs_vision_at_hint=None, triggers=(),
                note=f"page {hint_page} D3 CSV 부재",
            ))
            continue
        out.append(HintHit(
            golden_id=g.id, query_type=g.query_type,
            expected_doc_title=g.expected_doc_title,
            matched_d3_doc=d3_doc, hint_page=hint_page,
            needs_vision_at_hint=page_decision.needs_vision,
            triggers=page_decision.triggers, note="OK",
        ))
    return out


def _hint_hit_rate(hints: list[HintHit]) -> tuple[float, int]:
    """needs_vision=True 비율 / 측정 가능 row 수. None 은 분모 제외."""
    measurable = [h for h in hints if h.needs_vision_at_hint is not None]
    if not measurable:
        return 0.0, 0
    hits = sum(1 for h in measurable if h.needs_vision_at_hint)
    return hits / len(measurable), len(measurable)


def datacenter_p40_catch(decisions: list[PageDecision]) -> bool:
    """데이터센터 p.40 catch 여부 — G-A-008 회귀 row 의 needs_vision=True?"""
    by_doc: dict[str, list[PageDecision]] = defaultdict(list)
    for d in decisions:
        by_doc[d.doc].append(d)
    for doc, pages in by_doc.items():
        if _DATACENTER_P40_DOC_PREFIX in doc:
            for p in pages:
                if p.page == _DATACENTER_P40_PAGE:
                    return p.needs_vision
    return False


# ---------------------------------------------------------------------------
# DB chunk page cross-check (옵션 --use-db) — D4-pre 패턴 재사용
# ---------------------------------------------------------------------------


def _fetch_chunk_pages(
    golden_rows: list[GoldenRow],
) -> dict[tuple[str, int], int | None]:
    """chunks 테이블에서 (doc_id, chunk_idx) → page lookup (read-only)."""
    from app.db import get_supabase_client  # noqa: E402

    client = get_supabase_client()
    out: dict[tuple[str, int], int | None] = {}
    for g in golden_rows:
        if not g.doc_id:
            continue
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


def cross_check_chunks(
    golden_rows: list[GoldenRow],
    chunk_page_map: dict[tuple[str, int], int | None],
    decisions: list[PageDecision],
) -> list[ChunkHit]:
    """정답 chunk page 가 본 후보의 needs_vision=True/False 였는지."""
    by_doc: dict[str, list[PageDecision]] = defaultdict(list)
    for d in decisions:
        by_doc[d.doc].append(d)
    out: list[ChunkHit] = []
    for g in golden_rows:
        if not g.doc_id:
            continue
        d3_doc = _match_d3_doc(g.expected_doc_title, by_doc)
        for chunk_idx in g.relevant_chunks:
            page = chunk_page_map.get((g.doc_id, chunk_idx))
            if page is None:
                out.append(ChunkHit(
                    golden_id=g.id, chunk_idx=chunk_idx,
                    chunk_page=None, matched_d3_doc=d3_doc,
                    needs_vision_at_page=None, triggers=(),
                    note="chunk page DB 부재",
                ))
                continue
            if d3_doc is None:
                out.append(ChunkHit(
                    golden_id=g.id, chunk_idx=chunk_idx,
                    chunk_page=page, matched_d3_doc=None,
                    needs_vision_at_page=None, triggers=(),
                    note="doc 미매칭",
                ))
                continue
            page_decision = next(
                (p for p in by_doc[d3_doc] if p.page == page), None
            )
            if page_decision is None:
                out.append(ChunkHit(
                    golden_id=g.id, chunk_idx=chunk_idx,
                    chunk_page=page, matched_d3_doc=d3_doc,
                    needs_vision_at_page=None, triggers=(),
                    note=f"page {page} D3 CSV 부재",
                ))
                continue
            out.append(ChunkHit(
                golden_id=g.id, chunk_idx=chunk_idx,
                chunk_page=page, matched_d3_doc=d3_doc,
                needs_vision_at_page=page_decision.needs_vision,
                triggers=page_decision.triggers, note="OK",
            ))
    return out


def _chunk_hit_rate(chunks: list[ChunkHit]) -> float | None:
    measurable = [c for c in chunks if c.needs_vision_at_page is not None]
    if not measurable:
        return None
    hits = sum(1 for c in measurable if c.needs_vision_at_page)
    return hits / len(measurable)


# ---------------------------------------------------------------------------
# 후보 1개 평가 (= 시뮬레이션 + cross-check 통합)
# ---------------------------------------------------------------------------


def evaluate_candidate(
    threshold: Threshold,
    signals: list[D3PageSignal],
    golden_rows: list[GoldenRow],
    chunk_page_map: dict[tuple[str, int], int | None] | None = None,
) -> CandidateResult:
    """후보 1개에 대해 시뮬레이션 + hint / chunk cross-check 일괄 산출."""
    decisions = recompute_with_thresholds(signals, threshold)
    per_doc, total, skipped, rate = doc_skip_summary(decisions)
    hints = cross_check_hints(golden_rows, decisions)
    hit_rate, measurable = _hint_hit_rate(hints)
    p40_caught = datacenter_p40_catch(decisions)
    chunks: list[ChunkHit] | None = None
    chunk_rate: float | None = None
    if chunk_page_map is not None:
        chunks = cross_check_chunks(golden_rows, chunk_page_map, decisions)
        chunk_rate = _chunk_hit_rate(chunks)
    return CandidateResult(
        threshold=threshold,
        overall_skip_rate=rate,
        overall_total_pages=total,
        overall_skipped=skipped,
        per_doc_skip=per_doc,
        hint_hits=hints,
        hint_hit_rate=hit_rate,
        hint_measurable=measurable,
        datacenter_p40_caught=p40_caught,
        chunk_hits=chunks,
        chunk_hit_rate=chunk_rate,
    )


# ---------------------------------------------------------------------------
# §6.2 결정 트리 — 자동 권고 산출
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Recommendation:
    """결정 트리 산출물 — markdown § 마지막 + 보고용."""

    chosen_candidate: str | None
    rationale: str
    needs_user_confirm: bool
    user_confirm_reason: str
    s15_v3_trigger: bool


def build_recommendation(results: list[CandidateResult]) -> Recommendation:
    """§6.2 결정 트리 (Q1 → Q2 → Q3) 적용. 동률 다수 시 catch 우선 → skip rate 우선."""
    high_hit = [r for r in results if r.hint_hit_rate >= _DECISION_HIT_RATE_THRESHOLD]
    # Q1
    if not high_hit:
        return Recommendation(
            chosen_candidate=None,
            rationale=(
                f"hint hit_rate ≥ {_DECISION_HIT_RATE_THRESHOLD * 100:.1f}% 후보 0건. "
                "C0 baseline 유지 + S1.5 v3 (table 휴리스틱 v3 / multi-line table) 시급도 ↑."
            ),
            needs_user_confirm=False,
            user_confirm_reason="",
            s15_v3_trigger=True,
        )
    if len(high_hit) == 1:
        chosen = high_hit[0]
        return Recommendation(
            chosen_candidate=chosen.threshold.name,
            rationale=(
                f"hit_rate ≥ {_DECISION_HIT_RATE_THRESHOLD * 100:.1f}% 후보 단독 — "
                f"{chosen.threshold.name} (hit_rate={chosen.hint_hit_rate * 100:.1f}%, "
                f"skip_rate={chosen.overall_skip_rate * 100:.1f}%) 채택."
            ),
            needs_user_confirm=chosen.overall_skip_rate < _LOW_SKIP_RATE_WARN_AT,
            user_confirm_reason=(
                f"skip_rate {chosen.overall_skip_rate * 100:.1f}% "
                f"< {_LOW_SKIP_RATE_WARN_AT * 100:.0f}% (Q-S2-D4-4)"
                if chosen.overall_skip_rate < _LOW_SKIP_RATE_WARN_AT
                else ""
            ),
            s15_v3_trigger=False,
        )
    # Q2 — 동률 다수
    catchers = [r for r in high_hit if r.datacenter_p40_caught]
    if catchers:
        # cost ↓ = skip rate 가장 높은 catcher 채택
        chosen = max(catchers, key=lambda r: r.overall_skip_rate)
        return Recommendation(
            chosen_candidate=chosen.threshold.name,
            rationale=(
                f"동률 hit_rate ≥ {_DECISION_HIT_RATE_THRESHOLD * 100:.1f}% 후보 "
                f"{len(high_hit)}개 중 데이터센터 p.40 catch {len(catchers)}개. "
                f"catch 후보 중 skip_rate 가장 높은 {chosen.threshold.name} "
                f"(skip_rate={chosen.overall_skip_rate * 100:.1f}%, hit_rate={chosen.hint_hit_rate * 100:.1f}%) 채택."
            ),
            needs_user_confirm=chosen.overall_skip_rate < _LOW_SKIP_RATE_WARN_AT,
            user_confirm_reason=(
                f"skip_rate {chosen.overall_skip_rate * 100:.1f}% "
                f"< {_LOW_SKIP_RATE_WARN_AT * 100:.0f}% (Q-S2-D4-4)"
                if chosen.overall_skip_rate < _LOW_SKIP_RATE_WARN_AT
                else ""
            ),
            s15_v3_trigger=False,
        )
    # 동률이지만 catch 0개 → "5 신호로는 catch 불가" + skip rate 높은 후보 + S1.5 v3 trigger
    chosen = max(high_hit, key=lambda r: r.overall_skip_rate)
    return Recommendation(
        chosen_candidate=chosen.threshold.name,
        rationale=(
            f"동률 hit_rate ≥ {_DECISION_HIT_RATE_THRESHOLD * 100:.1f}% 후보 "
            f"{len(high_hit)}개 — 데이터센터 p.40 catch 0. **5 신호로는 구조적 catch 불가**, "
            f"skip_rate 가장 높은 {chosen.threshold.name} "
            f"(skip_rate={chosen.overall_skip_rate * 100:.1f}%, hit_rate={chosen.hint_hit_rate * 100:.1f}%) 채택 + "
            "S1.5 v3 (table 휴리스틱 v3 / multi-line table fallback) trigger 권고."
        ),
        needs_user_confirm=chosen.overall_skip_rate < _LOW_SKIP_RATE_WARN_AT,
        user_confirm_reason=(
            f"skip_rate {chosen.overall_skip_rate * 100:.1f}% "
            f"< {_LOW_SKIP_RATE_WARN_AT * 100:.0f}% (Q-S2-D4-4)"
            if chosen.overall_skip_rate < _LOW_SKIP_RATE_WARN_AT
            else ""
        ),
        s15_v3_trigger=True,
    )


# ---------------------------------------------------------------------------
# Markdown 출력 (D4-pre 의 _format_markdown 패턴 재사용)
# ---------------------------------------------------------------------------


def _format_markdown(
    results: list[CandidateResult],
    recommendation: Recommendation,
    *,
    use_db: bool,
) -> str:
    lines: list[str] = []
    lines.append("# S2 D4 옵션 A — vision_need_score threshold ablation")
    lines.append("")
    lines.append(
        "D3 raw signal CSV 기반 11 후보 (C0~C5 메인 조합 + A1~A5 단독 ablation) 시뮬레이션. "
        "vision_need_score 모듈 상수 변경 0, 외부 vision API 호출 0."
    )
    lines.append("")

    # §1 — 후보별 종합 표 (메인 측정 결과)
    lines.append("## §1 후보별 종합 결과")
    lines.append("")
    if use_db:
        lines.append(
            "| 후보 | density | table | image | quality | caption | overall_skip% | "
            "hint_hit_rate | DC p.40 catch | chunk_hit_rate |"
        )
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|:---:|---:|")
    else:
        lines.append(
            "| 후보 | density | table | image | quality | caption | overall_skip% | "
            "hint_hit_rate | DC p.40 catch |"
        )
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|:---:|")
    for r in results:
        t = r.threshold
        # ablation 후보는 비활성 신호를 — 로 표기
        d_str = f"{t.density:.1e}" if t.trigger_density else "—"
        tb_str = f"{t.table:.2f}" if t.trigger_table else "—"
        i_str = f"{t.image:.2f}" if t.trigger_image else "—"
        q_str = f"{t.quality:.2f}" if t.trigger_quality else "—"
        c_str = f"{t.caption:.2f}" if t.trigger_caption else "—"
        catch = "Yes" if r.datacenter_p40_caught else "No"
        hit_str = f"{r.hint_hit_rate * 100:.1f}% ({int(round(r.hint_hit_rate * r.hint_measurable))}/{r.hint_measurable})"
        skip_str = f"{r.overall_skip_rate * 100:.1f}% ({r.overall_skipped}/{r.overall_total_pages})"
        if use_db and r.chunk_hit_rate is not None:
            chunk_str = f"{r.chunk_hit_rate * 100:.1f}%"
            lines.append(
                f"| {t.name} | {d_str} | {tb_str} | {i_str} | {q_str} | {c_str} | "
                f"{skip_str} | {hit_str} | {catch} | {chunk_str} |"
            )
        elif use_db:
            lines.append(
                f"| {t.name} | {d_str} | {tb_str} | {i_str} | {q_str} | {c_str} | "
                f"{skip_str} | {hit_str} | {catch} | — |"
            )
        else:
            lines.append(
                f"| {t.name} | {d_str} | {tb_str} | {i_str} | {q_str} | {c_str} | "
                f"{skip_str} | {hit_str} | {catch} |"
            )
    lines.append("")
    lines.append(
        f"- 측정 기준 — overall = D3 raw signal CSV 모든 페이지 / "
        f"hint = 골든셋 vision_diagram + table_lookup row 의 source_hint(p.X) cross-check"
    )
    lines.append("")

    # §2 — 후보별 per-doc skip 표
    lines.append("## §2 후보별 per-doc skip rate")
    lines.append("")
    docs_sorted = sorted({d for r in results for d in r.per_doc_skip})
    header = "| 후보 | " + " | ".join(d[:30] for d in docs_sorted) + " |"
    lines.append(header)
    lines.append("|" + "---|" * (len(docs_sorted) + 1))
    for r in results:
        cells = [r.threshold.name]
        for d in docs_sorted:
            stats = r.per_doc_skip.get(d, {})
            if stats:
                cells.append(f"{stats['skip_rate'] * 100:.0f}% ({stats['skipped']}/{stats['total_pages']})")
            else:
                cells.append("—")
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")

    # §3 — hint cross-check 상세 (C0 baseline 만 — 나머지는 같은 row set, 결정값만 다름)
    if results:
        baseline = next((r for r in results if r.threshold.name == "C0_baseline"), results[0])
        lines.append("## §3 hint cross-check 상세 (C0_baseline 기준 row set, 모든 후보 공통)")
        lines.append("")
        lines.append("| id | query_type | doc title | hint page | C0 needs_vision | triggers | note |")
        lines.append("|---|---|---|---:|:---:|---|---|")
        for h in baseline.hint_hits:
            nv_str = (
                "True" if h.needs_vision_at_hint is True
                else ("False" if h.needs_vision_at_hint is False else "—")
            )
            page_str = str(h.hint_page) if h.hint_page else "—"
            trig_str = "|".join(h.triggers) if h.triggers else "—"
            title_short = h.expected_doc_title[:30]
            lines.append(
                f"| {h.golden_id} | {h.query_type} | {title_short} | {page_str} | "
                f"{nv_str} | {trig_str} | {h.note} |"
            )
        lines.append("")
        lines.append(
            f"- 측정 가능 row: **{baseline.hint_measurable}건** "
            "(나머지는 doc 미매칭/page 미상으로 측정 불가)"
        )
        lines.append("")

    # §4 — DB chunk cross-check (옵션)
    if use_db:
        lines.append("## §4 DB chunk page cross-check (--use-db)")
        lines.append("")
        lines.append(
            "정답 chunk 의 `page` 컬럼이 needs_vision=False 면 vision-derived 였을 경우 회귀 가능."
        )
        lines.append("")
        for r in results:
            if r.chunk_hits is None:
                continue
            measurable = [c for c in r.chunk_hits if c.needs_vision_at_page is not None]
            risky = sum(1 for c in measurable if not c.needs_vision_at_page)
            lines.append(
                f"- **{r.threshold.name}**: chunk-level 회귀 위험 "
                f"{risky}/{len(measurable)} ({100 * risky / max(1, len(measurable)):.1f}%)"
            )
        lines.append("")

    # §5 — 데이터센터 p.40 catch 상세
    lines.append("## §5 데이터센터 p.40 catch 후보 (회귀 위험 row G-A-008)")
    lines.append("")
    lines.append(
        "데이터센터 산업 활성화 안내서 p.40 (table_lookup) 의 D3 raw signal: "
        "density 1.62e-3 / table 0 / image_area 0.009 / text_quality 0.97 / caption 0.067 — "
        "C0 baseline 5 신호 모두 미달 → needs_vision=False (회귀 위험)."
    )
    lines.append("")
    catchers = [r for r in results if r.datacenter_p40_caught]
    if not catchers:
        lines.append(
            "- **catch 후보 0** — 5 신호 만으로는 본 페이지 catch 구조적 불가. "
            "S1.5 v3 (table 휴리스틱 v3) 또는 추가 신호 필요."
        )
    else:
        for r in catchers:
            lines.append(
                f"- {r.threshold.name} (skip_rate={r.overall_skip_rate * 100:.1f}%, "
                f"hit_rate={r.hint_hit_rate * 100:.1f}%)"
            )
    lines.append("")

    # §6 — 자동 권고
    lines.append("## §6 §6.2 결정 트리 자동 권고")
    lines.append("")
    if recommendation.chosen_candidate:
        lines.append(f"- **권고 후보**: `{recommendation.chosen_candidate}`")
    else:
        lines.append("- **권고 후보**: 없음 (C0 baseline 유지 + S1.5 v3 진입)")
    lines.append(f"- 근거: {recommendation.rationale}")
    if recommendation.needs_user_confirm:
        lines.append(
            f"- **사용자 확인 필요** (Q-S2-D4-4): {recommendation.user_confirm_reason}"
        )
    if recommendation.s15_v3_trigger:
        lines.append(
            "- **S1.5 v3 trigger 권고** — table 휴리스틱 v3 / multi-line table fallback / "
            "추가 신호 검토 필요."
        )
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# JSON / CSV 출력
# ---------------------------------------------------------------------------


def _result_to_json_dict(r: CandidateResult) -> dict:
    """CandidateResult 직렬화 — JSON 친화 dict (HintHit / ChunkHit 풀어서)."""
    return {
        "threshold": asdict(r.threshold),
        "overall_skip_rate": r.overall_skip_rate,
        "overall_total_pages": r.overall_total_pages,
        "overall_skipped": r.overall_skipped,
        "per_doc_skip": r.per_doc_skip,
        "hint_hit_rate": r.hint_hit_rate,
        "hint_measurable": r.hint_measurable,
        "datacenter_p40_caught": r.datacenter_p40_caught,
        "hint_hits": [
            {
                "golden_id": h.golden_id,
                "query_type": h.query_type,
                "expected_doc_title": h.expected_doc_title,
                "matched_d3_doc": h.matched_d3_doc,
                "hint_page": h.hint_page,
                "needs_vision_at_hint": h.needs_vision_at_hint,
                "triggers": list(h.triggers),
                "note": h.note,
            }
            for h in r.hint_hits
        ],
        "chunk_hit_rate": r.chunk_hit_rate,
        "chunk_hits": (
            None if r.chunk_hits is None else [
                {
                    "golden_id": c.golden_id,
                    "chunk_idx": c.chunk_idx,
                    "chunk_page": c.chunk_page,
                    "matched_d3_doc": c.matched_d3_doc,
                    "needs_vision_at_page": c.needs_vision_at_page,
                    "triggers": list(c.triggers),
                    "note": c.note,
                }
                for c in r.chunk_hits
            ]
        ),
    }


def _hint_csv_rows(results: list[CandidateResult]) -> list[dict]:
    """hint cross-check per-row CSV — 후보 × golden_id."""
    rows: list[dict] = []
    for r in results:
        for h in r.hint_hits:
            rows.append({
                "candidate": r.threshold.name,
                "golden_id": h.golden_id,
                "query_type": h.query_type,
                "expected_doc_title": h.expected_doc_title,
                "hint_page": h.hint_page if h.hint_page is not None else "",
                "needs_vision_at_hint": (
                    "" if h.needs_vision_at_hint is None
                    else str(h.needs_vision_at_hint)
                ),
                "triggers": "|".join(h.triggers),
                "note": h.note,
            })
    return rows


def _chunk_csv_rows(results: list[CandidateResult]) -> list[dict]:
    """chunk cross-check per-row CSV — 후보 × (golden_id, chunk_idx). chunks=None skip."""
    rows: list[dict] = []
    for r in results:
        if r.chunk_hits is None:
            continue
        for c in r.chunk_hits:
            rows.append({
                "candidate": r.threshold.name,
                "golden_id": c.golden_id,
                "chunk_idx": c.chunk_idx,
                "chunk_page": c.chunk_page if c.chunk_page is not None else "",
                "needs_vision_at_page": (
                    "" if c.needs_vision_at_page is None
                    else str(c.needs_vision_at_page)
                ),
                "triggers": "|".join(c.triggers),
                "note": c.note,
            })
    return rows


def _write_csv(rows: list[dict], path: Path) -> None:
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="S2 D4 옵션 A — vision_need_score threshold ablation"
    )
    parser.add_argument("--output", "-o", default=str(_DEFAULT_OUTPUT_MD))
    parser.add_argument("--json", "-j", default=str(_DEFAULT_OUTPUT_JSON))
    parser.add_argument(
        "--use-db", action="store_true",
        help="DB chunk page cross-check 활성 (read-only)",
    )
    parser.add_argument("--d3-csv", default=str(_D3_CSV))
    parser.add_argument("--golden", default=str(_GOLDEN_CSV))
    args = parser.parse_args()

    d3_path = Path(args.d3_csv)
    golden_path = Path(args.golden)
    if not d3_path.exists():
        print(
            f"[ERROR] D3 CSV 없음: {d3_path}\n"
            "  → cd api && uv run python scripts/poc_vision_need_score.py 로 재생성",
            file=sys.stderr,
        )
        return 1
    if not golden_path.exists():
        print(f"[ERROR] 골든셋 CSV 없음: {golden_path}", file=sys.stderr)
        return 1

    signals = _load_d3_signals(d3_path)
    golden_rows = _load_golden_targets(golden_path)
    print(
        f"[OK] D3 페이지 {len(signals)}건 / 골든셋 vision_diagram+table_lookup {len(golden_rows)}건 로드",
        file=sys.stderr,
    )

    chunk_page_map: dict[tuple[str, int], int | None] | None = None
    if args.use_db:
        try:
            chunk_page_map = _fetch_chunk_pages(golden_rows)
            print(
                f"[OK] DB chunk page lookup {len(chunk_page_map)}건",
                file=sys.stderr,
            )
        except Exception as exc:  # noqa: BLE001 — 부분 실패 허용
            print(f"[WARN] DB 조회 실패: {exc} → --use-db 미적용", file=sys.stderr)
            chunk_page_map = None

    candidates = _build_candidates()
    results = [
        evaluate_candidate(t, signals, golden_rows, chunk_page_map)
        for t in candidates
    ]

    recommendation = build_recommendation(results)
    md = _format_markdown(results, recommendation, use_db=chunk_page_map is not None)

    out_md = Path(args.output)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(md, encoding="utf-8")
    print(f"[OK] markdown → {out_md}", file=sys.stderr)

    out_json = Path(args.json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(
        json.dumps(
            {
                "candidates": [_result_to_json_dict(r) for r in results],
                "recommendation": asdict(recommendation),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[OK] JSON → {out_json}", file=sys.stderr)

    hint_csv = out_md.with_name(out_md.stem + "_hints.csv")
    _write_csv(_hint_csv_rows(results), hint_csv)
    print(f"[OK] hint CSV → {hint_csv}", file=sys.stderr)

    if chunk_page_map is not None:
        chunk_csv = out_md.with_name(out_md.stem + "_chunks.csv")
        rows = _chunk_csv_rows(results)
        if rows:
            _write_csv(rows, chunk_csv)
            print(f"[OK] chunk CSV → {chunk_csv}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
