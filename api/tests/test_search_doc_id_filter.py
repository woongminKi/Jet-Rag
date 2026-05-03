"""W11 Day 4 — `/search?doc_id=X` 단일 문서 스코프 필터 단위 테스트 (US-08).

배경
- 기획서 §3 US-08: "한 문서에 집중 질문" — 단일 문서 내 자연어 QA.
- 응용 layer 필터링 (RPC 결과 후 doc_id 일치만 보존) — 마이그레이션 회피.

검증 포인트
- doc_id 가 None: 모든 doc 검색 (기존 동작 보존)
- doc_id 지정: RPC 결과 중 해당 doc_id 만 통과
- doc_id 형식 검증 (빈 문자열 / 너무 긴 값 → 400)
- documents 메타 fetch 도 필터 후 doc_id 만 조회

stdlib unittest + mock only.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

import httpx

os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")


def _empty_chain_response() -> MagicMock:
    """Supabase chain mock — 모든 메서드 self 반환 + execute 빈 결과."""
    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.in_.return_value = chain
    chain.is_.return_value = chain
    chain.not_.is_.return_value = chain
    chain.gte.return_value = chain
    chain.lte.return_value = chain
    chain.contains.return_value = chain
    chain.limit.return_value = chain
    chain.execute.return_value.data = []
    return chain


def _client_with_rpc_rows(rows: list[dict]) -> MagicMock:
    """Supabase client mock — RPC 결과만 컨트롤. 그 외 .table() 은 빈 chain."""
    client = MagicMock()
    rpc_resp = MagicMock()
    rpc_resp.data = rows
    rpc_resp.execute.return_value.data = rows
    # client.rpc(...).execute() → resp.data
    rpc_call = MagicMock()
    rpc_call.execute.return_value = rpc_resp
    client.rpc.return_value = rpc_call
    # documents 메타 fetch — 빈 chain
    client.table.return_value = _empty_chain_response()
    return client


class DocIdFilterTest(unittest.TestCase):
    """W11 Day 4 — `?doc_id=X` 응용 layer 필터링."""

    def test_doc_id_filters_rpc_rows(self) -> None:
        """RPC 가 3 doc 결과 반환 → doc_id 1개 지정 시 그 doc 만 query_parsed.fused 카운트."""
        from app.routers import search as search_module

        provider_mock = MagicMock()
        provider_mock.embed_query.return_value = [0.0] * 1024
        provider_mock._last_cache_hit = False

        # RPC 가 3 doc 결과 반환
        rpc_rows = [
            {"chunk_id": "c1", "doc_id": "doc-A", "rrf_score": 0.5,
             "dense_rank": 1, "sparse_rank": 1},
            {"chunk_id": "c2", "doc_id": "doc-B", "rrf_score": 0.4,
             "dense_rank": 2, "sparse_rank": 2},
            {"chunk_id": "c3", "doc_id": "doc-A", "rrf_score": 0.3,
             "dense_rank": 3, "sparse_rank": 3},
        ]
        client_mock = _client_with_rpc_rows(rpc_rows)

        with patch.object(
            search_module, "get_bgem3_provider", return_value=provider_mock
        ), patch.object(
            search_module, "get_supabase_client", return_value=client_mock
        ):
            resp = search_module.search(
                q="test", limit=10, offset=0, tags=None, doc_type=None,
                from_date=None, to_date=None,
                doc_id="doc-A",
            )

        # doc-A 의 chunks 만 통과 (2개 — c1, c3)
        self.assertEqual(
            resp.query_parsed.fused, 2,
            f"doc-A 일치 RPC rows 2건 기대 — got {resp.query_parsed.fused}",
        )

    def test_doc_id_none_keeps_all_rpc_rows(self) -> None:
        """doc_id 미지정 시 기존 동작 보존 — 모든 RPC rows 통과."""
        from app.routers import search as search_module

        provider_mock = MagicMock()
        provider_mock.embed_query.return_value = [0.0] * 1024
        provider_mock._last_cache_hit = False

        rpc_rows = [
            {"chunk_id": "c1", "doc_id": "doc-A", "rrf_score": 0.5,
             "dense_rank": 1, "sparse_rank": 1},
            {"chunk_id": "c2", "doc_id": "doc-B", "rrf_score": 0.4,
             "dense_rank": 2, "sparse_rank": 2},
        ]
        client_mock = _client_with_rpc_rows(rpc_rows)

        with patch.object(
            search_module, "get_bgem3_provider", return_value=provider_mock
        ), patch.object(
            search_module, "get_supabase_client", return_value=client_mock
        ):
            resp = search_module.search(
                q="test", limit=10, offset=0, tags=None, doc_type=None,
                from_date=None, to_date=None, doc_id=None,
            )

        self.assertEqual(resp.query_parsed.fused, 2, "기존 동작 보존")

    def test_doc_id_no_match_returns_empty(self) -> None:
        """RPC 결과 모두 다른 doc_id → 0건."""
        from app.routers import search as search_module

        provider_mock = MagicMock()
        provider_mock.embed_query.return_value = [0.0] * 1024
        provider_mock._last_cache_hit = False

        rpc_rows = [
            {"chunk_id": "c1", "doc_id": "doc-A", "rrf_score": 0.5,
             "dense_rank": 1, "sparse_rank": 1},
        ]
        client_mock = _client_with_rpc_rows(rpc_rows)

        with patch.object(
            search_module, "get_bgem3_provider", return_value=provider_mock
        ), patch.object(
            search_module, "get_supabase_client", return_value=client_mock
        ):
            resp = search_module.search(
                q="test", limit=10, offset=0, tags=None, doc_type=None,
                from_date=None, to_date=None,
                doc_id="doc-Z",  # RPC 결과 와 일치 안 함
            )

        self.assertEqual(resp.total, 0)
        self.assertEqual(resp.items, [])
        self.assertEqual(resp.query_parsed.fused, 0)


class DocIdValidationTest(unittest.TestCase):
    """doc_id 형식 검증 — 응용 layer 보호 (SQL injection 위험은 0이지만 보수적)."""

    def test_empty_string_rejected(self) -> None:
        from fastapi import HTTPException
        from app.routers import search as search_module

        with self.assertRaises(HTTPException) as ctx:
            search_module.search(
                q="x", limit=10, offset=0, tags=None, doc_type=None,
                from_date=None, to_date=None, doc_id="   ",  # 공백만
            )
        self.assertEqual(ctx.exception.status_code, 400)

    def test_too_long_rejected(self) -> None:
        from fastapi import HTTPException
        from app.routers import search as search_module

        with self.assertRaises(HTTPException) as ctx:
            search_module.search(
                q="x", limit=10, offset=0, tags=None, doc_type=None,
                from_date=None, to_date=None, doc_id="x" * 100,  # > 64
            )
        self.assertEqual(ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
