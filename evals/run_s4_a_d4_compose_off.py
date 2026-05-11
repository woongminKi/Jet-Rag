"""S4-A D4 — golden_v2 R@10 3축 breakdown 측정 도구.

목적
----
S4-A D3 Phase 1 으로 보강된 골든셋 v2 (157 row × 14 컬럼) 를 입력으로,
hybrid retrieve 후 R@10 / R@5 / MRR 을 다음 3축으로 breakdown 측정한다.

3축 breakdown
-------------
(1) **qtype 별 9종** — exact_fact / cross_doc / vision_diagram / synonym_mismatch /
    fuzzy_memory / summary / numeric_lookup / table_lookup / out_of_scope.
    축당 N / R@10 / R@5 / MRR / fail (doc 매칭 fail + ERROR) 표.
(2) **caption_dependent** — true 18 row vs false 139 row 의 R@10 비교 + delta.
(3) **prompt_version (v1 vs v2)** — `vision_page_cache` 의 `prompt_version` 컬럼을
    row 의 expected_doc title sha256 + page 와 join 해 v1/v2 라벨링.
    현재 D4 시점 reingest 미진행 → v2 row 0건일 가능성 큰 hook (출력에서
    "N/A — D5 reingest 후 측정 예정" 으로 명시).

설계 원칙
---------
- **검색 path 변경 0** — S4-A D2 chunk.text 합성만 적용된 상태에서 측정.
- **외부 API cost 0** — Supabase 적재된 BGE-M3 embedding 만 활용. Gemini Vision
  호출 0. HF embed_query 는 search() 내부 호출 — 무료 티어, 측정 자체는 허용.
- **운영 코드 변경 0** — `api/` 하위 수정 없음. `evals/` 신규 1 파일 + tests.
- **재사용 우선** — `run_s3_d5_search_stack_eval.py` 의 search 호출 + chunk 정렬
  + retrieval_metrics 의 graded recall/mrr 패턴 그대로.

산출
----
- ``evals/results/s4_a_d4_results.md`` — 3축 breakdown 표 + 요약 + caveat
- ``evals/results/s4_a_d4_raw.json`` — per-query 결과 + groupby raw

실행
----
    cd api && uv run python ../evals/run_s4_a_d4_compose_off.py --goldenset v2

    # v1 비교 (옵션)
    uv run python ../evals/run_s4_a_d4_compose_off.py --goldenset v1

    # Phase 1 option C — D2 적용 전 baseline 측정 (in-memory caption 합성 해제 + re-rank)
    uv run python ../evals/run_s4_a_d4_compose_off.py --goldenset v2 \
        --no-vision-compose \
        --out ../evals/results/s4_a_d4_baseline_compose_off.md \
        --out-json ../evals/results/s4_a_d4_baseline_compose_off.json

전제
----
- DB 적재 완료 (golden_v2 의 doc 들이 모두 Supabase chunks 에 있어야 함)
- DEFAULT_USER_ID env 설정
- ``JETRAG_RERANKER_ENABLED=false`` 권장 (순수 RRF baseline 으로 데이터 효과 측정)

Phase 1 — `--no-vision-compose` option C 메커니즘
-------------------------------------------------
1. search() 응답을 받음 (DB 적재 dense_vec 기반 hybrid 결과)
2. caption_dependent 후보 chunk (chunks.metadata 의 ``table_caption`` /
   ``figure_caption`` 키 있는 chunk) 각각의 ``chunk.text`` 에서 합성 suffix
   (``\\n\\n[표: ...]\\n[그림: ...]``) 를 regex 로 제거 → ``base_text`` 복원
3. BGE-M3 (HF API) 로 ``base_text`` 재임베딩 → 임시 dense_vec
4. 원래 query embed 와의 cosine sim 재계산 → re-rank chunk_idx
5. 재계산된 ranking 으로 R@10 / R@5 / MRR 산출

caption 합성 없는 chunk 는 ranking 그대로 유지 — 합성 chunk 만 in-place sim 갱신.
chunks DB 변경 0 — 측정 도구 in-memory 차분 산출만.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import statistics
import sys
import time
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# api/ 를 import path 에 추가 — search() 직접 호출 위해
_API_PATH = Path(__file__).resolve().parents[0].parent / "api"
if (_API_PATH / "app").exists():
    sys.path.insert(0, str(_API_PATH))

_REPO_ROOT = Path(__file__).resolve().parent.parent
_GOLDEN_CSV_BY_VERSION: dict[str, Path] = {
    "v1": _REPO_ROOT / "evals" / "golden_v1.csv",
    "v2": _REPO_ROOT / "evals" / "golden_v2.csv",
}
_DEFAULT_OUT_MD = _REPO_ROOT / "evals" / "results" / "s4_a_d4_results.md"
_DEFAULT_OUT_JSON = _REPO_ROOT / "evals" / "results" / "s4_a_d4_raw.json"

# search 응답 top-K — 한 doc 최대 50 청크 (run_s3_d5 와 동일 의도)
_SEARCH_LIMIT = 50
_TOP_K_R10 = 10
_TOP_K_R5 = 5

# qtype 9종 화이트리스트 — golden_v2 의 query_type 컬럼 값 9 분류 (의뢰서 §1)
_QTYPE_ORDER: tuple[str, ...] = (
    "exact_fact",
    "cross_doc",
    "vision_diagram",
    "synonym_mismatch",
    "fuzzy_memory",
    "summary",
    "numeric_lookup",
    "table_lookup",
    "out_of_scope",
)

# v2 한정 컬럼 — v1 에서는 빈 문자열로 기본값.
_V2_ONLY_COLS = ("doc_type", "caption_dependent")


# ---------------------------------------------------------------------------
# DTO
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GoldenRow:
    """golden_v2 의 측정 대상 row — 14 컬럼.

    v1 row 도 동일 dataclass 로 로드 가능 — v2-only 컬럼은 기본값.
    """

    id: str
    query: str
    query_type: str
    doc_id: str
    expected_doc_title: str
    relevant_chunks: tuple[int, ...]
    acceptable_chunks: tuple[int, ...]
    doc_type: str  # v2 only — "pdf" | "hwpx" | "hwp" | "pptx" | "docx" | ""
    caption_dependent: bool  # v2 only — true=18 / false=139


@dataclass
class CellResult:
    """1 row × 1 측정 = 1 cell."""

    golden_id: str
    query_type: str
    doc_id: str
    caption_dependent: bool
    doc_type: str
    # chunk-level metrics — relevant/acceptable 비어있으면 None
    recall_at_10: float | None = None
    recall_at_5: float | None = None
    mrr: float | None = None
    # 항상 측정
    latency_ms: float = 0.0
    note: str = ""
    predicted_top10: list[int] = field(default_factory=list)
    # prompt_version hook — v1/v2 라벨 (vision_page_cache lookup 결과). 미해당 시 None.
    prompt_version: str | None = None


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _parse_bool(raw: str) -> bool:
    """CSV 의 boolean 문자열 → Python bool.

    v2 CSV 의 `caption_dependent` 컬럼은 "true" / "false" 소문자 형식.
    빈 문자열은 False 로 간주 (v1 으로 본 도구 실행 시 default).
    """
    return (raw or "").strip().lower() == "true"


def load_golden_rows(csv_path: Path) -> list[GoldenRow]:
    """golden CSV 전체 row 로드 — utf-8-sig BOM 제거.

    v1 / v2 양쪽 호환. v2-only 컬럼은 v1 에서 빈 문자열 → 기본값으로 처리.
    """
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
                    doc_type=(row.get("doc_type") or "").strip(),
                    caption_dependent=_parse_bool(row.get("caption_dependent") or ""),
                )
            )
    return out


# ---------------------------------------------------------------------------
# Schema 검증 — 단위 테스트에서도 호출
# ---------------------------------------------------------------------------


def validate_golden_v2_schema(csv_path: Path) -> dict[str, Any]:
    """golden_v2.csv schema 검증 — 단위 테스트와 도구 진입 모두에서 활용.

    Returns:
        {"columns": list[str], "n_rows": int, "qtype_counts": dict,
         "doc_type_counts": dict, "caption_counts": dict}

    Raises:
        ValueError: 14 컬럼 미달 / query_type 9종 외 값 / caption_dependent 비-boolean
    """
    with csv_path.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        cols = list(reader.fieldnames or [])
        rows = list(reader)

    expected_cols = {
        "id",
        "query",
        "query_type",
        "doc_id",
        "expected_doc_title",
        "relevant_chunks",
        "acceptable_chunks",
        "source_chunk_text",
        "expected_answer_summary",
        "must_include",
        "source_hint",
        "negative",
        "doc_type",
        "caption_dependent",
    }
    missing = expected_cols - set(cols)
    if missing:
        raise ValueError(f"누락된 컬럼: {sorted(missing)}")

    qtype_counts: dict[str, int] = defaultdict(int)
    doc_type_counts: dict[str, int] = defaultdict(int)
    caption_counts: dict[str, int] = defaultdict(int)
    for r in rows:
        qt = (r.get("query_type") or "").strip()
        qtype_counts[qt] += 1
        dt = (r.get("doc_type") or "").strip() or "(empty)"
        doc_type_counts[dt] += 1
        cap_raw = (r.get("caption_dependent") or "").strip().lower()
        if cap_raw not in {"true", "false", ""}:
            raise ValueError(
                f"caption_dependent 비-boolean 값: row {r.get('id')!r} → "
                f"{cap_raw!r}"
            )
        caption_counts["true" if cap_raw == "true" else "false"] += 1

    invalid_qtypes = set(qtype_counts) - set(_QTYPE_ORDER) - {""}
    if invalid_qtypes:
        raise ValueError(
            f"화이트리스트 외 query_type: {sorted(invalid_qtypes)} "
            f"(허용: {list(_QTYPE_ORDER)})"
        )

    return {
        "columns": cols,
        "n_rows": len(rows),
        "qtype_counts": dict(qtype_counts),
        "doc_type_counts": dict(doc_type_counts),
        "caption_counts": dict(caption_counts),
    }


# ---------------------------------------------------------------------------
# Phase 1 — caption 합성 해제 (option C, in-memory)
# ---------------------------------------------------------------------------


# 합성 포맷 (api/app/ingest/stages/chunk.py::_compose_vision_text):
#   {base}\n\n[표: {table}]\n[그림: {figure}]
#   {base}\n\n[표: {table}]
#   {base}\n\n[그림: {figure}]
# table/figure 본문은 임의 텍스트 — 줄바꿈·closing bracket 으로 경계 식별.
_CAPTION_COMPOSE_SUFFIX_RE = re.compile(
    r"\n\n(?:\[표:[^\n\]]*\](?:\n\[그림:[^\n\]]*\])?|\[그림:[^\n\]]*\])\s*$"
)


def _strip_caption_compose(text: str) -> str:
    """chunk.text 의 caption 합성 suffix 를 제거해 base_text 를 복원한다.

    합성 패턴 (chunk.py::_compose_vision_text):
        base + "\\n\\n" + ("[표: X]\\n[그림: Y]" 또는 sub).

    매칭되지 않는 chunk (합성 없음 / 합성 형식 일탈) 는 원문 그대로 반환 —
    측정 도구가 caller 측 (caption_dependent 여부 무관) 에서 unconditional 호출해도
    안전한 idempotent 동작.
    """
    if not text:
        return text
    return _CAPTION_COMPOSE_SUFFIX_RE.sub("", text)


def _cosine_sim(a: list[float], b: list[float]) -> float:
    """list[float] 코사인 유사도. 빈 벡터·차원 불일치 시 0.0."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _rerank_with_base_text(
    *,
    matched_chunks: list[dict[str, Any]],
    query_vec: list[float],
    embed_fn: Callable[[str], list[float]],
) -> list[int]:
    """caption 합성 해제 후 re-rank — option C 핵심.

    각 chunk 에 대해:
    - chunks.metadata 에 ``table_caption`` 또는 ``figure_caption`` 키가 있으면
      합성 chunk → text 에서 합성 suffix 제거 + base_text 재임베딩 → cosine 재계산.
    - 둘 다 없으면 합성 미적용 chunk → 원래 ``rrf_score`` 그대로 사용.

    재정렬 키:
    - 합성 chunk: 재계산된 cosine sim (정규화 차이 흡수 위해 0~1 → 그대로 사용)
    - 비합성 chunk: 기존 rrf_score (RRF score 는 0~1 범위, 직접 비교는 안전한
      편이지만 절대값 비교 caveat 는 markdown 에 명시).

    Args:
        matched_chunks: search() 응답의 target_item.matched_chunks 리스트.
            각 dict 는 ``chunk_idx`` / ``text`` / ``metadata`` / ``rrf_score`` 키 보유.
        query_vec: 원래 query 의 BGE-M3 dense vector.
        embed_fn: text → list[float] (caller 측에서 BGE-M3 provider 의
            embed_query 주입. 테스트에서는 mock).

    Returns:
        재정렬된 ``chunk_idx`` 리스트 (내림차순 점수).
    """
    if not matched_chunks:
        return []
    scored: list[tuple[int, float]] = []
    for c in matched_chunks:
        idx = c.get("chunk_idx")
        if idx is None:
            continue
        meta = c.get("metadata") or {}
        has_caption_meta = bool(
            meta.get("table_caption") or meta.get("figure_caption")
        )
        if has_caption_meta:
            base_text = _strip_caption_compose(c.get("text") or "")
            if base_text and query_vec:
                try:
                    base_vec = embed_fn(base_text)
                except Exception as exc:  # noqa: BLE001
                    # 재임베딩 실패 — 기존 rrf_score fallback
                    print(
                        f"  [WARN] base_text 재임베딩 실패 (chunk_idx={idx}): "
                        f"{exc.__class__.__name__}",
                        file=sys.stderr,
                    )
                    score = float(c.get("rrf_score") or 0.0)
                else:
                    score = _cosine_sim(query_vec, base_vec)
            else:
                score = float(c.get("rrf_score") or 0.0)
        else:
            score = float(c.get("rrf_score") or 0.0)
        scored.append((int(idx), score))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [idx for idx, _ in scored]


# ---------------------------------------------------------------------------
# search 호출 — run_s3_d5 패턴 재사용
# ---------------------------------------------------------------------------


def _pick_target_item(
    items: list[dict[str, Any]], g: GoldenRow
) -> dict[str, Any] | None:
    """search 응답 items 중 golden row 의 expected doc 와 매칭되는 item 1건 선택.

    `run_s3_d5_search_stack_eval._pick_target_item` 와 동일 로직.
    """
    if g.doc_id:
        for it in items:
            if it.get("doc_id") == g.doc_id:
                return it
        return None
    if not g.expected_doc_title:
        return items[0] if items else None
    title_norm = unicodedata.normalize("NFC", g.expected_doc_title).lower()
    head = title_norm[:12]
    for it in items:
        item_title = unicodedata.normalize(
            "NFC", it.get("doc_title") or ""
        ).lower()
        if head and head in item_title:
            return it
    return items[0] if items else None


def _measure_one_row(
    g: GoldenRow,
    *,
    compose_off: bool = False,
    embed_fn: Callable[[str], list[float]] | None = None,
) -> CellResult:
    """1 row search() 호출 → CellResult.

    검색 path 변경 0 — 기본 ENV 그대로 (mode=hybrid).

    Args:
        g: 측정 대상 GoldenRow.
        compose_off: True 시 search() 응답을 받은 후 caption 합성 chunk 만
            in-memory 로 base_text 재임베딩 + re-rank (Phase 1 option C).
            False 시 기존 rrf_score ranking 그대로 사용.
        embed_fn: compose_off=True 일 때 query embed + base_text 재임베딩 둘 다 위임.
            None 이면 BGE-M3 provider 를 lazy 로드.
    """
    from app.routers.search import search  # noqa: E402
    from app.services.retrieval_metrics import (  # noqa: E402
        mrr as mrr_fn,
        recall_at_k,
    )

    cell = CellResult(
        golden_id=g.id,
        query_type=g.query_type,
        doc_id=g.doc_id,
        caption_dependent=g.caption_dependent,
        doc_type=g.doc_type,
    )

    if not g.query:
        cell.note = "query 비어있음"
        return cell

    query_nfc = unicodedata.normalize("NFC", g.query)
    t_start = time.monotonic()
    try:
        resp = search(
            q=query_nfc,
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

    target_item = _pick_target_item(items, g)
    if target_item is None:
        cell.note = "doc 매칭 fail"
        return cell

    matched_raw = target_item.get("matched_chunks") or []

    if compose_off and embed_fn is not None:
        # Phase 1 option C — caption 합성 chunk 만 base_text 재임베딩 후 re-rank.
        # HF embed_query LRU 활용 — 같은 query 는 캐시 hit.
        try:
            query_vec = embed_fn(query_nfc)
        except Exception as exc:  # noqa: BLE001
            print(
                f"  [WARN] query 재임베딩 실패 ({g.id}): "
                f"{exc.__class__.__name__} — compose ON fallback",
                file=sys.stderr,
            )
            query_vec = []
        if query_vec:
            chunks_top = _rerank_with_base_text(
                matched_chunks=matched_raw,
                query_vec=query_vec,
                embed_fn=embed_fn,
            )
        else:
            matched = sorted(
                matched_raw,
                key=lambda c: (c.get("rrf_score") or 0.0),
                reverse=True,
            )
            chunks_top = [c["chunk_idx"] for c in matched]
    else:
        matched = sorted(
            matched_raw,
            key=lambda c: (c.get("rrf_score") or 0.0),
            reverse=True,
        )
        chunks_top = [c["chunk_idx"] for c in matched]

    cell.predicted_top10 = chunks_top[:10]

    if g.relevant_chunks or g.acceptable_chunks:
        relv = set(g.relevant_chunks)
        accept = set(g.acceptable_chunks)
        cell.recall_at_10 = recall_at_k(
            chunks_top, relv, k=_TOP_K_R10, acceptable_chunks=accept
        )
        cell.recall_at_5 = recall_at_k(
            chunks_top, relv, k=_TOP_K_R5, acceptable_chunks=accept
        )
        cell.mrr = mrr_fn(
            chunks_top, relv, k=_TOP_K_R10, acceptable_chunks=accept
        )
    else:
        cell.note = cell.note or "정답 chunks 없음"

    return cell


def measure_all(
    rows: list[GoldenRow],
    *,
    compose_off: bool = False,
    embed_fn: Callable[[str], list[float]] | None = None,
) -> list[CellResult]:
    """전체 row 순차 측정 + 진행률 로그.

    HF embed_query cold start 영향으로 row 당 평균 2~6초 (run_s3_d5 실측 기준).
    157 row × 평균 4초 ≈ 10분.

    Args:
        rows: 측정 대상 GoldenRow 리스트.
        compose_off: True 시 Phase 1 option C 적용 (caption 합성 chunk 만
            base_text 재임베딩 + re-rank).
        embed_fn: compose_off=True 일 때 필요. None 이면 호출부에서 lazy 로드.
    """
    cells: list[CellResult] = []
    n = len(rows)
    for idx, g in enumerate(rows, start=1):
        cell = _measure_one_row(g, compose_off=compose_off, embed_fn=embed_fn)
        cells.append(cell)
        if idx % 10 == 0 or idx == n:
            avg_latency = (
                statistics.mean([c.latency_ms for c in cells if c.latency_ms > 0])
                if cells
                else 0.0
            )
            print(
                f"  [{idx}/{n}] avg_latency={avg_latency:.0f}ms — "
                f"last_note='{cell.note or 'OK'}'",
                file=sys.stderr,
            )
    return cells


# ---------------------------------------------------------------------------
# prompt_version hook — vision_page_cache lookup
# ---------------------------------------------------------------------------


def label_prompt_version(
    cells: list[CellResult],
    rows: list[GoldenRow],
    *,
    cache_rows: list[dict[str, Any]] | None = None,
) -> dict[str, int]:
    """cells 의 `prompt_version` 필드를 vision_page_cache 로 채워넣는다.

    매칭 방식 (D4 시점 caveat — 정밀 매칭은 D5 에서):
    - row 의 ``doc_id`` 가 같은 chunks 가 vision-derived 라면 그 doc 의
      ``vision_page_cache`` 에 prompt_version 이 있다.
    - 본 도구는 doc_id 기준 majority prompt_version 을 row 에 라벨링.
    - cache_rows 미지정 시 Supabase 에서 직접 조회.

    Args:
        cells: 라벨링 대상 (in-place 수정).
        rows: cells 와 동일 순서의 GoldenRow.
        cache_rows: 단위 테스트용 주입 — None 시 DB 조회.
            shape: [{"doc_id": "...", "prompt_version": "v1"|"v2"}, ...]

    Returns:
        {"v1": int, "v2": int, "unlabeled": int} 라벨 분포.
    """
    if cache_rows is None:
        cache_rows = _fetch_vision_cache_rows()

    # doc_id 별 prompt_version 빈도 — majority 채택
    by_doc: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in cache_rows:
        did = r.get("doc_id") or ""
        pv = r.get("prompt_version") or ""
        if did and pv:
            by_doc[did][pv] += 1

    counts = {"v1": 0, "v2": 0, "unlabeled": 0}
    for cell, g in zip(cells, rows):
        if not g.doc_id or g.doc_id not in by_doc:
            cell.prompt_version = None
            counts["unlabeled"] += 1
            continue
        pv_counts = by_doc[g.doc_id]
        majority = max(pv_counts.items(), key=lambda kv: kv[1])[0]
        cell.prompt_version = majority
        if majority in counts:
            counts[majority] += 1
        else:
            counts["unlabeled"] += 1
    return counts


def _fetch_vision_cache_rows() -> list[dict[str, Any]]:
    """Supabase ``vision_page_cache`` 에서 (doc_id, prompt_version) 쌍 fetch.

    스키마: `vision_page_cache(sha256, page, prompt_version, ...)`.
    doc_id 컬럼이 없어 sha256 → documents.id 매핑 필요. documents 와 inner join.

    실패 시 빈 리스트 — 결과 markdown 에서 "N/A — D5 reingest 후 측정" 으로 표기.
    """
    try:
        from app.db import get_supabase_client  # noqa: E402
    except Exception as exc:  # noqa: BLE001
        print(
            f"[WARN] Supabase client import 실패: {exc} — prompt_version hook skip",
            file=sys.stderr,
        )
        return []

    try:
        client = get_supabase_client()
        # 1) vision_page_cache → (sha256, prompt_version)
        vc_resp = (
            client.table("vision_page_cache")
            .select("sha256, prompt_version")
            .limit(10000)
            .execute()
        )
        vc_rows = vc_resp.data or []
        if not vc_rows:
            return []
        sha_set = {r["sha256"] for r in vc_rows if r.get("sha256")}
        if not sha_set:
            return []
        # 2) documents → (sha256, id) 매핑
        doc_resp = (
            client.table("documents")
            .select("id, sha256")
            .in_("sha256", list(sha_set))
            .execute()
        )
        doc_rows = doc_resp.data or []
        sha_to_doc: dict[str, str] = {
            r["sha256"]: r["id"] for r in doc_rows if r.get("sha256") and r.get("id")
        }
        out: list[dict[str, Any]] = []
        for vc in vc_rows:
            did = sha_to_doc.get(vc.get("sha256") or "")
            if did:
                out.append(
                    {
                        "doc_id": did,
                        "prompt_version": vc.get("prompt_version") or "",
                    }
                )
        return out
    except Exception as exc:  # noqa: BLE001
        print(
            f"[WARN] vision_page_cache 조회 실패: {exc} — prompt_version hook skip",
            file=sys.stderr,
        )
        return []


# ---------------------------------------------------------------------------
# Breakdown — 3축 groupby 헬퍼 (단위 테스트 노출)
# ---------------------------------------------------------------------------


def group_by_qtype(cells: list[CellResult]) -> dict[str, list[CellResult]]:
    """cells 를 query_type 별 그룹화. 9종 화이트리스트만 노출, 빈 그룹은 skip.

    의뢰서 §1: qtype 9종 — exact_fact / cross_doc / vision / synonym / fuzzy /
    summary / numeric / table / out_of_scope. CSV 컬럼명은 정식 명 (vision_diagram /
    synonym_mismatch / fuzzy_memory / numeric_lookup / table_lookup) 그대로 사용.
    """
    out: dict[str, list[CellResult]] = {qt: [] for qt in _QTYPE_ORDER}
    for c in cells:
        if c.query_type in out:
            out[c.query_type].append(c)
    return out


def group_by_caption(cells: list[CellResult]) -> dict[str, list[CellResult]]:
    """cells 를 caption_dependent (true/false) 별 그룹화."""
    out: dict[str, list[CellResult]] = {"true": [], "false": []}
    for c in cells:
        out["true" if c.caption_dependent else "false"].append(c)
    return out


def group_by_prompt_version(
    cells: list[CellResult],
) -> dict[str, list[CellResult]]:
    """cells 를 prompt_version (v1/v2/unlabeled) 별 그룹화."""
    out: dict[str, list[CellResult]] = {"v1": [], "v2": [], "unlabeled": []}
    for c in cells:
        key = c.prompt_version if c.prompt_version in {"v1", "v2"} else "unlabeled"
        out[key].append(c)
    return out


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GroupSummary:
    """1 group 의 metric 집계."""

    label: str
    n_total: int
    n_eval: int  # recall_at_10 not None
    avg_recall_at_10: float
    avg_recall_at_5: float
    avg_mrr: float
    fail_count: int  # doc 매칭 fail + ERROR


def _aggregate_group(label: str, cells: list[CellResult]) -> GroupSummary:
    evals = [c for c in cells if c.recall_at_10 is not None]
    n_eval = len(evals)
    avg_r10 = sum(c.recall_at_10 for c in evals) / n_eval if n_eval else 0.0
    avg_r5 = sum(c.recall_at_5 for c in evals) / n_eval if n_eval else 0.0
    avg_mrr = sum(c.mrr for c in evals) / n_eval if n_eval else 0.0
    fails = sum(
        1
        for c in cells
        if c.note == "doc 매칭 fail" or c.note.startswith("ERROR")
    )
    return GroupSummary(
        label=label,
        n_total=len(cells),
        n_eval=n_eval,
        avg_recall_at_10=avg_r10,
        avg_recall_at_5=avg_r5,
        avg_mrr=avg_mrr,
        fail_count=fails,
    )


# ---------------------------------------------------------------------------
# Markdown 출력
# ---------------------------------------------------------------------------


def _format_summary_row(s: GroupSummary) -> str:
    """1 group → markdown table row."""
    return (
        f"| {s.label} | {s.n_total} | {s.n_eval} | "
        f"{s.avg_recall_at_10:.4f} | {s.avg_recall_at_5:.4f} | "
        f"{s.avg_mrr:.4f} | {s.fail_count} |"
    )


def _format_markdown(
    *,
    goldenset_version: str,
    n_total: int,
    cells: list[CellResult],
    qtype_groups: dict[str, list[CellResult]],
    caption_groups: dict[str, list[CellResult]],
    prompt_groups: dict[str, list[CellResult]],
    prompt_label_counts: dict[str, int],
    compose_off: bool = False,
) -> str:
    overall = _aggregate_group("(전체)", cells)
    lines: list[str] = []
    mode_suffix = " — option C (`--no-vision-compose`)" if compose_off else ""
    lines.append(f"# S4-A D4 — golden_v2 R@10 3축 breakdown 측정 결과{mode_suffix}")
    lines.append("")
    lines.append(
        f"- 골든셋: **{goldenset_version}** ({n_total} row) — "
        f"`evals/golden_{goldenset_version}.csv`"
    )
    if compose_off:
        lines.append(
            "- **측정 모드: `--no-vision-compose`** — caption 합성 chunk 의 "
            "`chunk.text` 에서 합성 suffix 를 in-memory 제거 + base_text "
            "재임베딩 + cosine 재정렬. chunks DB 변경 0."
        )
        lines.append(
            "- 본 결과는 **S4-A D2 적용 전 baseline 차분 산출용** — D2 적용 "
            "결과(`s4_a_d4_results.md`) 와 caption_dependent / qtype subset 단위로 "
            "delta 비교."
        )
    else:
        lines.append(
            "- 검색 path 변경 0 — S4-A D2 chunk.text 합성만 적용된 상태에서 측정 "
            "(외부 vision/Gemini 호출 0)"
        )
    lines.append(
        "- mode=hybrid (RRF) · reranker default ENV · 측정 metric = R@10 / R@5 / MRR"
    )
    lines.append("")

    # 전체 요약
    lines.append("## §0 전체 요약")
    lines.append("")
    lines.append("| label | n_total | n_eval | R@10 | R@5 | MRR | fail |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    lines.append(_format_summary_row(overall))
    lines.append("")

    # §1 — qtype 9종
    lines.append("## §1 qtype 별 breakdown (9종)")
    lines.append("")
    lines.append("| qtype | n_total | n_eval | R@10 | R@5 | MRR | fail |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for qt in _QTYPE_ORDER:
        group_cells = qtype_groups.get(qt, [])
        s = _aggregate_group(qt, group_cells)
        lines.append(_format_summary_row(s))
    lines.append("")

    # §2 — caption_dependent
    lines.append("## §2 caption_dependent breakdown (true 18 vs false 139)")
    lines.append("")
    lines.append("| caption_dependent | n_total | n_eval | R@10 | R@5 | MRR | fail |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    cap_summaries: dict[str, GroupSummary] = {}
    for key in ("true", "false"):
        s = _aggregate_group(key, caption_groups.get(key, []))
        cap_summaries[key] = s
        lines.append(_format_summary_row(s))
    lines.append("")
    delta = cap_summaries["true"].avg_recall_at_10 - cap_summaries["false"].avg_recall_at_10
    lines.append(
        f"- **R@10 delta (true - false)**: {delta:+.4f} "
        f"(true {cap_summaries['true'].avg_recall_at_10:.4f} vs "
        f"false {cap_summaries['false'].avg_recall_at_10:.4f})"
    )
    if cap_summaries["true"].n_eval == 0:
        lines.append(
            "- ⚠️ caption_dependent=true subset 의 n_eval=0 — "
            "relevant_chunks 비어있거나 doc 매칭 실패. raw json 검토 필요."
        )
    lines.append("")

    # §3 — prompt_version
    lines.append("## §3 prompt_version breakdown (v1 vs v2 hook)")
    lines.append("")
    v1_n = prompt_label_counts.get("v1", 0)
    v2_n = prompt_label_counts.get("v2", 0)
    unlabeled_n = prompt_label_counts.get("unlabeled", 0)
    lines.append(
        f"- prompt_version 라벨 분포: v1={v1_n} / v2={v2_n} / unlabeled={unlabeled_n}"
    )
    # doc 수 — confound 분석용
    v2_docs = {c.doc_id for c in prompt_groups.get("v2", []) if c.doc_id}
    ul_docs = {c.doc_id for c in prompt_groups.get("unlabeled", []) if c.doc_id}
    if v2_docs or ul_docs:
        lines.append(
            f"- v2 라벨 doc 수: {len(v2_docs)}건 / unlabeled 라벨 doc 수: "
            f"{len(ul_docs)}건"
        )
    lines.append("")
    if v2_n == 0:
        lines.append(
            "- ⚠️ **v2 row 0건** — S4-A D1 vision prompt v2 cache invalidate 후 "
            "reingest 미진행. v2 측정값 `N/A — S4-A D5 reingest 후 측정 예정`."
        )
        lines.append("")
    if v1_n == 0 and v2_n > 0:
        lines.append(
            "- ⚠️ **v1 라벨 0건** — v1 vs v2 직접 비교 불가. v2 row 는 이미 "
            "S4-A D1 ship 후 vision_enrich 진입한 doc 의 자동 캐시 결과. v2 vs "
            "unlabeled 차이는 **doc 종류 confound** (v2=vision 의존 PDF, "
            "unlabeled=HWP/HWPX/PPTX/DOCX + vision 호출 0 doc). 같은 doc 의 "
            "v1 → v2 추이는 D5 reingest 후에야 측정 가능."
        )
        lines.append("")
    lines.append("| prompt_version | n_total | n_eval | R@10 | R@5 | MRR | fail |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for key in ("v1", "v2", "unlabeled"):
        group_cells = prompt_groups.get(key, [])
        if not group_cells:
            lines.append(
                f"| {key} | 0 | 0 | N/A | N/A | N/A | 0 |"
            )
            continue
        s = _aggregate_group(key, group_cells)
        lines.append(_format_summary_row(s))
    lines.append("")

    # §4 — caveat
    lines.append("## §4 caveat / 활성 한계")
    lines.append("")
    if compose_off:
        lines.append(
            "- **option C 활성 (compose_off=True)**: caption 합성 chunk 만 "
            "in-memory base_text 재임베딩 후 cosine 재정렬. 비합성 chunk 는 "
            "기존 `rrf_score` 그대로 사용 — score 스케일 (RRF score vs cosine sim) "
            "**이질 혼합 caveat** 가 존재. 합성 chunk 의 절대 순위 변화는 신뢰 가능하나, "
            "비합성 chunk 와의 교차 순위는 절대값 비교 caveat 필요."
        )
        lines.append(
            "- BGE-M3 (HF Inference API) cold start 변동성 + free tier rate limit — "
            "여러 base_text 재임베딩 시 일부 chunk 가 fallback (rrf_score) 으로 빠질 수 "
            "있음. `[WARN] base_text 재임베딩 실패` 로그 발생 여부를 stderr 에서 확인 권장."
        )
    else:
        lines.append(
            "- 검색 path 변경 0 — 본 측정은 **순수 데이터(골든셋 v2 보강) 효과 확인 단계**."
        )
    lines.append(
        "- prompt_version 라벨은 doc_id 기준 majority. "
        "동일 doc 안에서 page 별 v1/v2 혼재 시 majority 채택 — D5 정밀 측정에서는 "
        "(doc_id, chunk_idx, page) 단위 lookup 으로 정정 예정."
    )
    lines.append(
        "- HF embed_query (BGE-M3) cold start / 5xx retry 영향으로 row 당 latency 변동 큼. "
        "본 도구는 latency 측정 목적 외 — `evals/results/s3_d5_results.md` 참조."
    )
    fail_rows = [
        c for c in cells
        if c.note == "doc 매칭 fail" or c.note.startswith("ERROR")
    ]
    if fail_rows:
        lines.append(
            f"- fail row {len(fail_rows)}건 — `s4_a_d4_raw.json` 의 `note` 컬럼 참조."
        )
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# raw JSON dump
# ---------------------------------------------------------------------------


def _serialize_cells(cells: list[CellResult]) -> list[dict[str, Any]]:
    """CellResult → dict (json 직렬화 가능)."""
    return [
        {
            "golden_id": c.golden_id,
            "query_type": c.query_type,
            "doc_id": c.doc_id,
            "caption_dependent": c.caption_dependent,
            "doc_type": c.doc_type,
            "recall_at_10": c.recall_at_10,
            "recall_at_5": c.recall_at_5,
            "mrr": c.mrr,
            "latency_ms": round(c.latency_ms, 2),
            "predicted_top10": c.predicted_top10,
            "prompt_version": c.prompt_version,
            "note": c.note,
        }
        for c in cells
    ]


def _summary_to_dict(s: GroupSummary) -> dict[str, Any]:
    return {
        "label": s.label,
        "n_total": s.n_total,
        "n_eval": s.n_eval,
        "avg_recall_at_10": s.avg_recall_at_10,
        "avg_recall_at_5": s.avg_recall_at_5,
        "avg_mrr": s.avg_mrr,
        "fail_count": s.fail_count,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="S4-A D4 — golden_v2 R@10 3축 breakdown 측정 도구",
    )
    p.add_argument(
        "--goldenset",
        choices=["v1", "v2"],
        default="v2",
        help="골든셋 버전 (default v2 — S4-A D3 Phase 1 보강 14 컬럼).",
    )
    p.add_argument(
        "--out",
        default=str(_DEFAULT_OUT_MD),
        help=f"markdown 결과 경로 (default {_DEFAULT_OUT_MD.name}).",
    )
    p.add_argument(
        "--out-json",
        default=str(_DEFAULT_OUT_JSON),
        help=f"raw json 경로 (default {_DEFAULT_OUT_JSON.name}).",
    )
    p.add_argument(
        "--limit-rows",
        type=int,
        default=0,
        help="측정 row 수 제한 (디버그용, 0=전체).",
    )
    p.add_argument(
        "--skip-validate",
        action="store_true",
        help="schema 검증 skip — 기존 v1 호환용.",
    )
    p.add_argument(
        "--no-vision-compose",
        action="store_true",
        help=(
            "Phase 1 option C — caption 합성 chunk 만 in-memory 로 합성 suffix "
            "제거 + base_text 재임베딩 + cosine 재정렬 후 R@K 산출. chunks DB "
            "변경 0. S4-A D2 적용 전 baseline 차분 산출용."
        ),
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    golden_path = _GOLDEN_CSV_BY_VERSION[args.goldenset]
    if not golden_path.exists():
        print(f"[ERROR] golden CSV 없음: {golden_path}", file=sys.stderr)
        return 1

    if args.goldenset == "v2" and not args.skip_validate:
        meta = validate_golden_v2_schema(golden_path)
        print(
            f"[OK] schema 검증 — {meta['n_rows']} row, "
            f"qtype={len(meta['qtype_counts'])}, "
            f"doc_type={len(meta['doc_type_counts'])}, "
            f"caption={meta['caption_counts']}",
            file=sys.stderr,
        )

    rows = load_golden_rows(golden_path)
    if args.limit_rows > 0:
        rows = rows[: args.limit_rows]
        print(
            f"[WARN] --limit-rows={args.limit_rows} 적용 — production 측정 아님",
            file=sys.stderr,
        )
    print(f"[INFO] golden {args.goldenset} row {len(rows)}건 로드", file=sys.stderr)

    # Phase 1 option C — BGE-M3 provider lazy 로드 후 embed_query 함수 주입.
    embed_fn: Callable[[str], list[float]] | None = None
    if args.no_vision_compose:
        from app.adapters.impl.bgem3_hf_embedding import (  # noqa: E402
            BGEM3HFEmbeddingProvider,
        )

        embedder = BGEM3HFEmbeddingProvider()
        embed_fn = embedder.embed_query
        print(
            "[INFO] --no-vision-compose 활성 — base_text 재임베딩 + re-rank 모드",
            file=sys.stderr,
        )

    t_start = time.monotonic()
    cells = measure_all(rows, compose_off=args.no_vision_compose, embed_fn=embed_fn)
    elapsed = time.monotonic() - t_start
    print(
        f"[INFO] 측정 완료 — {elapsed:.1f}s ({elapsed / max(1, len(rows)):.2f}s/row 평균)",
        file=sys.stderr,
    )

    prompt_counts = label_prompt_version(cells, rows)
    print(
        f"[INFO] prompt_version 라벨 — v1={prompt_counts['v1']} / "
        f"v2={prompt_counts['v2']} / unlabeled={prompt_counts['unlabeled']}",
        file=sys.stderr,
    )

    qtype_groups = group_by_qtype(cells)
    caption_groups = group_by_caption(cells)
    prompt_groups = group_by_prompt_version(cells)

    md = _format_markdown(
        goldenset_version=args.goldenset,
        n_total=len(rows),
        cells=cells,
        qtype_groups=qtype_groups,
        caption_groups=caption_groups,
        prompt_groups=prompt_groups,
        prompt_label_counts=prompt_counts,
        compose_off=args.no_vision_compose,
    )

    out_md = Path(args.out)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(md, encoding="utf-8")
    print(f"[OK] markdown report: {out_md}", file=sys.stderr)

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    raw: dict[str, Any] = {
        "goldenset": args.goldenset,
        "n_total": len(rows),
        "elapsed_seconds": round(elapsed, 2),
        "compose_off": bool(args.no_vision_compose),
        "overall": _summary_to_dict(_aggregate_group("(전체)", cells)),
        "by_qtype": {
            qt: _summary_to_dict(_aggregate_group(qt, qtype_groups.get(qt, [])))
            for qt in _QTYPE_ORDER
        },
        "by_caption": {
            key: _summary_to_dict(_aggregate_group(key, caption_groups.get(key, [])))
            for key in ("true", "false")
        },
        "by_prompt_version": {
            key: _summary_to_dict(_aggregate_group(key, prompt_groups.get(key, [])))
            for key in ("v1", "v2", "unlabeled")
        },
        "prompt_label_counts": prompt_counts,
        "cells": _serialize_cells(cells),
    }
    out_json.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[OK] raw json: {out_json}", file=sys.stderr)

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
