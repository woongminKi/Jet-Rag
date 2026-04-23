from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class ChunkRecord:
    doc_id: str
    chunk_idx: int
    text: str
    dense_vec: list[float] | None = None        # 임베딩 전(Day 4) 에는 None, Day 5 에 채움
    sparse_json: dict[str, float] = field(default_factory=dict)
    page: int | None = None
    section_title: str | None = None
    bbox: tuple[float, float, float, float] | None = None   # x0, y0, x1, y1 (PDF 좌표)
    char_range: tuple[int, int] | None = None
    metadata: dict = field(default_factory=dict)
    chunk_id: str | None = None  # None 이면 저장 시 서버에서 생성


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
