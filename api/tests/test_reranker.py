"""W25 D14+1 (S2) — BGE-reranker-v2-m3 cross-encoder rerank 단위 테스트.

검증 대상:
1. `BGERerankerHFProvider.rerank()` — request body schema, 응답 파싱, cache hit/miss
2. `is_transient_reranker_error()` — 4xx 영구 / 5xx transient / 네트워크 분류
3. `get_reranker_provider()` 싱글톤 (httpx.Client 누수 회피)
4. search.py 통합 — opt-in ENV (default off) + reranker 활성 시 RRF score 가 cross-encoder 로 대체
5. reranker 실패 시 RRF fallback (검색 자체는 차단 X)

stdlib `unittest` 만 사용 — 외부 의존성 0.
실행: `python -m unittest tests.test_reranker`
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

import httpx


class IsTransientRerankerErrorTest(unittest.TestCase):
    """transient/permanent 분류 — bgem3 패턴과 동일 정책."""

    def _status_error(self, code: int) -> httpx.HTTPStatusError:
        request = httpx.Request("POST", "https://example.invalid/x")
        response = httpx.Response(code, request=request, content=b"{}")
        return httpx.HTTPStatusError(
            f"{code}", request=request, response=response
        )

    def test_4xx_is_not_transient(self) -> None:
        from app.adapters.impl.bge_reranker_hf import is_transient_reranker_error
        for code in (400, 401, 403, 404):
            with self.subTest(code=code):
                self.assertFalse(is_transient_reranker_error(self._status_error(code)))

    def test_429_and_5xx_is_transient(self) -> None:
        from app.adapters.impl.bge_reranker_hf import is_transient_reranker_error
        for code in (429, 500, 502, 503, 504):
            with self.subTest(code=code):
                self.assertTrue(is_transient_reranker_error(self._status_error(code)))

    def test_network_errors_are_transient(self) -> None:
        from app.adapters.impl.bge_reranker_hf import is_transient_reranker_error
        self.assertTrue(is_transient_reranker_error(httpx.ConnectError("dns")))
        self.assertTrue(is_transient_reranker_error(httpx.ReadTimeout("slow")))
        self.assertTrue(is_transient_reranker_error(httpx.RemoteProtocolError("term")))

    def test_runtime_error_is_not_transient(self) -> None:
        from app.adapters.impl.bge_reranker_hf import is_transient_reranker_error
        self.assertFalse(is_transient_reranker_error(RuntimeError("parse fail")))


class GetRerankerProviderSingletonTest(unittest.TestCase):
    """`get_reranker_provider()` lru_cache → httpx.Client 누수 회피."""

    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")

    def setUp(self) -> None:
        from app.adapters.impl.bge_reranker_hf import get_reranker_provider
        get_reranker_provider.cache_clear()

    def test_returns_same_instance(self) -> None:
        from app.adapters.impl.bge_reranker_hf import (
            BGERerankerHFProvider,
            get_reranker_provider,
        )
        a = get_reranker_provider()
        b = get_reranker_provider()
        self.assertIsInstance(a, BGERerankerHFProvider)
        self.assertIs(a, b)


class RerankBodyAndResponseTest(unittest.TestCase):
    """`rerank()` 의 HF 요청 body 형식 + 응답 파싱 + cache 동작."""

    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")

    def setUp(self) -> None:
        from app.adapters.impl.bge_reranker_hf import get_reranker_provider
        get_reranker_provider.cache_clear()

    def _mock_post(self, scores: list[float]) -> MagicMock:
        resp = MagicMock(spec=httpx.Response)
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value=scores)
        post = MagicMock(return_value=resp)
        return post

    def test_body_uses_sentence_similarity_schema(self) -> None:
        """request body 가 sentence-similarity pipeline schema 인지 검증."""
        from app.adapters.impl.bge_reranker_hf import get_reranker_provider

        provider = get_reranker_provider()
        post_mock = self._mock_post([0.9, 0.5, 0.1])
        provider._client.post = post_mock

        candidates = [("c1", "텍스트 A"), ("c2", "텍스트 B"), ("c3", "텍스트 C")]
        scores = provider.rerank("질문", candidates)

        self.assertEqual(scores, [0.9, 0.5, 0.1])
        # 호출된 body 검증
        post_mock.assert_called_once()
        body = post_mock.call_args.kwargs["json"]
        self.assertIn("inputs", body)
        self.assertEqual(body["inputs"]["source_sentence"], "질문")
        self.assertEqual(body["inputs"]["sentences"], ["텍스트 A", "텍스트 B", "텍스트 C"])

    def test_empty_candidates_returns_empty_no_api_call(self) -> None:
        from app.adapters.impl.bge_reranker_hf import get_reranker_provider
        provider = get_reranker_provider()
        post_mock = MagicMock()
        provider._client.post = post_mock
        self.assertEqual(provider.rerank("q", []), [])
        post_mock.assert_not_called()

    def test_cache_hit_skips_hf_call(self) -> None:
        """동일 (query, chunk_id) 두 번째 호출은 HF 호출 0."""
        from app.adapters.impl.bge_reranker_hf import get_reranker_provider
        provider = get_reranker_provider()
        provider.clear_cache()

        post_mock = self._mock_post([0.7, 0.3])
        provider._client.post = post_mock

        candidates = [("c1", "A"), ("c2", "B")]
        s1 = provider.rerank("질문", candidates)
        s2 = provider.rerank("질문", candidates)

        self.assertEqual(s1, s2)
        # 두 번째 호출은 cache hit 만 → HF 호출 1회만
        self.assertEqual(post_mock.call_count, 1)
        self.assertEqual(provider._last_cache_hits, 2)
        self.assertEqual(provider._last_cache_misses, 0)

    def test_partial_cache_hit_calls_hf_for_misses_only(self) -> None:
        """일부 chunk_id 만 cache 면 miss 만 HF 호출."""
        from app.adapters.impl.bge_reranker_hf import get_reranker_provider
        provider = get_reranker_provider()
        provider.clear_cache()

        # 1차 호출 — c1, c2 cache 화
        post1 = self._mock_post([0.7, 0.3])
        provider._client.post = post1
        provider.rerank("질문", [("c1", "A"), ("c2", "B")])

        # 2차 — c1 (cache) + c3 (miss) → c3 만 HF 호출
        post2 = self._mock_post([0.5])  # c3 score
        provider._client.post = post2
        scores = provider.rerank("질문", [("c1", "A"), ("c3", "C")])

        self.assertEqual(scores[0], 0.7)  # cache hit
        self.assertEqual(scores[1], 0.5)  # miss → HF 호출 결과
        post2.assert_called_once()
        # body 의 sentences 는 miss 만 (c3) 포함
        body = post2.call_args.kwargs["json"]
        self.assertEqual(body["inputs"]["sentences"], ["C"])

    def test_response_length_mismatch_raises(self) -> None:
        from app.adapters.impl.bge_reranker_hf import get_reranker_provider
        provider = get_reranker_provider()
        provider.clear_cache()
        post_mock = self._mock_post([0.5])  # 1개만 — 2개 기대인데
        provider._client.post = post_mock
        with self.assertRaises(RuntimeError):
            provider.rerank("질문", [("c1", "A"), ("c2", "B")])

    def test_long_passage_truncation(self) -> None:
        """1200자 초과 chunk 는 앞부분만 HF 에 전송."""
        from app.adapters.impl.bge_reranker_hf import (
            _MAX_PASSAGE_CHARS,
            get_reranker_provider,
        )
        provider = get_reranker_provider()
        provider.clear_cache()
        post_mock = self._mock_post([0.5])
        provider._client.post = post_mock

        long_text = "가" * (_MAX_PASSAGE_CHARS + 500)
        provider.rerank("질문", [("c1", long_text)])
        body = post_mock.call_args.kwargs["json"]
        self.assertEqual(len(body["inputs"]["sentences"][0]), _MAX_PASSAGE_CHARS)


class SearchRerankerIntegrationTest(unittest.TestCase):
    """search.py 통합 — opt-in default off + 활성 시 reranker score 가 ranking 결정."""

    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")

    def setUp(self) -> None:
        # reranker 환경변수 원복 (test isolation)
        os.environ.pop("JETRAG_RERANKER_ENABLED", None)
        from app.adapters.impl.bge_reranker_hf import get_reranker_provider
        from app.adapters.impl.bgem3_hf_embedding import get_bgem3_provider
        get_reranker_provider.cache_clear()
        get_bgem3_provider.cache_clear()

    def tearDown(self) -> None:
        os.environ.pop("JETRAG_RERANKER_ENABLED", None)

    def _call_search(self, search_fn, q: str = "테스트"):
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

    def _build_mocks(self, rpc_rows: list[dict], chunks: list[dict], docs: list[dict]):
        """search 흐름의 supabase / embedding mock 표준 픽스처."""
        embed_provider = MagicMock()
        embed_provider.embed_query.return_value = [0.0] * 1024
        embed_provider._last_cache_hit = False

        client = MagicMock()
        rpc_resp = MagicMock()
        rpc_resp.data = rpc_rows
        client.rpc.return_value.execute.return_value = rpc_resp

        # chunks IN 쿼리 mock — 본문 fetch (top-K)
        chunks_resp = MagicMock()
        chunks_resp.data = chunks
        # documents IN 쿼리 mock — 메타 fetch
        docs_resp = MagicMock()
        docs_resp.data = docs

        # client.table("...").select(...).in_(...).execute() 체인
        # 두 가지 분기 (chunks vs documents) — table 인자로 분기
        def table_side_effect(name: str):
            tbl = MagicMock()
            if name == "chunks":
                tbl.select.return_value.in_.return_value.execute.return_value = chunks_resp
            elif name == "documents":
                docs_query = MagicMock()
                docs_query.execute.return_value = docs_resp
                # 메타 필터 체인 — eq/is_/contains/gte/lte 모두 self return
                for method in ("eq", "is_", "contains", "gte", "lte"):
                    setattr(docs_query, method, MagicMock(return_value=docs_query))
                tbl.select.return_value.in_.return_value.eq.return_value.is_.return_value = docs_query
            return tbl
        client.table.side_effect = table_side_effect
        return embed_provider, client

    def test_default_off_no_reranker_call(self) -> None:
        """ENV 미설정 default off — reranker 호출 0, RRF score 그대로."""
        from app.routers import search as search_module

        rpc_rows = [
            {"chunk_id": "c1", "doc_id": "d1", "rrf_score": 0.5,
             "dense_rank": 1, "sparse_rank": None},
            {"chunk_id": "c2", "doc_id": "d2", "rrf_score": 0.3,
             "dense_rank": 2, "sparse_rank": None},
        ]
        chunks = [
            {"id": "c1", "doc_id": "d1", "chunk_idx": 5, "page": 3,
             "section_title": None, "text": "내용 A", "metadata": {}},
            {"id": "c2", "doc_id": "d2", "chunk_idx": 7, "page": 4,
             "section_title": None, "text": "내용 B", "metadata": {}},
        ]
        docs = [
            {"id": "d1", "title": "A", "doc_type": "pdf", "tags": [],
             "summary": None, "created_at": "2026-01-01T00:00:00Z"},
            {"id": "d2", "title": "B", "doc_type": "pdf", "tags": [],
             "summary": None, "created_at": "2026-01-02T00:00:00Z"},
        ]
        embed_mock, client_mock = self._build_mocks(rpc_rows, chunks, docs)
        reranker_mock = MagicMock()

        with patch.object(search_module, "get_bgem3_provider", return_value=embed_mock), \
             patch.object(search_module, "get_supabase_client", return_value=client_mock), \
             patch.object(search_module, "get_reranker_provider", return_value=reranker_mock):
            resp = self._call_search(search_module.search)

        self.assertFalse(resp.query_parsed.reranker_used)
        self.assertIsNone(resp.query_parsed.reranker_fallback_reason)
        reranker_mock.rerank.assert_not_called()
        # RRF 순서 그대로 — d1 (0.5) > d2 (0.3)
        self.assertEqual(resp.items[0].doc_id, "d1")

    def test_reranker_enabled_reorders(self) -> None:
        """ENV on — reranker score 가 RRF 순서를 뒤집을 수 있어야 한다."""
        from app.routers import search as search_module
        os.environ["JETRAG_RERANKER_ENABLED"] = "true"

        rpc_rows = [
            {"chunk_id": "c1", "doc_id": "d1", "rrf_score": 0.9,
             "dense_rank": 1, "sparse_rank": None},
            {"chunk_id": "c2", "doc_id": "d2", "rrf_score": 0.5,
             "dense_rank": 2, "sparse_rank": None},
        ]
        chunks = [
            {"id": "c1", "doc_id": "d1", "chunk_idx": 5, "page": 3,
             "section_title": None, "text": "내용 A", "metadata": {}},
            {"id": "c2", "doc_id": "d2", "chunk_idx": 7, "page": 4,
             "section_title": None, "text": "내용 B", "metadata": {}},
        ]
        docs = [
            {"id": "d1", "title": "A", "doc_type": "pdf", "tags": [],
             "summary": None, "created_at": "2026-01-01T00:00:00Z"},
            {"id": "d2", "title": "B", "doc_type": "pdf", "tags": [],
             "summary": None, "created_at": "2026-01-02T00:00:00Z"},
        ]
        embed_mock, client_mock = self._build_mocks(rpc_rows, chunks, docs)
        # reranker 가 c1=0.1, c2=0.9 점수 → RRF 와 반대 ranking
        reranker_mock = MagicMock()
        reranker_mock.rerank.return_value = [0.1, 0.9]

        with patch.object(search_module, "get_bgem3_provider", return_value=embed_mock), \
             patch.object(search_module, "get_supabase_client", return_value=client_mock), \
             patch.object(search_module, "get_reranker_provider", return_value=reranker_mock):
            resp = self._call_search(search_module.search)

        self.assertTrue(resp.query_parsed.reranker_used)
        self.assertIsNone(resp.query_parsed.reranker_fallback_reason)
        reranker_mock.rerank.assert_called_once()
        # 순서 뒤집힘 — d2 가 top
        self.assertEqual(resp.items[0].doc_id, "d2")

    def test_reranker_transient_failure_falls_back(self) -> None:
        """reranker HTTP 503 → RRF fallback (검색 자체는 정상 응답)."""
        from app.routers import search as search_module
        os.environ["JETRAG_RERANKER_ENABLED"] = "true"

        rpc_rows = [
            {"chunk_id": "c1", "doc_id": "d1", "rrf_score": 0.9,
             "dense_rank": 1, "sparse_rank": None},
            {"chunk_id": "c2", "doc_id": "d2", "rrf_score": 0.5,
             "dense_rank": 2, "sparse_rank": None},
        ]
        chunks = [
            {"id": "c1", "doc_id": "d1", "chunk_idx": 5, "page": 3,
             "section_title": None, "text": "A", "metadata": {}},
            {"id": "c2", "doc_id": "d2", "chunk_idx": 7, "page": 4,
             "section_title": None, "text": "B", "metadata": {}},
        ]
        docs = [
            {"id": "d1", "title": "A", "doc_type": "pdf", "tags": [],
             "summary": None, "created_at": "2026-01-01T00:00:00Z"},
            {"id": "d2", "title": "B", "doc_type": "pdf", "tags": [],
             "summary": None, "created_at": "2026-01-02T00:00:00Z"},
        ]
        embed_mock, client_mock = self._build_mocks(rpc_rows, chunks, docs)
        reranker_mock = MagicMock()
        request = httpx.Request("POST", "https://example.invalid/x")
        response = httpx.Response(503, request=request, content=b"{}")
        reranker_mock.rerank.side_effect = httpx.HTTPStatusError(
            "503", request=request, response=response
        )

        with patch.object(search_module, "get_bgem3_provider", return_value=embed_mock), \
             patch.object(search_module, "get_supabase_client", return_value=client_mock), \
             patch.object(search_module, "get_reranker_provider", return_value=reranker_mock):
            resp = self._call_search(search_module.search)

        self.assertFalse(resp.query_parsed.reranker_used)
        self.assertEqual(resp.query_parsed.reranker_fallback_reason, "transient")
        # RRF 순서 보존 — d1 (0.9) > d2 (0.5)
        self.assertEqual(resp.items[0].doc_id, "d1")

    def test_reranker_permanent_failure_falls_back(self) -> None:
        """reranker HTTP 401 → permanent 분류, RRF fallback."""
        from app.routers import search as search_module
        os.environ["JETRAG_RERANKER_ENABLED"] = "true"

        rpc_rows = [
            {"chunk_id": "c1", "doc_id": "d1", "rrf_score": 0.9,
             "dense_rank": 1, "sparse_rank": None},
            {"chunk_id": "c2", "doc_id": "d2", "rrf_score": 0.5,
             "dense_rank": 2, "sparse_rank": None},
        ]
        chunks = [
            {"id": "c1", "doc_id": "d1", "chunk_idx": 5, "page": 3,
             "section_title": None, "text": "A", "metadata": {}},
            {"id": "c2", "doc_id": "d2", "chunk_idx": 7, "page": 4,
             "section_title": None, "text": "B", "metadata": {}},
        ]
        docs = [
            {"id": "d1", "title": "A", "doc_type": "pdf", "tags": [],
             "summary": None, "created_at": "2026-01-01T00:00:00Z"},
            {"id": "d2", "title": "B", "doc_type": "pdf", "tags": [],
             "summary": None, "created_at": "2026-01-02T00:00:00Z"},
        ]
        embed_mock, client_mock = self._build_mocks(rpc_rows, chunks, docs)
        reranker_mock = MagicMock()
        request = httpx.Request("POST", "https://example.invalid/x")
        response = httpx.Response(401, request=request, content=b"{}")
        reranker_mock.rerank.side_effect = httpx.HTTPStatusError(
            "401", request=request, response=response
        )

        with patch.object(search_module, "get_bgem3_provider", return_value=embed_mock), \
             patch.object(search_module, "get_supabase_client", return_value=client_mock), \
             patch.object(search_module, "get_reranker_provider", return_value=reranker_mock):
            resp = self._call_search(search_module.search)

        self.assertFalse(resp.query_parsed.reranker_used)
        self.assertEqual(resp.query_parsed.reranker_fallback_reason, "permanent")
        self.assertEqual(resp.items[0].doc_id, "d1")  # RRF 보존


if __name__ == "__main__":
    unittest.main()
