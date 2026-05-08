"""S3 D3 — `/answer` 라우터 분해 와이어 단위 테스트 (planner v0.1 §I).

검증 범위
---------
1. ENV off 시 meta 에 `decomposition_cost_usd=0.0` / `decomposed_subqueries=[]` 노출
   + `query_decomposer.decompose` 가 LLM 호출 0 path 진입 (회귀 0).
2. ENV on + mock decompose 가 sub-query 반환 시 meta 에 노출 +
   `_gather_chunks_with_decomposition` 호출 분기 동작.

본 테스트는 외부 API 호출 0 — Supabase RPC + LLM 호출은 mock.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

# 환경 변수 stub.
os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")
os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")
os.environ["JET_RAG_METRICS_PERSIST_ENABLED"] = "0"
os.environ["JET_RAG_METRICS_PERSIST_ASYNC"] = "0"

from app.services.intent_router import IntentRouterDecision  # noqa: E402
from app.services.query_decomposer import QueryDecomposition  # noqa: E402


def _decision(
    *,
    needs_decomp: bool,
    confidence: float = 0.6,
    signals: tuple[str, ...] = (),
) -> IntentRouterDecision:
    return IntentRouterDecision(
        needs_decomposition=needs_decomp,
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


def _provider_mock() -> MagicMock:
    provider = MagicMock()
    provider.embed_query.return_value = [0.0] * 1024
    provider._last_cache_hit = False
    return provider


class AnswerDecompositionMetaTest(unittest.TestCase):
    """planner v0.1 §I — meta 에 decomposition_* 필드 노출 + 분기 와이어."""

    def setUp(self) -> None:
        os.environ.pop("JETRAG_PAID_DECOMPOSITION_ENABLED", None)

    def tearDown(self) -> None:
        os.environ.pop("JETRAG_PAID_DECOMPOSITION_ENABLED", None)

    def test_env_off_meta_shows_empty_subqueries(self) -> None:
        """ENV off → decompose 가 () 반환 → meta 에 빈 리스트 + cost 0.0."""
        from app.routers import answer as answer_module

        client_mock = _client_with_zero_chunks()
        decision = _decision(needs_decomp=True, signals=("T1_cross_doc",))

        with patch.object(
            answer_module, "get_bgem3_provider", return_value=_provider_mock()
        ), patch.object(
            answer_module, "get_supabase_client", return_value=client_mock
        ), patch.object(
            answer_module.intent_router, "route", return_value=decision
        ):
            resp = answer_module.answer(
                q="작년 보고서랑 올해 자료 비교", top_k=5, doc_id=None
            )

        self.assertIsNotNone(resp.meta)
        assert resp.meta is not None
        self.assertEqual(resp.meta["decomposed_subqueries"], [])
        self.assertEqual(resp.meta["decomposition_cost_usd"], 0.0)
        self.assertFalse(resp.meta["decomposition_cached"])

    def test_env_on_with_mock_subqueries_invokes_decomposed_gather(self) -> None:
        """ENV on + mock decompose → meta 노출 + `_gather_chunks_with_decomposition` 호출."""
        from app.routers import answer as answer_module

        client_mock = _client_with_zero_chunks()
        decision = _decision(needs_decomp=True, signals=("T1_cross_doc",))

        decomp_mock = QueryDecomposition(
            subqueries=("sub1", "sub2", "sub3"),
            cost_usd=0.000123,
            cached=False,
            skipped_reason=None,
        )

        with patch.object(
            answer_module, "get_bgem3_provider", return_value=_provider_mock()
        ), patch.object(
            answer_module, "get_supabase_client", return_value=client_mock
        ), patch.object(
            answer_module.intent_router, "route", return_value=decision
        ), patch.object(
            answer_module.query_decomposer, "decompose", return_value=decomp_mock
        ) as decompose_spy, patch.object(
            answer_module,
            "_gather_chunks_with_decomposition",
            return_value=([], {"has_dense": True, "has_sparse": False, "dense_hits": 0, "sparse_hits": 0, "fused": 0}),
        ) as gather_spy, patch.object(
            answer_module, "_gather_chunks"
        ) as fallback_spy:
            resp = answer_module.answer(
                q="작년 보고서랑 올해 자료 비교", top_k=5, doc_id=None
            )

        # decompose 1회 + decomposed gather 1회, 기존 _gather_chunks 호출 0회 (분기 검증).
        decompose_spy.assert_called_once()
        gather_spy.assert_called_once()
        fallback_spy.assert_not_called()

        self.assertIsNotNone(resp.meta)
        assert resp.meta is not None
        self.assertEqual(resp.meta["decomposed_subqueries"], ["sub1", "sub2", "sub3"])
        self.assertAlmostEqual(resp.meta["decomposition_cost_usd"], 0.000123, places=6)
        self.assertFalse(resp.meta["decomposition_cached"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
