"""간이 키워드 검색 — W1 DoD "PDF 업로드 → 키워드 검색 가능" 최소 충족.

Postgres `ilike` 로 `chunks.text` 를 패턴 매칭. 한국어 특유의 조사·띄어쓰기 처리는 무시하고
리터럴 부분 문자열 일치만 찾는다. 하이브리드 (dense · sparse · RRF) 검색은 W3 에 별도 구현.

응답 구조: 기획서 §7 S2 검색 화면 UX 에 맞춰 **doc 단위 그룹화** + 매칭 청크 미리보기 최대 3건.
청크 평탄 응답에서 doc 카드 그룹 응답으로 이전 — 같은 문서가 여러 번 노출되는 문제 해결.
"""

from __future__ import annotations

import time
from collections import defaultdict

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from app.config import get_settings
from app.db import get_supabase_client

router = APIRouter(tags=["search"])

_MAX_QUERY_LEN = 200
_MAX_MATCHED_CHUNKS_PER_DOC = 3
_SNIPPET_AROUND = 80


class MatchedChunk(BaseModel):
    chunk_id: str
    chunk_idx: int
    text: str
    page: int | None
    section_title: str | None
    highlight: list[list[int]]  # [[start, end], ...] — 다음 커밋에서 채움


class SearchHit(BaseModel):
    doc_id: str
    doc_title: str
    doc_type: str
    tags: list[str]
    summary: str | None
    created_at: str
    relevance: float
    matched_chunk_count: int
    matched_chunks: list[MatchedChunk]  # 최대 3건 (chunk_idx 오름차순)


class SearchResponse(BaseModel):
    query: str
    total: int  # 매칭 doc 수 (청크 수 아님)
    limit: int
    offset: int
    items: list[SearchHit]
    took_ms: int


@router.get("/search", response_model=SearchResponse)
def search(
    q: str = Query(
        ...,
        min_length=1,
        max_length=_MAX_QUERY_LEN,
        description="검색어 (리터럴 부분 문자열 일치, ilike)",
    ),
    limit: int = Query(10, ge=1, le=50),
    offset: int = Query(0, ge=0),
) -> SearchResponse:
    start_t = time.monotonic()
    client = get_supabase_client()
    settings = get_settings()

    clean = q.strip()
    if not clean:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="검색어가 비어있습니다.",
        )
    pattern = f"%{_escape_like(clean)}%"

    # 1) 매칭된 청크 전체 fetch (documents inner join 으로 user/deleted 필터)
    matched_resp = (
        client.table("chunks")
        .select(
            "id, doc_id, chunk_idx, page, section_title, text, "
            "documents!inner(user_id, deleted_at)"
        )
        .ilike("text", pattern)
        .eq("documents.user_id", settings.default_user_id)
        .is_("documents.deleted_at", "null")
        .order("chunk_idx")
        .execute()
    )
    matched_rows = matched_resp.data or []

    # 2) doc_id 별 group + 매칭 순서 보존 (첫 매칭 청크의 등장 순서)
    grouped: dict[str, list[dict]] = defaultdict(list)
    doc_id_order: list[str] = []
    for row in matched_rows:
        doc_id = row["doc_id"]
        if doc_id not in grouped:
            doc_id_order.append(doc_id)
        grouped[doc_id].append(row)

    total_docs = len(doc_id_order)

    # 3) doc_id 페이지네이션
    page_doc_ids = doc_id_order[offset : offset + limit]

    if not page_doc_ids:
        return SearchResponse(
            query=clean,
            total=total_docs,
            limit=limit,
            offset=offset,
            items=[],
            took_ms=int((time.monotonic() - start_t) * 1000),
        )

    # 4) 페이지에 해당하는 doc 들의 메타 fetch
    docs_resp = (
        client.table("documents")
        .select("id, title, doc_type, tags, summary, created_at")
        .in_("id", page_doc_ids)
        .execute()
    )
    docs_meta: dict[str, dict] = {d["id"]: d for d in (docs_resp.data or [])}

    # 4-b) 각 doc 의 전체 청크 수 (relevance 정규화용)
    chunks_count_map: dict[str, int] = {}
    for doc_id in page_doc_ids:
        cnt_resp = (
            client.table("chunks")
            .select("id", count="exact")
            .eq("doc_id", doc_id)
            .execute()
        )
        chunks_count_map[doc_id] = cnt_resp.count or 0

    # 5) 응답 조립
    items: list[SearchHit] = []
    q_lower = clean.lower()
    for doc_id in page_doc_ids:
        meta = docs_meta.get(doc_id) or {}
        matched = grouped[doc_id]
        matched_count = len(matched)
        total_chunks = chunks_count_map.get(doc_id, 0) or 1  # 0 division 방지

        title = meta.get("title") or ""
        tags = meta.get("tags") or []
        title_hit = q_lower in title.lower()
        tag_hit = any(q_lower in (t or "").lower() for t in tags)

        relevance = min(
            1.0,
            0.5 * (matched_count / total_chunks)
            + 0.3 * (1 if title_hit else 0)
            + 0.2 * (1 if tag_hit else 0),
        )

        # chunk_idx 오름차순 상위 3건 (이미 ORDER BY chunk_idx 로 fetch 했으므로 그대로 슬라이스)
        top_chunks = matched[:_MAX_MATCHED_CHUNKS_PER_DOC]
        matched_chunks = []
        for c in top_chunks:
            snippet, highlights = _make_snippet_with_highlights(
                c.get("text") or "", clean
            )
            matched_chunks.append(
                MatchedChunk(
                    chunk_id=c["id"],
                    chunk_idx=c["chunk_idx"],
                    text=snippet,
                    page=c.get("page"),
                    section_title=c.get("section_title"),
                    highlight=highlights,
                )
            )

        items.append(
            SearchHit(
                doc_id=doc_id,
                doc_title=title,
                doc_type=meta.get("doc_type") or "",
                tags=tags,
                summary=meta.get("summary"),
                created_at=meta.get("created_at") or "",
                relevance=round(relevance, 4),
                matched_chunk_count=matched_count,
                matched_chunks=matched_chunks,
            )
        )

    return SearchResponse(
        query=clean,
        total=total_docs,
        limit=limit,
        offset=offset,
        items=items,
        took_ms=int((time.monotonic() - start_t) * 1000),
    )


# ---------------------- helpers ----------------------


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _make_snippet_with_highlights(
    text: str, query: str, around: int = _SNIPPET_AROUND
) -> tuple[str, list[list[int]]]:
    """매칭 위치 ±around 로 자른 스니펫 + 그 스니펫 내 매칭 구간 [start, end] 리스트 반환.

    프론트엔드는 반환된 highlight 인덱스로 <mark> 등을 직접 감싼다.
    인덱스는 모두 잘린 스니펫(반환되는 첫 번째 값) 기준이다.
    """
    if not text or not query:
        return text[: around * 2], []

    text_lower = text.lower()
    q_lower = query.lower()
    q_len = len(query)

    first_idx = text_lower.find(q_lower)
    if first_idx == -1:
        # 매칭 없음 — 본문 앞부분만 잘라 반환 (highlight 없음)
        return text[: around * 2], []

    start = max(0, first_idx - around)
    end = min(len(text), first_idx + q_len + around)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    snippet = f"{prefix}{text[start:end]}{suffix}"

    # 잘린 스니펫 내부 인덱스로 다시 매칭 위치 수집
    snippet_lower = snippet.lower()
    highlights: list[list[int]] = []
    pos = 0
    while True:
        hit = snippet_lower.find(q_lower, pos)
        if hit == -1:
            break
        highlights.append([hit, hit + q_len])
        pos = hit + q_len  # 비중첩 진행 (검색어가 자기 자신과 겹치는 케이스는 무시)
    return snippet, highlights
