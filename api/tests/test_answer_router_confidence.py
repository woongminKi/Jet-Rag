"""S3 D2 — `/answer` 라우터 confidence 안전망 와이어 단위 테스트 (planner v0.1 §A).

검증 범위
---------
- ``intent_router.route`` mock — confidence_score 0.6 → response.meta.low_confidence=True.
- mock 0.9 → False.
- 응답 meta 에 router_signals · router_confidence 노출 확인.

본 테스트는 외부 API 호출 0 — Supabase RPC + LLM 호출은 mock.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

# 환경 변수 사전 주입 (다른 테스트 파일과 동일 패턴).
os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")

from app.services.intent_router import IntentRouterDecision  # noqa: E402


def _decision(*, confidence: float, signals: tuple[str, ...] = ()) -> IntentRouterDecision:
    return IntentRouterDecision(
        needs_decomposition=False,
        triggered_signals=signals,
        confidence_score=confidence,
        query_normalized="dummy",
        matched_keywords=(),
    )


def _empty_chain() -> MagicMock:
    chain = MagicMock()
    chain.select.return_value = chain
    chain.in_.return_value = chain
    chain.execute.return_value.data = []
    return chain


def _client_with_zero_chunks() -> MagicMock:
    """검색 결과 0 → LLM 호출 회피 path 진입 (테스트 결정성)."""
    client = MagicMock()
    rpc_resp = MagicMock()
    rpc_resp.data = []
    rpc_call = MagicMock()
    rpc_call.execute.return_value = rpc_resp
    client.rpc.return_value = rpc_call
    client.table.return_value = _empty_chain()
    return client


class AnswerConfidenceMetaTest(unittest.TestCase):
    """planner v0.1 §A — confidence_score < 0.75 → meta.low_confidence True."""

    def _provider_mock(self) -> MagicMock:
        provider = MagicMock()
        provider.embed_query.return_value = [0.0] * 1024
        provider._last_cache_hit = False
        return provider

    def test_low_confidence_marked_when_score_below_threshold(self) -> None:
        from app.routers import answer as answer_module

        client_mock = _client_with_zero_chunks()
        decision = _decision(confidence=0.6, signals=("T1_cross_doc",))

        with patch.object(
            answer_module, "get_bgem3_provider", return_value=self._provider_mock()
        ), patch.object(
            answer_module, "get_supabase_client", return_value=client_mock
        ), patch.object(
            answer_module.intent_router, "route", return_value=decision
        ):
            resp = answer_module.answer(q="작년 보고서랑 올해 자료 비교", top_k=5, doc_id=None)

        self.assertIsNotNone(resp.meta)
        assert resp.meta is not None
        self.assertTrue(resp.meta["low_confidence"])
        self.assertEqual(resp.meta["router_signals"], ["T1_cross_doc"])
        self.assertAlmostEqual(resp.meta["router_confidence"], 0.6, places=6)

    def test_high_confidence_not_marked(self) -> None:
        from app.routers import answer as answer_module

        client_mock = _client_with_zero_chunks()
        decision = _decision(confidence=0.9, signals=())

        with patch.object(
            answer_module, "get_bgem3_provider", return_value=self._provider_mock()
        ), patch.object(
            answer_module, "get_supabase_client", return_value=client_mock
        ), patch.object(
            answer_module.intent_router, "route", return_value=decision
        ):
            resp = answer_module.answer(q="단순 질문", top_k=5, doc_id=None)

        self.assertIsNotNone(resp.meta)
        assert resp.meta is not None
        self.assertFalse(resp.meta["low_confidence"])
        self.assertEqual(resp.meta["router_signals"], [])
        self.assertAlmostEqual(resp.meta["router_confidence"], 0.9, places=6)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
