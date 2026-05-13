"""M1 W-1(b) — `/search` cross_doc-scoped 검색 (옵션 A) 단위 테스트.

배경
- W-1(a) paid LLM decomposition 단독은 cross_doc R@10 0.4424 → 0.4216 으로 4.7%
  악화. 원인 = sub-query 풀이 글로벌이라 noise doc chunk 가 라벨 doc chunk 의
  RRF rank 를 밀어내는 P2 함정.
- 옵션 A = production 측에서 cross_doc-class query 한정으로 rpc_rows 를 doc 단위
  그룹 → doc score = sum(top-3 chunk rrf_score) → 상위 N doc 만 유지.
- 게이트 4조건 AND: ENV `JETRAG_CROSS_DOC_SCOPED_SEARCH` ON + mode=hybrid +
  doc_id is None + `_is_cross_doc_class_query()` (T1/T2/T7).
- meta 3키 추가: cross_doc_scoped_applied / cross_doc_candidate_doc_ids /
  cross_doc_candidate_top_n.

검증 포인트 (T1~T12)
- T1: 순수 헬퍼 `_cross_doc_candidate_top_n()` ENV 미설정 → 4.
- T2: 동 헬퍼 clamp — "15"→10, "1"→2, "abc"→4.
- T3: `_cross_doc_scoped_enabled()` true 변형 4종 모두 True.
- T4: `_select_cross_doc_candidates()` doc-balanced score + 사전순 tie-break.
- T5: 동 헬퍼 edge — 빈 rpc_rows / top_n=0 / 모든 score 0 → 빈 list.
- T6: ENV OFF + cross_doc query → 필터 미적용 + meta 3키 기본값 (False / [] / 0).
- T7: ENV ON + cross_doc query (`_is_cross_doc_class_query`=True) → 필터 적용 +
  meta 3키 채워짐 + doc 후보 set 외 chunks 가 응답에서 제외.
- T8: ENV ON + non-cross_doc query (T1/T2/T7 미발화) → 필터 미적용.
- T9: ENV ON + doc_id 명시 → 필터 미적용 (게이트 ③).
- T10: ENV ON + mode=dense → 필터 미적용 (게이트 ②).
- T11: 후보 산출 결과 빈 list (모든 doc score 0) → meta scoped_applied=False,
  candidate_doc_ids=[], top_n=0 (환원).
- T12: W-1(a) decomposition 4키와 W-1(b) 3키가 같은 meta 에 공존.

stdlib unittest + mock only — 외부 API / DB 0 (paid $0).
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")

_ENV_SCOPED = "JETRAG_CROSS_DOC_SCOPED_SEARCH"
_ENV_TOP_N = "JETRAG_CROSS_DOC_CANDIDATE_TOP_N"

# cross_doc-class query — intent_router T2_compare 발화.
_CROSS_DOC_QUERY = "기웅민 이력서와 이한주 포트폴리오의 핵심 역량은 어떻게 다른가요?"
# non-cross_doc query — T1/T2/T7 미발화 (단순 키워드).
_PLAIN_QUERY = "데이터센터 모니터링 항목"


# ---------------------------------------------------------------------------
# Supabase / provider mock helpers (test_search_decomposition.py 패턴 차용)
# ---------------------------------------------------------------------------
def _provider_mock() -> MagicMock:
    m = MagicMock()
    m.embed_query.return_value = [0.0] * 1024
    m._last_cache_hit = False
    return m


def _doc_meta_chain(doc_ids: list[str]) -> MagicMock:
    """documents 메타 fetch mock — `.in_("id", [..])` 인자에 들어온 doc_ids 만 반환.

    실제 search.py 가 `candidate_doc_ids` (rpc_rows 의 doc_id set) 으로 limit 하는
    동작을 보존 — 필터 후 candidates 외 doc 은 docs_meta 에서 자동 누락.
    """
    chain = MagicMock()
    # in_ 호출 시 doc_ids 인자 캡처 → execute() 에서 그 doc_id 만 반환.
    captured_ids: list[str] = list(doc_ids)

    def _in_side_effect(column: str, ids):  # noqa: ANN001
        if column == "id":
            captured_ids.clear()
            captured_ids.extend(ids)
        return chain

    chain.select.return_value = chain
    chain.in_.side_effect = _in_side_effect
    chain.eq.return_value = chain
    chain.is_.return_value = chain
    chain.gte.return_value = chain
    chain.lte.return_value = chain
    chain.contains.return_value = chain

    def _execute():
        m = MagicMock()
        m.data = [
            {
                "id": did,
                "title": did,
                "doc_type": "pdf",
                "tags": [],
                "summary": None,
                "created_at": "2026-05-13T00:00:00+00:00",
            }
            for did in captured_ids
        ]
        return m

    chain.execute.side_effect = _execute
    return chain


def _chunks_chain(chunks_data: list[dict]) -> MagicMock:
    chain = MagicMock()
    chain.select.return_value = chain
    chain.in_.return_value = chain
    chain.eq.return_value = chain
    chain.execute.return_value.data = chunks_data
    return chain


def _make_rpc_rows(doc_scores: dict[str, list[float]]) -> list[dict]:
    """`{doc_id: [chunk0_score, chunk1_score, ...]}` → rpc_rows.

    chunk_id = `{doc_id}-c{idx}`, doc_id=did, rrf_score=score.
    """
    rows: list[dict] = []
    for did, scores in doc_scores.items():
        for idx, score in enumerate(scores):
            rows.append(
                {
                    "chunk_id": f"{did}-c{idx}",
                    "doc_id": did,
                    "rrf_score": score,
                    "dense_rank": idx + 1,
                    "sparse_rank": None,
                }
            )
    return rows


def _make_chunks(doc_chunks: dict[str, int]) -> list[dict]:
    out: list[dict] = []
    for did, n in doc_chunks.items():
        for i in range(n):
            out.append(
                {
                    "id": f"{did}-c{i}",
                    "doc_id": did,
                    "chunk_idx": i,
                    "page": 1,
                    "section_title": None,
                    "text": f"{did} 본문 {i}",
                    "metadata": {},
                }
            )
    return out


def _build_client(
    rpc_rows: list[dict], chunks_data: list[dict], doc_ids: list[str]
):
    """search_dense_only / search_sparse_only raise (008 미적용 simul) → hybrid fallback."""
    client = MagicMock()
    rpc_calls: list[str] = []

    def _rpc_side_effect(name: str, _args: dict) -> MagicMock:
        rpc_calls.append(name)
        if name in ("search_dense_only", "search_sparse_only"):
            raise RuntimeError(f"function {name} does not exist")
        rpc_resp = MagicMock()
        if name == "search_sparse_only_pgroonga":
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


def _call_search(
    search_module,
    *,
    q: str,
    client_mock,
    provider_mock=None,
    doc_id: str | None = None,
    mode: str = "hybrid",
):
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
            from_date=None, to_date=None, doc_id=doc_id, mode=mode,
        )


# ---------------------------------------------------------------------------
# T1~T5 — 순수 헬퍼 단위 테스트
# ---------------------------------------------------------------------------
class CrossDocScopedHelperTest(unittest.TestCase):
    """T1~T5 — ENV 파싱·clamp / 후보 산출 순수 함수."""

    def test_t1_default_top_n_when_env_unset(self) -> None:
        from app.routers import search as search_module
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(_ENV_TOP_N, None)
            self.assertEqual(search_module._cross_doc_candidate_top_n(), 4)

    def test_t2_top_n_clamp(self) -> None:
        from app.routers import search as search_module
        cases = [("15", 10), ("1", 2), ("abc", 4), ("", 4), ("5", 5)]
        for raw, expected in cases:
            with patch.dict(os.environ, {_ENV_TOP_N: raw}, clear=False):
                self.assertEqual(
                    search_module._cross_doc_candidate_top_n(), expected,
                    f"clamp '{raw}' → {expected}",
                )

    def test_t3_scoped_enabled_true_variants(self) -> None:
        from app.routers import search as search_module
        for v in ("true", "1", "yes", "on", "TRUE", "On"):
            with patch.dict(os.environ, {_ENV_SCOPED: v}, clear=False):
                self.assertTrue(
                    search_module._cross_doc_scoped_enabled(),
                    f"true 변형 '{v}'",
                )
        for v in ("false", "0", "no", "off", ""):
            with patch.dict(os.environ, {_ENV_SCOPED: v}, clear=False):
                self.assertFalse(
                    search_module._cross_doc_scoped_enabled(),
                    f"false 변형 '{v}'",
                )

    def test_t4_select_candidates_score_and_tiebreak(self) -> None:
        from app.routers import search as search_module
        # doc-A top-3 sum = 0.9+0.8+0.7 = 2.4 / doc-B = 0.85+0.5+0.3 = 1.65 /
        # doc-C = 0.9+0.5+0.4 = 1.8 / doc-D = 0.9+0.5+0.4 = 1.8 (D 와 동률, 사전순)
        rpc_rows = _make_rpc_rows(
            {
                "doc-A": [0.9, 0.8, 0.7, 0.1],
                "doc-B": [0.85, 0.5, 0.3],
                "doc-C": [0.9, 0.5, 0.4],
                "doc-D": [0.9, 0.5, 0.4],
            }
        )
        result = search_module._select_cross_doc_candidates(rpc_rows, top_n=3)
        # 정답 order: A(2.4) > C(1.8) = D(1.8, 사전순 C 우선) > B(1.65) → A,C,D
        self.assertEqual(result, ["doc-A", "doc-C", "doc-D"])

    def test_t5_select_candidates_edge_cases(self) -> None:
        from app.routers import search as search_module
        # 빈 rpc_rows
        self.assertEqual(search_module._select_cross_doc_candidates([], 4), [])
        # top_n <= 0
        rpc_rows = _make_rpc_rows({"doc-A": [0.5]})
        self.assertEqual(
            search_module._select_cross_doc_candidates(rpc_rows, 0), []
        )
        self.assertEqual(
            search_module._select_cross_doc_candidates(rpc_rows, -1), []
        )
        # 모든 doc score 0 — 의미 없는 ranking
        zero_rows = _make_rpc_rows({"doc-A": [0.0, 0.0], "doc-B": [0.0]})
        self.assertEqual(
            search_module._select_cross_doc_candidates(zero_rows, 4), []
        )
        # doc_id 없는 row 무시
        bad_rows = [
            {"chunk_id": "x-c0", "doc_id": None, "rrf_score": 0.9},
            {"chunk_id": "y-c0", "doc_id": "doc-Y", "rrf_score": 0.5},
        ]
        self.assertEqual(
            search_module._select_cross_doc_candidates(bad_rows, 4), ["doc-Y"]
        )


# ---------------------------------------------------------------------------
# T6~T12 — `/search` end-to-end 게이트 테스트
# ---------------------------------------------------------------------------
class CrossDocScopedSearchTest(unittest.TestCase):
    """T6~T12 — 게이트 4조건 AND + meta 3키 + 다른 path 미회귀."""

    def test_t6_env_off_no_filter_meta_defaults(self) -> None:
        """ENV OFF + cross_doc query → 필터 미적용, meta 3키 기본값."""
        from app.routers import search as search_module
        rpc_rows = _make_rpc_rows(
            {"doc-A": [0.9, 0.8, 0.7], "doc-B": [0.5], "doc-C": [0.3]}
        )
        chunks = _make_chunks({"doc-A": 3, "doc-B": 1, "doc-C": 1})
        client_mock, _ = _build_client(rpc_rows, chunks, ["doc-A", "doc-B", "doc-C"])
        with patch.dict(os.environ, {_ENV_SCOPED: "false"}, clear=False):
            resp = _call_search(
                search_module, q=_CROSS_DOC_QUERY, client_mock=client_mock
            )
        self.assertIsNotNone(resp.meta)
        self.assertEqual(resp.meta["cross_doc_scoped_applied"], False)
        self.assertEqual(resp.meta["cross_doc_candidate_doc_ids"], [])
        self.assertEqual(resp.meta["cross_doc_candidate_top_n"], 0)
        # 필터 미적용 — 3 doc 모두 응답에 포함.
        doc_ids = {item.doc_id for item in resp.items}
        self.assertEqual(doc_ids, {"doc-A", "doc-B", "doc-C"})

    def test_t7_env_on_cross_doc_applies_filter(self) -> None:
        """ENV ON + cross_doc-class query → 후보 doc 외 chunks 제외 + meta 채워짐."""
        from app.routers import search as search_module
        rpc_rows = _make_rpc_rows(
            {
                "doc-A": [0.9, 0.8, 0.7],  # top-3 sum 2.4
                "doc-B": [0.85, 0.5, 0.3],  # 1.65
                "doc-C": [0.95, 0.6, 0.5],  # 2.05
                "doc-D": [0.4, 0.3],  # 0.7
                "doc-E": [0.2],  # 0.2
            }
        )
        chunks = _make_chunks(
            {"doc-A": 3, "doc-B": 3, "doc-C": 3, "doc-D": 2, "doc-E": 1}
        )
        client_mock, _ = _build_client(
            rpc_rows, chunks, ["doc-A", "doc-B", "doc-C", "doc-D", "doc-E"]
        )
        with patch.dict(
            os.environ, {_ENV_SCOPED: "true", _ENV_TOP_N: "3"}, clear=False
        ):
            resp = _call_search(
                search_module, q=_CROSS_DOC_QUERY, client_mock=client_mock
            )
        self.assertEqual(resp.meta["cross_doc_scoped_applied"], True)
        # top-3 = A, C, B (score desc) → meta 는 정렬된 list 반환.
        self.assertEqual(
            resp.meta["cross_doc_candidate_doc_ids"],
            sorted(["doc-A", "doc-B", "doc-C"]),
        )
        self.assertEqual(resp.meta["cross_doc_candidate_top_n"], 3)
        # 응답에 doc-D / doc-E 없음.
        doc_ids = {item.doc_id for item in resp.items}
        self.assertEqual(doc_ids, {"doc-A", "doc-B", "doc-C"})

    def test_t8_env_on_non_cross_doc_query_no_filter(self) -> None:
        """ENV ON + T1/T2/T7 미발화 query → 게이트 ④ 실패, 필터 미적용."""
        from app.routers import search as search_module
        rpc_rows = _make_rpc_rows(
            {"doc-A": [0.9], "doc-B": [0.5], "doc-C": [0.3]}
        )
        chunks = _make_chunks({"doc-A": 1, "doc-B": 1, "doc-C": 1})
        client_mock, _ = _build_client(rpc_rows, chunks, ["doc-A", "doc-B", "doc-C"])
        with patch.dict(os.environ, {_ENV_SCOPED: "true"}, clear=False):
            resp = _call_search(
                search_module, q=_PLAIN_QUERY, client_mock=client_mock
            )
        self.assertEqual(resp.meta["cross_doc_scoped_applied"], False)
        self.assertEqual(resp.meta["cross_doc_candidate_doc_ids"], [])
        self.assertEqual(resp.meta["cross_doc_candidate_top_n"], 0)
        doc_ids = {item.doc_id for item in resp.items}
        self.assertEqual(doc_ids, {"doc-A", "doc-B", "doc-C"})

    def test_t9_env_on_doc_id_scope_no_filter(self) -> None:
        """ENV ON + cross_doc query + doc_id 명시 → 게이트 ③ 실패, 필터 미적용."""
        from app.routers import search as search_module
        # doc_id 스코프 — 단일 doc 만 반환.
        rpc_rows = _make_rpc_rows({"doc-A": [0.9, 0.8]})
        chunks = _make_chunks({"doc-A": 2})
        client_mock, _ = _build_client(rpc_rows, chunks, ["doc-A"])
        with patch.dict(os.environ, {_ENV_SCOPED: "true"}, clear=False):
            resp = _call_search(
                search_module,
                q=_CROSS_DOC_QUERY,
                client_mock=client_mock,
                doc_id="doc-A",
            )
        self.assertEqual(resp.meta["cross_doc_scoped_applied"], False)
        self.assertEqual(resp.meta["cross_doc_candidate_doc_ids"], [])
        self.assertEqual(resp.meta["cross_doc_candidate_top_n"], 0)

    def test_t10_env_on_mode_dense_no_filter(self) -> None:
        """ENV ON + cross_doc query + mode=dense → 게이트 ② 실패, 필터 미적용."""
        from app.routers import search as search_module
        rpc_rows = _make_rpc_rows({"doc-A": [0.9], "doc-B": [0.5]})
        chunks = _make_chunks({"doc-A": 1, "doc-B": 1})
        client_mock, _ = _build_client(rpc_rows, chunks, ["doc-A", "doc-B"])
        with patch.dict(os.environ, {_ENV_SCOPED: "true"}, clear=False):
            resp = _call_search(
                search_module,
                q=_CROSS_DOC_QUERY,
                client_mock=client_mock,
                mode="dense",
            )
        self.assertEqual(resp.meta["cross_doc_scoped_applied"], False)
        self.assertEqual(resp.meta["cross_doc_candidate_top_n"], 0)

    def test_t11_no_candidates_resets_top_n(self) -> None:
        """모든 doc score 0 → 후보 빈 list → scoped_applied=False, top_n=0 환원."""
        from app.routers import search as search_module
        # 모든 chunk rrf_score = 0
        rpc_rows = _make_rpc_rows({"doc-A": [0.0, 0.0], "doc-B": [0.0]})
        chunks = _make_chunks({"doc-A": 2, "doc-B": 1})
        client_mock, _ = _build_client(rpc_rows, chunks, ["doc-A", "doc-B"])
        with patch.dict(os.environ, {_ENV_SCOPED: "true"}, clear=False):
            resp = _call_search(
                search_module, q=_CROSS_DOC_QUERY, client_mock=client_mock
            )
        self.assertEqual(resp.meta["cross_doc_scoped_applied"], False)
        self.assertEqual(resp.meta["cross_doc_candidate_doc_ids"], [])
        self.assertEqual(resp.meta["cross_doc_candidate_top_n"], 0)

    def test_t12_decomposition_and_scoped_coexist(self) -> None:
        """W-1(a) decomposition 4키 + W-1(b) cross_doc-scoped 3키 = meta 7키 공존."""
        from app.routers import search as search_module
        from app.services.query_decomposer import QueryDecomposition

        rpc_rows = _make_rpc_rows(
            {
                "doc-A": [0.9, 0.8],  # 1.7
                "doc-B": [0.95, 0.5],  # 1.45
                "doc-C": [0.3],  # 0.3
            }
        )
        chunks = _make_chunks({"doc-A": 2, "doc-B": 2, "doc-C": 1})
        client_mock, _ = _build_client(
            rpc_rows, chunks, ["doc-A", "doc-B", "doc-C"]
        )
        fake_decomp = QueryDecomposition(
            subqueries=("기웅민 이력서 핵심 역량", "이한주 포트폴리오 핵심 역량"),
            cost_usd=0.00003,
            cached=False,
            skipped_reason=None,
        )
        with patch.dict(
            os.environ,
            {
                _ENV_SCOPED: "true",
                _ENV_TOP_N: "2",
                "JETRAG_PAID_DECOMPOSITION_ENABLED": "true",
            },
            clear=False,
        ), patch.object(
            search_module.query_decomposer, "decompose", return_value=fake_decomp
        ):
            resp = _call_search(
                search_module, q=_CROSS_DOC_QUERY, client_mock=client_mock
            )
        # W-1(a) 4키
        self.assertEqual(resp.meta["decomposition_fired"], True)
        self.assertEqual(
            resp.meta["decomposed_subqueries"],
            ["기웅민 이력서 핵심 역량", "이한주 포트폴리오 핵심 역량"],
        )
        self.assertAlmostEqual(
            resp.meta["decomposition_cost_usd"], 0.00003, places=8
        )
        self.assertEqual(resp.meta["decomposition_cached"], False)
        # W-1(b) 3키 — top-2 = A,B (정렬됨)
        self.assertEqual(resp.meta["cross_doc_scoped_applied"], True)
        self.assertEqual(
            resp.meta["cross_doc_candidate_doc_ids"], ["doc-A", "doc-B"]
        )
        self.assertEqual(resp.meta["cross_doc_candidate_top_n"], 2)


if __name__ == "__main__":
    unittest.main()
