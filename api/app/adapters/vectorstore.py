from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class ChunkRecord:
    doc_id: str
    chunk_idx: int
    text: str
    dense_vec: list[float]
    sparse_json: dict[str, float]
    page: int | None = None
    section_title: str | None = None
    char_range: tuple[int, int] | None = None
    metadata: dict = field(default_factory=dict)
    chunk_id: str | None = None  # None이면 저장 시 서버에서 생성


@dataclass(frozen=True)
class SearchHit:
    chunk_id: str
    doc_id: str
    text: str
    score: float
    metadata: dict


class VectorStore(Protocol):
    """벡터 + 메타 저장소. pgvector on Supabase(기본) · LanceDB(v2) · Chroma."""

    def upsert_chunks(self, chunks: list[ChunkRecord]) -> None: ...

    def search_dense(
        self,
        query_vec: list[float],
        *,
        top_k: int = 10,
        filters: dict | None = None,
    ) -> list[SearchHit]: ...

    def search_sparse(
        self,
        query_sparse: dict[str, float],
        *,
        top_k: int = 10,
        filters: dict | None = None,
    ) -> list[SearchHit]: ...

    def delete_document(self, doc_id: str) -> None: ...
