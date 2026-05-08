"""S3 D4 — search.py reranker path 통합 단위 테스트 (planner v0.1 §G #5·#6·#8).

검증 범위
---------
#5  cap 적용 — candidates 50 입력 시 reranker pair 20 만 (default cap=20).
#6  degrade 분기 — vision_usage_log 의 reranker_invoke COUNT 가 임계 ≥ 80%
    초과 → HF mock 호출 0 + path=degraded.
#8  X-Reranker-Path 헤더 노출 4 path (cached / invoked / degraded / disabled)
    각각 정확히 마킹.

stdlib unittest only — 외부 API 호출 0, 의존성 추가 0.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

# 환경변수 stub — 다른 통합 테스트와 동일 패턴.
os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")
os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")
os.environ["JET_RAG_METRICS_PERSIST_ENABLED"] = "0"
os.environ["JET_RAG_METRICS_PERSIST_ASYNC"] = "0"


def _make_rpc_rows(n: int) -> list[dict]:
    """n 개의 (chunk, doc) 페어 — chunk_id 'c-NNN', doc_id 'd-NNN'."""
    return [
        {
            "chunk_id": f"c-{i:03d}",
            "doc_id": f"d-{i:03d}",
            "rrf_score": 0.99 - i * 0.001,  # 결정성 — 같은 score 회피.
            "dense_rank": i + 1,
            "sparse_rank": None,
        }
        for i in range(n)
    ]


def _make_chunks(n: int) -> list[dict]:
    return [
        {
            "id": f"c-{i:03d}",
            "doc_id": f"d-{i:03d}",
            "chunk_idx": 5,
            "page": 3,
            "section_title": None,
            "text": f"본문 {i}",
            "metadata": {},
        }
        for i in range(n)
    ]


def _make_docs(n: int) -> list[dict]:
    return [
        {
            "id": f"d-{i:03d}",
            "title": f"doc-{i}",
            "doc_type": "pdf",
            "tags": [],
            "summary": None,
            "created_at": "2026-01-01T00:00:00Z",
        }
        for i in range(n)
    ]


def _build_search_mocks(rpc_rows: list[dict], chunks: list[dict], docs: list[dict]):
    """test_reranker.py SearchRerankerIntegrationTest._build_mocks 와 동일 패턴.

    chunks/documents IN 쿼리 + RPC 반환 mock. supabase-py 체인을 충실히 재현.
    """
    embed_provider = MagicMock()
    embed_provider.embed_query.return_value = [0.0] * 1024
    embed_provider._last_cache_hit = False

    client = MagicMock()
    rpc_resp = MagicMock()
    rpc_resp.data = rpc_rows
    client.rpc.return_value.execute.return_value = rpc_resp

    chunks_resp = MagicMock()
    chunks_resp.data = chunks
    docs_resp = MagicMock()
    docs_resp.data = docs

    def table_side_effect(name: str):
        tbl = MagicMock()
        if name == "chunks":
            tbl.select.return_value.in_.return_value.execute.return_value = chunks_resp
        elif name == "documents":
            docs_query = MagicMock()
            docs_query.execute.return_value = docs_resp
            for method in ("eq", "is_", "contains", "gte", "lte"):
                setattr(docs_query, method, MagicMock(return_value=docs_query))
            tbl.select.return_value.in_.return_value.eq.return_value.is_.return_value = (
                docs_query
            )
        elif name == "vision_usage_log":
            # _record_reranker_invoke insert + _count_reranker_invokes_last_30d 둘 다 graceful.
            insert_mock = MagicMock()
            insert_mock.execute.return_value = MagicMock(data=[])
            tbl.insert.return_value = insert_mock
            select_mock = MagicMock()
            select_mock.execute.return_value = MagicMock(data=[], count=0)
            for method in ("eq", "gte"):
                setattr(select_mock, method, MagicMock(return_value=select_mock))
            tbl.select.return_value = select_mock
        return tbl

    client.table.side_effect = table_side_effect
    return embed_provider, client


class _BaseSearchRerankerPathTest(unittest.TestCase):
    """공통 setup — ENV 정리 + cache reset + provider 캐시 비우기."""

    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")

    def setUp(self) -> None:
        from app.adapters.impl.bge_reranker_hf import get_reranker_provider
        from app.adapters.impl.bgem3_hf_embedding import get_bgem3_provider
        from app.services import reranker_cache

        get_reranker_provider.cache_clear()
        get_bgem3_provider.cache_clear()
        reranker_cache._reset_for_test()
        for k in (
            "JETRAG_RERANKER_ENABLED",
            "JETRAG_RERANKER_CANDIDATE_CAP",
            "JETRAG_RERANKER_MONTHLY_CAP_CALLS",
            "JETRAG_RERANKER_DEGRADE_THRESHOLD",
            "JETRAG_RERANKER_CACHE_DISABLE",
            "JETRAG_MMR_DISABLE",
        ):
            os.environ.pop(k, None)

    def tearDown(self) -> None:
        from app.services import reranker_cache

        reranker_cache._reset_for_test()
        for k in (
            "JETRAG_RERANKER_ENABLED",
            "JETRAG_RERANKER_CANDIDATE_CAP",
            "JETRAG_RERANKER_MONTHLY_CAP_CALLS",
            "JETRAG_RERANKER_DEGRADE_THRESHOLD",
            "JETRAG_RERANKER_CACHE_DISABLE",
            "JETRAG_MMR_DISABLE",
        ):
            os.environ.pop(k, None)

    def _call_search(self, search_fn, q: str = "테스트 쿼리"):
        return search_fn(
            q=q,
            limit=10,
            offset=0,
            tags=None,
            doc_type=None,
            from_date=None,
            to_date=None,
            doc_id=None,
            mode="hybrid",
        )


class RerankerCandidateCapTest(_BaseSearchRerankerPathTest):
    """#5 — candidates 50 입력 시 reranker.rerank() 가 받은 pair 길이 = 20 (default cap)."""

    def test_cap_limits_pairs_to_default_20(self) -> None:
        from app.routers import search as search_module

        n = 50
        rpc_rows = _make_rpc_rows(n)
        chunks = _make_chunks(n)
        docs = _make_docs(n)
        embed_mock, client_mock = _build_search_mocks(rpc_rows, chunks, docs)

        os.environ["JETRAG_RERANKER_ENABLED"] = "true"

        # rerank score — cap 적용 후 들어오는 pair 갯수만큼 점수 반환.
        reranker_mock = MagicMock()
        reranker_mock.rerank.side_effect = lambda q, pairs: [0.5] * len(pairs)

        with patch.object(search_module, "get_bgem3_provider", return_value=embed_mock), \
             patch.object(search_module, "get_supabase_client", return_value=client_mock), \
             patch.object(search_module, "get_reranker_provider", return_value=reranker_mock):
            self._call_search(search_module.search)

        reranker_mock.rerank.assert_called_once()
        called_pairs = reranker_mock.rerank.call_args.args[1]
        self.assertEqual(len(called_pairs), 20)


class RerankerDegradeBranchTest(_BaseSearchRerankerPathTest):
    """#6 — 월간 호출 카운트 ≥ 임계 → HF mock 호출 0 + path=degraded."""

    def test_degrade_skips_hf_call(self) -> None:
        from app.routers import search as search_module

        rpc_rows = _make_rpc_rows(3)
        chunks = _make_chunks(3)
        docs = _make_docs(3)
        embed_mock, client_mock = _build_search_mocks(rpc_rows, chunks, docs)

        os.environ["JETRAG_RERANKER_ENABLED"] = "true"
        # cap=10, threshold=0.8 → 8 도달 시 degrade. _count 가 9 반환하도록 patch.
        os.environ["JETRAG_RERANKER_MONTHLY_CAP_CALLS"] = "10"
        os.environ["JETRAG_RERANKER_DEGRADE_THRESHOLD"] = "0.8"

        reranker_mock = MagicMock()
        reranker_mock.rerank.return_value = [0.9, 0.5, 0.1]

        with patch.object(search_module, "get_bgem3_provider", return_value=embed_mock), \
             patch.object(search_module, "get_supabase_client", return_value=client_mock), \
             patch.object(search_module, "get_reranker_provider", return_value=reranker_mock), \
             patch.object(search_module, "_count_reranker_invokes_last_30d", return_value=9):
            resp = self._call_search(search_module.search)

        # HF 호출 0 — degrade 분기.
        reranker_mock.rerank.assert_not_called()
        self.assertEqual(resp.query_parsed.reranker_path, "degraded")
        self.assertFalse(resp.query_parsed.reranker_used)


class RerankerPathHeaderTest(_BaseSearchRerankerPathTest):
    """#8 — X-Reranker-Path 헤더 4 path 정확 노출 (cached / invoked / degraded / disabled)."""

    def _call_with_response(self, search_fn, q: str = "헤더 검증"):
        # Response 객체 mock — headers dict 만 캡처.
        from fastapi import Response

        resp_obj = Response()
        result = search_fn(
            q=q,
            limit=10,
            offset=0,
            tags=None,
            doc_type=None,
            from_date=None,
            to_date=None,
            doc_id=None,
            mode="hybrid",
            response=resp_obj,
        )
        return result, resp_obj

    def test_disabled_path_when_env_off(self) -> None:
        from app.routers import search as search_module

        rpc_rows = _make_rpc_rows(3)
        chunks = _make_chunks(3)
        docs = _make_docs(3)
        embed_mock, client_mock = _build_search_mocks(rpc_rows, chunks, docs)

        # ENV 미설정 — default off.
        reranker_mock = MagicMock()

        with patch.object(search_module, "get_bgem3_provider", return_value=embed_mock), \
             patch.object(search_module, "get_supabase_client", return_value=client_mock), \
             patch.object(search_module, "get_reranker_provider", return_value=reranker_mock):
            result, resp_obj = self._call_with_response(search_module.search)

        self.assertEqual(result.query_parsed.reranker_path, "disabled")
        self.assertEqual(resp_obj.headers.get("X-Reranker-Path"), "disabled")
        reranker_mock.rerank.assert_not_called()

    def test_invoked_path_when_hf_called(self) -> None:
        from app.routers import search as search_module

        rpc_rows = _make_rpc_rows(3)
        chunks = _make_chunks(3)
        docs = _make_docs(3)
        embed_mock, client_mock = _build_search_mocks(rpc_rows, chunks, docs)

        os.environ["JETRAG_RERANKER_ENABLED"] = "true"
        reranker_mock = MagicMock()
        reranker_mock.rerank.return_value = [0.9, 0.5, 0.1]

        with patch.object(search_module, "get_bgem3_provider", return_value=embed_mock), \
             patch.object(search_module, "get_supabase_client", return_value=client_mock), \
             patch.object(search_module, "get_reranker_provider", return_value=reranker_mock), \
             patch.object(search_module, "_count_reranker_invokes_last_30d", return_value=0):
            result, resp_obj = self._call_with_response(
                search_module.search, q="invoked path 쿼리"
            )

        self.assertEqual(result.query_parsed.reranker_path, "invoked")
        self.assertEqual(resp_obj.headers.get("X-Reranker-Path"), "invoked")
        reranker_mock.rerank.assert_called_once()

    def test_cached_path_on_second_call(self) -> None:
        from app.routers import search as search_module

        rpc_rows = _make_rpc_rows(3)
        chunks = _make_chunks(3)
        docs = _make_docs(3)
        embed_mock, client_mock = _build_search_mocks(rpc_rows, chunks, docs)

        os.environ["JETRAG_RERANKER_ENABLED"] = "true"
        reranker_mock = MagicMock()
        reranker_mock.rerank.return_value = [0.9, 0.5, 0.1]

        with patch.object(search_module, "get_bgem3_provider", return_value=embed_mock), \
             patch.object(search_module, "get_supabase_client", return_value=client_mock), \
             patch.object(search_module, "get_reranker_provider", return_value=reranker_mock), \
             patch.object(search_module, "_count_reranker_invokes_last_30d", return_value=0):
            # 1회차 — invoked + cache store.
            first_result, _ = self._call_with_response(
                search_module.search, q="cached path 동일 쿼리"
            )
            # 2회차 — 같은 query/chunk_ids → cache hit.
            second_result, second_resp = self._call_with_response(
                search_module.search, q="cached path 동일 쿼리"
            )

        self.assertEqual(first_result.query_parsed.reranker_path, "invoked")
        self.assertEqual(second_result.query_parsed.reranker_path, "cached")
        self.assertEqual(second_resp.headers.get("X-Reranker-Path"), "cached")
        # HF 호출은 1회차에서만.
        self.assertEqual(reranker_mock.rerank.call_count, 1)

    def test_degraded_path_when_quota_threshold_hit(self) -> None:
        from app.routers import search as search_module

        rpc_rows = _make_rpc_rows(3)
        chunks = _make_chunks(3)
        docs = _make_docs(3)
        embed_mock, client_mock = _build_search_mocks(rpc_rows, chunks, docs)

        os.environ["JETRAG_RERANKER_ENABLED"] = "true"
        os.environ["JETRAG_RERANKER_MONTHLY_CAP_CALLS"] = "10"
        os.environ["JETRAG_RERANKER_DEGRADE_THRESHOLD"] = "0.8"

        reranker_mock = MagicMock()

        with patch.object(search_module, "get_bgem3_provider", return_value=embed_mock), \
             patch.object(search_module, "get_supabase_client", return_value=client_mock), \
             patch.object(search_module, "get_reranker_provider", return_value=reranker_mock), \
             patch.object(search_module, "_count_reranker_invokes_last_30d", return_value=9):
            result, resp_obj = self._call_with_response(
                search_module.search, q="degraded path 쿼리"
            )

        self.assertEqual(result.query_parsed.reranker_path, "degraded")
        self.assertEqual(resp_obj.headers.get("X-Reranker-Path"), "degraded")
        reranker_mock.rerank.assert_not_called()


if __name__ == "__main__":
    unittest.main()
