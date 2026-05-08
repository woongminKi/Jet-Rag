"""W2 SLO 회복 — POST /documents 의 BackgroundTask 진입점.

수신 단계는 Storage upload 를 하지 않고 documents row 만 pending path 로 INSERT 후 202 반환.
본 함수는 응답이 클라이언트에 나간 뒤 비동기로 다음을 수행:
  1) Supabase Storage 에 final path (`<sha256>{ext}`) 로 업로드 (멱등 upsert)
  2) `documents.storage_path` 를 final path 로 update
  3) 기존 8-stage 파이프라인 (`run_pipeline`) 위임

실패 처리:
  - Storage upload / path update 예외 → `documents.flags.upload_failed = true` + `flags.failed = true`
    마킹 → `fail_job` 으로 ingest_jobs 도 failed 전이 → 파이프라인 호출하지 않음
  - 결과적으로 기존 reingest 자동 분기 (flags.failed=true 시) 와 동일한 회복 경로 진입 가능

기획서 §10.11 SLO + W2 명세 v0.3 §3.A.
"""

from __future__ import annotations

import logging

from app.adapters.impl.supabase_storage import SupabaseBlobStorage
from app.config import get_settings
from app.db import get_supabase_client

from .jobs import fail_job
from .pipeline import run_pipeline

logger = logging.getLogger(__name__)


def run_full_ingest(
    *,
    job_id: str,
    doc_id: str,
    raw: bytes,
    sha256: str,
    ext: str,
    content_type: str,
    page_cap_override: int | None = None,
) -> None:
    """Storage upload → storage_path update → run_pipeline 위임.

    S2 D3 — `page_cap_override` 가 주어지면 mode 별 vision page cap.
    None 이면 settings.vision_page_cap_per_doc (S2 D2 기존 동작).
    """
    settings = get_settings()
    supabase = get_supabase_client()

    final_path = f"{sha256}{ext}"
    storage = SupabaseBlobStorage(bucket=settings.supabase_storage_bucket)

    # 1) Storage upload (final path, 멱등)
    try:
        storage.put_at(
            path=final_path,
            data=raw,
            content_type=content_type,
            sha256=sha256,
        )
    except Exception as exc:  # noqa: BLE001 — BG 경계
        logger.exception("Storage upload 실패 doc_id=%s", doc_id)
        _mark_upload_failed(supabase, doc_id=doc_id, error=str(exc))
        fail_job(job_id, error_msg=f"Storage upload 실패: {exc}")
        return

    # 2) storage_path 를 final 로 갱신
    try:
        (
            supabase.table("documents")
            .update({"storage_path": final_path})
            .eq("id", doc_id)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("storage_path update 실패 doc_id=%s", doc_id)
        _mark_upload_failed(supabase, doc_id=doc_id, error=str(exc))
        fail_job(job_id, error_msg=f"storage_path update 실패: {exc}")
        return

    # 3) 정상 — 8-stage 파이프라인 본 실행 (S2 D3: page_cap_override 전달).
    run_pipeline(job_id, doc_id, page_cap_override=page_cap_override)


def _mark_upload_failed(supabase, *, doc_id: str, error: str) -> None:
    """`flags.upload_failed = true` + `flags.failed = true` 마킹.

    기존 reingest 자동 분기 (POST /documents 의 dedup→failed 경로) 가 `flags.failed=true`
    만 보고 동작하므로, upload 실패도 동일 경로로 회복되도록 함께 설정한다.
    """
    flags: dict = {}
    try:
        existing = (
            supabase.table("documents")
            .select("flags")
            .eq("id", doc_id)
            .limit(1)
            .execute()
        )
        if existing.data:
            flags = dict(existing.data[0].get("flags") or {})
    except Exception:  # noqa: BLE001
        logger.exception("flags 조회 실패 doc_id=%s — 빈 dict 로 진행", doc_id)

    flags.update(
        {
            "upload_failed": True,
            "failed": True,
            "error": error,
        }
    )
    try:
        (
            supabase.table("documents")
            .update({"flags": flags})
            .eq("id", doc_id)
            .execute()
        )
    except Exception:  # noqa: BLE001
        logger.exception("flags.upload_failed 마킹 실패 doc_id=%s", doc_id)
