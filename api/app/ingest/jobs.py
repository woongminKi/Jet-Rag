"""`ingest_jobs` / `ingest_logs` CRUD.

파이프라인 각 스테이지가 진행 상황을 기록하고, 상태 조회 API 가 이를 읽는다.

## 스테이지 로그 모델 (2026-04-23 개정)

Day 3 초안은 스테이지당 `(started, succeeded)` 2행을 `INSERT` 했다. DB `DEFAULT now()` 와
Python `_now_iso()` 의 clock skew 로 `finished_at < started_at` 역전이 관찰되어, 한 스테이지를
`begin_stage()` INSERT → `end_stage()` UPDATE 로 **1행** 으로 관리하도록 변경한다.

스키마 자체는 동일. `status` 컬럼이 그대로 started / succeeded / failed / skipped 전이를 담는다.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator

from app.db import get_supabase_client

logger = logging.getLogger(__name__)

_TABLE_JOBS = "ingest_jobs"
_TABLE_LOGS = "ingest_logs"


@dataclass(frozen=True)
class IngestJob:
    id: str
    doc_id: str | None
    status: str
    current_stage: str | None
    attempts: int
    error_msg: str | None
    queued_at: str
    started_at: str | None
    finished_at: str | None


# ---------------------- jobs ----------------------

def create_job(doc_id: str) -> IngestJob:
    client = get_supabase_client()
    resp = (
        client.table(_TABLE_JOBS)
        .insert({"doc_id": doc_id, "status": "queued"})
        .execute()
    )
    return _row_to_job(resp.data[0])


def start_job(job_id: str, *, stage: str) -> None:
    client = get_supabase_client()
    (
        client.table(_TABLE_JOBS)
        .update(
            {
                "status": "running",
                "current_stage": stage,
                "attempts": 1,
                "started_at": _now_iso(),
            }
        )
        .eq("id", job_id)
        .execute()
    )


def update_stage(job_id: str, *, stage: str) -> None:
    client = get_supabase_client()
    (
        client.table(_TABLE_JOBS)
        .update({"current_stage": stage})
        .eq("id", job_id)
        .execute()
    )


def finish_job(job_id: str) -> None:
    client = get_supabase_client()
    (
        client.table(_TABLE_JOBS)
        .update(
            {
                "status": "completed",
                "current_stage": "done",
                "finished_at": _now_iso(),
            }
        )
        .eq("id", job_id)
        .execute()
    )


def fail_job(job_id: str, *, error_msg: str) -> None:
    client = get_supabase_client()
    (
        client.table(_TABLE_JOBS)
        .update(
            {
                "status": "failed",
                "error_msg": error_msg,
                "finished_at": _now_iso(),
            }
        )
        .eq("id", job_id)
        .execute()
    )


def get_latest_job_for_doc(doc_id: str) -> IngestJob | None:
    client = get_supabase_client()
    resp = (
        client.table(_TABLE_JOBS)
        .select("*")
        .eq("doc_id", doc_id)
        .order("queued_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = resp.data or []
    return _row_to_job(rows[0]) if rows else None


# ---------------------- logs (1행 모델) ----------------------

def begin_stage(job_id: str, *, stage: str) -> int:
    """스테이지 시작을 기록하고 로그 row id 를 반환한다. 이후 `end_stage()` 로 마감."""
    client = get_supabase_client()
    resp = (
        client.table(_TABLE_LOGS)
        .insert(
            {
                "job_id": job_id,
                "stage": stage,
                "status": "started",
            }
        )
        .execute()
    )
    return int(resp.data[0]["id"])


def end_stage(
    log_id: int,
    *,
    status: str,
    error_msg: str | None = None,
    duration_ms: int | None = None,
) -> None:
    """`begin_stage()` 로 시작한 로그 행을 succeeded / failed / skipped 로 마감한다."""
    if status not in {"succeeded", "failed", "skipped"}:
        raise ValueError(f"end_stage 에 허용되지 않는 status: {status}")
    client = get_supabase_client()
    payload: dict[str, Any] = {
        "status": status,
        "finished_at": _now_iso(),
    }
    if error_msg is not None:
        payload["error_msg"] = error_msg
    if duration_ms is not None:
        payload["duration_ms"] = duration_ms
    (
        client.table(_TABLE_LOGS)
        .update(payload)
        .eq("id", log_id)
        .execute()
    )


@contextmanager
def stage(job_id: str, name: str) -> Iterator[None]:
    """파이프라인 스테이지 1회 실행을 감싸는 context manager.

    - `ingest_jobs.current_stage` 갱신
    - `ingest_logs` 에 1행 (started) 기록 → 정상 종료 시 succeeded, 예외 발생 시 failed
    - duration_ms 자동 측정
    """
    update_stage(job_id, stage=name)
    log_id = begin_stage(job_id, stage=name)
    start = time.monotonic()
    try:
        yield
    except Exception as exc:  # noqa: BLE001 — 최상위 스테이지 경계
        duration_ms = int((time.monotonic() - start) * 1000)
        try:
            end_stage(
                log_id,
                status="failed",
                error_msg=str(exc),
                duration_ms=duration_ms,
            )
        except Exception:
            logger.exception("end_stage(failed) bookkeeping 실패 job=%s stage=%s", job_id, name)
        raise
    else:
        duration_ms = int((time.monotonic() - start) * 1000)
        end_stage(log_id, status="succeeded", duration_ms=duration_ms)


def skip_stage(
    job_id: str,
    *,
    stage: str,
    reason: str | None = None,
) -> None:
    """스테이지를 실행하지 않고 skipped 로만 기록한다 (예: 비 PDF 의 extract)."""
    update_stage(job_id, stage=stage)
    log_id = begin_stage(job_id, stage=stage)
    end_stage(log_id, status="skipped", error_msg=reason)


def list_logs_for_job(job_id: str) -> list[dict]:
    client = get_supabase_client()
    resp = (
        client.table(_TABLE_LOGS)
        .select("*")
        .eq("job_id", job_id)
        .order("started_at", desc=False)
        .execute()
    )
    return resp.data or []


# ---------------------- helpers ----------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_job(row: dict) -> IngestJob:
    return IngestJob(
        id=row["id"],
        doc_id=row.get("doc_id"),
        status=row["status"],
        current_stage=row.get("current_stage"),
        attempts=row.get("attempts", 0),
        error_msg=row.get("error_msg"),
        queued_at=row["queued_at"],
        started_at=row.get("started_at"),
        finished_at=row.get("finished_at"),
    )
