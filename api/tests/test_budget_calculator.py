"""S0 D3 (2026-05-07) — budget_calculator 공식 + 누적 부족 fallback 단위 테스트.

검증 포인트
- 충분 sample (n≥30, doc≥5) 시 §7.5 공식 정확도
- 데이터 부족 시 잠정값 + WARN
- 0 row graceful (default fallback)
- doc_id NULL row 분리 집계 (image_parser 단독 호출 시뮬)
- 실패 row 평균 제외
- estimated_cost NULL 평균 제외
- render_markdown sanity (잠정/측정 양쪽)

stdlib unittest only — DB 호출 없음.
"""

from __future__ import annotations

import os
import unittest

# import-time ENV 회피 (config.get_settings 호출용).
os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")
os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")
os.environ.setdefault("SUPABASE_URL", "https://dummy.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "dummy-anon")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "dummy-service")

from app.services.budget_calculator import (  # noqa: E402
    BudgetSampleStats,
    aggregate_rows,
    compute_budget,
    render_markdown,
)


_FALLBACK_DOC = 0.10
_FALLBACK_DAILY = 0.50


def _make_rows(
    *,
    docs: int,
    pages_per_doc: int,
    cost_per_page: float,
    failed: int = 0,
    null_doc: int = 0,
) -> list[dict]:
    """충분 sample 생성 헬퍼."""
    rows: list[dict] = []
    for d in range(docs):
        for p in range(1, pages_per_doc + 1):
            rows.append(
                {
                    "success": True,
                    "estimated_cost": cost_per_page,
                    "doc_id": f"doc-{d}",
                    "page": p,
                }
            )
    for _ in range(failed):
        rows.append(
            {
                "success": False,
                "estimated_cost": None,
                "doc_id": None,
                "page": None,
            }
        )
    for i in range(null_doc):
        rows.append(
            {
                "success": True,
                "estimated_cost": cost_per_page,
                "doc_id": None,
                "page": i + 1,
            }
        )
    return rows


class TestAggregateRows(unittest.TestCase):
    def test_full_sample_yields_correct_stats(self) -> None:
        # 6 docs × 6 pages = 36 rows (>= 30, >= 5 doc)
        rows = _make_rows(docs=6, pages_per_doc=6, cost_per_page=0.005)
        stats = aggregate_rows(rows)
        self.assertEqual(stats.sample_rows, 36)
        self.assertEqual(stats.success_rows, 36)
        self.assertEqual(stats.failed_rows, 0)
        self.assertEqual(stats.unique_docs, 6)
        self.assertAlmostEqual(stats.avg_cost_per_page_usd or 0.0, 0.005, places=6)
        self.assertAlmostEqual(stats.avg_pages_per_doc or 0.0, 6.0, places=4)

    def test_failed_rows_excluded_from_sample(self) -> None:
        rows = _make_rows(docs=2, pages_per_doc=3, cost_per_page=0.01, failed=4)
        stats = aggregate_rows(rows)
        self.assertEqual(stats.sample_rows, 6)  # 실패 4건 제외
        self.assertEqual(stats.failed_rows, 4)
        self.assertEqual(stats.success_rows, 6)

    def test_null_doc_rows_isolated(self) -> None:
        # doc_id NULL = image_parser 단독 호출 (PDF 인제스트 budget 분석 외)
        rows = _make_rows(docs=2, pages_per_doc=2, cost_per_page=0.003, null_doc=3)
        stats = aggregate_rows(rows)
        self.assertEqual(stats.unique_docs, 2)  # NULL 은 doc count 제외
        self.assertEqual(stats.null_doc_rows, 3)
        self.assertEqual(stats.sample_rows, 7)  # cost 평균 sample 에는 포함

    def test_estimated_cost_null_excluded_from_avg(self) -> None:
        rows = [
            {"success": True, "estimated_cost": 0.01, "doc_id": "a", "page": 1},
            {"success": True, "estimated_cost": None, "doc_id": "a", "page": 2},
            {"success": True, "estimated_cost": 0.02, "doc_id": "b", "page": 1},
        ]
        stats = aggregate_rows(rows)
        self.assertEqual(stats.sample_rows, 2)  # NULL 제외
        self.assertEqual(stats.success_rows, 3)
        self.assertAlmostEqual(stats.avg_cost_per_page_usd or 0.0, 0.015, places=6)

    def test_zero_rows_graceful(self) -> None:
        stats = aggregate_rows([])
        self.assertEqual(stats.sample_rows, 0)
        self.assertEqual(stats.unique_docs, 0)
        self.assertIsNone(stats.avg_cost_per_page_usd)
        self.assertIsNone(stats.avg_pages_per_doc)


class TestComputeBudget(unittest.TestCase):
    """master plan §7.5 공식 정확도 + 누적 부족 fallback 검증."""

    def test_sufficient_sample_applies_formula(self) -> None:
        # 충분 sample: 6 doc × 6 page = 36 rows, cost 0.005
        # 기대값: 0.005 × 6 × 0.5 × 1.5 = 0.0225
        rows = _make_rows(docs=6, pages_per_doc=6, cost_per_page=0.005)
        stats = aggregate_rows(rows)
        est = compute_budget(
            stats,
            daily_docs=5,
            krw_per_usd=1380.0,
            fallback_doc_budget_usd=_FALLBACK_DOC,
            fallback_daily_budget_usd=_FALLBACK_DAILY,
        )
        self.assertFalse(est.is_provisional)
        self.assertAlmostEqual(est.doc_budget_usd, 0.0225, places=6)
        # daily = 0.0225 × 5
        self.assertAlmostEqual(est.daily_budget_usd, 0.1125, places=6)
        # KRW 환산
        self.assertAlmostEqual(est.doc_budget_krw, 0.0225 * 1380.0, places=4)

    def test_insufficient_sample_uses_fallback(self) -> None:
        # n=2, doc=1 — 매우 부족
        rows = [
            {"success": True, "estimated_cost": 0.005, "doc_id": "x", "page": 1},
            {"success": True, "estimated_cost": 0.006, "doc_id": "x", "page": 2},
        ]
        stats = aggregate_rows(rows)
        est = compute_budget(
            stats,
            daily_docs=5,
            krw_per_usd=1380.0,
            fallback_doc_budget_usd=_FALLBACK_DOC,
            fallback_daily_budget_usd=_FALLBACK_DAILY,
        )
        self.assertTrue(est.is_provisional)
        self.assertEqual(est.doc_budget_usd, _FALLBACK_DOC)
        self.assertEqual(est.daily_budget_usd, _FALLBACK_DAILY)
        # WARN 메시지 sample 부족 + doc 부족 + 잠정값 안내 포함
        joined = " ".join(est.warnings)
        self.assertIn("sample", joined)
        self.assertIn("unique doc", joined)
        self.assertIn("잠정값", joined)

    def test_zero_rows_uses_fallback(self) -> None:
        stats = aggregate_rows([])
        est = compute_budget(
            stats,
            daily_docs=5,
            krw_per_usd=1380.0,
            fallback_doc_budget_usd=_FALLBACK_DOC,
            fallback_daily_budget_usd=_FALLBACK_DAILY,
        )
        self.assertTrue(est.is_provisional)
        self.assertEqual(est.doc_budget_usd, _FALLBACK_DOC)
        self.assertEqual(est.daily_budget_usd, _FALLBACK_DAILY)

    def test_failed_rows_warn_present(self) -> None:
        rows = _make_rows(docs=6, pages_per_doc=6, cost_per_page=0.005, failed=2)
        stats = aggregate_rows(rows)
        est = compute_budget(
            stats,
            daily_docs=5,
            krw_per_usd=1380.0,
            fallback_doc_budget_usd=_FALLBACK_DOC,
            fallback_daily_budget_usd=_FALLBACK_DAILY,
        )
        # 실패 row 가 있어도 충분 sample 이면 측정값 채택
        self.assertFalse(est.is_provisional)
        joined = " ".join(est.warnings)
        self.assertIn("실패 row", joined)


class TestRenderMarkdown(unittest.TestCase):
    """markdown 렌더 sanity — 운영자가 stdout 으로 읽는 형식 회귀 방지."""

    def test_provisional_estimate_has_warning_section(self) -> None:
        stats = aggregate_rows(
            [{"success": True, "estimated_cost": 0.005, "doc_id": "a", "page": 1}]
        )
        est = compute_budget(
            stats,
            daily_docs=5,
            krw_per_usd=1380.0,
            fallback_doc_budget_usd=_FALLBACK_DOC,
            fallback_daily_budget_usd=_FALLBACK_DAILY,
        )
        md = render_markdown(est, lookback_days=7, source_type="pdf_vision_enrich")
        self.assertIn("잠정값", md)
        self.assertIn("## 경고", md)
        self.assertIn("JETRAG_DOC_BUDGET_USD", md)

    def test_measured_estimate_has_no_warning_section(self) -> None:
        rows = _make_rows(docs=6, pages_per_doc=6, cost_per_page=0.005)
        stats = aggregate_rows(rows)
        est = compute_budget(
            stats,
            daily_docs=5,
            krw_per_usd=1380.0,
            fallback_doc_budget_usd=_FALLBACK_DOC,
            fallback_daily_budget_usd=_FALLBACK_DAILY,
        )
        md = render_markdown(est, lookback_days=7, source_type="pdf_vision_enrich")
        self.assertIn("측정값", md)
        # WARN 섹션은 실패 row 0 이고 충분 sample 이라 없어야 함
        self.assertNotIn("## 경고", md)


class TestSettingsBudgetEnv(unittest.TestCase):
    """config.Settings 의 budget 변수 ENV 파싱 — D4 cap 의존성."""

    def setUp(self) -> None:
        self._saved = {
            k: os.environ.pop(k, None)
            for k in (
                "JETRAG_DOC_BUDGET_USD",
                "JETRAG_DAILY_BUDGET_USD",
                "JETRAG_BUDGET_KRW_PER_USD",
            )
        }
        # lru_cache 초기화 — 다른 테스트 누수 회피
        from app.config import get_settings

        get_settings.cache_clear()

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        from app.config import get_settings

        get_settings.cache_clear()

    def test_default_values_when_env_absent(self) -> None:
        from app.config import get_settings

        s = get_settings()
        self.assertAlmostEqual(s.doc_budget_usd, 0.10, places=4)
        self.assertAlmostEqual(s.daily_budget_usd, 0.50, places=4)
        self.assertAlmostEqual(s.budget_krw_per_usd, 1380.0, places=2)

    def test_env_override(self) -> None:
        os.environ["JETRAG_DOC_BUDGET_USD"] = "0.025"
        os.environ["JETRAG_DAILY_BUDGET_USD"] = "0.15"
        os.environ["JETRAG_BUDGET_KRW_PER_USD"] = "1400"
        from app.config import get_settings

        get_settings.cache_clear()
        s = get_settings()
        self.assertAlmostEqual(s.doc_budget_usd, 0.025, places=4)
        self.assertAlmostEqual(s.daily_budget_usd, 0.15, places=4)
        self.assertAlmostEqual(s.budget_krw_per_usd, 1400.0, places=2)

    def test_invalid_env_falls_back_to_default(self) -> None:
        os.environ["JETRAG_DOC_BUDGET_USD"] = "not-a-number"
        os.environ["JETRAG_DAILY_BUDGET_USD"] = "-1"
        from app.config import get_settings

        get_settings.cache_clear()
        s = get_settings()
        self.assertAlmostEqual(s.doc_budget_usd, 0.10, places=4)
        self.assertAlmostEqual(s.daily_budget_usd, 0.50, places=4)


if __name__ == "__main__":
    unittest.main()
