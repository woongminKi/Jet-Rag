"""인제스트 파이프라인 entrypoint — `BackgroundTasks` 로 호출되는 단일 진입점.

8-stage (W2 명세 v0.3 §3.A 확정)
    extract → chunk → content_gate → tag_summarize → load → embed → doc_embed → dedup
"""

from __future__ import annotations

import logging

from app.db import get_supabase_client

from .jobs import _now_iso, fail_job, finish_job, start_job
from .stages.chunk import run_chunk_stage
from .stages.content_gate import run_content_gate_stage
from .stages.dedup import run_dedup_stage
from .stages.doc_embed import run_doc_embed_stage
from .stages.embed import run_embed_stage
from .stages.extract import run_extract_stage
from .stages.load import run_load_stage
from .stages.tag_summarize import run_tag_summarize_stage

logger = logging.getLogger(__name__)

# 사이드 이펙트로 documents.flags.error_msg 에 보존할 에러 메시지 길이 상한
# (UI 노출 시 한 줄 카드 영역에 들어가도록)
_FAIL_ERROR_MSG_LIMIT = 500


def run_pipeline(job_id: str, doc_id: str) -> None:
    try:
        start_job(job_id, stage="extract")

        extraction = run_extract_stage(job_id, doc_id)
        if extraction is None:
            # 비 PDF graceful skip — 후속 스테이지 스킵, job 은 정상 완료
            finish_job(job_id)
            return

        chunk_records = run_chunk_stage(
            job_id, doc_id=doc_id, extraction=extraction
        )

        # content_gate — chunks metadata 에 PII/워터마크 부착 + doc flags 마킹
        chunk_records, _gate_flags = run_content_gate_stage(
            job_id,
            doc_id=doc_id,
            chunks=chunk_records,
            extraction=extraction,
        )

        # 태그·요약은 §10.10 정책상 실패해도 파이프라인 중단하지 않음 (NULL 유지)
        run_tag_summarize_stage(job_id, doc_id=doc_id, extraction=extraction)

        loaded = run_load_stage(job_id, chunks=chunk_records)
        embedded = run_embed_stage(job_id, doc_id=doc_id)
        doc_embedded = run_doc_embed_stage(
            job_id, doc_id=doc_id, extraction=extraction
        )
        dedup_match = run_dedup_stage(job_id, doc_id=doc_id) if doc_embedded else None

        logger.info(
            "ingest pipeline done: job=%s doc=%s chunks_loaded=%s embedded=%s "
            "doc_embedded=%s dedup_tier=%s warnings=%s",
            job_id,
            doc_id,
            loaded,
            embedded,
            doc_embedded,
            (dedup_match.get("duplicate_tier") if dedup_match else None),
            len(extraction.warnings),
        )

        finish_job(job_id)
    except Exception as exc:  # noqa: BLE001 — 최상위 경계
        logger.exception(
            "ingest pipeline failed: job=%s doc=%s", job_id, doc_id
        )
        try:
            fail_job(job_id, error_msg=str(exc))
        except Exception:
            logger.exception("ingest pipeline failure bookkeeping 실패")
        try:
            _cleanup_failed_doc(doc_id, error_msg=str(exc))
        except Exception:
            logger.exception("ingest pipeline cleanup 실패: doc=%s", doc_id)


def _cleanup_failed_doc(doc_id: str, *, error_msg: str) -> None:
    """파이프라인 실패 시 chunks 정리 + documents.flags.failed 마킹.

    정책 (B 안 Hybrid)
    - chunks 와 doc_embedding 은 검색 품질 보호를 위해 즉시 제거
    - documents row 자체는 유지 (사용자가 같은 파일 재업로드 시 자동 reingest 식별)
    - tag_summarize 로 채워진 tags / summary 는 디버깅용으로 보존
    - 기존 flags 는 보존 + failed / failed_at / error_msg 만 추가 (select-then-merge)
    """
    client = get_supabase_client()

    client.table("chunks").delete().eq("doc_id", doc_id).execute()

    existing_resp = (
        client.table("documents")
        .select("flags")
        .eq("id", doc_id)
        .limit(1)
        .execute()
    )
    existing_rows = existing_resp.data or []
    existing_flags = (
        dict(existing_rows[0].get("flags") or {}) if existing_rows else {}
    )

    next_flags = {
        **existing_flags,
        "failed": True,
        "failed_at": _now_iso(),
        "error_msg": error_msg[:_FAIL_ERROR_MSG_LIMIT],
    }

    (
        client.table("documents")
        .update({"doc_embedding": None, "flags": next_flags})
        .eq("id", doc_id)
        .execute()
    )
