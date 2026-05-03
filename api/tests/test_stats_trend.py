"""W16 Day 2 — `/stats/trend` endpoint 단위 테스트.

검증 포인트:
    - metric=search: RPC 결과 → p50_ms / p95_ms / fallback_count 매핑
    - metric=vision: RPC 결과 → success_count / quota_exhausted_count 매핑
    - RPC raise (마이그레이션 미적용) → graceful: error_code='migrations_pending'
    - RPC 빈 배열 → buckets=[], error_code=None

stdlib unittest + mock only. Supabase env 없이도 실행됨.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch


def _client_with_rpc_rows(rows: list[dict]) -> MagicMock:
    """Supabase client mock — RPC 결과만 컨트롤."""
    client = MagicMock()
    rpc_resp = MagicMock()
    rpc_resp.data = rows
    rpc_call = MagicMock()
    rpc_call.execute.return_value = rpc_resp
    client.rpc.return_value = rpc_call
    return client


def _client_with_rpc_raise(exc: Exception) -> MagicMock:
    """Supabase client mock — RPC 호출 시 raise (마이그레이션 미적용 시뮬)."""
    client = MagicMock()
    rpc_call = MagicMock()
    rpc_call.execute.side_effect = exc
    client.rpc.return_value = rpc_call
    return client


class StatsTrendSearchTest(unittest.TestCase):
    """metric=search 정상 경로 — RPC row → p50/p95/fallback 매핑."""

    def test_search_metric_returns_buckets(self) -> None:
        from app.routers import stats as stats_module

        rpc_rows = [
            {
                "bucket_start": "2026-05-03T00:00:00+00:00",
                "sample_count": 3,
                "p50_ms": 150,
                "p95_ms": 280,
                "fallback_count": 1,
            },
            {
                "bucket_start": "2026-05-03T06:00:00+00:00",
                "sample_count": 0,
                "p50_ms": 0,
                "p95_ms": 0,
                "fallback_count": 0,
            },
        ]
        client_mock = _client_with_rpc_rows(rpc_rows)

        with patch.object(
            stats_module, "get_supabase_client", return_value=client_mock
        ):
            resp = stats_module.stats_trend(range="7d", mode="hybrid", metric="search")

        # RPC 호출 시 인자 검증 — get_search_metrics_trend(range_label, mode_label)
        client_mock.rpc.assert_called_once_with(
            "get_search_metrics_trend",
            {"range_label": "7d", "mode_label": "hybrid"},
        )

        self.assertEqual(resp.metric, "search")
        self.assertEqual(resp.range, "7d")
        self.assertEqual(resp.mode, "hybrid")
        self.assertIsNone(resp.error_code)
        self.assertEqual(len(resp.buckets), 2)

        # 첫 bucket — sample 있음
        b0 = resp.buckets[0]
        self.assertEqual(b0.sample_count, 3)
        self.assertEqual(b0.p50_ms, 150)
        self.assertEqual(b0.p95_ms, 280)
        self.assertEqual(b0.fallback_count, 1)
        # search metric 응답에는 vision 필드 미사용
        self.assertIsNone(b0.success_count)
        self.assertIsNone(b0.quota_exhausted_count)

        # 두번째 bucket — 빈 bucket (zero-fill)
        b1 = resp.buckets[1]
        self.assertEqual(b1.sample_count, 0)
        self.assertEqual(b1.p50_ms, 0)


class StatsTrendVisionTest(unittest.TestCase):
    """metric=vision — RPC row → success_count / quota_exhausted_count 매핑."""

    def test_vision_metric_returns_buckets(self) -> None:
        from app.routers import stats as stats_module

        rpc_rows = [
            {
                "bucket_start": "2026-05-03T00:00:00+00:00",
                "sample_count": 5,
                "success_count": 4,
                "quota_exhausted_count": 1,
            },
        ]
        client_mock = _client_with_rpc_rows(rpc_rows)

        with patch.object(
            stats_module, "get_supabase_client", return_value=client_mock
        ):
            resp = stats_module.stats_trend(range="24h", mode="all", metric="vision")

        # vision RPC 는 mode 인자 미전달
        client_mock.rpc.assert_called_once_with(
            "get_vision_usage_trend",
            {"range_label": "24h"},
        )

        self.assertEqual(resp.metric, "vision")
        self.assertEqual(resp.range, "24h")
        # vision 응답의 mode 는 None — frontend 가 mode 토글 비활성
        self.assertIsNone(resp.mode)
        self.assertIsNone(resp.error_code)
        self.assertEqual(len(resp.buckets), 1)

        b0 = resp.buckets[0]
        self.assertEqual(b0.sample_count, 5)
        self.assertEqual(b0.success_count, 4)
        self.assertEqual(b0.quota_exhausted_count, 1)
        # vision 응답에는 search 필드 미사용
        self.assertIsNone(b0.p50_ms)
        self.assertIsNone(b0.p95_ms)
        self.assertIsNone(b0.fallback_count)


class StatsTrendGracefulTest(unittest.TestCase):
    """마이그레이션 미적용 graceful — RPC raise 시 error_code='migrations_pending'."""

    def test_rpc_failure_returns_migrations_pending(self) -> None:
        from app.routers import stats as stats_module

        client_mock = _client_with_rpc_raise(
            RuntimeError("function get_search_metrics_trend does not exist")
        )

        with patch.object(
            stats_module, "get_supabase_client", return_value=client_mock
        ):
            resp = stats_module.stats_trend(range="7d", mode="all", metric="search")

        self.assertEqual(resp.error_code, "migrations_pending")
        self.assertEqual(resp.buckets, [])
        # 응답 메타는 정상 — frontend 가 안내 카드 분기
        self.assertEqual(resp.metric, "search")
        self.assertEqual(resp.range, "7d")
        self.assertEqual(resp.mode, "all")


class StatsTrendEmptyTest(unittest.TestCase):
    """RPC 가 빈 배열 반환 시 정상 응답 (error_code=None, buckets=[])."""

    def test_empty_rpc_returns_empty_buckets(self) -> None:
        from app.routers import stats as stats_module

        client_mock = _client_with_rpc_rows([])

        with patch.object(
            stats_module, "get_supabase_client", return_value=client_mock
        ):
            resp = stats_module.stats_trend(range="30d", mode="dense", metric="search")

        self.assertIsNone(resp.error_code)
        self.assertEqual(resp.buckets, [])


if __name__ == "__main__":
    unittest.main()
