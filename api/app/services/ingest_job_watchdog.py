"""M0-a W-14 — `ingest_jobs` 의 고아 `running` job watchdog.

배경 (2026-05-12 sample-report 사고)
    BackgroundTasks 인제스트 (§10 파이프라인) 도중 프로세스가 비정상 종료하면
    `ingest_jobs` row 가 `status='running'` 인 채로 영구히 남는다 — `finished_at NULL`,
    `current_stage` 가 멈춘 지점(예: `embed`)에 고정. 이런 stale row 는 진행률 표시
    (ActiveDocsIndicator)·재인제스트 판정·측정 도구의 "running job 없는지" 사전 점검을
    오염시킨다. `ingest_jobs` 에는 heartbeat / `updated_at` 컬럼이 없으므로 (스키마
    001/010 기준) "마지막 진전 시각" 을 알 수 없다 — 대신 `started_at` 으로부터
    경과 시간만으로 보수적으로 판정한다.

설계
    - 선택 A: chunks 는 **절대 삭제하지 않음**. embed 도중 멈춘 job 의 부분 chunk 를
      보존한다 (sample-report 교훈 — `_cleanup_failed_doc` 호출은 1000 chunk 를 날린다).
      복구는 `evals/_repair_*` 도구의 몫. watchdog 은 `ingest_jobs.status` 만 마킹.
    - 선택 B: `started_at IS NULL` 인 `running` 은 잡지 않는다 (`start_job` 이 항상
      `started_at` 을 set 하므로 정상 흐름에서 발생하지 않는 케이스 — 잡아도 cutoff
      비교가 불가능).
    - threshold 기본 24h (config). `JETRAG_STALE_INGEST_JOB_HOURS` ENV 로 `[1,168]`
      clamp (config 측 처리).
    - UPDATE WHERE 절에 `status='running'` 을 재확인 — 동시 실행(startup hook + 수동
      CLI) 시 멱등. 이미 다른 주체가 terminal 로 전이한 row 는 0 rows affected.

사용
    - `app/main.py` lifespan 에서 startup 시 fire-and-forget 1회 sweep (apply=True).
    - `evals/sweep_stale_ingest_jobs.py` 로 수동 dry-run / apply.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.db import get_supabase_client

_TABLE_JOBS = "ingest_jobs"
_STATUS_RUNNING = "running"
_STATUS_FAILED = "failed"


@dataclass(frozen=True)
class SweepResult:
    """sweep 1회의 결과 요약.

    - scanned: cutoff 보다 오래된 `running` job 수 (apply 여부 무관 — 발견 건수).
    - marked_failed: 실제로 `status='failed'` 로 마킹한 수 (dry-run 이면 0).
    - stale_job_ids: 발견된 stale job id 목록 (보고·로그용).
    """

    scanned: int = 0
    marked_failed: int = 0
    stale_job_ids: list[str] = field(default_factory=list)


def _stale_error_msg(*, threshold_hours: int, stage: str | None) -> str:
    """stale job 의 `error_msg` — 사람이 읽고 원인·후속(chunks 미정리)을 알 수 있게."""
    return (
        f"watchdog: orphaned running > {threshold_hours}h (stage={stage}) — "
        "프로세스 비정상 종료 추정, status만 마킹 (chunks 미정리)"
    )


def sweep_stale_ingest_jobs(
    *,
    threshold_hours: int,
    apply: bool,
    client=None,
) -> SweepResult:
    """`started_at` 이 `threshold_hours` 이전인 `running` job 을 찾아 (apply 시) `failed` 마킹.

    chunks 는 건드리지 않는다 (선택 A). `started_at IS NULL` 은 대상에서 제외된다 —
    `.lt("started_at", cutoff)` 필터가 NULL row 를 자연히 제외하기 때문 (선택 B).

    Args:
        threshold_hours: 이 시간(시) 이상 `running` 상태로 머문 job 을 stale 로 본다.
        apply: True 면 실제 UPDATE, False 면 dry-run (조회만 — marked_failed=0).
        client: 테스트 주입용 Supabase 클라이언트. None 이면 service_role 싱글톤.

    Returns:
        SweepResult — scanned / marked_failed / stale_job_ids.
    """
    client = client or get_supabase_client()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=threshold_hours)).isoformat()

    resp = (
        client.table(_TABLE_JOBS)
        .select("id, doc_id, current_stage, started_at")
        .eq("status", _STATUS_RUNNING)
        .lt("started_at", cutoff)
        .execute()
    )
    stale = resp.data or []
    stale_ids = [j["id"] for j in stale]

    if not apply or not stale:
        return SweepResult(scanned=len(stale), marked_failed=0, stale_job_ids=stale_ids)

    marked = 0
    for j in stale:
        # UPDATE WHERE 에 status='running' 재확인 — 동시 실행 멱등 (이미 terminal 이면 no-op).
        (
            client.table(_TABLE_JOBS)
            .update(
                {
                    "status": _STATUS_FAILED,
                    "error_msg": _stale_error_msg(
                        threshold_hours=threshold_hours,
                        stage=j.get("current_stage"),
                    ),
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            .eq("id", j["id"])
            .eq("status", _STATUS_RUNNING)
            .execute()
        )
        marked += 1

    return SweepResult(scanned=len(stale), marked_failed=marked, stale_job_ids=stale_ids)
