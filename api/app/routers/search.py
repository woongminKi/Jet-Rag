"""W3 Day 2 — `/search` 라우터: 하이브리드 RRF (HNSW dense + PGroonga sparse).

- BGE-M3 `embed_query` → 1024 dim dense vector
- Postgres RPC `search_hybrid_rrf` → top 50 chunks + RRF 점수 + dense/sparse rank
- 메타 필터 4종 (`tags` · `doc_type` · `from_date` · `to_date`) 은 documents 단계에 WHERE
- doc_id 별 그룹화 (max RRF score) + 매칭 청크 최대 3건 (chunk_idx 오름차순 표시)
- `query_parsed` (S2 §7 투명성): has_dense · has_sparse · dense_hits · sparse_hits · fused
- HF API 실패 시 sparse-only fallback (RPC `search_sparse_only_pgroonga`)

명세:
    - work-log/2026-04-28 W3 스프린트 명세.md (v0.4 CONFIRMED, 항목 A)
    - work-log/2026-04-29 W3 스프린트 명세 v0.5.md §3.A (DE-60 PGroonga 교체)
RPC:
    - api/migrations/003_hybrid_search.sql (RRF k=60, dense=sparse=1.0)
    - api/migrations/004_pgroonga_korean_fts.sql (Mecab 형태소 + flags 필터)
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from app.adapters.impl.bgem3_hf_embedding import (
    get_bgem3_provider,
    is_transient_hf_error,
)
from app.config import get_settings
from app.db import get_supabase_client
from app.services import search_metrics

logger = logging.getLogger(__name__)
router = APIRouter(tags=["search"])

_MAX_QUERY_LEN = 200
_MAX_MATCHED_CHUNKS_PER_DOC = 3
_SNIPPET_AROUND = 80
_RRF_K = 60
_RPC_TOP_K = 50  # RPC 의 dense / sparse path 각각 상위 K
# W19 Day 2 한계 #75 — mode=dense/sparse 응용 layer 필터 후 부족 방지 cap.
# hybrid (default) 는 _RPC_TOP_K, mode 필터 시 2배 pre-allocate (latency 영향 미미).
_RPC_TOP_K_ABLATION = 100
# W19 Day 3 한계 #66 — doc_id 응용 layer 필터 시 부족 방지 cap (RPC 인자 추가 회피).
# doc_id 가 지정된 경우 RPC 결과 N건 중 일치만 통과 → 4배 pre-allocate.
_RPC_TOP_K_DOC_FILTER = 200
# 001_init.sql 의 doc_type CHECK 제약과 동일 — 화이트리스트 검증용
_DOC_TYPES = {"pdf", "hwp", "hwpx", "docx", "pptx", "image", "url", "txt", "md"}
# 503 응답의 Retry-After 헤더 — RFC 7231. HF cold start (5~20s) + 안전 마진.
_RETRY_AFTER_SECONDS = "60"


class MatchedChunk(BaseModel):
    chunk_id: str
    chunk_idx: int
    text: str
    page: int | None
    section_title: str | None
    highlight: list[list[int]]
    # W6 Day 5 추가 — 디버깅/투명성 가시성. backward compatible (기존 필드 변경 0).
    rrf_score: float | None = None  # 본 청크의 RRF 점수 (검색 결과 ranking 근거)
    metadata: dict | None = None  # chunk metadata (overlap_with_prev_chunk_idx 등)


class SearchHit(BaseModel):
    doc_id: str
    doc_title: str
    doc_type: str
    tags: list[str]
    summary: str | None
    created_at: str
    relevance: float  # 결과 집합 내 정규화 (top=1.0) — 프론트 % 표시용
    matched_chunk_count: int
    matched_chunks: list[MatchedChunk]


class QueryParsedInfo(BaseModel):
    """W3 S2 §7 투명성 — 검색 경로 진단.

    - has_dense: dense path 가 실행됐는가 (HF API 성공 여부)
    - has_sparse: sparse path 가 1건 이상 매칭됐는가
    - dense_hits: dense path 가 반환한 chunks 수 (sparse-only fallback 시 0)
    - sparse_hits: sparse path 가 반환한 chunks 수
    - fused: RRF 후 unique chunks 수 (= rpc 응답 row 수)
    - fallback_reason: HF API 실패 분류 (W3 Day 2 Phase 3 D-1 통합)
        - None: dense path 정상
        - "transient_5xx": 일시 오류 → sparse-only 로 본 응답 반환됨
        - "permanent_4xx" / "unknown": 본 응답 자체에는 등장 안 함 (503 raise 경로)
    """
    has_dense: bool
    has_sparse: bool
    dense_hits: int
    sparse_hits: int
    fused: int
    fallback_reason: str | None = None


class SearchResponse(BaseModel):
    query: str
    total: int  # 매칭 doc 수 (메타 필터 적용 후)
    limit: int
    offset: int
    items: list[SearchHit]
    took_ms: int
    query_parsed: QueryParsedInfo  # W3 신규 — 기존 필드는 변경 X (backward compatible)


@router.get("/search", response_model=SearchResponse)
def search(
    q: str = Query(
        ...,
        min_length=1,
        max_length=_MAX_QUERY_LEN,
        description="검색어 (자연어 / 키워드 모두 허용, 최대 200자)",
    ),
    limit: int = Query(10, ge=1, le=50),
    offset: int = Query(0, ge=0),
    tags: list[str] | None = Query(
        default=None,
        description="태그 필터 (반복 허용 — `?tags=A&tags=B` 시 A AND B 모두 일치)",
    ),
    doc_type: str | None = Query(
        default=None,
        description="doc_type 필터 (pdf · hwp · hwpx · docx · pptx · image · url · txt · md)",
    ),
    from_date: str | None = Query(
        default=None,
        description="created_at 시작 ISO 8601 (`2026-04-01` 또는 `2026-04-01T00:00:00Z`)",
    ),
    to_date: str | None = Query(
        default=None,
        description="created_at 종료 ISO 8601 (포함)",
    ),
    doc_id: str | None = Query(
        default=None,
        description=(
            "단일 문서 스코프 자연어 QA — 해당 doc 의 chunks 만 검색 (US-08, W11 Day 4). "
            "응용 layer 필터 (RPC 결과 후) — 마이그레이션 회피 trade-off."
        ),
    ),
    mode: str = Query(
        default="hybrid",
        description=(
            "검색 모드 — hybrid (default, dense + sparse RRF) / dense / sparse. "
            "ablation 측정용 (W13 Day 2 — KPI '하이브리드 +5pp 우세' 비교 인프라)."
        ),
    ),
) -> SearchResponse:
    start_t = time.monotonic()
    client = get_supabase_client()
    settings = get_settings()
    user_id = settings.default_user_id

    clean_q = q.strip()
    if not clean_q:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="검색어가 비어있습니다.",
        )
    if doc_type is not None and doc_type not in _DOC_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"doc_type='{doc_type}' 가 유효하지 않습니다.",
        )
    # W11 Day 4 — doc_id 형식 검증 (UUID v4 / 비어있지 않은 문자열).
    # 잘못된 입력 보호 — 응용 layer 필터링이라 SQL injection 위험은 0.
    if doc_id is not None:
        doc_id = doc_id.strip()
        if not doc_id or len(doc_id) > 64:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="doc_id 형식이 유효하지 않습니다.",
            )
    # W13 Day 2 — mode 화이트리스트 (hybrid/dense/sparse) 검증
    if mode not in ("hybrid", "dense", "sparse"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"mode='{mode}' 가 유효하지 않습니다 (hybrid/dense/sparse).",
        )
    from_dt = _parse_iso_date(from_date, "from_date")
    to_dt = _parse_iso_date(to_date, "to_date")

    # ------------------------------------------------------------------
    # 1) dense embedding (HF API).
    #    - transient (5xx / network) → sparse-only fallback (degraded ranking 수용)
    #    - 영구 실패 (401/404/400 등 4xx) → 503 raise. silent degradation 방지.
    #      (한 달 동안 토큰 만료를 모르고 sparse-only 운영하는 위험 차단)
    # ------------------------------------------------------------------
    dense_vec: list[float] | None = None
    fallback_reason: str | None = None
    embed_cache_hit: bool = False
    try:
        provider = get_bgem3_provider()
        dense_vec = provider.embed_query(clean_q)
        # W4-Q-3 — embed_query 직후 LRU hit 여부 스냅샷.
        # race condition 한계 (provider 의 docstring 참조): 멀티 스레드 환경에서
        # 타 호출자가 사이에 끼어들면 hit/miss 가 뒤바뀌어 보일 수 있음. 메트릭 비율 측정 용도라 수용.
        embed_cache_hit = bool(getattr(provider, "_last_cache_hit", False))
    except Exception as exc:  # noqa: BLE001
        if is_transient_hf_error(exc):
            fallback_reason = "transient_5xx"
            logger.warning(
                "embed_query transient 실패 — sparse-only fallback 진입: %s", exc
            )
        else:
            # 영구 실패: 운영자가 알아채야 함. logger.exception 으로 stacktrace 보존.
            # 가시성 위해 metrics 에는 record (fallback_reason="permanent_4xx") 후 503 raise.
            logger.exception(
                "embed_query 영구 실패 — 검색 503 반환 (HF 토큰/엔드포인트 점검 필요)"
            )
            search_metrics.record_search(
                took_ms=int((time.monotonic() - start_t) * 1000),
                dense_hits=0,
                sparse_hits=0,
                fused=0,
                has_dense=False,
                fallback_reason="permanent_4xx",
                embed_cache_hit=False,
                mode=mode,
                query_text=clean_q,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="검색 일시 오류 — 임베딩 서비스에 연결할 수 없습니다. 잠시 후 다시 시도해주세요.",
                headers={"Retry-After": _RETRY_AFTER_SECONDS},
            ) from exc

    # ------------------------------------------------------------------
    # 2) 검색 (dense 성공 시 RPC, 실패 시 sparse-only)
    # ------------------------------------------------------------------
    # W19 Day 2·3 — 응용 layer 필터 시 부족 방지 pre-allocate.
    # 우선순위: doc_id 필터 (#66, 4배) > mode ablation (#75, 2배) > default.
    if doc_id is not None:
        rpc_top_k = _RPC_TOP_K_DOC_FILTER
    elif mode in ("dense", "sparse"):
        rpc_top_k = _RPC_TOP_K_ABLATION
    else:
        rpc_top_k = _RPC_TOP_K
    if dense_vec is not None:
        rpc_resp = client.rpc(
            "search_hybrid_rrf",
            {
                "query_text": clean_q,
                "query_dense": dense_vec,
                "k_rrf": _RRF_K,
                "top_k": rpc_top_k,
                "user_id_arg": str(user_id),
            },
        ).execute()
        rpc_rows = rpc_resp.data or []
    else:
        rpc_rows = _sparse_only_fallback(client, clean_q, user_id, rpc_top_k)

    # W11 Day 4 — 단일 문서 스코프 (US-08): RPC 결과 중 해당 doc_id 만 보존.
    # 응용 layer 필터 — RPC 결과 N 개 중 doc_id 일치만 통과 → 자연스럽게 dense·sparse·fused 카운트도 갱신.
    if doc_id is not None:
        rpc_rows = [r for r in rpc_rows if r.get("doc_id") == doc_id]

    # W13 Day 2 — ablation mode 응용 layer 처리 (KPI '하이브리드 +5pp 우세' 비교 인프라)
    #   · hybrid (default): RPC 결과 그대로 (dense + sparse RRF)
    #   · dense: dense_rank 가 있는 row 만 보존 (sparse-only 매칭 row 제외)
    #   · sparse: sparse_rank 가 있는 row 만 보존 (dense-only 매칭 row 제외)
    # dense_vec 이 None 이면 (sparse-only fallback path) mode 무관하게 sparse-only 동작 — 기존 동작 보존.
    if mode == "dense":
        rpc_rows = [r for r in rpc_rows if r.get("dense_rank") is not None]
    elif mode == "sparse":
        rpc_rows = [r for r in rpc_rows if r.get("sparse_rank") is not None]

    dense_hits = sum(1 for r in rpc_rows if r.get("dense_rank") is not None)
    sparse_hits = sum(1 for r in rpc_rows if r.get("sparse_rank") is not None)
    query_parsed = QueryParsedInfo(
        has_dense=dense_vec is not None,
        has_sparse=sparse_hits > 0,
        dense_hits=dense_hits,
        sparse_hits=sparse_hits,
        fused=len(rpc_rows),
        fallback_reason=fallback_reason,
    )

    if not rpc_rows:
        took_ms = int((time.monotonic() - start_t) * 1000)
        search_metrics.record_search(
            took_ms=took_ms,
            dense_hits=dense_hits,
            sparse_hits=sparse_hits,
            fused=0,
            has_dense=dense_vec is not None,
            fallback_reason=fallback_reason,
            embed_cache_hit=embed_cache_hit,
                mode=mode,
                query_text=clean_q,
        )
        return SearchResponse(
            query=clean_q,
            total=0,
            limit=limit,
            offset=offset,
            items=[],
            took_ms=took_ms,
            query_parsed=query_parsed,
        )

    # ------------------------------------------------------------------
    # 3) doc_id 별 RRF 그룹 (max score) + chunk_id ↔ doc_id 매핑
    # ------------------------------------------------------------------
    doc_score: dict[str, float] = {}
    doc_chunk_scores: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for r in rpc_rows:
        doc_id = r["doc_id"]
        chunk_id = r["chunk_id"]
        score = float(r["rrf_score"])
        doc_score[doc_id] = max(doc_score.get(doc_id, 0.0), score)
        doc_chunk_scores[doc_id].append((chunk_id, score))

    candidate_doc_ids = list(doc_score.keys())

    # ------------------------------------------------------------------
    # 4) documents 메타 fetch + 메타 필터 4종 적용
    # ------------------------------------------------------------------
    docs_query = (
        client.table("documents")
        .select("id, title, doc_type, tags, summary, created_at")
        .in_("id", candidate_doc_ids)
        .eq("user_id", user_id)
        .is_("deleted_at", "null")
    )
    if doc_type:
        docs_query = docs_query.eq("doc_type", doc_type)
    if tags:
        # GIN tags @> ARRAY[...] — 모든 요청 태그 포함 (AND)
        docs_query = docs_query.contains("tags", tags)
    if from_dt:
        docs_query = docs_query.gte("created_at", from_dt.isoformat())
    if to_dt:
        docs_query = docs_query.lte("created_at", to_dt.isoformat())
    docs_meta: dict[str, dict] = {
        d["id"]: d for d in (docs_query.execute().data or [])
    }

    # ------------------------------------------------------------------
    # 5) RRF 점수 내림차순 정렬 + 페이지네이션
    # ------------------------------------------------------------------
    sorted_doc_ids = sorted(
        docs_meta.keys(), key=lambda did: doc_score[did], reverse=True
    )
    total_docs = len(sorted_doc_ids)
    page_doc_ids = sorted_doc_ids[offset : offset + limit]

    if not page_doc_ids:
        took_ms = int((time.monotonic() - start_t) * 1000)
        search_metrics.record_search(
            took_ms=took_ms,
            dense_hits=dense_hits,
            sparse_hits=sparse_hits,
            fused=len(rpc_rows),
            has_dense=dense_vec is not None,
            fallback_reason=fallback_reason,
            embed_cache_hit=embed_cache_hit,
                mode=mode,
                query_text=clean_q,
        )
        return SearchResponse(
            query=clean_q,
            total=total_docs,
            limit=limit,
            offset=offset,
            items=[],
            took_ms=took_ms,
            query_parsed=query_parsed,
        )

    # ------------------------------------------------------------------
    # 6) 페이지의 매칭 청크 본문 fetch (각 doc 의 RRF top 3)
    # ------------------------------------------------------------------
    selected_chunk_ids: list[str] = []
    for doc_id in page_doc_ids:
        top3 = sorted(
            doc_chunk_scores[doc_id], key=lambda x: x[1], reverse=True
        )[:_MAX_MATCHED_CHUNKS_PER_DOC]
        selected_chunk_ids.extend(cid for cid, _ in top3)

    chunks_resp = (
        client.table("chunks")
        .select("id, doc_id, chunk_idx, page, section_title, text, metadata")
        .in_("id", selected_chunk_ids)
        .execute()
    )
    chunks_by_id: dict[str, dict] = {
        c["id"]: c for c in (chunks_resp.data or [])
    }
    # chunk_id → rrf_score 매핑 (페이지 내 응답에서만 사용)
    chunk_rrf: dict[str, float] = {}
    for doc_id in page_doc_ids:
        for cid, score in doc_chunk_scores[doc_id]:
            # 같은 chunk_id 가 dense/sparse path 양쪽에 등장 시 max
            chunk_rrf[cid] = max(chunk_rrf.get(cid, 0.0), score)

    # ------------------------------------------------------------------
    # 7) 응답 조립 (relevance 는 결과 집합 내 정규화 — top=1.0)
    # ------------------------------------------------------------------
    top_score = doc_score[sorted_doc_ids[0]] if sorted_doc_ids else 1.0
    normalize = top_score if top_score > 0 else 1.0

    items: list[SearchHit] = []
    for doc_id in page_doc_ids:
        meta = docs_meta[doc_id]
        all_matches = doc_chunk_scores[doc_id]
        matched_count = len(all_matches)
        top3_ids = [
            cid
            for cid, _ in sorted(
                all_matches, key=lambda x: x[1], reverse=True
            )[:_MAX_MATCHED_CHUNKS_PER_DOC]
        ]
        # chunk_idx 오름차순 (UX 일관) — 본문 등장 순서대로 노출
        top_chunks = sorted(
            (chunks_by_id[cid] for cid in top3_ids if cid in chunks_by_id),
            key=lambda c: c["chunk_idx"],
        )

        matched_chunks = []
        for c in top_chunks:
            snippet, highlights = _make_snippet_with_highlights(
                c.get("text") or "", clean_q
            )
            chunk_meta = c.get("metadata") or None
            matched_chunks.append(
                MatchedChunk(
                    chunk_id=c["id"],
                    chunk_idx=c["chunk_idx"],
                    text=snippet,
                    page=c.get("page"),
                    section_title=c.get("section_title"),
                    highlight=highlights,
                    rrf_score=chunk_rrf.get(c["id"]),
                    metadata=chunk_meta if chunk_meta else None,
                )
            )

        items.append(
            SearchHit(
                doc_id=doc_id,
                doc_title=meta.get("title") or "",
                doc_type=meta.get("doc_type") or "",
                tags=meta.get("tags") or [],
                summary=meta.get("summary"),
                created_at=meta.get("created_at") or "",
                relevance=round(min(1.0, doc_score[doc_id] / normalize), 4),
                matched_chunk_count=matched_count,
                matched_chunks=matched_chunks,
            )
        )

    took_ms = int((time.monotonic() - start_t) * 1000)
    search_metrics.record_search(
        took_ms=took_ms,
        dense_hits=dense_hits,
        sparse_hits=sparse_hits,
        fused=len(rpc_rows),
        has_dense=dense_vec is not None,
        fallback_reason=fallback_reason,
        embed_cache_hit=embed_cache_hit,
                mode=mode,
                query_text=clean_q,
    )
    return SearchResponse(
        query=clean_q,
        total=total_docs,
        limit=limit,
        offset=offset,
        items=items,
        took_ms=took_ms,
        query_parsed=query_parsed,
    )


# ---------------------- helpers ----------------------


def _sparse_only_fallback(
    client, q: str, user_id: str, top_k: int
) -> list[dict]:
    """HF API 실패 시 — PGroonga 한국어 형태소 매칭만으로 검색.

    W3 v0.5 §3.A (DE-60) 적용 — `search_sparse_only_pgroonga` RPC 호출.
    PostgREST 가 PGroonga `&@~` 연산자를 직접 노출하지 않으므로 RPC 캡슐화.

    이전 (003 simple FTS) 의 한계 (ts_rank 정렬 미노출, E-6) 를 본 RPC 가 해결:
        - 정렬 보장 (`ORDER BY pgroonga_score(...) DESC`)
        - deleted_at IS NULL 필터를 RPC 내부에서 적용 (E-4 일관)
        - flags.filtered_reason 자동 제외 (DE-62)
    """
    rpc_resp = client.rpc(
        "search_sparse_only_pgroonga",
        {
            "query_text": q,
            "user_id_arg": str(user_id),
            "top_k": top_k,
        },
    ).execute()
    rows = rpc_resp.data or []
    out: list[dict] = []
    for r in rows:
        rank = int(r["sparse_rank"])
        out.append(
            {
                "chunk_id": r["chunk_id"],
                "doc_id": r["doc_id"],
                "rrf_score": 1.0 / (_RRF_K + rank),
                "dense_rank": None,
                "sparse_rank": rank,
            }
        )
    return out


def _parse_iso_date(value: str | None, field: str) -> datetime | None:
    """`YYYY-MM-DD` 또는 ISO 8601 datetime 을 tz-aware datetime 으로 파싱.

    날짜만 입력하면 UTC 0시로 간주.
    """
    if not value:
        return None
    try:
        if len(value) == 10:  # YYYY-MM-DD
            return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
        normalized = (
            value.replace("Z", "+00:00") if value.endswith("Z") else value
        )
        dt = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field}='{value}' 가 ISO 8601 형식이 아닙니다.",
        ) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _make_snippet_with_highlights(
    text: str, query: str, around: int = _SNIPPET_AROUND
) -> tuple[str, list[list[int]]]:
    """매칭 위치 ±around 로 자른 스니펫 + 그 스니펫 내 매칭 구간 [start, end] 리스트.

    리터럴 부분 문자열 매칭만 — 하이브리드 RRF 결과의 chunks 가 항상 q 를
    리터럴로 포함하지는 않으므로, 매칭 0건이면 본문 앞부분만 반환 (highlight=[]).
    """
    if not text or not query:
        return text[: around * 2], []

    text_lower = text.lower()
    q_lower = query.lower()
    q_len = len(query)

    first_idx = text_lower.find(q_lower)
    if first_idx == -1:
        return text[: around * 2], []

    start = max(0, first_idx - around)
    end = min(len(text), first_idx + q_len + around)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    snippet = f"{prefix}{text[start:end]}{suffix}"

    snippet_lower = snippet.lower()
    highlights: list[list[int]] = []
    pos = 0
    while True:
        hit = snippet_lower.find(q_lower, pos)
        if hit == -1:
            break
        highlights.append([hit, hit + q_len])
        pos = hit + q_len
    return snippet, highlights
