"""Supabase Storage 기반 BlobStorage 구현체.

저장 경로 규칙 (D2 — 2026-05-20, plan §3.1):
    user/<user_id>/<sha256>{ext}             — final path (인제스트 완료본)
    user/<user_id>/pending/<doc_uuid>{ext}   — pending path (수신 직후, BG 가 final 로 이관)

D2 이전 (legacy):
    <sha256>{ext}                            — user 격리 없음 (단일 사용자 MVP)
    pending/default/<doc_uuid>{ext}          — pending 도 user 미구분

D2 마이그(020) 가 `documents.storage_path` 를 일괄 `user/<uid>/` prefix 로 갱신한 뒤,
Storage RLS 정책 4개(SELECT/INSERT/UPDATE/DELETE on storage.objects) 가 prefix 기반
per-user 격리를 강제한다. 백엔드는 service_role 라 RLS bypass — 인증 후의 호출자
user_id 를 직접 전달해 path 를 결정한다.

helper API (D2 신규):
    SupabaseBlobStorage.build_user_path(user_id, sha256, file_name) -> str
    SupabaseBlobStorage.build_pending_path(user_id, doc_uuid, ext)  -> str

Protocol 시그니처는 무변경 — `put()` 자동 path 생성은 D2 이전 legacy fallback 로 유지하되
호출처는 D2 부터 `put_at(path=build_user_path(...))` 명시 사용. 본 모듈의 `_legacy_path`
private helper 가 기존 sha256-only 경로를 캡슐화한다.
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
        """legacy 자동 path 생성. D2 이후 신규 호출처는 `put_at()` + `build_user_path()` 사용.

        실제 호출처 0건이라 보존만 — Protocol 시그니처 호환을 위해 유지.
        """
        sha256 = hashlib.sha256(data).hexdigest()
        path = self._legacy_path(sha256=sha256, file_name=file_name)
        return self._upload(
            path=path, data=data, content_type=content_type, sha256=sha256
        )

    def put_at(
        self,
        *,
        path: str,
        data: bytes,
        content_type: str,
        sha256: str | None = None,
    ) -> StoredBlob:
        """호출자가 결정한 path 로 업로드. SHA-256 은 외부에서 이미 계산했으면 전달.

        D2 (plan §3) — `path` 는 통상 `build_user_path()` 결과. 응답 단계에서 결정한
        pending path 도 동일하게 받아 멱등 upsert.
        """
        if sha256 is None:
            sha256 = hashlib.sha256(data).hexdigest()
        return self._upload(
            path=path, data=data, content_type=content_type, sha256=sha256
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

    # ---------------------- path builders (D2) ----------------------

    @staticmethod
    def build_user_path(*, user_id: str, sha256: str, file_name: str) -> str:
        """D2 final path: `user/<user_id>/<sha256>{ext}`.

        plan §3.1 — Storage RLS 정책이 (storage.foldername(name))[1]='user' AND
        [2]=auth.uid() 패턴으로 per-user 격리. ext 는 file_name 에서 추출
        (소문자, 빈 ext 면 mimetypes guess fallback — legacy 와 동일).
        """
        ext = _ext_for(file_name)
        return f"user/{user_id}/{sha256}{ext}"

    @staticmethod
    def build_pending_path(*, user_id: str, doc_uuid: str, ext: str) -> str:
        """D2 pending path: `user/<user_id>/pending/<doc_uuid>{ext}`.

        documents row 가 INSERT 되는 수신 단계의 placeholder. BG run_full_ingest 가
        final path 로 이관 후 documents.storage_path UPDATE. ext 는 호출자가 이미
        검증/추출했으므로 그대로 사용 (소문자, '.' 포함 또는 빈 문자열).
        """
        return f"user/{user_id}/pending/{doc_uuid}{ext}"

    # ---------------------- internals ----------------------

    def _upload(
        self,
        *,
        path: str,
        data: bytes,
        content_type: str,
        sha256: str,
    ) -> StoredBlob:
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

    @staticmethod
    def _legacy_path(*, sha256: str, file_name: str) -> str:
        """D2 이전 sha256-only path. `put()` 의 자동 path 생성에만 사용."""
        ext = _ext_for(file_name)
        return f"{sha256}{ext}"


def _ext_for(file_name: str) -> str:
    """확장자 추출 — 소문자 + '.' 포함. 비어있으면 mimetypes guess fallback."""
    ext = PurePosixPath(file_name).suffix.lower()
    if not ext:
        guessed = mimetypes.guess_extension("application/octet-stream") or ""
        ext = guessed
    return ext
