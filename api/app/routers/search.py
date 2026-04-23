"""간이 키워드 검색 — W1 DoD "PDF 업로드 → 키워드 검색 가능" 최소 충족.

Postgres `ilike` 로 `chunks.text` 를 패턴 매칭. 한국어 특유의 조사·띄어쓰기 처리는 무시하고
리터럴 부분 문자열 일치만 찾는다. 하이브리드 (dense · sparse · RRF) 검색은 W3 에 별도 구현.

반환 포맷: 기획서 §7 S2 검색 화면 UX 전제로 doc 메타 + 청크 스니펫 포함.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from app.config import get_settings
from app.db import get_supabase_client

router = APIRouter(tags=["search"])

_SNIPPET_AROUND = 80
_MAX_QUERY_LEN = 200


class SearchHit(BaseModel):
    chunk_id: str
    doc_id: str
    doc_title: str
    doc_type: str
    chunk_idx: int
    page: int | None
    section_title: str | None
    snippet: str


class SearchResponse(BaseModel):
    query: str
    total: int
    limit: int
    offset: int
    items: list[SearchHit]


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
    client = get_supabase_client()
    settings = get_settings()

    clean = q.strip()
    if not clean:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="검색어가 비어있습니다.",
        )
    pattern = f"%{_escape_like(clean)}%"

    count_resp = (
        client.table("chunks")
        .select("id, documents!inner(user_id, deleted_at)", count="exact")
        .ilike("text", pattern)
        .eq("documents.user_id", settings.default_user_id)
        .is_("documents.deleted_at", "null")
        .execute()
    )
    total = count_resp.count or 0

    resp = (
        client.table("chunks")
        .select(
            "id, doc_id, chunk_idx, page, section_title, text, "
            "documents!inner(title, doc_type, user_id, deleted_at)"
        )
        .ilike("text", pattern)
        .eq("documents.user_id", settings.default_user_id)
        .is_("documents.deleted_at", "null")
        .order("chunk_idx")
        .range(offset, offset + limit - 1)
        .execute()
    )

    items: list[SearchHit] = []
    for row in resp.data or []:
        doc = row.get("documents") or {}
        items.append(
            SearchHit(
                chunk_id=row["id"],
                doc_id=row["doc_id"],
                doc_title=doc.get("title") or "",
                doc_type=doc.get("doc_type") or "",
                chunk_idx=row["chunk_idx"],
                page=row.get("page"),
                section_title=row.get("section_title"),
                snippet=_make_snippet(row.get("text") or "", clean),
            )
        )

    return SearchResponse(
        query=clean, total=total, limit=limit, offset=offset, items=items
    )


# ---------------------- helpers ----------------------


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _make_snippet(text: str, query: str) -> str:
    idx = text.lower().find(query.lower())
    if idx == -1:
        return text[: _SNIPPET_AROUND * 2]
    start = max(0, idx - _SNIPPET_AROUND)
    end = min(len(text), idx + len(query) + _SNIPPET_AROUND)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end]}{suffix}"
