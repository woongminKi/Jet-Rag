"""W25 D14 Sprint B + E1 1차 ship — 인제스트 ETA 회귀 보호.

cache + 일괄 조회 + fallback + status 분기 + median 계산 검증. 외부 API 비용 0
(supabase mock), 임베딩 본 파이프라인 무영향 (read-only) 검증.

E1 1차 ship (2026-05-07) — stage_progress 분해 + sample<3 None + TTL 단축
- sample <3 → 전체 ETA None (첫 ingest "처음에는 시간 추정이 부정확합니다" 카피)
- extract + unit='pages' → vision_usage_log p95 × 남은 페이지 sub-stage 분해
- TTL 90s — 503 wave 직후 baseline 갱신 빠름
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock


def _mock_supabase_with_logs(rows: list[dict]) -> MagicMock:
    """기존 단일 chain mock — table().select().eq().order().limit().execute() 모두 같은 응답.

    ingest_logs / vision_usage_log 둘 다 같은 rows 반환 (rows 가 latency_ms 없으면
    vision_per_page_ms 는 None — 즉 fallback 30000 사용).
    """
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


def _mock_supabase_dual(
    ingest_rows: list[dict],
    vision_rows: list[dict] | None = None,
) -> MagicMock:
    """E1 1차 ship — ingest_logs 와 vision_usage_log 분리 mock.

    table('ingest_logs') / table('vision_usage_log') 별 다른 응답 반환.
    """
    client = MagicMock()

    def make_chain(rows: list[dict]) -> MagicMock:
        chain = MagicMock()
        resp = MagicMock(); resp.data = rows
        chain.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = resp
        return chain

    ingest_chain = make_chain(ingest_rows)
    vision_chain = make_chain(vision_rows or [])

    def table_side_effect(name: str) -> MagicMock:
        if name == "ingest_logs":
            return ingest_chain
        if name == "vision_usage_log":
            return vision_chain
        raise AssertionError(f"unexpected table: {name}")

    client.table.side_effect = table_side_effect
    return client


def _enough_extract_rows(extract_ms: int = 5000) -> list[dict]:
    """E1-A5 — sample 충분 (>=3) 으로 ETA None 회피용."""
    return [
        {"stage": "extract", "duration_ms": extract_ms} for _ in range(3)
    ]


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

    def test_cold_start_returns_none_for_first_ingest(self) -> None:
        """E1-A5 — ingest_logs 0건 → ETA None (sample <3 정책).

        web 측이 None 받으면 "처음에는 시간 추정이 부정확합니다" 카피 노출.
        """
        from app.ingest.eta import compute_remaining_ms

        client = _mock_supabase_with_logs([])
        result = compute_remaining_ms(client, job_status="queued", current_stage=None)
        self.assertIsNone(result)

    def test_running_with_insufficient_samples_returns_none(self) -> None:
        """E1-A5 — running + sample <3 → None."""
        from app.ingest.eta import compute_remaining_ms

        client = _mock_supabase_with_logs([])
        result = compute_remaining_ms(client, job_status="running", current_stage="embed")
        self.assertIsNone(result)

    def test_median_computed_from_succeeded_logs(self) -> None:
        """ingest_logs 에 extract succeeded 5건 → median 사용 (fallback 무시)."""
        from app.ingest.eta import compute_remaining_ms

        rows = [{"stage": "extract", "duration_ms": ms} for ms in (3000, 4000, 5000, 6000, 7000)]
        client = _mock_supabase_with_logs(rows)
        result = compute_remaining_ms(client, job_status="running", current_stage="extract")
        # extract median = 5000ms. 다른 stage 는 fallback (sample 0건)
        from app.ingest.eta import _FALLBACK_STAGE_MS, STAGE_ORDER

        expected = 5000 + sum(_FALLBACK_STAGE_MS[s] for s in STAGE_ORDER[1:])
        self.assertEqual(result, expected)

    def test_cache_avoids_repeated_db_hits(self) -> None:
        """90s TTL cache — 동일 baseline fetch 후 추가 호출은 cache 히트.

        E1 1차 ship 후 cache 가 ingest_logs + vision_usage_log 2개 fetch 하므로,
        compute_remaining_ms 1회 호출 = table call 2회 (ingest_logs 1 + vision_usage_log 1).
        cache hit 시 추가 table call 0.
        """
        from app.ingest.eta import compute_remaining_ms

        client = _mock_supabase_with_logs(_enough_extract_rows())
        compute_remaining_ms(client, job_status="running", current_stage="embed")
        baseline_calls = client.table.call_count
        # 추가 호출은 cache hit → table call 증가 없음
        compute_remaining_ms(client, job_status="running", current_stage="extract")
        compute_remaining_ms(client, job_status="queued", current_stage=None)
        self.assertEqual(client.table.call_count, baseline_calls)
        # baseline 은 ingest_logs + vision_usage_log = 2회 fetch (cold start 1회만)
        self.assertEqual(baseline_calls, 2)

    def test_unknown_stage_falls_back_to_full_sum(self) -> None:
        """STAGE_ORDER 에 없는 stage 이름 → 전체 합산 fallback (sample 충분 시)."""
        from app.ingest.eta import _FALLBACK_STAGE_MS, STAGE_ORDER, compute_remaining_ms

        client = _mock_supabase_with_logs(_enough_extract_rows())
        result = compute_remaining_ms(client, job_status="running", current_stage="bogus")
        # extract median = 5000, 나머지는 fallback
        expected = 5000 + sum(_FALLBACK_STAGE_MS[s] for s in STAGE_ORDER[1:])
        self.assertEqual(result, expected)

    def test_db_error_returns_none(self) -> None:
        """ingest_logs 쿼리 예외 시 medians 비어있음 → None (fallback 대신 안내).

        E1-A5 정책: sample <3 → None. DB error 도 sample 0 으로 취급.
        """
        from app.ingest.eta import compute_remaining_ms

        client = MagicMock()
        client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.side_effect = RuntimeError("DB down")
        result = compute_remaining_ms(client, job_status="queued", current_stage=None)
        self.assertIsNone(result)

    # ============================================================
    # W25 D14 + E1 1차 ship — stage_progress 분해 회귀
    # ============================================================
    def test_compute_remaining_ms_with_stage_progress_non_pages(self) -> None:
        """stage_progress unit≠'pages' → 기존 비율 분해 (vision sub-stage 비활성).

        chunk 등 sub-step 이 'rows'/'chunks' 인 stage 보호.
        """
        from app.ingest.eta import _FALLBACK_STAGE_MS, STAGE_ORDER, compute_remaining_ms

        client = _mock_supabase_with_logs(_enough_extract_rows())
        result = compute_remaining_ms(
            client,
            job_status="running",
            current_stage="chunk",
            stage_progress={"current": 5, "total": 10, "unit": "rows"},
        )
        # extract 는 이미 끝났으므로 chunk 부터 합산. chunk_remaining = 2000 * 0.5 = 1000
        chunk_remaining = int(_FALLBACK_STAGE_MS["chunk"] * 0.5)
        chunk_idx = STAGE_ORDER.index("chunk")
        later_sum = sum(_FALLBACK_STAGE_MS[s] for s in STAGE_ORDER[chunk_idx + 1 :])
        self.assertEqual(result, chunk_remaining + later_sum)

    def test_compute_remaining_ms_no_progress(self) -> None:
        """stage_progress=None → current 전체 + 이후 stages 합 (기존 동작 호환)."""
        from app.ingest.eta import _FALLBACK_STAGE_MS, STAGE_ORDER, compute_remaining_ms

        client = _mock_supabase_with_logs(_enough_extract_rows())
        result = compute_remaining_ms(
            client,
            job_status="running",
            current_stage="extract",
            stage_progress=None,
        )
        # extract median = 5000, 이후 fallback
        expected = 5000 + sum(_FALLBACK_STAGE_MS[s] for s in STAGE_ORDER[1:])
        self.assertEqual(result, expected)

    def test_compute_remaining_ms_invalid_progress(self) -> None:
        """total<=0 또는 dict 타입 불일치 → fallback (current 전체 사용)."""
        from app.ingest.eta import _FALLBACK_STAGE_MS, STAGE_ORDER, compute_remaining_ms

        client = _mock_supabase_with_logs(_enough_extract_rows())
        full_expected = 5000 + sum(_FALLBACK_STAGE_MS[s] for s in STAGE_ORDER[1:])

        for invalid in (
            {"current": 5, "total": 0, "unit": "pages"},
            {"current": 5, "total": -1, "unit": "pages"},
            {"current": "x", "total": 10, "unit": "pages"},
            {},
        ):
            with self.subTest(progress=invalid):
                # 매 subTest 마다 cache 초기화 (vision fetch fallback 영향 차단)
                from app.ingest.eta import reset_cache

                reset_cache()
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

    # ============================================================
    # E1 1차 ship (2026-05-07) — extract sub-stage 분해 (vision pages)
    # ============================================================
    def test_extract_pages_substage_uses_vision_p95(self) -> None:
        """E1-A1 — extract + unit='pages' → vision_usage_log p95 × 남은 페이지 × 1.2.

        진단 §10.2 case: 15p PDF, 13페이지 완료 시 남은 = 2 × p95(latency) × 1.2.
        """
        from app.ingest.eta import _FALLBACK_STAGE_MS, STAGE_ORDER, compute_remaining_ms

        # vision latency 5건: median=4s, max=8s. p95 ≈ 8s
        vision_rows = [
            {"latency_ms": ms, "success": True}
            for ms in (3000, 4000, 5000, 6000, 8000)
        ]
        ingest_rows = _enough_extract_rows()
        client = _mock_supabase_dual(ingest_rows, vision_rows)

        result = compute_remaining_ms(
            client,
            job_status="running",
            current_stage="extract",
            stage_progress={"current": 13, "total": 15, "unit": "pages"},
        )
        # extract sub-stage = (15-13) × 8000 × 1.2 = 19200
        # 이후 stages = fallback 합
        from app.ingest.eta import _VISION_SWEEP_BUFFER_FACTOR

        expected_extract = int(2 * 8000 * _VISION_SWEEP_BUFFER_FACTOR)
        later_sum = sum(_FALLBACK_STAGE_MS[s] for s in STAGE_ORDER[1:])
        self.assertEqual(result, expected_extract + later_sum)

    def test_extract_pages_substage_uses_fallback_when_no_vision_log(self) -> None:
        """E1-A1 — vision_usage_log 미가용 (D2 미진입) → fallback per-page 사용."""
        from app.ingest.eta import (
            _FALLBACK_STAGE_MS,
            _FALLBACK_VISION_PER_PAGE_MS,
            _VISION_SWEEP_BUFFER_FACTOR,
            STAGE_ORDER,
            compute_remaining_ms,
        )

        # ingest_logs 만 충분, vision_usage_log 빈 (또는 sample <3)
        client = _mock_supabase_dual(_enough_extract_rows(), [])

        result = compute_remaining_ms(
            client,
            job_status="running",
            current_stage="extract",
            stage_progress={"current": 0, "total": 15, "unit": "pages"},
        )
        expected_extract = int(
            15 * _FALLBACK_VISION_PER_PAGE_MS * _VISION_SWEEP_BUFFER_FACTOR
        )
        later_sum = sum(_FALLBACK_STAGE_MS[s] for s in STAGE_ORDER[1:])
        self.assertEqual(result, expected_extract + later_sum)

    def test_extract_pages_substage_full_done_only_later_stages(self) -> None:
        """E1-A1 — current==total → vision sub-stage 0 + 이후 stages 합."""
        from app.ingest.eta import _FALLBACK_STAGE_MS, STAGE_ORDER, compute_remaining_ms

        client = _mock_supabase_dual(_enough_extract_rows(), [])

        result = compute_remaining_ms(
            client,
            job_status="running",
            current_stage="extract",
            stage_progress={"current": 15, "total": 15, "unit": "pages"},
        )
        # 0 + 이후 stages
        expected = sum(_FALLBACK_STAGE_MS[s] for s in STAGE_ORDER[1:])
        self.assertEqual(result, expected)

    def test_diagnostic_case_ratio_within_target(self) -> None:
        """E1 진단 §10 case — 표시/실측 ratio 0.7~1.3 진입 검증.

        측정 PDF: 15p, 실측 957.9s, T+0 ETA = 224.5s (ratio 0.23).
        E1-A1 적용 후 ETA 가 합리적 추정에 도달하는지 시뮬레이션.

        가정: vision p95 ≈ 50000 (50s/page, 503 retry 흡수). T+0 (current=0/15).
        기대 ETA ≈ 15 × 50000 × 1.2 + 후속 stages = 900,000 + ~50,000 = ~950s
        실측 957.9s 대비 ratio ≈ 1.0 (목표 0.7~1.3 진입).
        """
        from app.ingest.eta import (
            _FALLBACK_STAGE_MS,
            _VISION_SWEEP_BUFFER_FACTOR,
            STAGE_ORDER,
            compute_remaining_ms,
        )

        # 진단 §10 가정 baseline: vision p95 = 50000ms (page 12/14 503 retry burst)
        vision_rows = [
            {"latency_ms": ms, "success": True}
            for ms in (5000, 8000, 10000, 30000, 50000)
        ]
        # extract median 도 충분
        ingest_rows = [
            {"stage": "extract", "duration_ms": 244756} for _ in range(3)
        ]
        client = _mock_supabase_dual(ingest_rows, vision_rows)

        result = compute_remaining_ms(
            client,
            job_status="running",
            current_stage="extract",
            stage_progress={"current": 0, "total": 15, "unit": "pages"},
        )
        self.assertIsNotNone(result)
        # 실측 957900 ms 대비 ratio
        actual_ms = 957900
        ratio = (result or 0) / actual_ms
        # ratio 0.7~1.3 진입 (목표 DoD)
        self.assertGreaterEqual(
            ratio, 0.7, f"ratio={ratio} < 0.7 (under-estimate, 회귀)"
        )
        self.assertLessEqual(
            ratio, 1.3, f"ratio={ratio} > 1.3 (over-estimate, 회귀)"
        )


if __name__ == "__main__":
    unittest.main()
