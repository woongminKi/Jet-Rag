"""W-6 — `evals/run_s4_a_d4_breakdown_eval.py` 측정 신뢰성 가드 단위 테스트.

PRD `work-log/2026-05-12 검색 정확도 80% 달성 PRD.md` §3 W-6 / §4 DECISION-6.

검증 범위
- (A) doc-match-fail row (정답 라벨 有) 가 R@10=0 으로 ``n_chunk_evaluable`` 분모에 포함
- (A) ground-truth 없는 row 는 분모 제외 + ``n_no_ground_truth`` 카운트
- (불변 가드) 정상 cross_doc cell 은 zeroing 안 됨 — 9 row 라운드로빈 회귀 가드
- (B) ``_warn_stale_dense_vec`` — fake supabase client 주입 시 WARN 경로 + strict 종료코드 2
- (C) ``_format_markdown`` 헤더가 ``n_golden`` 동적 — ``"157 row"`` 미포함

stdlib unittest only — 실제 Supabase / search() 호출 0 (fake client 주입).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

_EVALS_DIR = Path(__file__).resolve().parents[2] / "evals"
sys.path.insert(0, str(_EVALS_DIR))


def _make_cell(
    *,
    golden_id: str,
    query_type: str = "exact_fact",
    doc_type: str = "pdf",
    caption_dependent: bool = False,
    doc_id: str = "doc-A",
    recall_at_10: float | None = 0.8,
    ndcg_at_10: float | None = 0.8,
    mrr: float | None = 0.5,
    top1_hit: bool | None = True,
    note: str = "",
    doc_match_fail_zeroed: bool = False,
    no_ground_truth: bool = False,
    latency_ms: float = 100.0,
):
    """CellResult 생성 헬퍼 — 측정 path 우회, aggregate 로직만 검증."""
    from run_s4_a_d4_breakdown_eval import CellResult

    return CellResult(
        golden_id=golden_id,
        query_type=query_type,
        doc_type=doc_type,
        caption_dependent=caption_dependent,
        doc_id=doc_id,
        recall_at_10=recall_at_10,
        ndcg_at_10=ndcg_at_10,
        mrr=mrr,
        top1_hit=top1_hit,
        note=note,
        latency_ms=latency_ms,
        doc_match_fail_zeroed=doc_match_fail_zeroed,
        no_ground_truth=no_ground_truth,
    )


# ---------------------------------------------------------------------------
# (A) doc-match-fail / no-ground-truth 분모 정책 — DECISION-6
# ---------------------------------------------------------------------------


class DocMatchFailZeroedTests(unittest.TestCase):
    def test_doc_match_fail_row_zeroed_into_denominator(self) -> None:
        """doc-match-fail(라벨 有) 1 cell + 정상 cell 2 → n_chunk_evaluable==3,
        R@10 = (1.0 + 0.5 + 0.0) / 3."""
        from run_s4_a_d4_breakdown_eval import _aggregate_group

        cells = [
            _make_cell(golden_id="g1", recall_at_10=1.0, ndcg_at_10=1.0, mrr=1.0, top1_hit=True),
            _make_cell(golden_id="g2", recall_at_10=0.5, ndcg_at_10=0.5, mrr=0.5, top1_hit=False),
            _make_cell(
                golden_id="g_fail",
                recall_at_10=0.0,
                ndcg_at_10=0.0,
                mrr=0.0,
                top1_hit=False,
                note="doc 매칭 fail",
                doc_match_fail_zeroed=True,
            ),
        ]
        s = _aggregate_group("test", cells)
        self.assertEqual(s.n_chunk_evaluable, 3)
        self.assertEqual(s.n_doc_match_fail_zeroed, 1)
        self.assertEqual(s.n_no_ground_truth, 0)
        self.assertAlmostEqual(s.avg_recall_at_10, (1.0 + 0.5 + 0.0) / 3, places=6)
        self.assertAlmostEqual(s.top1_rate, 1.0 / 3, places=6)

    def test_measure_one_cell_zeroes_doc_match_fail_with_labels(self) -> None:
        """`_zero_doc_match_fail` 헬퍼 경로 — 정답 라벨 有 row 가 search 응답에서
        target 못 찾으면 R@10=0 + doc_match_fail_zeroed=True (search mock)."""
        import types

        from run_s4_a_d4_breakdown_eval import GoldenV2Row, _measure_one_cell

        # `|` separator 가 있는 U-row → multi-doc 매칭 path. 어느 sub-title 도 응답
        # item 과 안 맞으므로 matched == [] → "doc 매칭 fail" path 진입.
        g = GoldenV2Row(
            id="G-U-XXX",
            query="없는 자료 질의",
            query_type="exact_fact",
            doc_id="",
            expected_doc_title="존재하지않는문서A|존재하지않는문서B",
            relevant_chunks=(10, 20),
            acceptable_chunks=(),
            doc_type="",
            caption_dependent=False,
        )

        class _FakeResp:
            def model_dump(self):
                # items 의 doc_title 이 어느 sub-title 과도 안 맞음 → _pick_target_items == []
                return {
                    "items": [
                        {"doc_id": "other-doc", "doc_title": "전혀 다른 문서", "matched_chunks": []}
                    ],
                    "query_parsed": {"reranker_path": "disabled"},
                }

        # 함수 내부 `from app.routers.search import search` 를 가로채기 위해 모듈 주입.
        fake_search_mod = types.ModuleType("app.routers.search")
        fake_search_mod.search = lambda **_k: _FakeResp()  # type: ignore[attr-defined]
        fake_metrics_mod = types.ModuleType("app.services.retrieval_metrics")
        fake_metrics_mod.recall_at_k = lambda *a, **k: 0.0  # type: ignore[attr-defined]
        fake_metrics_mod.ndcg_at_k = lambda *a, **k: 0.0  # type: ignore[attr-defined]
        fake_metrics_mod.mrr = lambda *a, **k: 0.0  # type: ignore[attr-defined]
        with mock.patch.dict(
            sys.modules,
            {
                "app.routers.search": fake_search_mod,
                "app.services.retrieval_metrics": fake_metrics_mod,
            },
        ):
            cell = _measure_one_cell(g)
        self.assertEqual(cell.note, "doc 매칭 fail")
        self.assertTrue(cell.doc_match_fail_zeroed)
        self.assertEqual(cell.recall_at_10, 0.0)
        self.assertEqual(cell.ndcg_at_10, 0.0)
        self.assertEqual(cell.mrr, 0.0)
        self.assertIs(cell.top1_hit, False)
        self.assertFalse(cell.no_ground_truth)

    def test_acceptable_only_row_doc_match_fail_zeroed(self) -> None:
        """relevant 비어있고 acceptable-only 인 golden row 도 doc-match-fail path 에서
        has_ground_truth=True 로 인식 → zeroing (golden_v2 acceptable-only 6 row 회귀 가드)."""
        import types

        from run_s4_a_d4_breakdown_eval import GoldenV2Row, _measure_one_cell

        g = GoldenV2Row(
            id="G-U-ACC",
            query="acceptable-only 질의",
            query_type="exact_fact",
            doc_id="",
            expected_doc_title="존재하지않는문서A|존재하지않는문서B",
            relevant_chunks=(),
            acceptable_chunks=(5,),
            doc_type="",
            caption_dependent=False,
        )

        class _FakeResp:
            def model_dump(self):
                return {
                    "items": [
                        {"doc_id": "other-doc", "doc_title": "전혀 다른 문서", "matched_chunks": []}
                    ],
                    "query_parsed": {"reranker_path": "disabled"},
                }

        fake_search_mod = types.ModuleType("app.routers.search")
        fake_search_mod.search = lambda **_k: _FakeResp()  # type: ignore[attr-defined]
        fake_metrics_mod = types.ModuleType("app.services.retrieval_metrics")
        fake_metrics_mod.recall_at_k = lambda *a, **k: 0.0  # type: ignore[attr-defined]
        fake_metrics_mod.ndcg_at_k = lambda *a, **k: 0.0  # type: ignore[attr-defined]
        fake_metrics_mod.mrr = lambda *a, **k: 0.0  # type: ignore[attr-defined]
        with mock.patch.dict(
            sys.modules,
            {
                "app.routers.search": fake_search_mod,
                "app.services.retrieval_metrics": fake_metrics_mod,
            },
        ):
            cell = _measure_one_cell(g)
        self.assertEqual(cell.note, "doc 매칭 fail")
        self.assertTrue(cell.doc_match_fail_zeroed)
        self.assertEqual(cell.recall_at_10, 0.0)
        self.assertEqual(cell.ndcg_at_10, 0.0)
        self.assertEqual(cell.mrr, 0.0)
        self.assertIs(cell.top1_hit, False)
        self.assertFalse(cell.no_ground_truth)


class NoGroundTruthTests(unittest.TestCase):
    def test_no_ground_truth_row_excluded(self) -> None:
        """라벨 없는 cell → n_chunk_evaluable 제외, n_no_ground_truth==1."""
        from run_s4_a_d4_breakdown_eval import _aggregate_group

        cells = [
            _make_cell(golden_id="g1", recall_at_10=0.6),
            _make_cell(
                golden_id="g_no_gt",
                recall_at_10=None,
                ndcg_at_10=None,
                mrr=None,
                top1_hit=None,
                note="정답 chunks 없음 (latency 만 측정)",
                no_ground_truth=True,
            ),
        ]
        s = _aggregate_group("test", cells)
        self.assertEqual(s.n_chunk_evaluable, 1)
        self.assertEqual(s.n_no_ground_truth, 1)
        self.assertEqual(s.n_doc_match_fail_zeroed, 0)
        self.assertAlmostEqual(s.avg_recall_at_10, 0.6, places=6)

    def test_measure_one_cell_no_ground_truth_flag(self) -> None:
        """relevant/acceptable 둘 다 비어있는 GoldenV2Row → no_ground_truth=True,
        recall_at_10 is None (search mock)."""
        import types

        from run_s4_a_d4_breakdown_eval import GoldenV2Row, _measure_one_cell

        g = GoldenV2Row(
            id="G-N-XXX",
            query="범위 밖 질의",
            query_type="out_of_scope",
            doc_id="",
            expected_doc_title="",
            relevant_chunks=(),
            acceptable_chunks=(),
            doc_type="",
            caption_dependent=False,
        )

        class _FakeResp:
            def model_dump(self):
                return {
                    "items": [
                        {"doc_id": "x", "doc_title": "아무거나", "matched_chunks": []}
                    ],
                    "query_parsed": {"reranker_path": "disabled"},
                }

        fake_search_mod = types.ModuleType("app.routers.search")
        fake_search_mod.search = lambda **_k: _FakeResp()  # type: ignore[attr-defined]
        fake_metrics_mod = types.ModuleType("app.services.retrieval_metrics")
        fake_metrics_mod.recall_at_k = lambda *a, **k: 0.0  # type: ignore[attr-defined]
        fake_metrics_mod.ndcg_at_k = lambda *a, **k: 0.0  # type: ignore[attr-defined]
        fake_metrics_mod.mrr = lambda *a, **k: 0.0  # type: ignore[attr-defined]
        with mock.patch.dict(
            sys.modules,
            {
                "app.routers.search": fake_search_mod,
                "app.services.retrieval_metrics": fake_metrics_mod,
            },
        ):
            cell = _measure_one_cell(g)
        self.assertTrue(cell.no_ground_truth)
        self.assertIsNone(cell.recall_at_10)
        self.assertFalse(cell.doc_match_fail_zeroed)


# ---------------------------------------------------------------------------
# 불변 가드 — 정상 cross_doc cell zeroing 안 됨 (9 row 회귀 가드)
# ---------------------------------------------------------------------------


class RoundRobinUnchangedTests(unittest.TestCase):
    def test_roundrobin_unchanged_for_normal_cross_doc(self) -> None:
        """정상 cross_doc cell (recall 산출됨) 은 doc_match_fail_zeroed=False —
        라운드로빈 머지 path 가 W-6 으로 인해 바뀌지 않음."""
        from run_s4_a_d4_breakdown_eval import _aggregate_group

        # cross_doc cell — recall 정상 산출 (라벨 있음 + target 매칭됨).
        cells = [
            _make_cell(
                golden_id="G-U-015",
                query_type="cross_doc",
                doc_type="",
                doc_id="",
                recall_at_10=0.5,
                ndcg_at_10=0.4,
                mrr=0.33,
                top1_hit=False,
            ),
            _make_cell(
                golden_id="G-U-031",
                query_type="cross_doc",
                doc_type="",
                doc_id="",
                recall_at_10=0.75,
                ndcg_at_10=0.6,
                mrr=0.5,
                top1_hit=True,
            ),
        ]
        s = _aggregate_group("cross_doc", cells)
        self.assertEqual(s.n_chunk_evaluable, 2)
        self.assertEqual(s.n_doc_match_fail_zeroed, 0)
        self.assertEqual(s.n_no_ground_truth, 0)
        self.assertAlmostEqual(s.avg_recall_at_10, 0.625, places=6)

    def test_round_robin_helper_signature_unchanged(self) -> None:
        """`_round_robin_cross_doc_chunks` 시그니처/로직 불변 — W-6 이 안 건드림."""
        from run_s4_a_d4_breakdown_eval import (
            _ALIAS_MAP,
            _round_robin_cross_doc_chunks,
        )

        alias_a = sorted(_ALIAS_MAP)[0]
        items = [
            {
                "doc_id": _ALIAS_MAP[alias_a].doc_id,
                "matched_chunks": [{"chunk_idx": 7, "rrf_score": 0.9}],
            }
        ]
        merged = _round_robin_cross_doc_chunks(items)
        self.assertEqual(merged, [(alias_a, 7)])


# ---------------------------------------------------------------------------
# (B) dense_vec NULL preflight WARN
# ---------------------------------------------------------------------------


class _FakeQuery:
    """supabase-py 의 fluent query builder 흉내 — 최소 메서드만."""

    def __init__(self, *, count: int, data: list[dict]):
        self._count = count
        self._data = data
        self._is_dense_null = False
        self._select_doc_id = False

    def select(self, *cols, count=None):  # noqa: A002 - mirror supabase API
        self._select_doc_id = "doc_id" in cols
        return self

    def is_(self, col, val):
        if col == "dense_vec" and val == "null":
            self._is_dense_null = True
        return self

    def limit(self, _n):
        return self

    def range(self, _lo, _hi):
        return self

    def order(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def like(self, *_a, **_k):
        return self

    def execute(self):
        class _R:
            pass

        r = _R()
        # doc_id 페이지 조회 → data, count 쿼리 → count
        if self._select_doc_id:
            r.data = self._data
            r.count = None
        else:
            r.data = None
            r.count = self._count
        return r


class _FakeClient:
    def __init__(self, *, count: int, data: list[dict]):
        self._count = count
        self._data = data
        # range() 가 2회차에 빈 페이지를 반환하도록 — 단순화: 1페이지 < PAGE_SIZE 면 break.
        self._data_served = False

    def table(self, _name):
        # 매 호출마다 새 query — count 쿼리, doc_id 페이지 쿼리 둘 다 같은 data/count 사용.
        # doc_id 페이지가 PAGE_SIZE 미만이면 _warn_stale_dense_vec 가 1회 후 break.
        return _FakeQuery(count=self._count, data=list(self._data))


class WarnStaleDenseVecTests(unittest.TestCase):
    def _patch_client(self, client):
        import app.db.client as db_client_mod

        return mock.patch.object(
            db_client_mod, "get_supabase_client", lambda: client
        )

    def test_warn_stale_dense_vec_zero_returns_zero(self) -> None:
        from run_s4_a_d4_breakdown_eval import _warn_stale_dense_vec

        client = _FakeClient(count=0, data=[])
        with self._patch_client(client):
            n = _warn_stale_dense_vec(strict=False)
        self.assertEqual(n, 0)

    def test_warn_stale_dense_vec_with_null_rows(self) -> None:
        """NULL row 가 있으면 N 반환 (WARN-only). strict=False 면 측정 진행."""
        from run_s4_a_d4_breakdown_eval import _warn_stale_dense_vec

        data = [{"doc_id": "doc-A"}, {"doc_id": "doc-A"}, {"doc_id": "doc-B"}]
        client = _FakeClient(count=3, data=data)
        with self._patch_client(client):
            n = _warn_stale_dense_vec(strict=False)
        self.assertEqual(n, 3)

    def test_warn_stale_dense_vec_strict_signals_abort(self) -> None:
        """strict=True 면 N>0 반환 — main 이 이 값을 보고 종료코드 2 로 중단."""
        from run_s4_a_d4_breakdown_eval import _warn_stale_dense_vec

        client = _FakeClient(count=5, data=[{"doc_id": "doc-X"}] * 5)
        with self._patch_client(client):
            n = _warn_stale_dense_vec(strict=True)
        self.assertEqual(n, 5)
        self.assertGreater(n, 0)  # main: `if strict and n > 0: return 2`

    def test_warn_stale_dense_vec_connection_failure_returns_minus_one(self) -> None:
        """Supabase 접속 실패 → -1 반환 (eval 막지 않음)."""
        import app.db.client as db_client_mod
        from run_s4_a_d4_breakdown_eval import _warn_stale_dense_vec

        def _boom():
            raise RuntimeError("no service_role key")

        with mock.patch.object(db_client_mod, "get_supabase_client", _boom):
            n = _warn_stale_dense_vec(strict=True)
        self.assertEqual(n, -1)

    def test_fail_on_null_dense_vec_with_connection_failure_does_not_abort(self) -> None:
        """strict + 접속 실패 = abort 안 함 — `_warn_stale_dense_vec` 가 -1 반환,
        main 의 `if args.fail_on_null_dense_vec and n > 0: return 2` 분기는 `-1 > 0 == False`
        라 종료코드 2 를 안 냄 (측정 계속)."""
        import app.db.client as db_client_mod
        from run_s4_a_d4_breakdown_eval import _warn_stale_dense_vec

        def _boom():
            raise RuntimeError("no service_role key")

        with mock.patch.object(db_client_mod, "get_supabase_client", _boom):
            n = _warn_stale_dense_vec(strict=True)
        self.assertEqual(n, -1)
        fail_on_null_dense_vec = True
        self.assertFalse(fail_on_null_dense_vec and n > 0)


# ---------------------------------------------------------------------------
# (C) cosmetic — _format_markdown 헤더 동적화
# ---------------------------------------------------------------------------


class FormatMarkdownNGoldenTests(unittest.TestCase):
    def test_format_markdown_uses_n_golden(self) -> None:
        """`_format_markdown(..., n_golden=183)` → "183 row" 포함, "157 row" 미포함."""
        from run_s4_a_d4_breakdown_eval import _format_markdown, aggregate_all

        cells = [
            _make_cell(golden_id="g1", recall_at_10=0.8),
            _make_cell(
                golden_id="g_fail",
                recall_at_10=0.0,
                ndcg_at_10=0.0,
                mrr=0.0,
                top1_hit=False,
                note="doc 매칭 fail",
                doc_match_fail_zeroed=True,
            ),
            _make_cell(
                golden_id="g_no_gt",
                recall_at_10=None,
                ndcg_at_10=None,
                mrr=None,
                top1_hit=None,
                no_ground_truth=True,
            ),
        ]
        overall, by_qt, by_dt, by_cap, by_qt_cap = aggregate_all(cells)
        md = _format_markdown(
            overall=overall,
            by_qtype=by_qt,
            by_doc_type=by_dt,
            by_caption=by_cap,
            by_qtype_caption=by_qt_cap,
            n_golden=183,
            doc_match_fail_zeroed_ids=["g_fail"],
            no_ground_truth_ids=["g_no_gt"],
        )
        self.assertIn("183 row", md)
        self.assertNotIn("157 row", md)
        # DECISION-6 정책 문구 / 버킷 id 노출 확인
        self.assertIn("DECISION-6", md)
        self.assertIn("g_fail", md)
        self.assertIn("g_no_gt", md)


if __name__ == "__main__":
    unittest.main()
