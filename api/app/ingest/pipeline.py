"""인제스트 파이프라인 entrypoint.

Day 4 전반부까지: `start_job` → `stage()` 하나 (placeholder) → `finish_job`.
Day 4 실 스테이지(`extract`·`chunk`·`load`) 는 후속 커밋에서 채운다 (§10.2 [4]~[10]).
"""

from __future__ import annotations

import logging

from .jobs import fail_job, finish_job, stage, start_job

logger = logging.getLogger(__name__)

_STAGE_PLACEHOLDER = "placeholder"


def run_pipeline(job_id: str) -> None:
    """BackgroundTasks 에 등록되는 단일 진입점."""
    try:
        start_job(job_id, stage=_STAGE_PLACEHOLDER)
        with stage(job_id, _STAGE_PLACEHOLDER):
            # TODO(Day 4): extract → chunk → tag_summarize → load
            pass
        finish_job(job_id)
    except Exception as exc:  # noqa: BLE001 — 최상위 경계
        logger.exception("ingest pipeline failed: job_id=%s", job_id)
        try:
            fail_job(job_id, error_msg=str(exc))
        except Exception:
            logger.exception("ingest pipeline failure bookkeeping 실패")
