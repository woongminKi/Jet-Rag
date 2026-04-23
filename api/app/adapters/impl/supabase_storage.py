"""Supabase Storage 기반 BlobStorage 구현체.

저장 경로 규칙: `<sha256>.<ext>` — 파일 해시가 곧 식별자.
동일 해시 파일을 여러 유저가 업로드해도 Storage 객체는 1개로 공유되고, 유저별 귀속은 `documents.user_id` 에서 처리한다.
"""

from __future__ import annotations

import hashlib
import mimetypes
from pathlib import PurePosixPath

from app.adapters.storage import StoredBlob
from app.db import get_supabase_client


class SupabaseBlobStorage:
    """`BlobStorage` Protocol 구현체."""

    def __init__(self, bucket: str) -> None:
        self._bucket = bucket
        self._client = get_supabase_client()

    # ---------------------- public API ----------------------

    def put(
        self,
        data: bytes,
        *,
        file_name: str,
        content_type: str,
    ) -> StoredBlob:
        sha256 = hashlib.sha256(data).hexdigest()
        path = self._build_path(sha256=sha256, file_name=file_name)

        self._client.storage.from_(self._bucket).upload(
            path=path,
            file=data,
            file_options={
                "content-type": content_type,
                # 동일 해시 객체가 이미 있으면 덮어쓰지 않고 성공으로 간주 (멱등)
                "upsert": "true",
            },
        )

        return StoredBlob(
            blob_id=path,
            path=path,
            content_type=content_type,
            size_bytes=len(data),
            sha256=sha256,
        )

    def get(self, blob_id: str) -> bytes:
        return self._client.storage.from_(self._bucket).download(blob_id)

    def delete(self, blob_id: str) -> None:
        self._client.storage.from_(self._bucket).remove([blob_id])

    def signed_url(self, blob_id: str, *, ttl_seconds: int = 3600) -> str:
        result = self._client.storage.from_(self._bucket).create_signed_url(
            path=blob_id,
            expires_in=ttl_seconds,
        )
        # supabase-py 는 {'signedURL': ...} 또는 {'signedUrl': ...} 로 반환 — 둘 다 대응
        url = result.get("signedURL") or result.get("signedUrl")
        if not url:
            raise RuntimeError(f"signed url 생성 실패: {result!r}")
        return url

    # ---------------------- internals ----------------------

    @staticmethod
    def _build_path(*, sha256: str, file_name: str) -> str:
        ext = PurePosixPath(file_name).suffix.lower()
        if not ext:
            guessed = mimetypes.guess_extension("application/octet-stream") or ""
            ext = guessed
        return f"{sha256}{ext}"
