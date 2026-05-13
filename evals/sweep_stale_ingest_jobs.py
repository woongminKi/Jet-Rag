"""M0-a W-14 — `ingest_jobs` 고아 running job 수동 sweep CLI.

배경
    BackgroundTasks 인제스트 도중 프로세스가 비정상 종료하면 `ingest_jobs` 가
    `status='running'` 인 채로 남는다 (`ingest_jobs` 에 heartbeat/`updated_at` 컬럼이
    없어 마지막 진전 시각은 알 수 없음 — `started_at` 경과 시간만으로 보수적 판정).
    `app/main.py` lifespan 이 기동 시 1회 자동 sweep 하지만, 백엔드를 재시작하지 않고
    바로 정리하고 싶을 때 본 CLI 를 쓴다.

동작
    - 기본 dry-run — 발견 건수·job id 만 출력, DB 쓰기 0.
    - `--apply` 로만 실제 `status='failed'` 마킹. chunks 는 절대 안 건드림 (선택 A —
      embed 도중 멈춘 job 의 부분 chunk 보존, 복구는 `_repair_*` 도구 몫).
    - `--hours N` 으로 threshold 조정. 미지정 시 config 값(`JETRAG_STALE_INGEST_JOB_HOURS`,
      default 24, `[1,168]` clamp).

사용
    # dry-run (DB 변경 0)
    cd api && uv run python ../evals/sweep_stale_ingest_jobs.py

    # 실제 마킹
    cd api && uv run python ../evals/sweep_stale_ingest_jobs.py --apply

    # threshold 48h 로 dry-run
    cd api && uv run python ../evals/sweep_stale_ingest_jobs.py --hours 48

비용 / SLO
    Supabase Postgres 쿼리 1 + UPDATE N건만 — paid API 호출 0, 추가 비용 0.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "api"))

# noqa: E402 — sys.path 확정 후 import
from app.config import get_settings  # noqa: E402
from app.services.ingest_job_watchdog import sweep_stale_ingest_jobs  # noqa: E402


def main() -> int:
    settings = get_settings()
    default_hours = settings.stale_ingest_job_hours

    parser = argparse.ArgumentParser(
        description="ingest_jobs 고아 running job sweep. 기본 dry-run."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="실제 DB 쓰기 (없으면 dry-run — 발견 건수만 출력).",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=default_hours,
        help=f"이 시간(시) 이상 running 상태면 stale 로 본다 (default: {default_hours} = config).",
    )
    args = parser.parse_args()

    threshold_hours = max(1, args.hours)
    mode = "APPLY" if args.apply else "DRY-RUN"

    result = sweep_stale_ingest_jobs(threshold_hours=threshold_hours, apply=args.apply)

    print("=" * 72)
    print(f"[{mode}] ingest_jobs 고아 running sweep — threshold={threshold_hours}h")
    print("=" * 72)
    print(f"  scanned (고아 running 발견): {result.scanned}건")
    print(f"  marked_failed (failed 마킹): {result.marked_failed}건")
    if result.stale_job_ids:
        print("  stale job id:")
        for job_id in result.stale_job_ids:
            print(f"    {job_id}")
    if not args.apply and result.scanned:
        print("\n  [DRY-RUN] --apply 없이는 DB 쓰기 안 함. 실제 마킹:")
        print("    cd api && uv run python ../evals/sweep_stale_ingest_jobs.py --apply")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
