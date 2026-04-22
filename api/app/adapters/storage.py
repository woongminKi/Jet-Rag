from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class StoredBlob:
    blob_id: str
    path: str
    content_type: str
    size_bytes: int
    sha256: str


class BlobStorage(Protocol):
    """원본 파일 저장소. Supabase Storage(기본) · LocalFS(v2)."""

    def put(
        self,
        data: bytes,
        *,
        file_name: str,
        content_type: str,
    ) -> StoredBlob: ...

    def get(self, blob_id: str) -> bytes: ...

    def delete(self, blob_id: str) -> None: ...

    def signed_url(self, blob_id: str, *, ttl_seconds: int = 3600) -> str: ...
