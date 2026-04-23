"""인제스트 파이프라인 entrypoint — `BackgroundTasks` 로 호출되는 단일 진입점.

Day 5 스코프 (§10.2 [4] · [7] · [8] · [10] · [9])
    extract → chunk → tag_summarize → load → embed

Tier 2/3 dedup 과 doc_embedding 은 embed 이후 별도 스테이지로 Day 5 B4/B6 에서 추가.
"""

from __future__ import annotations

import logging

from .jobs import fail_job, finish_job, start_job
from .stages.chunk import run_chunk_stage
from .stages.embed import run_embed_stage
from .stages.extract import run_extract_stage
from .stages.load import run_load_stage
from .stages.tag_summarize import run_tag_summarize_stage

logger = logging.getLogger(__name__)


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

        # 태그·요약은 §10.10 정책상 실패해도 파이프라인 중단하지 않음 (NULL 유지)
        run_tag_summarize_stage(job_id, doc_id=doc_id, extraction=extraction)

        loaded = run_load_stage(job_id, chunks=chunk_records)
        embedded = run_embed_stage(job_id, doc_id=doc_id)

        # TODO(Day 5 B4): doc_embedding 생성
        # TODO(Day 5 B6): Tier 2/3 dedup

        logger.info(
            "ingest pipeline done: job=%s doc=%s chunks_loaded=%s embedded=%s warnings=%s",
            job_id,
            doc_id,
            loaded,
            embedded,
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
