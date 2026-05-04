"""W25 D4 Phase 2 — 표지 청크 가드 heuristic 단위 테스트.

배경
- 사용자 시나리오 ("소나타에서 제공하는 시트 종류 뭐가 있어?") 에서 sonata 카탈로그 p.1 의
  6자 표지 청크 ("SONATA") 가 dense (BGE-M3) 단독으로 cos sim 비정상 우세 → top-1 진입.
- 후처리 패널티 안 (a): `text_len <= 30` AND (`chunk_idx == 0` OR `page == 1`) 동시 만족 시
  rrf_score *= _COVER_GUARD_PENALTY (0.3) — chunks 테이블 lookup 1회 추가.

검증 포인트
- 짧은 표지 청크 (text_len=6, chunk_idx=0, page=1) → 패널티 적용 → ranking 하락
- 짧은 헤딩 (text_len=15, chunk_idx=10, page=5) → 패널티 안 받음 (false positive 방지)
- 긴 표지 페이지 청크 (text_len=300, chunk_idx=0, page=1) → 패널티 안 받음

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


def _empty_chain_response() -> MagicMock:
    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.in_.return_value = chain
    chain.is_.return_value = chain
    chain.gte.return_value = chain
    chain.lte.return_value = chain
    chain.contains.return_value = chain
    chain.execute.return_value.data = []
    return chain


def _make_client(
    rpc_rows: list[dict],
    chunks_meta: list[dict],
    docs_meta: list[dict],
) -> MagicMock:
    """RPC + chunks + documents 3 layer mock — search.py path 전체 커버.

    - RPC: search_dense_only / search_sparse_only 는 raise (008 미적용 fallback) →
            search_hybrid_rrf 가 rpc_rows 반환
    - chunks (W25 D4 가드 fetch + 본문 fetch 모두): chunks_meta 반환
    - documents: docs_meta 반환
    """
    client = MagicMock()

    rpc_resp = MagicMock()
    rpc_resp.data = rpc_rows
    rpc_call = MagicMock()
    rpc_call.execute.return_value = rpc_resp

    def _rpc_side_effect(name, args):
        if name in ("search_dense_only", "search_sparse_only"):
            raise RuntimeError(f"function {name} does not exist (008 미적용)")
        return rpc_call

    client.rpc.side_effect = _rpc_side_effect

    docs_chain = MagicMock()
    docs_chain.select.return_value = docs_chain
    docs_chain.in_.return_value = docs_chain
    docs_chain.eq.return_value = docs_chain
    docs_chain.is_.return_value = docs_chain
    docs_chain.contains.return_value = docs_chain
    docs_chain.gte.return_value = docs_chain
    docs_chain.lte.return_value = docs_chain
    docs_chain.execute.return_value.data = docs_meta

    chunks_chain = MagicMock()
    chunks_chain.select.return_value = chunks_chain
    chunks_chain.in_.return_value = chunks_chain
    chunks_chain.execute.return_value.data = chunks_meta

    def _table(name: str):
        if name == "documents":
            return docs_chain
        return chunks_chain

    client.table.side_effect = _table
    return client


class CoverGuardPenaltyTest(unittest.TestCase):
    """짧은 표지 청크 (text_len<=30 AND (chunk_idx=0 OR page=1)) 패널티 검증."""

    def test_short_cover_chunk_loses_top1(self) -> None:
        """표지 청크 (rrf=0.5) + 본문 청크 (rrf=0.4) 동시 → 표지 0.5*0.3=0.15 → 본문이 top-1."""
        from app.routers import search as search_module

        rpc_rows = [
            # 표지 청크 — sonata 카탈로그 p.1 시뮬
            {"chunk_id": "c-cover", "doc_id": "doc-sonata", "rrf_score": 0.5,
             "dense_rank": 1, "sparse_rank": None},
            # 본문 청크 — 시트 정보 페이지 시뮬
            {"chunk_id": "c-body", "doc_id": "doc-other", "rrf_score": 0.4,
             "dense_rank": 2, "sparse_rank": None},
        ]
        chunks_meta = [
            {"id": "c-cover", "doc_id": "doc-sonata", "chunk_idx": 0,
             "page": 1, "section_title": None, "text": "SONATA",
             "metadata": {}},
            {"id": "c-body", "doc_id": "doc-other", "chunk_idx": 5,
             "page": 22, "section_title": "시트 종류",
             "text": "디럭스 시트 / 스마트 시트 / 통풍 시트 옵션 구성", "metadata": {}},
        ]
        docs_meta = [
            {"id": "doc-sonata", "title": "SONATA 카탈로그", "doc_type": "pdf",
             "tags": [], "summary": None, "created_at": "2026-05-04T00:00:00+00:00"},
            {"id": "doc-other", "title": "시트 옵션 가이드", "doc_type": "pdf",
             "tags": [], "summary": None, "created_at": "2026-05-04T00:00:00+00:00"},
        ]
        client = _make_client(rpc_rows, chunks_meta, docs_meta)

        with patch.object(
            search_module, "get_bgem3_provider", return_value=_provider_mock()
        ), patch.object(
            search_module, "get_supabase_client", return_value=client
        ):
            resp = search_module.search(
                q="소나타 시트 종류", limit=10, offset=0, tags=None, doc_type=None,
                from_date=None, to_date=None, doc_id=None, mode="hybrid",
            )

        # 표지 가드 적용 후 doc-other 가 top-1 (0.4 > 0.5*0.3=0.15)
        self.assertEqual(len(resp.items), 2)
        self.assertEqual(
            resp.items[0].doc_id, "doc-other",
            "표지 가드 적용 후 본문 doc 이 top-1 으로 이동해야 함",
        )

    def test_short_heading_at_mid_doc_no_penalty(self) -> None:
        """짧은 헤딩 (text_len=15, chunk_idx=10, page=5) — chunk_idx>0 AND page>1 → 패널티 X."""
        from app.routers import search as search_module

        rpc_rows = [
            # 짧은 헤딩 — chunk_idx 10 + page 5 (표지 아님)
            {"chunk_id": "c-heading", "doc_id": "doc-A", "rrf_score": 0.5,
             "dense_rank": 1, "sparse_rank": None},
            # 본문 청크 — score 더 낮음
            {"chunk_id": "c-body", "doc_id": "doc-B", "rrf_score": 0.3,
             "dense_rank": 2, "sparse_rank": None},
        ]
        chunks_meta = [
            {"id": "c-heading", "doc_id": "doc-A", "chunk_idx": 10,
             "page": 5, "section_title": None, "text": "결론 및 향후 과제",
             "metadata": {}},
            {"id": "c-body", "doc_id": "doc-B", "chunk_idx": 3,
             "page": 2, "section_title": None, "text": "본문",
             "metadata": {}},
        ]
        docs_meta = [
            {"id": "doc-A", "title": "리포트 A", "doc_type": "pdf",
             "tags": [], "summary": None, "created_at": "2026-05-04T00:00:00+00:00"},
            {"id": "doc-B", "title": "리포트 B", "doc_type": "pdf",
             "tags": [], "summary": None, "created_at": "2026-05-04T00:00:00+00:00"},
        ]
        client = _make_client(rpc_rows, chunks_meta, docs_meta)

        with patch.object(
            search_module, "get_bgem3_provider", return_value=_provider_mock()
        ), patch.object(
            search_module, "get_supabase_client", return_value=client
        ):
            resp = search_module.search(
                q="결론", limit=10, offset=0, tags=None, doc_type=None,
                from_date=None, to_date=None, doc_id=None, mode="hybrid",
            )

        # 짧은 헤딩에 패널티 안 적용 → doc-A 가 top-1 보존
        self.assertEqual(resp.items[0].doc_id, "doc-A",
                         "짧은 헤딩 (chunk_idx=10/page=5) 은 표지 가드 적용 안 됨")
        # rrf_score 비교: c-heading 0.5 (패널티 X) > c-body 0.3
        self.assertAlmostEqual(
            resp.items[0].matched_chunks[0].rrf_score or 0.0, 0.5, places=4
        )

    def test_long_first_page_chunk_no_penalty(self) -> None:
        """긴 표지 페이지 청크 (text_len=300, chunk_idx=0, page=1) — text_len>30 → 패널티 X."""
        from app.routers import search as search_module

        long_text = "긴 표지 본문 " * 50  # 약 350자
        self.assertGreater(len(long_text), 30)

        rpc_rows = [
            # 긴 본문이 있는 첫 페이지 청크 — 표지가 아닌 진짜 본문 (TOC 등)
            {"chunk_id": "c-long-cover", "doc_id": "doc-A", "rrf_score": 0.5,
             "dense_rank": 1, "sparse_rank": None},
            {"chunk_id": "c-other", "doc_id": "doc-B", "rrf_score": 0.3,
             "dense_rank": 2, "sparse_rank": None},
        ]
        chunks_meta = [
            {"id": "c-long-cover", "doc_id": "doc-A", "chunk_idx": 0,
             "page": 1, "section_title": None, "text": long_text,
             "metadata": {}},
            {"id": "c-other", "doc_id": "doc-B", "chunk_idx": 3,
             "page": 5, "section_title": None, "text": "기타 본문",
             "metadata": {}},
        ]
        docs_meta = [
            {"id": "doc-A", "title": "doc A", "doc_type": "pdf",
             "tags": [], "summary": None, "created_at": "2026-05-04T00:00:00+00:00"},
            {"id": "doc-B", "title": "doc B", "doc_type": "pdf",
             "tags": [], "summary": None, "created_at": "2026-05-04T00:00:00+00:00"},
        ]
        client = _make_client(rpc_rows, chunks_meta, docs_meta)

        with patch.object(
            search_module, "get_bgem3_provider", return_value=_provider_mock()
        ), patch.object(
            search_module, "get_supabase_client", return_value=client
        ):
            resp = search_module.search(
                q="t", limit=10, offset=0, tags=None, doc_type=None,
                from_date=None, to_date=None, doc_id=None, mode="hybrid",
            )

        # 긴 첫 페이지 청크는 패널티 X → doc-A 가 top-1 보존
        self.assertEqual(resp.items[0].doc_id, "doc-A",
                         "긴 첫 페이지 청크 (text_len>30) 는 표지 가드 적용 안 됨")
        self.assertAlmostEqual(
            resp.items[0].matched_chunks[0].rrf_score or 0.0, 0.5, places=4
        )


class CoverGuardConstantsTest(unittest.TestCase):
    """가드 상수 default 값 검증 (회귀 안전망)."""

    def test_default_threshold_and_penalty(self) -> None:
        from app.routers.search import (
            _COVER_GUARD_PENALTY,
            _COVER_GUARD_TEXT_LEN,
        )

        self.assertEqual(_COVER_GUARD_TEXT_LEN, 30)
        self.assertAlmostEqual(_COVER_GUARD_PENALTY, 0.3, places=4)


if __name__ == "__main__":
    unittest.main()
