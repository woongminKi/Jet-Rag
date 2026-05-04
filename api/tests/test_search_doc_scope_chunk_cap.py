"""W25 D5 — `?doc_id=X` 단일 문서 스코프 시 매칭 청크 cap 우회 단위 테스트.

배경
- 사용자 시나리오: 검색 결과 카드의 `+89개 더 매칭 (이 문서에서 모두 보기 →)` Link
  → doc 페이지 (`/doc/{id}?q=...`) 가 백엔드 `?doc_id=X&q=...` 호출.
  → 응답에 매칭 청크 90+개 모두 포함되어야 한다.
- 그러나 `_MAX_MATCHED_CHUNKS_PER_DOC=3` 가 doc_id 스코프에서도 3 cap 적용 중 → fix 필요.

검증 포인트
- doc_id 미지정: 카드 모드 — 응답 청크 ≤ 3 (기존 동작 보존)
- doc_id 지정: 스코프 모드 — `_MAX_MATCHED_CHUNKS_DOC_SCOPE=200` cap 까지 모두 응답
- 정렬 정책:
    - 카드 모드: chunk_idx 오름차순
    - 스코프 모드: rrf_score 내림차순

stdlib unittest + mock only.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")


def _provider_mock() -> MagicMock:
    m = MagicMock()
    m.embed_query.return_value = [0.0] * 1024
    m._last_cache_hit = False
    return m


def _make_doc_meta_chain(doc_id: str = "doc-A") -> MagicMock:
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
            "id": doc_id,
            "title": doc_id,
            "doc_type": "pdf",
            "tags": [],
            "summary": None,
            "created_at": "2026-05-04T00:00:00+00:00",
        }
    ]
    return chain


def _make_chunks_chain(chunks_data: list[dict]) -> MagicMock:
    chain = MagicMock()
    chain.select.return_value = chain
    chain.in_.return_value = chain
    chain.execute.return_value.data = chunks_data
    return chain


def _build_client(rpc_rows: list[dict], chunks_data: list[dict]) -> MagicMock:
    """Supabase client mock — RPC + documents + chunks + cover_guard chain 분기."""
    client = MagicMock()
    rpc_resp = MagicMock()
    rpc_resp.data = rpc_rows
    rpc_call = MagicMock()
    rpc_call.execute.return_value = rpc_resp

    # split RPC (search_dense_only / search_sparse_only) 미적용 시뮬 — hybrid fallback.
    def _rpc_side_effect(name: str, _args: dict) -> MagicMock:
        if name in ("search_dense_only", "search_sparse_only"):
            raise RuntimeError(f"function {name} does not exist (008 미적용)")
        return rpc_call
    client.rpc.side_effect = _rpc_side_effect

    docs_chain = _make_doc_meta_chain()
    # cover_guard 가 chunks 로 fetch — chunks_data 의 일부 컬럼 (id/chunk_idx/page/text) 사용.
    # 메인 chunks fetch 와 cover_guard fetch 는 같은 chain 으로 응답해도 무방
    # (어차피 두 호출 모두 chunks 테이블에 일치하는 row 만 응답하므로 cover_guard 가 chunk_idx>0 + page>1
    # 인 row 만 보고 _is_cover_chunk=False 처리).
    chunks_chain = _make_chunks_chain(chunks_data)

    def _table(name: str) -> MagicMock:
        if name == "documents":
            return docs_chain
        return chunks_chain
    client.table.side_effect = _table

    return client


def _make_rpc_rows_for_doc(doc_id: str, count: int) -> list[dict]:
    """`count` 개 unique chunk 를 동일 doc_id 로 — score 내림차순."""
    rows = []
    for i in range(count):
        rows.append(
            {
                "chunk_id": f"c-{i}",
                "doc_id": doc_id,
                "rrf_score": 1.0 - (i * 0.01),  # 0.99, 0.98, ...
                "dense_rank": i + 1,
                "sparse_rank": None,
            }
        )
    return rows


def _make_chunks_data(count: int) -> list[dict]:
    """`count` 개 chunk row — chunk_idx>0 + page>1 (cover_guard 회피)."""
    return [
        {
            "id": f"c-{i}",
            "doc_id": "doc-A",
            "chunk_idx": i + 1,  # 0 회피
            "page": 2,  # 1 회피
            "section_title": None,
            "text": f"청크 {i} 본문 내용",
            "metadata": {},
        }
        for i in range(count)
    ]


class DocScopeCapBypassTest(unittest.TestCase):
    """`?doc_id=X` 명시 시 cap 우회 검증."""

    def test_doc_scope_returns_all_matched_chunks(self) -> None:
        """92개 매칭 chunk → doc_id 명시 시 응답에 92개 모두 포함."""
        from app.routers import search as search_module

        rpc_rows = _make_rpc_rows_for_doc("doc-A", 92)
        chunks_data = _make_chunks_data(92)
        client_mock = _build_client(rpc_rows, chunks_data)

        with patch.object(
            search_module, "get_bgem3_provider", return_value=_provider_mock()
        ), patch.object(
            search_module, "get_supabase_client", return_value=client_mock
        ):
            resp = search_module.search(
                q="시트", limit=10, offset=0, tags=None, doc_type=None,
                from_date=None, to_date=None,
                doc_id="doc-A", mode="hybrid",
            )

        self.assertEqual(len(resp.items), 1)
        self.assertEqual(resp.items[0].matched_chunk_count, 92)
        self.assertEqual(
            len(resp.items[0].matched_chunks), 92,
            "doc_id 스코프 — cap 우회로 92개 모두 응답에 포함되어야 함",
        )

    def test_doc_scope_chunks_sorted_by_score_desc(self) -> None:
        """doc_id 스코프 시 응답 청크 정렬 = rrf_score 내림차순 (관련도 순)."""
        from app.routers import search as search_module

        rpc_rows = _make_rpc_rows_for_doc("doc-A", 5)
        chunks_data = _make_chunks_data(5)
        client_mock = _build_client(rpc_rows, chunks_data)

        with patch.object(
            search_module, "get_bgem3_provider", return_value=_provider_mock()
        ), patch.object(
            search_module, "get_supabase_client", return_value=client_mock
        ):
            resp = search_module.search(
                q="시트", limit=10, offset=0, tags=None, doc_type=None,
                from_date=None, to_date=None,
                doc_id="doc-A", mode="hybrid",
            )

        scores = [
            c.rrf_score or 0.0 for c in resp.items[0].matched_chunks
        ]
        self.assertEqual(
            scores, sorted(scores, reverse=True),
            f"doc_id 스코프 — score 내림차순 정렬 깨짐: {scores}",
        )

    def test_list_mode_keeps_3_cap(self) -> None:
        """doc_id 미지정 (list 모드) 시 기존 동작 — 청크 ≤ 3 cap 유지."""
        from app.routers import search as search_module

        rpc_rows = _make_rpc_rows_for_doc("doc-A", 10)
        chunks_data = _make_chunks_data(10)
        client_mock = _build_client(rpc_rows, chunks_data)

        with patch.object(
            search_module, "get_bgem3_provider", return_value=_provider_mock()
        ), patch.object(
            search_module, "get_supabase_client", return_value=client_mock
        ):
            resp = search_module.search(
                q="시트", limit=10, offset=0, tags=None, doc_type=None,
                from_date=None, to_date=None,
                doc_id=None, mode="hybrid",  # 카드 모드
            )

        self.assertEqual(resp.items[0].matched_chunk_count, 10)
        self.assertLessEqual(
            len(resp.items[0].matched_chunks), 3,
            "list 모드 — 기존 3 cap 유지",
        )

    def test_list_mode_chunks_sorted_by_chunk_idx(self) -> None:
        """list 모드 — 응답 청크 정렬 = chunk_idx 오름차순 (기존 UX 보존)."""
        from app.routers import search as search_module

        # 일부러 score 와 chunk_idx 가 역순이 되도록 — score 가 0.99/0.98/0.97
        # 인 chunk 의 chunk_idx 를 3/2/1 로 강제. score 정렬 시 [3, 2, 1] · idx 정렬 시 [1, 2, 3].
        rpc_rows = [
            {"chunk_id": "c-A", "doc_id": "doc-A", "rrf_score": 0.99,
             "dense_rank": 1, "sparse_rank": None},
            {"chunk_id": "c-B", "doc_id": "doc-A", "rrf_score": 0.98,
             "dense_rank": 2, "sparse_rank": None},
            {"chunk_id": "c-C", "doc_id": "doc-A", "rrf_score": 0.97,
             "dense_rank": 3, "sparse_rank": None},
        ]
        chunks_data = [
            {"id": "c-A", "doc_id": "doc-A", "chunk_idx": 3, "page": 2,
             "section_title": None, "text": "...", "metadata": {}},
            {"id": "c-B", "doc_id": "doc-A", "chunk_idx": 2, "page": 2,
             "section_title": None, "text": "...", "metadata": {}},
            {"id": "c-C", "doc_id": "doc-A", "chunk_idx": 1, "page": 2,
             "section_title": None, "text": "...", "metadata": {}},
        ]
        client_mock = _build_client(rpc_rows, chunks_data)

        with patch.object(
            search_module, "get_bgem3_provider", return_value=_provider_mock()
        ), patch.object(
            search_module, "get_supabase_client", return_value=client_mock
        ):
            resp = search_module.search(
                q="시트", limit=10, offset=0, tags=None, doc_type=None,
                from_date=None, to_date=None,
                doc_id=None, mode="hybrid",
            )

        idxs = [c.chunk_idx for c in resp.items[0].matched_chunks]
        self.assertEqual(
            idxs, sorted(idxs),
            f"list 모드 — chunk_idx 오름차순 정렬 깨짐: {idxs}",
        )


if __name__ == "__main__":
    unittest.main()
