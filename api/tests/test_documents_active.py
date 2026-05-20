"""W25 D14 — GET /documents/active 회귀 보호.

설계: 같은 doc 의 historical row 가 여러 개 있을 때 (예: 어제 failed → 오늘 completed)
status filter 를 SQL 단에 두면 newer completed 를 못 보고 stale 로 indicator 노출됨.
→ 모든 status SELECT → doc-latest 추출 → latest 의 status 가 active 인 doc 만 응답.

mock supabase chain 외부 의존성 0 검증.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch


def _mock_supabase_with(jobs_rows: list[dict], docs_rows: list[dict]) -> MagicMock:
    """ingest_jobs 와 documents 두 테이블 chain 을 응답별로 분기.

    ingest_jobs chain (W25 D14 doc-latest 우선): table.select.gte.order.execute
    documents chain: table.select.in_.execute
    """
    client = MagicMock()
    jobs_resp = MagicMock(); jobs_resp.data = jobs_rows
    docs_resp = MagicMock(); docs_resp.data = docs_rows

    def _table_dispatch(name: str):
        m = MagicMock()
        if name == "ingest_jobs":
            m.select.return_value.gte.return_value.order.return_value.execute.return_value = jobs_resp
        else:  # documents — D1 P1#1 부터 .eq("user_id") 한 단계 추가
            m.select.return_value.in_.return_value.eq.return_value.execute.return_value = docs_resp
        return m

    client.table.side_effect = _table_dispatch
    return client


class DocumentsActiveTest(unittest.TestCase):
    def _call(self, client_mock: MagicMock, hours: int = 24):
        from app.routers import documents as docs_module

        with patch.object(docs_module, "get_supabase_client", return_value=client_mock):
            return docs_module.list_active_documents(hours=hours)

    def test_returns_latest_job_per_doc(self) -> None:
        """같은 doc_id 에 historical 2건 모두 active → latest 1건만."""
        jobs_rows = [
            {  # latest (queued_at desc)
                "id": "job-2", "doc_id": "doc-1", "status": "running",
                "current_stage": "embed", "attempts": 1, "error_msg": None,
                "queued_at": "2026-05-05T08:00:00Z",
                "started_at": "2026-05-05T08:00:01Z", "finished_at": None,
            },
            {  # older
                "id": "job-1", "doc_id": "doc-1", "status": "failed",
                "current_stage": "extract", "attempts": 3, "error_msg": "x",
                "queued_at": "2026-05-04T08:00:00Z",
                "started_at": "2026-05-04T08:00:01Z", "finished_at": "2026-05-04T08:00:30Z",
            },
        ]
        docs_rows = [{"id": "doc-1", "title": "report.pdf", "size_bytes": 12345}]
        resp = self._call(_mock_supabase_with(jobs_rows, docs_rows))

        self.assertEqual(len(resp.items), 1)
        item = resp.items[0]
        self.assertEqual(item.job.job_id, "job-2")
        self.assertEqual(item.job.status, "running")

    def test_excludes_doc_when_latest_is_completed(self) -> None:
        """W25 D14 — 어제 failed + 오늘 completed → latest 가 completed 면 자연 제외.

        sample-report 처럼 한 번 실패 후 reingest 로 성공한 doc 가 indicator 에
        stale 로 남는 회귀 차단 케이스.
        """
        jobs_rows = [
            {  # latest — completed (오늘)
                "id": "job-completed", "doc_id": "doc-1", "status": "completed",
                "current_stage": "done", "attempts": 1, "error_msg": None,
                "queued_at": "2026-05-05T10:00:00Z",
                "started_at": "2026-05-05T10:00:01Z", "finished_at": "2026-05-05T10:30:00Z",
            },
            {  # older — failed (어제)
                "id": "job-failed", "doc_id": "doc-1", "status": "failed",
                "current_stage": "extract", "attempts": 1, "error_msg": "vision timeout",
                "queued_at": "2026-05-04T15:41:48Z",
                "started_at": "2026-05-04T15:41:50Z", "finished_at": "2026-05-04T16:20:00Z",
            },
        ]
        docs_rows = [{"id": "doc-1", "title": "report.pdf", "size_bytes": 12345}]
        resp = self._call(_mock_supabase_with(jobs_rows, docs_rows))

        # latest 가 completed → active 응답에서 자연 제외
        self.assertEqual(resp.items, [])

    def test_includes_doc_when_latest_is_failed_after_completed(self) -> None:
        """반대 케이스: 어제 completed + 오늘 failed (예: reingest 실패) → latest=failed 노출."""
        jobs_rows = [
            {  # latest — failed (오늘)
                "id": "job-failed-today", "doc_id": "doc-1", "status": "failed",
                "current_stage": "extract", "attempts": 1, "error_msg": "x",
                "queued_at": "2026-05-05T10:00:00Z",
                "started_at": "2026-05-05T10:00:01Z", "finished_at": "2026-05-05T10:05:00Z",
            },
            {  # older — completed (어제)
                "id": "job-old-success", "doc_id": "doc-1", "status": "completed",
                "current_stage": "done", "attempts": 1, "error_msg": None,
                "queued_at": "2026-05-04T08:00:00Z",
                "started_at": "2026-05-04T08:00:01Z", "finished_at": "2026-05-04T08:30:00Z",
            },
        ]
        docs_rows = [{"id": "doc-1", "title": "report.pdf", "size_bytes": 12345}]
        resp = self._call(_mock_supabase_with(jobs_rows, docs_rows))

        self.assertEqual(len(resp.items), 1)
        self.assertEqual(resp.items[0].job.job_id, "job-failed-today")
        self.assertEqual(resp.items[0].job.status, "failed")

    def test_skips_doc_when_documents_row_missing(self) -> None:
        jobs_rows = [
            {
                "id": "job-1", "doc_id": "doc-orphan", "status": "queued",
                "current_stage": None, "attempts": 0, "error_msg": None,
                "queued_at": "2026-05-05T08:00:00Z",
                "started_at": None, "finished_at": None,
            },
        ]
        resp = self._call(_mock_supabase_with(jobs_rows, []))
        self.assertEqual(resp.items, [])

    def test_empty_result_returns_empty_items(self) -> None:
        resp = self._call(_mock_supabase_with([], []))
        self.assertEqual(resp.items, [])


class StageProgressSelectGracefulTest(unittest.TestCase):
    """W25 D14 — 마이그레이션 010 미적용 환경에서 SELECT 첫 실패 시 컬럼 빼고 재시도."""

    def setUp(self) -> None:
        from app.routers.documents import reset_stage_progress_select_enabled

        reset_stage_progress_select_enabled()

    def test_first_query_failure_disables_column_and_retries(self) -> None:
        from app.routers import documents as docs_module

        client = MagicMock()
        empty_resp = MagicMock(); empty_resp.data = []
        api_err = RuntimeError(
            "{'message': 'column ingest_jobs.stage_progress does not exist', 'code': '42703'}"
        )
        chain = (
            client.table.return_value
            .select.return_value
            .gte.return_value
            .order.return_value
        )
        chain.execute.side_effect = [api_err, empty_resp]

        with patch.object(docs_module, "get_supabase_client", return_value=client):
            resp = docs_module.list_active_documents(hours=24)

        self.assertEqual(resp.items, [])
        self.assertEqual(chain.execute.call_count, 2)
        self.assertFalse(docs_module._stage_progress_select_enabled)

    def test_subsequent_calls_skip_stage_progress_column(self) -> None:
        from app.routers import documents as docs_module

        docs_module._stage_progress_select_enabled = False

        client = MagicMock()
        empty_resp = MagicMock(); empty_resp.data = []
        client.table.return_value.select.return_value.gte.return_value.order.return_value.execute.return_value = empty_resp

        with patch.object(docs_module, "get_supabase_client", return_value=client):
            resp = docs_module.list_active_documents(hours=24)
        self.assertEqual(resp.items, [])
        select_arg = client.table.return_value.select.call_args.args[0]
        self.assertNotIn("stage_progress", select_arg)


class DocumentsActiveHoursValidationTest(unittest.TestCase):
    def test_hours_default_is_24(self) -> None:
        import inspect

        from app.routers.documents import list_active_documents

        sig = inspect.signature(list_active_documents)
        hours_default = sig.parameters["hours"].default
        self.assertEqual(getattr(hours_default, "default", hours_default), 24)


if __name__ == "__main__":
    unittest.main()
