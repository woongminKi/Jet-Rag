"""Supabase(pgvector) 기반 VectorStore 구현체.

Day 3 범위
- `upsert_chunks`: 청크 레코드 삽입·갱신 (`(doc_id, chunk_idx)` 유니크)
- `delete_document`: ON DELETE CASCADE 로 chunks 까지 정리

Day 4 예정
- `search_dense`, `search_sparse`: pgvector · JSONB 기반 하이브리드 검색 RPC 추가 후 구현
"""

from __future__ import annotations

from app.adapters.vectorstore import ChunkRecord, SearchHit
from app.db import get_supabase_client


class SupabasePgVectorStore:
    """`VectorStore` Protocol 구현체 (뼈대)."""

    _TABLE_CHUNKS = "chunks"
    _TABLE_DOCUMENTS = "documents"

    def __init__(self) -> None:
        self._client = get_supabase_client()

    # ---------------------- write ----------------------

    def upsert_chunks(self, chunks: list[ChunkRecord]) -> None:
        if not chunks:
            return
        payload = [self._serialize_chunk(c) for c in chunks]
        (
            self._client.table(self._TABLE_CHUNKS)
            .upsert(payload, on_conflict="doc_id,chunk_idx")
            .execute()
        )

    def delete_document(self, doc_id: str) -> None:
        # chunks 는 ON DELETE CASCADE 로 자동 삭제.
        (
            self._client.table(self._TABLE_DOCUMENTS)
            .delete()
            .eq("id", doc_id)
            .execute()
        )

    # ---------------------- search (Day 4에서 구현) ----------------------

    def search_dense(
        self,
        query_vec: list[float],
        *,
        top_k: int = 10,
        filters: dict | None = None,
    ) -> list[SearchHit]:
        raise NotImplementedError(
            "search_dense 는 Day 4 에서 pgvector RPC 와 함께 구현 예정."
        )

    def search_sparse(
        self,
        query_sparse: dict[str, float],
        *,
        top_k: int = 10,
        filters: dict | None = None,
    ) -> list[SearchHit]:
        raise NotImplementedError(
            "search_sparse 는 Day 4 에서 JSONB 기반 스코어링 RPC 와 함께 구현 예정."
        )

    # ---------------------- internals ----------------------

    @staticmethod
    def _serialize_chunk(chunk: ChunkRecord) -> dict:
        row: dict = {
            "doc_id": chunk.doc_id,
            "chunk_idx": chunk.chunk_idx,
            "text": chunk.text,
            "page": chunk.page,
            "section_title": chunk.section_title,
            "bbox": list(chunk.bbox) if chunk.bbox else None,
            "dense_vec": chunk.dense_vec,
            "sparse_json": chunk.sparse_json,
            "metadata": chunk.metadata,
        }
        if chunk.char_range is not None:
            start, end = chunk.char_range
            row["char_range"] = f"[{start},{end})"
        if chunk.chunk_id:
            row["id"] = chunk.chunk_id
        return row
