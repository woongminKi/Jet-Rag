"""인제스트 파이프라인 entrypoint.

Day 3 범위: `BackgroundTasks` 로 호출되는 뼈대만. 아직 실제 추출·청킹·임베딩은 수행하지 않고
job 을 running → completed 로 바로 전환한다.

Day 4~5 에 단계별 구현을 이 모듈에 채운다 (기획서 §10.2 [4]~[11]).
"""

from __future__ import annotations

import logging

from .jobs import fail_job, finish_job, log_stage, start_job

logger = logging.getLogger(__name__)

_STAGE_PLACEHOLDER = "placeholder"


def run_pipeline(job_id: str) -> None:
    """BackgroundTasks 에 등록되는 단일 진입점.

    실제 파이프라인은 Day 4 부터. 지금은 job 상태만 정상 전이시켜서 상태 API 흐름을 검증한다.
    """
    try:
        start_job(job_id, stage=_STAGE_PLACEHOLDER)
        log_stage(job_id, stage=_STAGE_PLACEHOLDER, status="started")
        # TODO(Day 4): 포맷별 추출 → 청킹 → 태깅·요약 → 임베딩 → 적재 → 중복 감지
        log_stage(job_id, stage=_STAGE_PLACEHOLDER, status="succeeded")
        finish_job(job_id)
    except Exception as exc:  # noqa: BLE001 — 최상위 경계
        logger.exception("ingest pipeline failed: job_id=%s", job_id)
        try:
            log_stage(
                job_id,
                stage=_STAGE_PLACEHOLDER,
                status="failed",
                error_msg=str(exc),
            )
            fail_job(job_id, error_msg=str(exc))
        except Exception:
            logger.exception("ingest pipeline failure bookkeeping 실패")
