"""수익화 W4 — 이메일 인제스트 (주소 발급·검증·첨부 → 파이프라인).

Pro 유저 전용 u-{token}@<domain> 수신 주소. Cloudflare Email Worker 가
POST /ingest/email 로 첨부를 전달하면, 업로드 수신 단계와 동일한 게이트
(확장자·50MB·magic bytes·SHA-256 dedup)를 거쳐 run_full_ingest 를 재사용한다.

정책
- 거절(알 수 없는 토큰/발신자 불일치/Free/비허용 첨부) = 조용히 skip + warning 로그.
- 발신자 화이트리스트 = 주소 발급 시점의 가입 이메일(owner_email, JWT claim).
- 허용 확장자 = 스펙 명시분(pdf/hwp/hwpx/docx/이미지)만 — 업로드보다 좁음(스팸 벡터 축소).
- documents.source_channel = "email".
"""
from __future__ import annotations

import hashlib
import logging
import re
import secrets
import string
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import PurePosixPath

from fastapi import BackgroundTasks, HTTPException

from app.adapters.impl.supabase_storage import SupabaseBlobStorage
from app.config import get_settings
from app.db import get_supabase_client
from app.ingest import create_job, run_full_ingest
from app.routers._input_gate import HEAD_BYTES, validate_magic
from app.services.ingest_mode import resolve_page_cap

logger = logging.getLogger(__name__)

_TOKEN_ALPHABET = string.ascii_lowercase + string.digits
_TOKEN_LEN = 8
# 스펙 §2 — 이메일 첨부 허용 포맷. 업로드(_ALLOWED_EXTENSIONS)의 부분집합.
_EMAIL_ALLOWED_EXTENSIONS: dict[str, str] = {
    ".pdf": "pdf",
    ".hwp": "hwp",
    ".hwpx": "hwpx",
    ".docx": "docx",
    ".jpg": "image",
    ".jpeg": "image",
    ".png": "image",
    ".heic": "image",
}
_MAX_SIZE_BYTES = 50 * 1024 * 1024  # 업로드와 동일 (documents.py:79)
_EMAIL_RE = re.compile(r"^(?:[^<]*<)?([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+)>?\s*$")


def generate_token() -> str:
    return "".join(secrets.choice(_TOKEN_ALPHABET) for _ in range(_TOKEN_LEN))


def build_address(token: str, domain: str) -> str:
    return f"u-{token}@{domain}"


def _extract_email(raw: str) -> str | None:
    """'Name <a@b.c>' / 'a@b.c' → 'a@b.c' (소문자). 실패 시 None."""
    match = _EMAIL_RE.search(raw.strip())
    return match.group(1).lower() if match else None


def parse_token(to_address: str) -> str | None:
    """수신(To) 주소에서 토큰 추출. u-{token}@... 형식 아니면 None."""
    email = _extract_email(to_address)
    if not email or not email.startswith("u-"):
        return None
    local = email.split("@", 1)[0]
    token = local[2:]
    if len(token) != _TOKEN_LEN or not token.isalnum():
        return None
    return token


def lookup_by_token(token: str) -> dict | None:
    """token → {user_id, token, owner_email} row. 없으면/실패 시 None."""
    try:
        rows = (
            get_supabase_client()
            .table("email_ingest_addresses")
            .select("user_id, token, owner_email")
            .eq("token", token)
            .limit(1)
            .execute()
            .data
        ) or []
        return rows[0] if rows else None
    except Exception as exc:  # noqa: BLE001 — 쓰기 경로: 조회 실패 = 거절 (fail-closed)
        logger.warning("email_ingest 주소 조회 실패 (token=%s...): %s", token[:4], exc)
        return None


def sender_allowed(from_address: str, owner_email: str | None) -> bool:
    """발신자 화이트리스트 — 가입 이메일과 일치해야 통과. owner_email 없으면 거절."""
    if not owner_email:
        return False
    sender = _extract_email(from_address)
    return sender is not None and sender == owner_email.strip().lower()


def get_or_create_address(user_id: str, user_email: str | None) -> dict:
    """유저의 인제스트 주소 row 반환 — 없으면 발급. owner_email 은 최신값으로 갱신."""
    client = get_supabase_client()
    rows = (
        client.table("email_ingest_addresses")
        .select("user_id, token, owner_email")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
        .data
    ) or []
    if rows:
        row = rows[0]
        if user_email and row.get("owner_email") != user_email:
            client.table("email_ingest_addresses").update(
                {"owner_email": user_email}
            ).eq("user_id", user_id).execute()
            row["owner_email"] = user_email
        return row
    row = {
        "user_id": user_id,
        "token": generate_token(),
        "owner_email": user_email,
    }
    client.table("email_ingest_addresses").insert(row).execute()
    return row


def rotate_address(user_id: str, user_email: str | None) -> dict:
    """토큰 재발급 (스팸/유출 대응). 구 토큰 즉시 무효."""
    client = get_supabase_client()
    row = {
        "user_id": user_id,
        "token": generate_token(),
        "owner_email": user_email,
        "rotated_at": datetime.now(timezone.utc).isoformat(),
    }
    client.table("email_ingest_addresses").upsert(row, on_conflict="user_id").execute()
    return row


def ingest_email_attachment(
    *,
    user_id: str,
    filename: str,
    content_type: str,
    raw: bytes,
    background_tasks: BackgroundTasks,
) -> dict:
    """첨부 1건을 업로드 수신 단계와 동일 게이트로 검증 후 BG 파이프라인 큐잉.

    반환: {"status": "accepted"|"duplicated"|"skipped", ...}. 예외를 던지지 않는다
    (webhook 은 첨부별 결과를 모아 항상 200 — 거절 정책 '조용히 무시').

    documents.py upload_document(402-575) 수신 단계의 의도적 미러 — 공용 추출
    리팩토링은 hot path(업로드) 회귀 리스크로 보류. 게이트 값(_MAX_SIZE_BYTES,
    validate_magic)은 동일 소스를 공유한다.
    """
    ext = PurePosixPath(filename).suffix.lower()
    doc_type = _EMAIL_ALLOWED_EXTENSIONS.get(ext)
    if doc_type is None:
        logger.warning("email_ingest skip — 비허용 확장자 %s (user=%s)", ext, user_id)
        return {"status": "skipped", "filename": filename, "reason": f"비허용 확장자: {ext or '(없음)'}"}

    if len(raw) == 0:
        return {"status": "skipped", "filename": filename, "reason": "빈 첨부"}
    if len(raw) > _MAX_SIZE_BYTES:
        logger.warning("email_ingest skip — 50MB 초과 (user=%s, %d bytes)", user_id, len(raw))
        return {"status": "skipped", "filename": filename, "reason": "50MB 초과"}

    try:
        validate_magic(ext=ext, raw_head=raw[:HEAD_BYTES])
    except HTTPException as exc:
        logger.warning("email_ingest skip — magic bytes 불일치 (user=%s, %s): %s", user_id, filename, exc.detail)
        return {"status": "skipped", "filename": filename, "reason": "파일 형식 불일치"}

    sha256 = hashlib.sha256(raw).hexdigest()
    settings = get_settings()
    supabase = get_supabase_client()

    try:
        # Tier 1 dedup (upload_document 와 동일 — 실패 문서 재시도 분기는 웹 UI 전용)
        existing = (
            supabase.table("documents")
            .select("id, flags")
            .eq("user_id", user_id)
            .eq("sha256", sha256)
            .is_("deleted_at", "null")
            .limit(1)
            .execute()
        )
        if existing.data:
            return {"status": "duplicated", "filename": filename, "doc_id": existing.data[0]["id"]}

        pending_path = SupabaseBlobStorage.build_pending_path(
            user_id=user_id, doc_uuid=uuid.uuid4().hex, ext=ext
        )
        doc_title = unicodedata.normalize("NFC", PurePosixPath(filename).stem)
        page_cap_override = resolve_page_cap("default", settings)
        doc_row = (
            supabase.table("documents")
            .insert(
                {
                    "user_id": user_id,
                    "title": doc_title,
                    "doc_type": doc_type,
                    "source_channel": "email",
                    "storage_path": pending_path,
                    "sha256": sha256,
                    "size_bytes": len(raw),
                    "content_type": content_type or "application/octet-stream",
                    "flags": {"ingest_mode": "default"},
                }
            )
            .execute()
        )
        doc_id = doc_row.data[0]["id"]
        job = create_job(doc_id=doc_id)
        background_tasks.add_task(
            run_full_ingest,
            job_id=job.id,
            doc_id=doc_id,
            raw=raw,
            sha256=sha256,
            ext=ext,
            content_type=content_type or "application/octet-stream",
            page_cap_override=page_cap_override,
            user_id=user_id,
        )
        return {"status": "accepted", "filename": filename, "doc_id": doc_id, "job_id": job.id}
    except Exception as exc:  # noqa: BLE001
        logger.warning("email_ingest skip — 내부 오류 (user=%s, %s): %s", user_id, filename, exc)
        return {"status": "skipped", "filename": filename, "reason": "내부 오류"}
