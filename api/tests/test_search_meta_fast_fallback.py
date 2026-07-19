"""UX-1 fix — meta fast path 0건 시 RAG fallback 단위 테스트.

배경
----
`meta_filter_fast_path.is_meta_only()` 는 "SK 사업보고서 매출" 처럼 문서유형어
(doc-suffix, 예: "보고서") 가 있는 query 를 fast path 로 판정한다. 잔여 텍스트
("SK 사업보고서 매출") 전체가 `title ILIKE %...%` 패턴 하나가 되는데, title 에
"매출" 이 없는 문서라면 0건 — 이전에는 그대로 빈 결과를 반환해 "어렴풋한 기억으로
검색" 기획 의도에 반하는 트랩이었다 (2026-05-19 세션 UX-1 발견).

수정: `search.py::_run_meta_fast_path` 가 0건이면 ``None`` 을 반환 → 호출자
`search()` 가 fast path 를 버리고 일반 RAG(하이브리드) 경로로 계속 진행한다.
관측성: `X-Search-Path` 헤더가 ``meta_fast_fallback`` 값으로 fallback 발생을 구분.

검증 포인트
----------
- FB1: fast path 0건 → RAG 경로 결과 반환 + `X-Search-Path: meta_fast_fallback`.
- FB2: fast path non-zero → 기존과 동일 (`X-Search-Path: meta_fast`), RAG 파이프라인
  (임베딩·RPC) 호출 0 — 회귀 없음 확인.

stdlib unittest + mock only — 외부 API / DB 0 (paid $0). 패턴은
`test_search_cross_doc_scoped.py` 의 mock helper 를 차용.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")

# doc-suffix "보고서" 포함 — is_meta_only 가 실제로 fast path 판정하도록 실제 detector 사용.
_FALLBACK_TRAP_QUERY = "SK 사업보고서 매출"


def _provider_mock() -> MagicMock:
    m = MagicMock()
    m.embed_query.return_value = [0.0] * 1024
    m._last_cache_hit = False
    return m


def _docs_meta_chain(doc_ids: list[str]) -> MagicMock:
    """RAG path 의 documents 메타 enrich SELECT mock (fast path 의 SELECT 와 별개)."""
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
            "created_at": "2026-05-19T00:00:00+00:00",
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


def _make_rpc_rows(doc_id: str, scores: list[float]) -> list[dict]:
    return [
        {
            "chunk_id": f"{doc_id}-c{idx}",
            "doc_id": doc_id,
            "rrf_score": score,
            "dense_rank": idx + 1,
            "sparse_rank": None,
        }
        for idx, score in enumerate(scores)
    ]


def _make_chunks(doc_id: str, n: int) -> list[dict]:
    return [
        {
            "id": f"{doc_id}-c{i}",
            "doc_id": doc_id,
            "chunk_idx": i,
            "page": 1,
            "section_title": None,
            "text": f"{doc_id} 매출 본문 {i}",
            "metadata": {},
        }
        for i in range(n)
    ]


def _build_rag_client(rpc_rows: list[dict], chunks_data: list[dict], doc_ids: list[str]):
    """RAG path 용 client — search_dense_only/sparse_only 미적용 simul → hybrid RPC 사용."""
    client = MagicMock()

    def _rpc_side_effect(name: str, _args: dict) -> MagicMock:
        if name in ("search_dense_only", "search_sparse_only"):
            raise RuntimeError(f"function {name} does not exist")
        rpc_resp = MagicMock()
        rpc_resp.data = rpc_rows
        call = MagicMock()
        call.execute.return_value = rpc_resp
        return call

    client.rpc.side_effect = _rpc_side_effect

    docs_chain = _docs_meta_chain(doc_ids)
    chunks_chain = _chunks_chain(chunks_data)

    def _table(name: str) -> MagicMock:
        if name == "documents":
            return docs_chain
        return chunks_chain

    client.table.side_effect = _table
    return client


def _fast_path_docs_client(rows: list[dict]) -> MagicMock:
    """`meta_filter_fast_path.run()` 내부 `get_supabase_client()` 용 — documents SELECT mock."""
    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.is_.return_value = chain
    chain.gte.return_value = chain
    chain.lt.return_value = chain
    chain.contains.return_value = chain
    chain.ilike.return_value = chain
    chain.order.return_value = chain
    chain.limit.return_value = chain
    chain.execute.return_value.data = rows
    client = MagicMock()
    client.table.return_value = chain
    return client


class MetaFastPathZeroHitFallbackTest(unittest.TestCase):
    """FB1 — fast path 0건 → RAG path 로 fallback."""

    def test_zero_hit_falls_back_to_rag_and_returns_results(self) -> None:
        from app.routers import search as search_module
        from fastapi import Response

        rag_client = _build_rag_client(
            _make_rpc_rows("doc-A", [0.9, 0.7]),
            _make_chunks("doc-A", 2),
            ["doc-A"],
        )
        # fast path 의 title ILIKE 가 0건 — "SK 사업보고서 매출" title 매칭 문서 없음.
        fast_path_client = _fast_path_docs_client([])
        provider = _provider_mock()
        response = Response()

        with patch.object(
            search_module, "get_bgem3_provider", return_value=provider
        ), patch.object(
            search_module, "get_supabase_client", return_value=rag_client
        ), patch.object(
            search_module.meta_filter_fast_path,
            "get_supabase_client",
            return_value=fast_path_client,
        ):
            resp = search_module.search(
                q=_FALLBACK_TRAP_QUERY,
                limit=10,
                offset=0,
                tags=None,
                doc_type=None,
                from_date=None,
                to_date=None,
                doc_id=None,
                mode="hybrid",
                response=response,
            )

        # fast path 를 버리고 RAG 경로로 진행 — doc-A 결과가 반환됨.
        self.assertEqual({item.doc_id for item in resp.items}, {"doc-A"})
        self.assertEqual(resp.query_parsed.fused, 2)
        # meta 는 RAG path 의 dict shape (fast path 전용 title_ilike 키가 아님).
        self.assertIsNotNone(resp.meta)
        self.assertIn("cross_doc_scoped_applied", resp.meta)
        # 관측성 — fallback 발생이 헤더로 구분됨.
        self.assertEqual(response.headers["X-Search-Path"], "meta_fast_fallback")
        # 임베딩·RPC 는 실제로 호출됨 (RAG 경로 실행 증거).
        provider.embed_query.assert_called_once()
        rag_client.rpc.assert_called()


class MetaFastPathNonZeroHitUnchangedTest(unittest.TestCase):
    """FB2 — fast path non-zero → 기존과 동일 (회귀 없음)."""

    def test_non_zero_hit_returns_fast_path_response_without_touching_rag(
        self,
    ) -> None:
        from app.routers import search as search_module
        from fastapi import Response

        rag_client = MagicMock()  # 호출되면 즉시 감지되도록 순수 MagicMock (미설정).
        fast_path_rows = [
            {
                "id": "doc-A",
                "title": "SK 2025 사업보고서",
                "doc_type": "pdf",
                "tags": [],
                "summary": None,
                "created_at": "2026-05-19T00:00:00+00:00",
            }
        ]
        fast_path_client = _fast_path_docs_client(fast_path_rows)
        provider = _provider_mock()
        response = Response()

        with patch.object(
            search_module, "get_bgem3_provider", return_value=provider
        ), patch.object(
            search_module, "get_supabase_client", return_value=rag_client
        ), patch.object(
            search_module.meta_filter_fast_path,
            "get_supabase_client",
            return_value=fast_path_client,
        ):
            resp = search_module.search(
                q=_FALLBACK_TRAP_QUERY,
                limit=10,
                offset=0,
                tags=None,
                doc_type=None,
                from_date=None,
                to_date=None,
                doc_id=None,
                mode="hybrid",
                response=response,
            )

        self.assertEqual([item.doc_id for item in resp.items], ["doc-A"])
        self.assertEqual(resp.meta["path"], "meta_fast")
        self.assertEqual(response.headers["X-Search-Path"], "meta_fast")
        # RAG 파이프라인 미호출 — 임베딩/RPC 0 (기존 fast-path 동작·성능 불변).
        provider.embed_query.assert_not_called()
        rag_client.rpc.assert_not_called()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
