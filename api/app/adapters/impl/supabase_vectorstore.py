"""Supabase(pgvector) 기반 VectorStore 구현체.

Day 3 범위
- `upsert_chunks`: 청크 레코드 삽입·갱신 (`(doc_id, chunk_idx)` 유니크)
- `delete_document`: ON DELETE CASCADE 로 chunks 까지 정리

Day 4 예정
- `search_dense`, `search_sparse`: pgvector · JSONB 기반 하이브리드 검색 RPC 추가 후 구현

2026-05-14 robustness fix (generic 인제스트 보호)
- `_strip_null_bytes`: Postgres TEXT/JSONB 가 거부하는 `\\x00` NULL byte 재귀 제거.
  arXiv 같은 LaTeX 기반 PDF 추출 시 chunk text 에 NULL byte 가 흘러들어와 SQL 22P05
  ("unsupported Unicode escape sequence") 로 적재 실패하던 case fix.
- `upsert_chunks` batch split: chunks 를 `settings.chunk_upsert_batch_size` (default 50)
  로 분할 upsert. SK 사업보고서 (~300+ chunks) 일괄 upsert 가 Supabase statement_timeout
  으로 SQL 57014 실패하던 case fix.
"""

from __future__ import annotations

from typing import Any

from app.adapters.vectorstore import ChunkRecord, SearchHit
from app.config import get_settings
from app.db import get_supabase_client


class SupabasePgVectorStore:
    """`VectorStore` Protocol 구현체 (뼈대)."""

    _TABLE_CHUNKS = "chunks"
    _TABLE_DOCUMENTS = "documents"

    def __init__(self) -> None:
        self._client = get_supabase_client()

    # ---------------------- write ----------------------

    def upsert_chunks(self, chunks: list[ChunkRecord]) -> None:
        """chunks 를 batch 분할 upsert. batch size = `settings.chunk_upsert_batch_size`.

        Supabase statement_timeout (default ~30~60s) 안에 들어가도록 작은 batch 로 자름.
        한 batch 실패 시 RuntimeError raise (caller 가 stage retry / partial 결정).
        """
        if not chunks:
            return
        batch_size = max(1, get_settings().chunk_upsert_batch_size)
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            payload = [self._serialize_chunk(c) for c in batch]
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
    def _strip_null_bytes(value: Any) -> Any:
        """Postgres TEXT/JSONB 가 거부하는 `\\x00` NULL byte 재귀 제거.

        - str: `replace("\\x00", "")`
        - dict / list / tuple: 재귀
        - 그 외 (None / int / float / bool / bytes): 그대로 (NULL byte 는 str 영역만 문제)
        bytes 는 PostgREST 가 base64 인코딩이라 안전. dense_vec list[float] 도 무관.
        """
        if isinstance(value, str):
            return value.replace("\x00", "") if "\x00" in value else value
        if isinstance(value, dict):
            return {
                k: SupabasePgVectorStore._strip_null_bytes(v) for k, v in value.items()
            }
        if isinstance(value, list):
            return [SupabasePgVectorStore._strip_null_bytes(v) for v in value]
        if isinstance(value, tuple):
            return tuple(SupabasePgVectorStore._strip_null_bytes(v) for v in value)
        return value

    @staticmethod
    def _serialize_chunk(chunk: ChunkRecord) -> dict:
        row: dict = {
            "doc_id": chunk.doc_id,
            "chunk_idx": chunk.chunk_idx,
            "text": chunk.text,
            "page": chunk.page,
            "section_title": chunk.section_title,
            "sparse_json": chunk.sparse_json or {},
            "metadata": chunk.metadata or {},
            # 마이그레이션 004 의 chunks.flags JSONB (NOT NULL DEFAULT '{}') 미러링.
            # 빈 dict 도 명시 직렬화 — 직전 레코드 flags 가 잔존하지 않도록.
            "flags": chunk.flags or {},
        }
        if chunk.bbox is not None:
            row["bbox"] = list(chunk.bbox)
        if chunk.dense_vec is not None:
            row["dense_vec"] = chunk.dense_vec
        if chunk.char_range is not None:
            start, end = chunk.char_range
            row["char_range"] = f"[{start},{end})"
        if chunk.chunk_id:
            row["id"] = chunk.chunk_id
        # 2026-05-14 — NULL byte sanitize (arXiv 같은 LaTeX PDF 추출 보호).
        return SupabasePgVectorStore._strip_null_bytes(row)
