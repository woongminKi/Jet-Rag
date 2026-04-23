"""`ingest_jobs` / `ingest_logs` CRUD.

파이프라인 각 스테이지가 진행 상황을 기록하고, 상태 조회 API가 이를 읽는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.db import get_supabase_client

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


# ---------------------- logs ----------------------

def log_stage(
    job_id: str,
    *,
    stage: str,
    status: str,
    error_msg: str | None = None,
    duration_ms: int | None = None,
) -> None:
    client = get_supabase_client()
    payload: dict[str, Any] = {
        "job_id": job_id,
        "stage": stage,
        "status": status,
        "error_msg": error_msg,
        "duration_ms": duration_ms,
    }
    if status in {"succeeded", "failed", "skipped"}:
        payload["finished_at"] = _now_iso()
    client.table(_TABLE_LOGS).insert(payload).execute()


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
