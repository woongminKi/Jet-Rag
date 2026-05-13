"""M0-a W-14 — `app.services.ingest_job_watchdog.sweep_stale_ingest_jobs` + main hook 단위 테스트.

검증 범위
- stale running job 마킹 (apply=True) / recent running 보존 / terminal job 무시
- dry-run (apply=False) write 0 / threshold_hours 반영 / UPDATE 멱등 필터(.eq status running)
- `app.main._sweep_stale_ingest_jobs()` graceful (DB 예외 전파 X) / SUPABASE_URL 미설정 시 skip
- `config.get_settings().stale_ingest_job_hours` clamp `[1,168]` (invalid → 24)

stdlib unittest + 가벼운 fake Supabase client (외부 의존성·실 DB 없음).
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch


# ---------------------- fake supabase client ----------------------


class _FakeResp:
    def __init__(self, data: list[dict]) -> None:
        self.data = data


class _FakeQuery:
    """`.table().select().eq().lt().execute()` / `.table().update().eq().eq().execute()` 흉내.

    - select 체인: 메모리 rows 를 status / started_at(< cutoff) 로 필터해 `.data` 반환.
    - update 체인: payload + eq 필터 인자를 client 에 기록 (멱등 필터·error_msg 검증용).
    """

    def __init__(self, client: "_FakeClient", table: str) -> None:
        self._client = client
        self._table = table
        self._op: str | None = None
        self._payload: dict | None = None
        self._eq: dict[str, object] = {}
        self._lt: dict[str, object] = {}

    def select(self, _cols: str):  # noqa: ANN001
        self._op = "select"
        return self

    def update(self, payload: dict):
        self._op = "update"
        self._payload = payload
        return self

    def eq(self, col: str, value):  # noqa: ANN001
        self._eq[col] = value
        return self

    def lt(self, col: str, value):  # noqa: ANN001
        self._lt[col] = value
        return self

    def execute(self) -> _FakeResp:
        if self._client.execute_error is not None and self._op == "select":
            raise self._client.execute_error
        if self._op == "update":
            self._client.updates.append(
                {
                    "table": self._table,
                    "payload": self._payload,
                    "eq": dict(self._eq),
                }
            )
            return _FakeResp([])
        # select — status 필터 + started_at < cutoff 필터.
        rows = [r for r in self._client.rows if r.get("status") == self._eq.get("status")]
        cutoff = self._lt.get("started_at")
        if cutoff is not None:
            rows = [
                r
                for r in rows
                if r.get("started_at") is not None and r["started_at"] < cutoff
            ]
        # select 컬럼은 무시하고 row 전체 반환 (테스트 단순화).
        return _FakeResp([dict(r) for r in rows])


class _FakeClient:
    def __init__(self, rows: list[dict], *, execute_error: Exception | None = None) -> None:
        self.rows = rows
        self.updates: list[dict] = []
        self.execute_error = execute_error

    def table(self, name: str) -> _FakeQuery:
        return _FakeQuery(self, name)


def _iso_hours_ago(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


# ---------------------- sweep_stale_ingest_jobs ----------------------


class SweepStaleIngestJobsTest(unittest.TestCase):
    def test_marks_stale_running_job_failed(self) -> None:
        from app.services.ingest_job_watchdog import sweep_stale_ingest_jobs

        client = _FakeClient(
            [{"id": "j1", "status": "running", "current_stage": "embed",
              "started_at": _iso_hours_ago(25)}]
        )
        result = sweep_stale_ingest_jobs(threshold_hours=24, apply=True, client=client)

        self.assertEqual(result.scanned, 1)
        self.assertEqual(result.marked_failed, 1)
        self.assertEqual(result.stale_job_ids, ["j1"])
        self.assertEqual(len(client.updates), 1)
        upd = client.updates[0]
        self.assertEqual(upd["payload"]["status"], "failed")
        self.assertIn("watchdog", upd["payload"]["error_msg"])
        self.assertIn("finished_at", upd["payload"])

    def test_keeps_recent_running_job(self) -> None:
        from app.services.ingest_job_watchdog import sweep_stale_ingest_jobs

        client = _FakeClient(
            [{"id": "j1", "status": "running", "current_stage": "embed",
              "started_at": _iso_hours_ago(1)}]
        )
        result = sweep_stale_ingest_jobs(threshold_hours=24, apply=True, client=client)

        self.assertEqual(result.scanned, 0)
        self.assertEqual(result.marked_failed, 0)
        self.assertEqual(client.updates, [])

    def test_ignores_terminal_jobs(self) -> None:
        from app.services.ingest_job_watchdog import sweep_stale_ingest_jobs

        client = _FakeClient(
            [
                {"id": "c1", "status": "completed", "current_stage": "done",
                 "started_at": _iso_hours_ago(100)},
                {"id": "f1", "status": "failed", "current_stage": "embed",
                 "started_at": _iso_hours_ago(100)},
                {"id": "x1", "status": "cancelled", "current_stage": "extract",
                 "started_at": _iso_hours_ago(100)},
            ]
        )
        result = sweep_stale_ingest_jobs(threshold_hours=24, apply=True, client=client)

        self.assertEqual(result.scanned, 0)
        self.assertEqual(client.updates, [])

    def test_dry_run_does_not_write(self) -> None:
        from app.services.ingest_job_watchdog import sweep_stale_ingest_jobs

        client = _FakeClient(
            [{"id": "j1", "status": "running", "current_stage": "embed",
              "started_at": _iso_hours_ago(99)}]
        )
        result = sweep_stale_ingest_jobs(threshold_hours=24, apply=False, client=client)

        self.assertEqual(result.scanned, 1)
        self.assertEqual(result.marked_failed, 0)
        self.assertEqual(result.stale_job_ids, ["j1"])
        self.assertEqual(client.updates, [])

    def test_threshold_hours_respected(self) -> None:
        from app.services.ingest_job_watchdog import sweep_stale_ingest_jobs

        # 30h 전 — threshold 48h 에서는 stale 아님.
        client_recent = _FakeClient(
            [{"id": "j1", "status": "running", "current_stage": "embed",
              "started_at": _iso_hours_ago(30)}]
        )
        r1 = sweep_stale_ingest_jobs(threshold_hours=48, apply=True, client=client_recent)
        self.assertEqual(r1.scanned, 0)
        self.assertEqual(client_recent.updates, [])

        # 50h 전 — threshold 48h 에서는 stale.
        client_stale = _FakeClient(
            [{"id": "j2", "status": "running", "current_stage": "embed",
              "started_at": _iso_hours_ago(50)}]
        )
        r2 = sweep_stale_ingest_jobs(threshold_hours=48, apply=True, client=client_stale)
        self.assertEqual(r2.scanned, 1)
        self.assertEqual(r2.marked_failed, 1)

    def test_idempotent_update_filter(self) -> None:
        from app.services.ingest_job_watchdog import sweep_stale_ingest_jobs

        client = _FakeClient(
            [{"id": "j1", "status": "running", "current_stage": "tag_summarize",
              "started_at": _iso_hours_ago(48)}]
        )
        sweep_stale_ingest_jobs(threshold_hours=24, apply=True, client=client)

        self.assertEqual(len(client.updates), 1)
        # UPDATE WHERE 에 id + status='running' 둘 다 있어야 동시 실행 멱등.
        eq_filters = client.updates[0]["eq"]
        self.assertEqual(eq_filters.get("id"), "j1")
        self.assertEqual(eq_filters.get("status"), "running")


# ---------------------- app.main hook ----------------------


class MainSweepHookTest(unittest.TestCase):
    def test_main_sweep_hook_graceful_on_db_error(self) -> None:
        """sweep 가 예외를 던져도 `_sweep_stale_ingest_jobs()` 는 전파하지 않는다."""
        from app import main as main_module

        class _S:
            supabase_url = "https://example.supabase.co"
            stale_ingest_job_hours = 24

        with (
            patch.object(main_module, "logger"),
            patch("app.config.get_settings", return_value=_S()),
            patch(
                "app.services.ingest_job_watchdog.sweep_stale_ingest_jobs",
                side_effect=RuntimeError("DB down"),
            ),
        ):
            # raise 안 함
            asyncio.run(main_module._sweep_stale_ingest_jobs())

    def test_main_sweep_hook_skips_when_no_supabase_url(self) -> None:
        """SUPABASE_URL 미설정 → sweep_stale_ingest_jobs 호출 자체가 안 일어남."""
        from app import main as main_module

        class _S:
            supabase_url = ""
            stale_ingest_job_hours = 24

        with (
            patch.object(main_module, "logger"),
            patch("app.config.get_settings", return_value=_S()),
            patch(
                "app.services.ingest_job_watchdog.sweep_stale_ingest_jobs"
            ) as mock_sweep,
        ):
            asyncio.run(main_module._sweep_stale_ingest_jobs())
        mock_sweep.assert_not_called()


# ---------------------- config clamp ----------------------


class ConfigClampTest(unittest.TestCase):
    def _read_hours(self, env_value: str | None) -> int:
        import os

        from app.config import get_settings

        get_settings.cache_clear()
        try:
            if env_value is None:
                os.environ.pop("JETRAG_STALE_INGEST_JOB_HOURS", None)
            else:
                os.environ["JETRAG_STALE_INGEST_JOB_HOURS"] = env_value
            return get_settings().stale_ingest_job_hours
        finally:
            os.environ.pop("JETRAG_STALE_INGEST_JOB_HOURS", None)
            get_settings.cache_clear()

    def test_config_clamps_threshold(self) -> None:
        self.assertEqual(self._read_hours("0"), 1)
        self.assertEqual(self._read_hours("-5"), 1)
        self.assertEqual(self._read_hours("999"), 168)
        self.assertEqual(self._read_hours("abc"), 24)
        self.assertEqual(self._read_hours(None), 24)
        self.assertEqual(self._read_hours("48"), 48)


if __name__ == "__main__":
    unittest.main()
