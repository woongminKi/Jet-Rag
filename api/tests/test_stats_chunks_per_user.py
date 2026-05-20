"""D2 P1#2 — stats chunks_total 전역 누출 차단 (plan §4 / §8).

검증:
- `_compute_chunks_stats_via_rpc` — RPC 정상 응답 → ChunksStats schema 동일
- RPC 예외(마이그 미적용) → graceful 빈 통계
- RPC 빈 응답 → 빈 통계
- 다른 user_id 호출 시 다른 인자 전달 검증

전략: supabase.rpc(name, args) → execute() chain mock. ChunksStats schema 호환성은
응답 필드 (total/effective/filtered_breakdown/filtered_ratio) 직접 비교.

실행: `python -m unittest tests.test_stats_chunks_per_user`
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from app.routers.stats import (
    ChunksStats,
    _compute_chunks_stats_via_rpc,
)


def _make_supabase_rpc_mock(*, data=None, raise_exc: Exception | None = None) -> MagicMock:
    """supabase.rpc(name, args).execute() 체인을 흉내내는 mock.

    data 가 주어지면 .execute() 가 그 data 를 가진 응답 반환.
    raise_exc 가 주어지면 .execute() 가 예외 raise (RPC 미적용 시나리오).
    """
    fake = MagicMock()
    rpc_call = MagicMock()
    if raise_exc is not None:
        rpc_call.execute.side_effect = raise_exc
    else:
        rpc_call.execute.return_value = MagicMock(data=data)
    fake.rpc.return_value = rpc_call
    return fake


class ComputeChunksStatsViaRpcTest(unittest.TestCase):
    def test_normal_rpc_response_maps_to_chunks_stats(self) -> None:
        rows = [
            {
                "total": 1000,
                "filtered": 200,
                "breakdown": {"table_noise": 120, "header_footer": 80},
            }
        ]
        fake = _make_supabase_rpc_mock(data=rows)

        total, stats = _compute_chunks_stats_via_rpc(fake, "user-a")

        self.assertEqual(total, 1000)
        self.assertIsInstance(stats, ChunksStats)
        self.assertEqual(stats.total, 1000)
        self.assertEqual(stats.effective, 800)
        self.assertEqual(
            stats.filtered_breakdown,
            {"table_noise": 120, "header_footer": 80},
        )
        self.assertEqual(stats.filtered_ratio, 0.2)
        # RPC 가 정확히 user_id_arg 로 호출됐는지
        fake.rpc.assert_called_once_with(
            "get_chunks_stats_for_user", {"user_id_arg": "user-a"}
        )

    def test_zero_filtered_means_ratio_zero(self) -> None:
        rows = [{"total": 100, "filtered": 0, "breakdown": {}}]
        fake = _make_supabase_rpc_mock(data=rows)

        total, stats = _compute_chunks_stats_via_rpc(fake, "u")
        self.assertEqual(total, 100)
        self.assertEqual(stats.effective, 100)
        self.assertEqual(stats.filtered_breakdown, {})
        self.assertEqual(stats.filtered_ratio, 0.0)

    def test_zero_total_does_not_divide_by_zero(self) -> None:
        rows = [{"total": 0, "filtered": 0, "breakdown": {}}]
        fake = _make_supabase_rpc_mock(data=rows)

        total, stats = _compute_chunks_stats_via_rpc(fake, "u")
        self.assertEqual(total, 0)
        self.assertEqual(stats.effective, 0)
        self.assertEqual(stats.filtered_ratio, 0.0)

    def test_rpc_exception_returns_empty_stats(self) -> None:
        """마이그 019 미적용 환경 — RPC 미존재 예외를 graceful 처리."""
        fake = _make_supabase_rpc_mock(
            raise_exc=RuntimeError("function get_chunks_stats_for_user does not exist")
        )
        total, stats = _compute_chunks_stats_via_rpc(fake, "u")
        self.assertEqual(total, 0)
        self.assertEqual(stats.total, 0)
        self.assertEqual(stats.effective, 0)
        self.assertEqual(stats.filtered_breakdown, {})
        self.assertEqual(stats.filtered_ratio, 0.0)

    def test_empty_response_returns_empty_stats(self) -> None:
        """RPC 가 빈 배열 반환 (예: 일치 row 없음) → 빈 통계."""
        fake = _make_supabase_rpc_mock(data=[])
        total, stats = _compute_chunks_stats_via_rpc(fake, "u")
        self.assertEqual(total, 0)
        self.assertEqual(stats.filtered_breakdown, {})

    def test_different_users_pass_different_args(self) -> None:
        """user_id 격리 — 다른 user 호출 시 다른 RPC 인자가 전달."""
        rows = [{"total": 5, "filtered": 1, "breakdown": {"empty": 1}}]
        fake = _make_supabase_rpc_mock(data=rows)

        _compute_chunks_stats_via_rpc(fake, "alice")
        _compute_chunks_stats_via_rpc(fake, "bob")

        call_args = [call.args for call in fake.rpc.call_args_list]
        self.assertEqual(
            call_args,
            [
                ("get_chunks_stats_for_user", {"user_id_arg": "alice"}),
                ("get_chunks_stats_for_user", {"user_id_arg": "bob"}),
            ],
        )


if __name__ == "__main__":
    unittest.main()
