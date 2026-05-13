"""M1 W-1(a) — `/search` 의 paid LLM query decomposition 옵션 단위 테스트.

배경
- `/answer` 에만 배선돼 있던 query decomposition 을 `/search` 에도 노출.
- 게이트 = `router_decision.needs_decomposition` AND ENV
  `JETRAG_PAID_DECOMPOSITION_ENABLED ∈ {true,1,yes,on}` — confidence 임계는 게이트에 미포함.
- 분해 활성 시 원본 query 풀(top_k=20) + sub-query 당 풀(top_k=10) → RRF(k=60) merge.
- meta 4키: decomposition_fired / decomposed_subqueries / decomposition_cost_usd /
  decomposition_cached — ENV OFF·미발화 시 false / [] / 0.0 / false.

검증 포인트
(a) ENV OFF → 분해 미발화 + meta 4키 기본값 + decompose 호출 0 + 추가 RPC 0
(b) ENV ON + needs_decomposition + decompose 가 subqueries 반환 → merge 경로 + meta 반영
(c) ENV ON + not needs_decomposition → decompose 호출 0 (스레드 안 탐) + 미발화
(d) ENV ON + decompose 가 빈 tuple → 미발화 + original pool only
(e) ENV ON + decompose timeout/예외 → graceful (미발화, 검색 정상)
(f) `intent_router.route()` 가 `/search` 호출 1회 (decision 재사용 검증)
(g) cost_usd / cached meta 전파

stdlib unittest + mock only — 외부 API/DB 0 (paid $0).
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")
# decomposition 테스트는 ENV 를 케이스별로 명시 set/del — 사전 상태 무관하게 만들기 위해
# 모듈 import 시점엔 건드리지 않음 (각 테스트가 patch.dict 로 격리).

_ENV_DECOMP = "JETRAG_PAID_DECOMPOSITION_ENABLED"

# 분해 게이트 발화 query — intent_router T2(비교) 발화 → needs_decomposition=True.
_DECOMP_QUERY = "기웅민 이력서와 이한주 포트폴리오의 핵심 역량은 어떻게 다른가요?"
# 분해 게이트 미발화 query — 단순 키워드 (T1~T7 어느 것도 발화 안 함).
_PLAIN_QUERY = "데이터센터 모니터링 항목"


def _provider_mock() -> MagicMock:
    m = MagicMock()
    m.embed_query.return_value = [0.0] * 1024
    m._last_cache_hit = False
    return m


def _doc_meta_chain(doc_ids: list[str]) -> MagicMock:
    chain = MagicMock()
    chain.select.return_value = chain
    chain.in_.return_value = chain
    chain.eq.return_value = chain
    chain.is_.return_value = chain
    chain.gte.return_value = chain
    chain.lte.return_value = chain
    chain.contains.return_value = chain
    chain.execute.return_value.data = [
        {
            "id": did,
            "title": did,
            "doc_type": "pdf",
            "tags": [],
            "summary": None,
            "created_at": "2026-05-04T00:00:00+00:00",
        }
        for did in doc_ids
    ]
    return chain


def _chunks_chain(chunks_data: list[dict]) -> MagicMock:
    chain = MagicMock()
    chain.select.return_value = chain
    chain.in_.return_value = chain
    chain.eq.return_value = chain
    chain.execute.return_value.data = chunks_data
    return chain


def _make_rpc_rows(doc_ids_with_counts: dict[str, int]) -> list[dict]:
    rows: list[dict] = []
    base = 1.0
    for did, n in doc_ids_with_counts.items():
        for i in range(n):
            rows.append(
                {
                    "chunk_id": f"{did}-c{i}",
                    "doc_id": did,
                    "rrf_score": base - i * 0.01,
                    "dense_rank": i + 1,
                    "sparse_rank": None,
                }
            )
        base -= 0.05
    return rows


def _make_chunks(doc_ids_with_counts: dict[str, int]) -> list[dict]:
    out: list[dict] = []
    for did, n in doc_ids_with_counts.items():
        for i in range(n):
            out.append(
                {
                    "id": f"{did}-c{i}",
                    "doc_id": did,
                    "chunk_idx": i,
                    "page": 1,
                    "section_title": None,
                    "text": f"{did} 청크 {i} 본문",
                    "metadata": {},
                }
            )
    return out


def _build_client(rpc_rows: list[dict], chunks_data: list[dict], doc_ids: list[str]):
    """Supabase client mock + RPC 호출 카운터 반환.

    search_dense_only / search_sparse_only 는 raise (008 미적용 시뮬) → hybrid fallback.
    search_hybrid_rrf / search_sparse_only_pgroonga 는 동일 rpc_rows 반환.
    """
    client = MagicMock()
    rpc_calls: list[str] = []

    def _rpc_side_effect(name: str, _args: dict) -> MagicMock:
        rpc_calls.append(name)
        if name in ("search_dense_only", "search_sparse_only"):
            raise RuntimeError(f"function {name} does not exist (008 미적용)")
        rpc_resp = MagicMock()
        if name == "search_sparse_only_pgroonga":
            # _sparse_only_fallback 가 기대하는 schema (sparse_rank int)
            rpc_resp.data = [
                {"chunk_id": r["chunk_id"], "doc_id": r["doc_id"], "sparse_rank": 1}
                for r in rpc_rows
            ]
        else:
            rpc_resp.data = rpc_rows
        call = MagicMock()
        call.execute.return_value = rpc_resp
        return call

    client.rpc.side_effect = _rpc_side_effect

    docs_chain = _doc_meta_chain(doc_ids)
    chunks_chain = _chunks_chain(chunks_data)

    def _table(name: str) -> MagicMock:
        if name == "documents":
            return docs_chain
        return chunks_chain

    client.table.side_effect = _table
    return client, rpc_calls


def _call_search(search_module, *, q: str, client_mock, provider_mock=None):
    provider_mock = provider_mock or _provider_mock()
    with patch.object(
        search_module, "get_bgem3_provider", return_value=provider_mock
    ), patch.object(
        search_module, "get_supabase_client", return_value=client_mock
    ), patch.object(
        search_module.meta_filter_fast_path, "is_meta_only", return_value=None
    ):
        return search_module.search(
            q=q, limit=10, offset=0, tags=None, doc_type=None,
            from_date=None, to_date=None, doc_id=None, mode="hybrid",
        )


class SearchDecompositionEnvOffTest(unittest.TestCase):
    """(a) ENV OFF — 분해 자체가 일어나지 않음, meta 기본값, decompose/RPC 추가 0."""

    def test_env_off_no_decomposition_and_meta_defaults(self) -> None:
        from app.routers import search as search_module

        rpc_rows = _make_rpc_rows({"doc-A": 3, "doc-B": 2})
        chunks = _make_chunks({"doc-A": 3, "doc-B": 2})
        client_mock, rpc_calls = _build_client(rpc_rows, chunks, ["doc-A", "doc-B"])

        with patch.dict(os.environ, {_ENV_DECOMP: "false"}, clear=False), patch.object(
            search_module.query_decomposer, "decompose"
        ) as decompose_mock:
            resp = _call_search(search_module, q=_DECOMP_QUERY, client_mock=client_mock)

        decompose_mock.assert_not_called()
        # meta 4키 기본값
        self.assertIsNotNone(resp.meta)
        self.assertEqual(resp.meta["decomposition_fired"], False)
        self.assertEqual(resp.meta["decomposed_subqueries"], [])
        self.assertEqual(resp.meta["decomposition_cost_usd"], 0.0)
        self.assertEqual(resp.meta["decomposition_cached"], False)
        # 추가 RPC 호출 0 — search_hybrid_rrf 1회 (+ split RPC probe 1회) 만, sub-query RPC 없음.
        hybrid_calls = [c for c in rpc_calls if c == "search_hybrid_rrf"]
        self.assertEqual(len(hybrid_calls), 1, f"sub-query RPC 가 추가됨: {rpc_calls}")


class SearchDecompositionFiredTest(unittest.TestCase):
    """(b)(g) ENV ON + needs_decomposition + subqueries 반환 → merge + meta 반영."""

    def test_env_on_decomposition_merges_pools_and_sets_meta(self) -> None:
        from app.routers import search as search_module
        from app.services.query_decomposer import QueryDecomposition

        rpc_rows = _make_rpc_rows({"doc-A": 3, "doc-B": 2})
        chunks = _make_chunks({"doc-A": 3, "doc-B": 2})
        client_mock, rpc_calls = _build_client(rpc_rows, chunks, ["doc-A", "doc-B"])

        fake_decomp = QueryDecomposition(
            subqueries=("기웅민 이력서 핵심 역량", "이한주 포트폴리오 핵심 역량"),
            cost_usd=0.000026,
            cached=False,
            skipped_reason=None,
        )
        with patch.dict(os.environ, {_ENV_DECOMP: "true"}, clear=False), patch.object(
            search_module.query_decomposer, "decompose", return_value=fake_decomp
        ) as decompose_mock:
            resp = _call_search(search_module, q=_DECOMP_QUERY, client_mock=client_mock)

        decompose_mock.assert_called_once()
        self.assertEqual(resp.meta["decomposition_fired"], True)
        self.assertEqual(
            resp.meta["decomposed_subqueries"],
            ["기웅민 이력서 핵심 역량", "이한주 포트폴리오 핵심 역량"],
        )
        self.assertAlmostEqual(resp.meta["decomposition_cost_usd"], 0.000026, places=8)
        self.assertEqual(resp.meta["decomposition_cached"], False)
        # sub-query 2개 → search_hybrid_rrf 가 원본 1 + sub 2 = 3회 호출.
        hybrid_calls = [c for c in rpc_calls if c == "search_hybrid_rrf"]
        self.assertEqual(len(hybrid_calls), 3, f"sub-query RPC merge 안 됨: {rpc_calls}")
        # 결과 자체는 정상 (chunk dedupe — 동일 chunk_id 라 doc 당 청크 수 불변)
        self.assertGreaterEqual(len(resp.items), 1)

    def test_cached_decomposition_propagates_meta(self) -> None:
        from app.routers import search as search_module
        from app.services.query_decomposer import QueryDecomposition

        rpc_rows = _make_rpc_rows({"doc-A": 2})
        chunks = _make_chunks({"doc-A": 2})
        client_mock, _ = _build_client(rpc_rows, chunks, ["doc-A"])

        cached_decomp = QueryDecomposition(
            subqueries=("부분 질의 1", "부분 질의 2"),
            cost_usd=0.0,
            cached=True,
            skipped_reason=None,
        )
        with patch.dict(os.environ, {_ENV_DECOMP: "1"}, clear=False), patch.object(
            search_module.query_decomposer, "decompose", return_value=cached_decomp
        ):
            resp = _call_search(search_module, q=_DECOMP_QUERY, client_mock=client_mock)

        self.assertEqual(resp.meta["decomposition_fired"], True)
        self.assertEqual(resp.meta["decomposition_cached"], True)
        self.assertEqual(resp.meta["decomposition_cost_usd"], 0.0)


class SearchDecompositionGateTest(unittest.TestCase):
    """(c) ENV ON + not needs_decomposition → decompose 호출 0 (스레드 안 탐)."""

    def test_env_on_but_not_needs_decomposition_skips_decompose(self) -> None:
        from app.routers import search as search_module

        rpc_rows = _make_rpc_rows({"doc-A": 2})
        chunks = _make_chunks({"doc-A": 2})
        client_mock, rpc_calls = _build_client(rpc_rows, chunks, ["doc-A"])

        with patch.dict(os.environ, {_ENV_DECOMP: "true"}, clear=False), patch.object(
            search_module.query_decomposer, "decompose"
        ) as decompose_mock:
            resp = _call_search(search_module, q=_PLAIN_QUERY, client_mock=client_mock)

        decompose_mock.assert_not_called()
        self.assertEqual(resp.meta["decomposition_fired"], False)
        hybrid_calls = [c for c in rpc_calls if c == "search_hybrid_rrf"]
        self.assertEqual(len(hybrid_calls), 1)


class SearchDecompositionEmptyTest(unittest.TestCase):
    """(d) ENV ON + decompose 가 빈 tuple → 미발화 + original pool only."""

    def test_empty_subqueries_no_extra_rpc(self) -> None:
        from app.routers import search as search_module
        from app.services.query_decomposer import QueryDecomposition

        rpc_rows = _make_rpc_rows({"doc-A": 2, "doc-B": 2})
        chunks = _make_chunks({"doc-A": 2, "doc-B": 2})
        client_mock, rpc_calls = _build_client(rpc_rows, chunks, ["doc-A", "doc-B"])

        empty_decomp = QueryDecomposition(
            subqueries=(),
            cost_usd=0.0,
            cached=False,
            skipped_reason="LLM 응답 JSON 파싱 실패",
        )
        with patch.dict(os.environ, {_ENV_DECOMP: "true"}, clear=False), patch.object(
            search_module.query_decomposer, "decompose", return_value=empty_decomp
        ) as decompose_mock:
            resp = _call_search(search_module, q=_DECOMP_QUERY, client_mock=client_mock)

        decompose_mock.assert_called_once()
        self.assertEqual(resp.meta["decomposition_fired"], False)
        self.assertEqual(resp.meta["decomposed_subqueries"], [])
        hybrid_calls = [c for c in rpc_calls if c == "search_hybrid_rrf"]
        self.assertEqual(len(hybrid_calls), 1, f"빈 분해인데 sub RPC 발생: {rpc_calls}")


class SearchDecompositionGracefulTest(unittest.TestCase):
    """(e) ENV ON + decompose timeout/예외 → graceful (미발화, 검색 정상)."""

    def test_decompose_raises_is_graceful(self) -> None:
        from app.routers import search as search_module

        rpc_rows = _make_rpc_rows({"doc-A": 2})
        chunks = _make_chunks({"doc-A": 2})
        client_mock, rpc_calls = _build_client(rpc_rows, chunks, ["doc-A"])

        with patch.dict(os.environ, {_ENV_DECOMP: "true"}, clear=False), patch.object(
            search_module.query_decomposer,
            "decompose",
            side_effect=RuntimeError("LLM boom"),
        ):
            resp = _call_search(search_module, q=_DECOMP_QUERY, client_mock=client_mock)

        # 검색 자체는 정상 (503 아님), 분해 미발화.
        self.assertEqual(resp.meta["decomposition_fired"], False)
        self.assertGreaterEqual(len(resp.items), 1)
        hybrid_calls = [c for c in rpc_calls if c == "search_hybrid_rrf"]
        self.assertEqual(len(hybrid_calls), 1)

    def test_decompose_timeout_is_graceful(self) -> None:
        from app.routers import search as search_module

        rpc_rows = _make_rpc_rows({"doc-A": 2})
        chunks = _make_chunks({"doc-A": 2})
        client_mock, _ = _build_client(rpc_rows, chunks, ["doc-A"])

        # timeout 을 1초로 강제 + decompose 가 그보다 오래 sleep → future.result(timeout) 발동.
        # (worker 스레드는 sleep 후 정상 반환 — 단 결과는 폐기됨. 노이즈 회피용으로 raise 안 함.)
        from app.services.query_decomposer import QueryDecomposition

        def _slow_decompose(_q, _decision):  # noqa: ANN001
            import time as _t

            _t.sleep(1.5)
            return QueryDecomposition(
                subqueries=("늦은 결과 1", "늦은 결과 2"),
                cost_usd=0.0,
                cached=False,
                skipped_reason=None,
            )

        with patch.dict(
            os.environ,
            {_ENV_DECOMP: "true", "JETRAG_DECOMPOSITION_TIMEOUT_SEC": "1"},
            clear=False,
        ), patch.object(
            search_module.query_decomposer, "decompose", side_effect=_slow_decompose
        ):
            resp = _call_search(search_module, q=_DECOMP_QUERY, client_mock=client_mock)

        self.assertEqual(resp.meta["decomposition_fired"], False)
        self.assertGreaterEqual(len(resp.items), 1)


class SearchDecompositionRouteOnceTest(unittest.TestCase):
    """(f) intent_router.route() 가 `/search` 1회 호출 (decision 재사용)."""

    def test_intent_router_route_called_once(self) -> None:
        from app.routers import search as search_module

        rpc_rows = _make_rpc_rows({"doc-A": 2, "doc-B": 2})
        chunks = _make_chunks({"doc-A": 2, "doc-B": 2})
        client_mock, _ = _build_client(rpc_rows, chunks, ["doc-A", "doc-B"])

        real_route = search_module.intent_router.route
        with patch.dict(os.environ, {_ENV_DECOMP: "false"}, clear=False), patch.object(
            search_module.intent_router, "route", side_effect=real_route
        ) as route_mock:
            _call_search(search_module, q=_DECOMP_QUERY, client_mock=client_mock)

        self.assertEqual(
            route_mock.call_count, 1,
            f"intent_router.route() 가 {route_mock.call_count}회 호출 — 1회여야 (decision 재사용)",
        )


if __name__ == "__main__":
    unittest.main()
