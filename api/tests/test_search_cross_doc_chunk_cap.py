"""S4-A P1 — cross_doc-class query 시 list 모드 청크 cap 3 → 8 + RRF desc 정렬 단위 테스트.

배경
- `_MAX_MATCHED_CHUNKS_PER_DOC=3` 가 cross_doc 응답에서도 doc 당 3개로 잘라
  비교/대조 query 의 근거 청크가 탈락 (eval cross_doc cell R@10 저조 원인).
- intent_router T1/T2/T7 발화 시 (=cross_doc-class) doc 당 8개 + RRF 내림차순 정렬.

검증 포인트
- cross_doc-class query (예: "...와 ...어떻게 다른가요?") → doc 당 ≤ 8 청크, RRF desc
- 일반 query (cross_doc-class 아님) → 기존 ≤ 3 청크, chunk_idx asc (회귀 0)

stdlib unittest + mock only — 외부 API/DB 0.
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


def _make_doc_meta_chain(doc_ids: list[str]) -> MagicMock:
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


def _make_chunks_chain(chunks_data: list[dict]) -> MagicMock:
    chain = MagicMock()
    chain.select.return_value = chain
    chain.in_.return_value = chain
    chain.execute.return_value.data = chunks_data
    return chain


def _build_client(rpc_rows: list[dict], chunks_data: list[dict], doc_ids: list[str]) -> MagicMock:
    client = MagicMock()
    rpc_resp = MagicMock()
    rpc_resp.data = rpc_rows
    rpc_call = MagicMock()
    rpc_call.execute.return_value = rpc_resp

    def _rpc_side_effect(name: str, _args: dict) -> MagicMock:
        if name in ("search_dense_only", "search_sparse_only"):
            raise RuntimeError(f"function {name} does not exist (008 미적용)")
        return rpc_call

    client.rpc.side_effect = _rpc_side_effect
    docs_chain = _make_doc_meta_chain(doc_ids)
    chunks_chain = _make_chunks_chain(chunks_data)

    def _table(name: str) -> MagicMock:
        if name == "documents":
            return docs_chain
        return chunks_chain

    client.table.side_effect = _table
    return client


def _rpc_rows_two_docs(per_doc: int) -> list[dict]:
    """doc-A / doc-B 각 `per_doc` 개 unique chunk — score 내림차순 (doc-A 가 약간 높게)."""
    rows: list[dict] = []
    for did, base in (("doc-A", 1.0), ("doc-B", 0.95)):
        for i in range(per_doc):
            rows.append(
                {
                    "chunk_id": f"{did}-c{i}",
                    "doc_id": did,
                    "rrf_score": base - i * 0.01,
                    "dense_rank": i + 1,
                    "sparse_rank": None,
                }
            )
    return rows


def _chunks_two_docs(per_doc: int) -> list[dict]:
    out: list[dict] = []
    for did in ("doc-A", "doc-B"):
        for i in range(per_doc):
            out.append(
                {
                    "id": f"{did}-c{i}",
                    "doc_id": did,
                    # chunk_idx 를 RRF 와 역순으로 — 정렬 정책 분기 검증용 (RRF 1순위 chunk 의 idx 가 가장 큼)
                    "chunk_idx": per_doc - i,
                    "page": 2,
                    "section_title": None,
                    "text": f"{did} 청크 {i} 본문",
                    "metadata": {},
                }
            )
    return out


_CROSS_DOC_QUERY = "기웅민 이력서와 이한주 포트폴리오의 핵심 역량은 어떻게 다른가요?"
_PLAIN_QUERY = "데이터센터 모니터링 항목"


class CrossDocChunkCapTest(unittest.TestCase):
    """cross_doc-class query 시 doc 당 8 cap + RRF desc."""

    def test_cross_doc_query_caps_at_8_per_doc(self) -> None:
        from app.routers import search as search_module

        rpc_rows = _rpc_rows_two_docs(per_doc=12)
        chunks_data = _chunks_two_docs(per_doc=12)
        client_mock = _build_client(rpc_rows, chunks_data, ["doc-A", "doc-B"])

        with patch.object(
            search_module, "get_bgem3_provider", return_value=_provider_mock()
        ), patch.object(
            search_module, "get_supabase_client", return_value=client_mock
        ):
            resp = search_module.search(
                q=_CROSS_DOC_QUERY, limit=10, offset=0, tags=None, doc_type=None,
                from_date=None, to_date=None, doc_id=None, mode="hybrid",
            )

        self.assertGreaterEqual(len(resp.items), 2)
        for hit in resp.items:
            self.assertEqual(hit.matched_chunk_count, 12)
            self.assertLessEqual(
                len(hit.matched_chunks), 8,
                "cross_doc-class — doc 당 최대 8 청크",
            )
            self.assertGreater(
                len(hit.matched_chunks), 3,
                "cross_doc-class — 3 cap 보다 많아야 (8 cap 적용 확인)",
            )

    def test_cross_doc_query_chunks_sorted_by_rrf_desc(self) -> None:
        from app.routers import search as search_module

        rpc_rows = _rpc_rows_two_docs(per_doc=5)
        chunks_data = _chunks_two_docs(per_doc=5)
        client_mock = _build_client(rpc_rows, chunks_data, ["doc-A", "doc-B"])

        with patch.object(
            search_module, "get_bgem3_provider", return_value=_provider_mock()
        ), patch.object(
            search_module, "get_supabase_client", return_value=client_mock
        ):
            resp = search_module.search(
                q=_CROSS_DOC_QUERY, limit=10, offset=0, tags=None, doc_type=None,
                from_date=None, to_date=None, doc_id=None, mode="hybrid",
            )

        for hit in resp.items:
            scores = [c.rrf_score or 0.0 for c in hit.matched_chunks]
            self.assertEqual(
                scores, sorted(scores, reverse=True),
                f"cross_doc-class — RRF 내림차순 정렬 깨짐: {scores}",
            )

    def test_plain_query_keeps_3_cap_and_idx_order(self) -> None:
        """cross_doc-class 아닌 query — 기존 3 cap + chunk_idx 오름차순 보존 (회귀 0)."""
        from app.routers import search as search_module

        rpc_rows = _rpc_rows_two_docs(per_doc=8)
        chunks_data = _chunks_two_docs(per_doc=8)
        client_mock = _build_client(rpc_rows, chunks_data, ["doc-A", "doc-B"])

        with patch.object(
            search_module, "get_bgem3_provider", return_value=_provider_mock()
        ), patch.object(
            search_module, "get_supabase_client", return_value=client_mock
        ):
            resp = search_module.search(
                q=_PLAIN_QUERY, limit=10, offset=0, tags=None, doc_type=None,
                from_date=None, to_date=None, doc_id=None, mode="hybrid",
            )

        for hit in resp.items:
            self.assertLessEqual(
                len(hit.matched_chunks), 3, "일반 query — 기존 3 cap 유지"
            )
            idxs = [c.chunk_idx for c in hit.matched_chunks]
            self.assertEqual(
                idxs, sorted(idxs),
                f"일반 query — chunk_idx 오름차순 보존 깨짐: {idxs}",
            )

    def test_is_cross_doc_class_query_helper(self) -> None:
        from app.routers import search as search_module

        self.assertTrue(search_module._is_cross_doc_class_query(_CROSS_DOC_QUERY))
        self.assertTrue(
            search_module._is_cross_doc_class_query("운영내규랑 직제규정에서 위원회 역할 어떻게 달라")
        )
        self.assertFalse(search_module._is_cross_doc_class_query(_PLAIN_QUERY))
        self.assertFalse(search_module._is_cross_doc_class_query(""))


if __name__ == "__main__":
    unittest.main()
