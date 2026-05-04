"""W25 D3 — 검색 결과 UX Phase 1 단위 테스트.

검증 포인트
- B-1: `_SNIPPET_AROUND` 환경변수화 (default 240) — `_make_snippet_with_highlights`
       매칭 위치 ±240자 윈도우 반환.
- C-1a: chunk_id dedupe — dense path + sparse path 가 같은 chunk_id 를 두 번
        반환해도 `matched_chunk_count` 가 unique 수만 카운트.

stdlib unittest + mock only.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")


class SnippetAroundExtensionTest(unittest.TestCase):
    """B-1 — `_SNIPPET_AROUND=240` 으로 확장된 윈도우 검증.

    `_make_snippet_with_highlights(text, query, around)` 가 매칭 위치 ±around 만큼
    잘라 반환. 충분히 긴 본문 (1000자) + 중앙 매칭 시 around * 2 + query_len 길이.
    """

    def test_snippet_window_uses_240_default(self) -> None:
        from app.routers.search import _SNIPPET_AROUND, _make_snippet_with_highlights

        # 기본값이 240 인지 확인 (env 변경 안 한 경우)
        # 환경변수 우선 적용되므로 명시적 240 인자로 회귀 검증.
        body = "가" * 500 + "MATCH" + "나" * 500
        snippet, highlights = _make_snippet_with_highlights(
            body, "MATCH", around=240
        )
        # 윈도우 = 240 + 5 (MATCH) + 240 = 485 (+ '…' prefix·suffix 2자)
        self.assertEqual(len(snippet), 485 + 2)
        self.assertTrue(snippet.startswith("…"))
        self.assertTrue(snippet.endswith("…"))
        self.assertEqual(highlights, [[241, 246]])  # '…' (1) + 가*240 (240)
        # _SNIPPET_AROUND 자체가 240 으로 default (env 미설정 시)
        self.assertGreaterEqual(_SNIPPET_AROUND, 240)

    def test_snippet_short_text_no_truncation(self) -> None:
        """본문이 윈도우보다 짧으면 ellipsis 없이 전체 반환."""
        from app.routers.search import _make_snippet_with_highlights

        body = "짧은 본문 MATCH 끝"
        snippet, highlights = _make_snippet_with_highlights(body, "MATCH", around=240)
        self.assertEqual(snippet, body)
        self.assertFalse(snippet.startswith("…"))
        self.assertFalse(snippet.endswith("…"))
        self.assertEqual(len(highlights), 1)


class ChunkIdDedupeTest(unittest.TestCase):
    """C-1a — dense path + sparse path 가 같은 chunk_id 두 번 반환 시 dedupe."""

    def _provider_mock(self) -> MagicMock:
        m = MagicMock()
        m.embed_query.return_value = [0.0] * 1024
        m._last_cache_hit = False
        return m

    def _client_with_rpc_rows(self, rows: list[dict]) -> MagicMock:
        from tests.test_search_doc_id_filter import _client_with_rpc_rows
        return _client_with_rpc_rows(rows)

    def test_duplicate_chunk_id_counts_once(self) -> None:
        """RPC 가 같은 chunk_id 를 dense_rank + sparse_rank 양쪽으로 2 row 반환 →
        matched_chunk_count = 1 (unique 수).
        """
        from app.routers import search as search_module

        # 같은 chunk_id "c-shared" 가 dense path 와 sparse path 에 각각 등장.
        # 008 split RPC 미적용 환경의 search_hybrid_rrf 는 보통 dedupe 해서 반환하지만,
        # 008 적용 후 dense+sparse 동시 호출 시 별개 row 로 누적될 가능성 검증.
        rpc_rows = [
            {
                "chunk_id": "c-shared", "doc_id": "doc-A",
                "rrf_score": 0.5, "dense_rank": 1, "sparse_rank": None,
            },
            {
                "chunk_id": "c-shared", "doc_id": "doc-A",
                "rrf_score": 0.4, "dense_rank": None, "sparse_rank": 1,
            },
            {
                "chunk_id": "c-other", "doc_id": "doc-A",
                "rrf_score": 0.3, "dense_rank": 2, "sparse_rank": None,
            },
        ]
        client_mock = self._client_with_rpc_rows(rpc_rows)
        # documents 메타 fetch 가 doc-A 정보 반환하도록 chain mock 보강
        docs_chain = MagicMock()
        docs_chain.select.return_value = docs_chain
        docs_chain.in_.return_value = docs_chain
        docs_chain.eq.return_value = docs_chain
        docs_chain.is_.return_value = docs_chain
        docs_chain.execute.return_value.data = [
            {
                "id": "doc-A", "title": "doc-A", "doc_type": "pdf",
                "tags": [], "summary": None,
                "created_at": "2026-05-04T00:00:00+00:00",
            }
        ]
        # chunks fetch 도 chain mock — 빈 결과 OK (matched_count 만 검증)
        chunks_chain = MagicMock()
        chunks_chain.select.return_value = chunks_chain
        chunks_chain.in_.return_value = chunks_chain
        chunks_chain.execute.return_value.data = []

        def _table(name: str):
            if name == "documents":
                return docs_chain
            return chunks_chain
        client_mock.table.side_effect = _table

        with patch.object(
            search_module, "get_bgem3_provider", return_value=self._provider_mock()
        ), patch.object(
            search_module, "get_supabase_client", return_value=client_mock
        ):
            resp = search_module.search(
                q="t", limit=10, offset=0, tags=None, doc_type=None,
                from_date=None, to_date=None, doc_id=None, mode="hybrid",
            )

        # doc-A 단일 — matched_chunk_count = unique chunk 수 = 2 (c-shared + c-other)
        # dedupe 전 동작: 3 (중복 카운트). dedupe 후: 2.
        self.assertEqual(len(resp.items), 1)
        self.assertEqual(
            resp.items[0].matched_chunk_count, 2,
            f"chunk_id dedupe 후 unique 수 2 기대 — got {resp.items[0].matched_chunk_count}",
        )

    def test_chunk_id_dedupe_keeps_max_score(self) -> None:
        """같은 chunk_id 가 두 번 등장 시 max RRF score 보존 (top-3 정렬 무결성)."""
        from app.routers import search as search_module

        rpc_rows = [
            # c-shared: dense rank 1 (score 0.5) + sparse rank 1 (score 0.4)
            {"chunk_id": "c-shared", "doc_id": "doc-A", "rrf_score": 0.5,
             "dense_rank": 1, "sparse_rank": None},
            {"chunk_id": "c-shared", "doc_id": "doc-A", "rrf_score": 0.4,
             "dense_rank": None, "sparse_rank": 1},
            # c-better: 단독 등장이지만 score 더 낮음 (0.45)
            {"chunk_id": "c-mid", "doc_id": "doc-A", "rrf_score": 0.45,
             "dense_rank": 2, "sparse_rank": None},
        ]
        client_mock = self._client_with_rpc_rows(rpc_rows)

        # documents 메타
        docs_chain = MagicMock()
        docs_chain.select.return_value = docs_chain
        docs_chain.in_.return_value = docs_chain
        docs_chain.eq.return_value = docs_chain
        docs_chain.is_.return_value = docs_chain
        docs_chain.execute.return_value.data = [
            {"id": "doc-A", "title": "doc-A", "doc_type": "pdf",
             "tags": [], "summary": None,
             "created_at": "2026-05-04T00:00:00+00:00"}
        ]
        chunks_chain = MagicMock()
        chunks_chain.select.return_value = chunks_chain
        chunks_chain.in_.return_value = chunks_chain
        # chunks fetch 응답 — matched_chunks 의 rrf_score 검증용.
        # W25 D4 Phase 2 — 표지 가드 (chunk_idx=0 OR page=1) 회피 위해 chunk_idx>0 + page=2 사용.
        chunks_chain.execute.return_value.data = [
            {"id": "c-shared", "doc_id": "doc-A", "chunk_idx": 1,
             "page": 2, "section_title": None, "text": "shared text", "metadata": {}},
            {"id": "c-mid", "doc_id": "doc-A", "chunk_idx": 2,
             "page": 2, "section_title": None, "text": "mid text", "metadata": {}},
        ]

        def _table(name: str):
            if name == "documents":
                return docs_chain
            return chunks_chain
        client_mock.table.side_effect = _table

        with patch.object(
            search_module, "get_bgem3_provider", return_value=self._provider_mock()
        ), patch.object(
            search_module, "get_supabase_client", return_value=client_mock
        ):
            resp = search_module.search(
                q="t", limit=10, offset=0, tags=None, doc_type=None,
                from_date=None, to_date=None, doc_id=None, mode="hybrid",
            )

        # c-shared 의 rrf_score = max(0.5, 0.4) = 0.5 — 후순위 row 0.4 무시 검증
        chunks_by_id = {c.chunk_id: c for c in resp.items[0].matched_chunks}
        self.assertIn("c-shared", chunks_by_id)
        self.assertAlmostEqual(chunks_by_id["c-shared"].rrf_score or 0.0, 0.5, places=4)


if __name__ == "__main__":
    unittest.main()
