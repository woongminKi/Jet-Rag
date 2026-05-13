"""S1 D3 ship — `/admin/queries/stats` endpoint 단위 테스트.

검증 포인트:
- 정상 경로: search_metrics_log row → daily/distribution/failed_samples 매핑
- 마이그 006 미적용 (DB raise) → graceful: error_code='migrations_pending'
- 빈 결과 (row 0건) → daily 는 days 일 수 만큼 0 row, distribution 는 9 키 모두 0
- 실패 분류 — fallback_reason 우선, fused == 0 → no_hits
- 실패 샘플 cap 10 — recorded_at desc 정렬 입력 가정

stdlib unittest + mock only. Supabase env 없이도 실행됨.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch


def _client_with_rows(rows: list[dict]) -> MagicMock:
    """Supabase client mock — search_metrics_log SELECT 결과만 컨트롤."""
    client = MagicMock()
    table = MagicMock()
    select = MagicMock()
    gte = MagicMock()
    order = MagicMock()
    resp = MagicMock()
    resp.data = rows
    order.execute.return_value = resp
    gte.order.return_value = order
    select.gte.return_value = gte
    table.select.return_value = select
    client.table.return_value = table
    return client


def _client_raise(exc: Exception) -> MagicMock:
    """Supabase client mock — table().execute() raise (마이그 미적용 시뮬)."""
    client = MagicMock()
    client.table.side_effect = exc
    return client


def _row(
    *,
    recorded_at: str,
    query_text: str,
    fused: int,
    took_ms: int = 200,
    fallback_reason: str | None = None,
) -> dict:
    return {
        "recorded_at": recorded_at,
        "took_ms": took_ms,
        "fused": fused,
        "fallback_reason": fallback_reason,
        "query_text": query_text,
    }


class AdminQueriesStatsHappyPathTest(unittest.TestCase):
    """정상 경로 — row 매핑 검증."""

    def test_basic_mapping(self) -> None:
        from app.routers import admin as admin_module
        from app.routers.admin import KST

        # 2026-05-14 P3 fix — `now_utc` 기반 상대 시각은 KST 자정 직후 (00:00~00:29)
        # 환경에서 row 시각이 KST 자정 경계를 넘어 yesterday bucket 으로 분리되는
        # 회귀가 발생. 모든 row 를 KST today 정오 (UTC 03:00) 기준으로 고정 → timezone
        # 경계 무관 deterministic. (recorded_at 은 UTC 로 저장되므로 ISO+00:00 사용.)
        base_kst_noon = datetime.now(KST).replace(
            hour=12, minute=0, second=0, microsecond=0,
        )
        base_utc = base_kst_noon.astimezone(timezone.utc)
        rows = [
            _row(
                recorded_at=base_utc.isoformat(),
                query_text="휠 사이즈 표 어디",  # table_lookup
                fused=5,
            ),
            _row(
                recorded_at=(base_utc - timedelta(minutes=30)).isoformat(),
                query_text="환경 인증 절차",  # exact_fact (negative 키워드 없음)
                fused=0,  # → no_hits 실패
                fallback_reason=None,
            ),
        ]
        client = _client_with_rows(rows)
        with patch.object(admin_module, "get_supabase_client", return_value=client):
            resp = admin_module.admin_queries_stats(range="7d")

        self.assertEqual(resp.range, "7d")
        self.assertEqual(resp.total_queries, 2)
        # 성공 1 / 실패 1 → 0.5
        self.assertEqual(resp.success_rate, 0.5)
        # daily — 7일치 row, 마지막은 오늘 (count=2).
        self.assertEqual(len(resp.daily), 7)
        self.assertEqual(resp.daily[-1].count, 2)
        self.assertEqual(resp.daily[-1].success_count, 1)
        self.assertEqual(resp.daily[-1].fail_count, 1)
        # distribution — 9 라벨 모두 노출, table_lookup 1, 그 외 1 (exact_fact 추정).
        self.assertEqual(set(resp.query_type_distribution.keys()) | {"out_of_scope"},
                         set(resp.query_type_distribution.keys()))
        self.assertEqual(resp.query_type_distribution["table_lookup"], 1)
        # 실패 샘플 1건 (no_hits)
        self.assertEqual(len(resp.failed_samples), 1)
        self.assertEqual(resp.failed_samples[0].reason, "no_hits")
        self.assertIn("환경", resp.failed_samples[0].query)
        # avg_latency_ms 200 (단일 평균)
        self.assertEqual(resp.avg_latency_ms, 200)
        self.assertIsNone(resp.error_code)

    def test_empty_rows_zero_fill(self) -> None:
        """row 0건 — daily 7 row 모두 0, distribution 9 키 모두 0, success_rate=None."""
        from app.routers import admin as admin_module

        client = _client_with_rows([])
        with patch.object(admin_module, "get_supabase_client", return_value=client):
            resp = admin_module.admin_queries_stats(range="7d")

        self.assertEqual(resp.total_queries, 0)
        self.assertIsNone(resp.success_rate)
        self.assertIsNone(resp.avg_latency_ms)
        self.assertEqual(len(resp.daily), 7)
        for bucket in resp.daily:
            self.assertEqual(bucket.count, 0)
            self.assertEqual(bucket.success_count, 0)
            self.assertEqual(bucket.fail_count, 0)
        # distribution — 9 키 모두 0
        self.assertEqual(len(resp.query_type_distribution), 9)
        self.assertTrue(all(v == 0 for v in resp.query_type_distribution.values()))
        self.assertEqual(resp.failed_samples, [])
        self.assertIsNone(resp.error_code)

    def test_range_30d(self) -> None:
        """range=30d → daily 30 row."""
        from app.routers import admin as admin_module

        client = _client_with_rows([])
        with patch.object(admin_module, "get_supabase_client", return_value=client):
            resp = admin_module.admin_queries_stats(range="30d")

        self.assertEqual(resp.range, "30d")
        self.assertEqual(len(resp.daily), 30)


class FailureClassificationTest(unittest.TestCase):
    """실패 분류 룰 — fallback_reason 우선, fused==0 → no_hits."""

    def test_permanent_4xx(self) -> None:
        from app.routers import admin as admin_module

        rows = [
            _row(
                recorded_at=datetime.now(timezone.utc).isoformat(),
                query_text="잘못된 query",
                fused=0,
                fallback_reason="permanent_4xx",
            ),
        ]
        client = _client_with_rows(rows)
        with patch.object(admin_module, "get_supabase_client", return_value=client):
            resp = admin_module.admin_queries_stats(range="7d")
        self.assertEqual(len(resp.failed_samples), 1)
        self.assertEqual(resp.failed_samples[0].reason, "permanent_4xx")

    def test_transient_5xx(self) -> None:
        from app.routers import admin as admin_module

        rows = [
            _row(
                recorded_at=datetime.now(timezone.utc).isoformat(),
                query_text="HF 5xx 시뮬",
                fused=3,  # fallback 진입했지만 sparse-only 로 hits 있음
                fallback_reason="transient_5xx",
            ),
        ]
        client = _client_with_rows(rows)
        with patch.object(admin_module, "get_supabase_client", return_value=client):
            resp = admin_module.admin_queries_stats(range="7d")
        self.assertEqual(resp.failed_samples[0].reason, "transient_5xx")

    def test_no_hits(self) -> None:
        """fused == 0 + fallback_reason None → no_hits."""
        from app.routers import admin as admin_module

        rows = [
            _row(
                recorded_at=datetime.now(timezone.utc).isoformat(),
                query_text="문서에 없는 query",
                fused=0,
            ),
        ]
        client = _client_with_rows(rows)
        with patch.object(admin_module, "get_supabase_client", return_value=client):
            resp = admin_module.admin_queries_stats(range="7d")
        self.assertEqual(resp.failed_samples[0].reason, "no_hits")

    def test_failed_samples_cap_10(self) -> None:
        """실패가 15건이라도 10건만 노출."""
        from app.routers import admin as admin_module

        now_utc = datetime.now(timezone.utc)
        rows = [
            _row(
                recorded_at=(now_utc - timedelta(minutes=i)).isoformat(),
                query_text=f"실패 query {i}",
                fused=0,
            )
            for i in range(15)
        ]
        client = _client_with_rows(rows)
        with patch.object(admin_module, "get_supabase_client", return_value=client):
            resp = admin_module.admin_queries_stats(range="7d")
        self.assertEqual(len(resp.failed_samples), 10)
        self.assertEqual(resp.total_queries, 15)


class MigrationPendingTest(unittest.TestCase):
    """DB raise (마이그 006 미적용) → graceful 응답."""

    def test_db_raise_graceful(self) -> None:
        from app.routers import admin as admin_module

        client = _client_raise(RuntimeError("relation search_metrics_log does not exist"))
        with patch.object(admin_module, "get_supabase_client", return_value=client):
            resp = admin_module.admin_queries_stats(range="7d")

        self.assertEqual(resp.error_code, "migrations_pending")
        self.assertEqual(resp.daily, [])
        self.assertEqual(resp.query_type_distribution, {})
        self.assertEqual(resp.failed_samples, [])
        self.assertEqual(resp.total_queries, 0)
        self.assertIsNone(resp.success_rate)
        self.assertIsNone(resp.avg_latency_ms)


if __name__ == "__main__":
    unittest.main()
