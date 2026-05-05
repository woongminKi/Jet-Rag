"""W25 D14 Sprint B — 인제스트 ETA 회귀 보호.

cache + 일괄 조회 + fallback + status 분기 + median 계산 검증. 외부 API 비용 0
(supabase mock), 임베딩 본 파이프라인 무영향 (read-only) 검증.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock


def _mock_supabase_with_logs(rows: list[dict]) -> MagicMock:
    client = MagicMock()
    resp = MagicMock(); resp.data = rows
    chain = (
        client.table.return_value
        .select.return_value
        .eq.return_value
        .order.return_value
        .limit.return_value
    )
    chain.execute.return_value = resp
    return client


class IngestEtaTest(unittest.TestCase):
    def setUp(self) -> None:
        from app.ingest.eta import reset_cache

        reset_cache()

    def test_completed_status_returns_none(self) -> None:
        from app.ingest.eta import compute_remaining_ms

        client = _mock_supabase_with_logs([])
        for status in ("completed", "failed", "cancelled"):
            self.assertIsNone(
                compute_remaining_ms(client, job_status=status, current_stage="embed")
            )
        # ETA 의미 없는 status 는 supabase 호출도 안 함 → 임베딩 무영향 보장
        client.table.assert_not_called()

    def test_cold_start_uses_fallback_for_queued(self) -> None:
        """ingest_logs 0건 → fallback 합산. queued 는 전체 stages."""
        from app.ingest.eta import _FALLBACK_STAGE_MS, STAGE_ORDER, compute_remaining_ms

        client = _mock_supabase_with_logs([])
        result = compute_remaining_ms(client, job_status="queued", current_stage=None)
        expected = sum(_FALLBACK_STAGE_MS[s] for s in STAGE_ORDER)
        self.assertEqual(result, expected)

    def test_running_with_current_stage_sums_remaining_only(self) -> None:
        """running + current_stage='embed' → embed 부터 합산 (보수적, current 포함)."""
        from app.ingest.eta import _FALLBACK_STAGE_MS, STAGE_ORDER, compute_remaining_ms

        client = _mock_supabase_with_logs([])
        result = compute_remaining_ms(client, job_status="running", current_stage="embed")
        embed_idx = STAGE_ORDER.index("embed")
        expected = sum(_FALLBACK_STAGE_MS[s] for s in STAGE_ORDER[embed_idx:])
        self.assertEqual(result, expected)

    def test_median_computed_from_succeeded_logs(self) -> None:
        """ingest_logs 에 stage='extract' succeeded 5건 → median 사용 (fallback 무시)."""
        from app.ingest.eta import compute_remaining_ms

        rows = [{"stage": "extract", "duration_ms": ms} for ms in (3000, 4000, 5000, 6000, 7000)]
        client = _mock_supabase_with_logs(rows)
        result = compute_remaining_ms(client, job_status="running", current_stage="extract")
        # extract median = 5000ms (fallback 5000과 동일하지만 호출 경로 검증)
        self.assertGreaterEqual(result or 0, 5000)
        # 다른 stage 는 fallback (sample 0건)
        from app.ingest.eta import _FALLBACK_STAGE_MS, STAGE_ORDER

        expected = 5000 + sum(_FALLBACK_STAGE_MS[s] for s in STAGE_ORDER[1:])
        self.assertEqual(result, expected)

    def test_cache_avoids_repeated_db_hits(self) -> None:
        """5분 cache — 1회 호출 후 같은 결과 반환, supabase.table 1번만 호출."""
        from app.ingest.eta import compute_remaining_ms

        client = _mock_supabase_with_logs([])
        compute_remaining_ms(client, job_status="running", current_stage="embed")
        compute_remaining_ms(client, job_status="running", current_stage="extract")
        compute_remaining_ms(client, job_status="queued", current_stage=None)
        # 3회 호출했지만 cache 로 인해 supabase.table 은 1회만
        self.assertEqual(client.table.call_count, 1)

    def test_unknown_stage_falls_back_to_full_sum(self) -> None:
        """STAGE_ORDER 에 없는 stage 이름 → 전체 합산 fallback."""
        from app.ingest.eta import _FALLBACK_STAGE_MS, STAGE_ORDER, compute_remaining_ms

        client = _mock_supabase_with_logs([])
        result = compute_remaining_ms(client, job_status="running", current_stage="bogus")
        expected = sum(_FALLBACK_STAGE_MS[s] for s in STAGE_ORDER)
        self.assertEqual(result, expected)

    def test_db_error_falls_back_gracefully(self) -> None:
        """ingest_logs 쿼리 예외 시 fallback 사용 (임베딩 무영향, batch-status 도 500 안 남)."""
        from app.ingest.eta import _FALLBACK_STAGE_MS, STAGE_ORDER, compute_remaining_ms

        client = MagicMock()
        client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.side_effect = RuntimeError("DB down")
        result = compute_remaining_ms(client, job_status="queued", current_stage=None)
        expected = sum(_FALLBACK_STAGE_MS[s] for s in STAGE_ORDER)
        self.assertEqual(result, expected)


if __name__ == "__main__":
    unittest.main()
