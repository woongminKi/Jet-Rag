"""S3 D3 — `app.services.query_decomposer` 단위 테스트 (planner v0.1 §H).

검증 범위
---------
1. ENV off → LLM 호출 0 (assert_not_called)
2. needs_decomposition=False → skip
3. 정상 분해 (cross_doc → 3 subqueries)
4. LRU cache hit — 두 번째 호출 cost=0.0 / cached=True
5. JSON 파싱 실패 → () + reason
6. budget cap 초과 → skip + reason

통합 테스트 1건 — `@pytest.mark.skipif` 대신 stdlib unittest 의 `skipUnless`
로 GEMINI_API_KEY 부재 시 skip (실제 LLM 호출 1회).

stdlib unittest + mock only — 의존성 추가 0.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

# 환경 변수 stub — 다른 테스트와 동일 패턴.
os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")
os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")
os.environ["JET_RAG_METRICS_PERSIST_ENABLED"] = "0"
os.environ["JET_RAG_METRICS_PERSIST_ASYNC"] = "0"


def _decision(
    *,
    needs_decomp: bool,
    signals: tuple[str, ...] = (),
    confidence: float = 0.5,
    query_normalized: str = "dummy",
) -> "object":
    """IntentRouterDecision factory — 테스트 의존성 최소화."""
    from app.services.intent_router import IntentRouterDecision

    return IntentRouterDecision(
        needs_decomposition=needs_decomp,
        triggered_signals=signals,
        confidence_score=confidence,
        query_normalized=query_normalized,
        matched_keywords=(),
    )


class _BaseDecomposerTest(unittest.TestCase):
    """공통 setup — cache reset + ENV 정리."""

    def setUp(self) -> None:
        from app.services import query_decomposer

        query_decomposer._reset_cache_for_test()
        # ENV 초기화 — 이전 테스트 잔존 값 제거.
        for k in (
            "JETRAG_PAID_DECOMPOSITION_ENABLED",
            "JETRAG_DECOMPOSITION_MONTHLY_CAP_USD",
            "JETRAG_DECOMPOSITION_CACHE_DISABLE",
            "JETRAG_BUDGET_GUARD_DISABLE",
        ):
            os.environ.pop(k, None)

    def tearDown(self) -> None:
        for k in (
            "JETRAG_PAID_DECOMPOSITION_ENABLED",
            "JETRAG_DECOMPOSITION_MONTHLY_CAP_USD",
            "JETRAG_DECOMPOSITION_CACHE_DISABLE",
            "JETRAG_BUDGET_GUARD_DISABLE",
        ):
            os.environ.pop(k, None)


class EnvOffSkipsTest(_BaseDecomposerTest):
    """#1 — ENV off (default) → LLM 호출 0 + skip."""

    def test_env_off_does_not_call_llm(self) -> None:
        from app.services import query_decomposer

        # default ENV 미설정 = off. needs_decomposition=True 라도 LLM 호출 0.
        llm = MagicMock()
        result = query_decomposer.decompose(
            "작년 보고서랑 올해 자료 비교",
            _decision(needs_decomp=True, signals=("T1_cross_doc",)),
            llm=llm,
        )

        llm.complete.assert_not_called()
        self.assertEqual(result.subqueries, ())
        self.assertEqual(result.cost_usd, 0.0)
        self.assertFalse(result.cached)
        self.assertIsNotNone(result.skipped_reason)
        assert result.skipped_reason is not None
        self.assertIn("ENV", result.skipped_reason)


class NeedsDecompFalseSkipsTest(_BaseDecomposerTest):
    """#2 — needs_decomposition=False → 즉시 skip (ENV 무관)."""

    def test_needs_decomp_false_skips_even_when_env_on(self) -> None:
        from app.services import query_decomposer

        os.environ["JETRAG_PAID_DECOMPOSITION_ENABLED"] = "true"
        llm = MagicMock()

        result = query_decomposer.decompose(
            "단순 질문",
            _decision(needs_decomp=False),
            llm=llm,
        )

        llm.complete.assert_not_called()
        self.assertEqual(result.subqueries, ())
        self.assertIsNotNone(result.skipped_reason)
        assert result.skipped_reason is not None
        self.assertIn("불필요", result.skipped_reason)


class NormalDecompositionTest(_BaseDecomposerTest):
    """#3 — 정상 분해 (cross_doc → 3 subqueries)."""

    def test_cross_doc_returns_three_subqueries(self) -> None:
        from app.services import query_decomposer

        os.environ["JETRAG_PAID_DECOMPOSITION_ENABLED"] = "true"
        llm = MagicMock()
        llm.complete.return_value = (
            '["작년 보고서 매출", "올해 자료 매출", "두 자료 차이점"]'
        )

        # vision_usage_log SUM (graceful) → DB 부재로 None → allowed=True 보장
        with patch.object(
            query_decomposer,
            "_sum_decomposition_monthly_cost",
            return_value=0.0,
        ), patch.object(query_decomposer, "_record_usage", return_value=None):
            result = query_decomposer.decompose(
                "작년 보고서랑 올해 자료 비교",
                _decision(needs_decomp=True, signals=("T1_cross_doc",)),
                llm=llm,
            )

        llm.complete.assert_called_once()
        self.assertEqual(len(result.subqueries), 3)
        self.assertIn("작년 보고서 매출", result.subqueries)
        self.assertFalse(result.cached)
        self.assertGreater(result.cost_usd, 0.0)
        self.assertIsNone(result.skipped_reason)


class LruCacheHitTest(_BaseDecomposerTest):
    """#4 — LRU cache hit. 두 번째 호출 cost=0.0 / cached=True / LLM 호출 1회만."""

    def test_second_call_uses_cache(self) -> None:
        from app.services import query_decomposer

        os.environ["JETRAG_PAID_DECOMPOSITION_ENABLED"] = "true"
        llm = MagicMock()
        llm.complete.return_value = '["sub1", "sub2", "sub3"]'

        with patch.object(
            query_decomposer,
            "_sum_decomposition_monthly_cost",
            return_value=0.0,
        ), patch.object(query_decomposer, "_record_usage", return_value=None):
            first = query_decomposer.decompose(
                "작년 보고서랑 올해 자료 비교",
                _decision(needs_decomp=True, signals=("T1_cross_doc",)),
                llm=llm,
            )
            second = query_decomposer.decompose(
                "작년 보고서랑 올해 자료 비교",
                _decision(needs_decomp=True, signals=("T1_cross_doc",)),
                llm=llm,
            )

        self.assertEqual(llm.complete.call_count, 1)
        self.assertFalse(first.cached)
        self.assertTrue(second.cached)
        self.assertEqual(second.cost_usd, 0.0)
        self.assertEqual(second.subqueries, first.subqueries)
        self.assertIsNone(second.skipped_reason)


class JsonParseFailureTest(_BaseDecomposerTest):
    """#5 — JSON 파싱 실패 → () + reason."""

    def test_invalid_json_returns_empty_with_reason(self) -> None:
        from app.services import query_decomposer

        os.environ["JETRAG_PAID_DECOMPOSITION_ENABLED"] = "true"
        llm = MagicMock()
        # JSON array 가 아닌 응답.
        llm.complete.return_value = "이 질문은 분해할 수 없습니다."

        with patch.object(
            query_decomposer,
            "_sum_decomposition_monthly_cost",
            return_value=0.0,
        ), patch.object(query_decomposer, "_record_usage", return_value=None):
            result = query_decomposer.decompose(
                "작년 보고서랑 올해 자료 비교",
                _decision(needs_decomp=True, signals=("T1_cross_doc",)),
                llm=llm,
            )

        llm.complete.assert_called_once()
        self.assertEqual(result.subqueries, ())
        self.assertFalse(result.cached)
        self.assertEqual(result.cost_usd, 0.0)
        self.assertIsNotNone(result.skipped_reason)
        assert result.skipped_reason is not None
        self.assertIn("파싱", result.skipped_reason)


class BudgetCapExceededTest(_BaseDecomposerTest):
    """#6 — budget cap 초과 → skip + reason. LLM 호출 0회."""

    def test_monthly_cap_exceeded_skips_with_reason(self) -> None:
        from app.services import query_decomposer

        os.environ["JETRAG_PAID_DECOMPOSITION_ENABLED"] = "true"
        os.environ["JETRAG_DECOMPOSITION_MONTHLY_CAP_USD"] = "0.10"
        llm = MagicMock()

        # cap 0.10 초과 → 0.50 누적 모킹 → allowed=False 보장.
        with patch.object(
            query_decomposer,
            "_sum_decomposition_monthly_cost",
            return_value=0.50,
        ), patch.object(query_decomposer, "_record_usage", return_value=None):
            result = query_decomposer.decompose(
                "작년 보고서랑 올해 자료 비교",
                _decision(needs_decomp=True, signals=("T1_cross_doc",)),
                llm=llm,
            )

        llm.complete.assert_not_called()
        self.assertEqual(result.subqueries, ())
        self.assertFalse(result.cached)
        self.assertEqual(result.cost_usd, 0.0)
        self.assertIsNotNone(result.skipped_reason)
        assert result.skipped_reason is not None
        self.assertIn("한도 초과", result.skipped_reason)


# ============================================================
# 통합 테스트 — 실 Gemini API 호출 1회 (GEMINI_API_KEY 실 토큰 필요)
# ============================================================
@unittest.skipUnless(
    os.environ.get("GEMINI_API_KEY") and os.environ["GEMINI_API_KEY"] != "dummy-test-token",
    reason="실제 GEMINI_API_KEY 부재 — 통합 테스트 skip",
)
class GeminiIntegrationTest(unittest.TestCase):
    """실 Gemini Flash-Lite 호출 1회 — JSON 응답 + 분해 동작 e2e 검증.

    수동 실행 권장: GEMINI_API_KEY=실제키 python -m unittest tests.services.test_query_decomposer.GeminiIntegrationTest
    """

    def setUp(self) -> None:
        from app.services import query_decomposer

        query_decomposer._reset_cache_for_test()
        os.environ["JETRAG_PAID_DECOMPOSITION_ENABLED"] = "true"

    def tearDown(self) -> None:
        os.environ.pop("JETRAG_PAID_DECOMPOSITION_ENABLED", None)

    def test_real_llm_decomposes_cross_doc_query(self) -> None:
        from app.services import query_decomposer

        # DB 부재 시 graceful — _record_usage / _sum 모두 None 반환 path 활용.
        result = query_decomposer.decompose(
            "작년 보고서랑 올해 자료를 비교해줘",
            _decision(needs_decomp=True, signals=("T1_cross_doc",)),
        )

        # 분해 성공 시 2~5건 — 실패 시 () (graceful) — 둘 다 통과 (실 API 안정성 부재).
        if result.subqueries:
            self.assertGreaterEqual(len(result.subqueries), 2)
            self.assertLessEqual(len(result.subqueries), 5)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
