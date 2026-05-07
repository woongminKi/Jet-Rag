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

    # ============================================================
    # E1 1차 ship (2026-05-07) — stage_progress 분해 회귀
    # ============================================================
    def test_compute_remaining_ms_with_stage_progress(self) -> None:
        """stage_progress=13/29 → current stage 의 (1-13/29) + 이후 stages 합."""
        from app.ingest.eta import _FALLBACK_STAGE_MS, STAGE_ORDER, compute_remaining_ms

        client = _mock_supabase_with_logs([])
        result = compute_remaining_ms(
            client,
            job_status="running",
            current_stage="extract",
            stage_progress={"current": 13, "total": 29, "unit": "pages"},
        )
        extract_remaining = int(_FALLBACK_STAGE_MS["extract"] * (1.0 - 13.0 / 29.0))
        later_sum = sum(_FALLBACK_STAGE_MS[s] for s in STAGE_ORDER[1:])
        self.assertEqual(result, extract_remaining + later_sum)

    def test_compute_remaining_ms_progress_full(self) -> None:
        """stage_progress current==total → current stage 0 ms + 이후 stages 합."""
        from app.ingest.eta import _FALLBACK_STAGE_MS, STAGE_ORDER, compute_remaining_ms

        client = _mock_supabase_with_logs([])
        result = compute_remaining_ms(
            client,
            job_status="running",
            current_stage="extract",
            stage_progress={"current": 29, "total": 29, "unit": "pages"},
        )
        # extract 는 0 ms, 이후 stages 만 합산
        expected = sum(_FALLBACK_STAGE_MS[s] for s in STAGE_ORDER[1:])
        self.assertEqual(result, expected)

    def test_compute_remaining_ms_no_progress(self) -> None:
        """stage_progress=None → current 전체 + 이후 stages 합 (기존 동작 호환)."""
        from app.ingest.eta import _FALLBACK_STAGE_MS, STAGE_ORDER, compute_remaining_ms

        client = _mock_supabase_with_logs([])
        result = compute_remaining_ms(
            client,
            job_status="running",
            current_stage="extract",
            stage_progress=None,
        )
        expected = sum(_FALLBACK_STAGE_MS[s] for s in STAGE_ORDER)
        self.assertEqual(result, expected)

    def test_compute_remaining_ms_invalid_progress(self) -> None:
        """total<=0 또는 dict 타입 불일치 → fallback (current 전체 사용)."""
        from app.ingest.eta import _FALLBACK_STAGE_MS, STAGE_ORDER, compute_remaining_ms

        client = _mock_supabase_with_logs([])
        full_expected = sum(_FALLBACK_STAGE_MS[s] for s in STAGE_ORDER)

        for invalid in (
            {"current": 5, "total": 0, "unit": "pages"},
            {"current": 5, "total": -1, "unit": "pages"},
            {"current": "x", "total": 10, "unit": "pages"},
            {},
        ):
            with self.subTest(progress=invalid):
                result = compute_remaining_ms(
                    client,
                    job_status="running",
                    current_stage="extract",
                    stage_progress=invalid,
                )
                self.assertEqual(result, full_expected)

    def test_chunk_filter_in_stage_order(self) -> None:
        """STAGE_ORDER 에 chunk_filter 가 포함되어야 함 (web 측 정합 + ETA 합산)."""
        from app.ingest.eta import _FALLBACK_STAGE_MS, STAGE_ORDER

        self.assertIn("chunk_filter", STAGE_ORDER)
        self.assertIn("chunk_filter", _FALLBACK_STAGE_MS)
        # chunk 다음, content_gate 이전 위치
        self.assertEqual(STAGE_ORDER.index("chunk_filter"), STAGE_ORDER.index("chunk") + 1)
        self.assertEqual(
            STAGE_ORDER.index("content_gate"), STAGE_ORDER.index("chunk_filter") + 1
        )


if __name__ == "__main__":
    unittest.main()
