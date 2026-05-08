"""S4-A D4 — `evals/run_s4_a_d4_breakdown_eval.py` 단위 테스트.

검증 범위
- golden v2 CSV 로더 — 14 컬럼 추출 (`doc_type` / `caption_dependent`)
- `aggregate_all` — overall + qtype/doc_type/caption_dependent/cross-tab 분리
- caption_dependent gap 계산 (false vs true)
- doc 매칭 fail / ERROR row 의 chunk-level metric None 처리

외부 의존성 0 — search() / DB / HF 호출 0. CellResult 직접 조립 후 aggregator 검증.
"""

from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

# evals/ 의 D4 도구 import — api/tests/ 에서 evals 경로 보정
_EVALS_DIR = Path(__file__).resolve().parents[2] / "evals"
sys.path.insert(0, str(_EVALS_DIR))


def _make_cell(
    *,
    golden_id: str,
    qtype: str = "exact_fact",
    doc_type: str = "pdf",
    caption_dependent: bool = False,
    recall: float | None = 0.5,
    ndcg: float | None = 0.5,
    mrr: float | None = 0.5,
    top1: bool | None = True,
    latency_ms: float = 100.0,
    note: str = "",
):
    """CellResult 조립 헬퍼."""
    from run_s4_a_d4_breakdown_eval import CellResult

    cell = CellResult(
        golden_id=golden_id,
        query_type=qtype,
        doc_type=doc_type,
        caption_dependent=caption_dependent,
        doc_id="dummy-doc",
    )
    cell.recall_at_10 = recall
    cell.ndcg_at_10 = ndcg
    cell.mrr = mrr
    cell.top1_hit = top1
    cell.latency_ms = latency_ms
    cell.note = note
    return cell


class GoldenV2LoaderTest(unittest.TestCase):
    """`_load_golden_v2` — 14 컬럼 추출 + caption_dependent bool 변환."""

    def test_load_extracts_doc_type_and_caption_dependent(self) -> None:
        from run_s4_a_d4_breakdown_eval import _load_golden_v2

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8-sig"
        ) as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "id",
                    "query",
                    "query_type",
                    "doc_id",
                    "expected_doc_title",
                    "relevant_chunks",
                    "acceptable_chunks",
                    "source_chunk_text",
                    "expected_answer_summary",
                    "must_include",
                    "source_hint",
                    "negative",
                    "doc_type",
                    "caption_dependent",
                ]
            )
            writer.writerow(
                [
                    "G-T-001",
                    "쏘나타 휠 사이즈",
                    "table_lookup",
                    "doc-A",
                    "sonata-the-edge",
                    "102",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "false",
                    "pdf",
                    "true",
                ]
            )
            writer.writerow(
                [
                    "G-T-002",
                    "외장 색상",
                    "exact_fact",
                    "doc-B",
                    "test-doc",
                    "10,11",
                    "12",
                    "",
                    "",
                    "",
                    "",
                    "false",
                    "pdf",
                    "false",
                ]
            )
            tmp_path = Path(f.name)
        try:
            rows = _load_golden_v2(tmp_path)
        finally:
            tmp_path.unlink()

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].id, "G-T-001")
        self.assertEqual(rows[0].doc_type, "pdf")
        self.assertTrue(rows[0].caption_dependent)
        self.assertEqual(rows[0].relevant_chunks, (102,))
        self.assertEqual(rows[0].acceptable_chunks, ())
        self.assertEqual(rows[1].relevant_chunks, (10, 11))
        self.assertEqual(rows[1].acceptable_chunks, (12,))
        self.assertFalse(rows[1].caption_dependent)

    def test_caption_dependent_case_insensitive(self) -> None:
        """`TRUE` / `True` 도 True 로 인식."""
        from run_s4_a_d4_breakdown_eval import _load_golden_v2

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8-sig"
        ) as f:
            f.write(
                "id,query,query_type,doc_id,expected_doc_title,"
                "relevant_chunks,acceptable_chunks,source_chunk_text,"
                "expected_answer_summary,must_include,source_hint,negative,"
                "doc_type,caption_dependent\n"
            )
            f.write(
                "G-1,q,exact_fact,d,t,1,,,,,,false,pdf,TRUE\n"
            )
            f.write(
                "G-2,q,exact_fact,d,t,2,,,,,,false,pdf,True\n"
            )
            f.write(
                "G-3,q,exact_fact,d,t,3,,,,,,false,pdf,FALSE\n"
            )
            tmp_path = Path(f.name)
        try:
            rows = _load_golden_v2(tmp_path)
        finally:
            tmp_path.unlink()

        self.assertTrue(rows[0].caption_dependent)
        self.assertTrue(rows[1].caption_dependent)
        self.assertFalse(rows[2].caption_dependent)


class AggregateGroupTest(unittest.TestCase):
    """`_aggregate_group` — chunk-evaluable 분리 + percentile."""

    def test_aggregate_with_mixed_evaluable(self) -> None:
        from run_s4_a_d4_breakdown_eval import _aggregate_group

        cells = [
            _make_cell(golden_id="A", recall=1.0, ndcg=1.0, mrr=1.0, top1=True),
            _make_cell(golden_id="B", recall=0.5, ndcg=0.5, mrr=0.5, top1=False),
            _make_cell(
                golden_id="C",
                recall=None,
                ndcg=None,
                mrr=None,
                top1=None,
                note="정답 chunks 없음 (latency 만 측정)",
            ),
        ]
        s = _aggregate_group("test", cells)

        self.assertEqual(s.n_rows, 3)
        self.assertEqual(s.n_chunk_evaluable, 2)
        self.assertAlmostEqual(s.avg_recall_at_10, 0.75)
        self.assertAlmostEqual(s.avg_ndcg_at_10, 0.75)
        self.assertAlmostEqual(s.avg_mrr, 0.75)
        self.assertAlmostEqual(s.top1_rate, 0.5)

    def test_aggregate_counts_doc_match_fail_and_error(self) -> None:
        from run_s4_a_d4_breakdown_eval import _aggregate_group

        cells = [
            _make_cell(golden_id="A", recall=None, top1=None, note="doc 매칭 fail"),
            _make_cell(
                golden_id="B",
                recall=None,
                top1=None,
                note="ERROR: TimeoutError: HF",
            ),
            _make_cell(golden_id="C", recall=0.8, top1=True, note=""),
        ]
        s = _aggregate_group("test", cells)
        self.assertEqual(s.doc_match_fail, 1)
        self.assertEqual(s.error_count, 1)
        self.assertEqual(s.n_chunk_evaluable, 1)

    def test_aggregate_empty(self) -> None:
        from run_s4_a_d4_breakdown_eval import _aggregate_group

        s = _aggregate_group("empty", [])
        self.assertEqual(s.n_rows, 0)
        self.assertEqual(s.n_chunk_evaluable, 0)
        self.assertEqual(s.avg_recall_at_10, 0.0)
        self.assertEqual(s.top1_rate, 0.0)


class AggregateAllTest(unittest.TestCase):
    """`aggregate_all` — overall + 4개 breakdown."""

    def test_aggregate_all_breakdowns(self) -> None:
        from run_s4_a_d4_breakdown_eval import aggregate_all

        cells = [
            # qtype=table_lookup, caption=true, doc_type=pdf — R@10 0.4
            _make_cell(
                golden_id="T-1",
                qtype="table_lookup",
                doc_type="pdf",
                caption_dependent=True,
                recall=0.4,
                top1=False,
            ),
            # qtype=table_lookup, caption=true, doc_type=pdf — R@10 0.6
            _make_cell(
                golden_id="T-2",
                qtype="table_lookup",
                doc_type="pdf",
                caption_dependent=True,
                recall=0.6,
                top1=True,
            ),
            # qtype=exact_fact, caption=false, doc_type=pdf — R@10 0.9
            _make_cell(
                golden_id="E-1",
                qtype="exact_fact",
                doc_type="pdf",
                caption_dependent=False,
                recall=0.9,
                top1=True,
            ),
            # qtype=exact_fact, caption=false, doc_type=hwpx — R@10 1.0
            _make_cell(
                golden_id="E-2",
                qtype="exact_fact",
                doc_type="hwpx",
                caption_dependent=False,
                recall=1.0,
                top1=True,
            ),
        ]
        overall, by_qt, by_dt, by_cap, by_qt_cap = aggregate_all(cells)

        self.assertEqual(overall.n_rows, 4)
        self.assertEqual(overall.n_chunk_evaluable, 4)
        self.assertAlmostEqual(overall.avg_recall_at_10, 0.725)

        # qtype 2종
        qt_labels = {s.label for s in by_qt}
        self.assertEqual(qt_labels, {"table_lookup", "exact_fact"})
        # exact_fact 가 더 강함 (0.95 vs 0.5) → R@10 desc 정렬 시 첫 번째
        self.assertEqual(by_qt[0].label, "exact_fact")

        # doc_type 2종 (pdf 3, hwpx 1) — n_rows desc 정렬 시 첫 번째 pdf
        self.assertEqual(by_dt[0].label, "pdf")
        self.assertEqual(by_dt[0].n_rows, 3)

        # caption_dependent 2종
        cap_labels = {s.label for s in by_cap}
        self.assertEqual(cap_labels, {"true", "false"})
        cap_map = {s.label: s for s in by_cap}
        self.assertAlmostEqual(cap_map["true"].avg_recall_at_10, 0.5)
        self.assertAlmostEqual(cap_map["false"].avg_recall_at_10, 0.95)

        # cross-tab — exact_fact|false / table_lookup|true 두 cell
        qt_cap_labels = {s.label for s in by_qt_cap}
        self.assertEqual(
            qt_cap_labels, {"exact_fact|false", "table_lookup|true"}
        )

    def test_caption_gap_computed(self) -> None:
        """caption_dependent=true 의 R@10 이 false 보다 낮으면 gap 양수."""
        from run_s4_a_d4_breakdown_eval import aggregate_all

        cells = [
            _make_cell(
                golden_id="T",
                caption_dependent=True,
                recall=0.3,
                top1=False,
            ),
            _make_cell(
                golden_id="F",
                caption_dependent=False,
                recall=0.8,
                top1=True,
            ),
        ]
        _, _, _, by_cap, _ = aggregate_all(cells)
        cap_map = {s.label: s for s in by_cap}
        gap = (
            cap_map["false"].avg_recall_at_10
            - cap_map["true"].avg_recall_at_10
        )
        self.assertAlmostEqual(gap, 0.5)


class PercentileTest(unittest.TestCase):
    """`_percentile` — n=1 ValueError 회피 + 분위수 정확."""

    def test_single_value(self) -> None:
        from run_s4_a_d4_breakdown_eval import _percentile

        self.assertEqual(_percentile([100.0], 95.0), 100.0)

    def test_empty_returns_zero(self) -> None:
        from run_s4_a_d4_breakdown_eval import _percentile

        self.assertEqual(_percentile([], 95.0), 0.0)

    def test_p95_of_20(self) -> None:
        """20개 [1..20] 에서 P95 = 19.05 (linear interpolation)."""
        from run_s4_a_d4_breakdown_eval import _percentile

        vals = sorted(float(i) for i in range(1, 21))
        # k = 19 * 0.95 = 18.05 → vals[18] * 0.95 + vals[19] * 0.05
        # = 19 * 0.95 + 20 * 0.05 = 18.05 + 1.00 = 19.05
        self.assertAlmostEqual(_percentile(vals, 95.0), 19.05)


class MarkdownFormatTest(unittest.TestCase):
    """`_format_markdown` — 한계 §0 명시 + 모든 섹션 출력."""

    def test_markdown_includes_limit_and_sections(self) -> None:
        from run_s4_a_d4_breakdown_eval import (
            _format_markdown,
            aggregate_all,
        )

        cells = [
            _make_cell(
                golden_id="A",
                caption_dependent=True,
                recall=0.4,
                top1=False,
            ),
            _make_cell(
                golden_id="B",
                caption_dependent=False,
                recall=0.8,
                top1=True,
            ),
        ]
        overall, by_qt, by_dt, by_cap, by_qt_cap = aggregate_all(cells)
        md = _format_markdown(
            overall=overall,
            by_qtype=by_qt,
            by_doc_type=by_dt,
            by_caption=by_cap,
            by_qtype_caption=by_qt_cap,
            n_golden=2,
        )
        # 한계 §0 — prompt v1↔v2 비교 불가 명시
        self.assertIn("D4 시점 한계", md)
        self.assertIn("prompt v1↔v2 직접 비교 불가", md)
        # 모든 섹션
        self.assertIn("§1 Overall", md)
        self.assertIn("§2 qtype", md)
        self.assertIn("§3 doc_type", md)
        self.assertIn("§4 caption_dependent gap", md)
        self.assertIn("§5 qtype × caption_dependent", md)
        self.assertIn("§6 DoD KPI", md)
        # caption gap 표시
        self.assertIn("R@10 gap (false − true)", md)


class BaselineEnvTest(unittest.TestCase):
    """`_apply_baseline_env` — RRF-only 강제 + restore."""

    def test_apply_and_restore(self) -> None:
        import os

        from run_s4_a_d4_breakdown_eval import (
            _apply_baseline_env,
            _restore_env,
        )

        prev_rerank = os.environ.get("JETRAG_RERANKER_ENABLED")
        prev_mmr = os.environ.get("JETRAG_MMR_DISABLE")

        try:
            os.environ["JETRAG_RERANKER_ENABLED"] = "true"
            os.environ.pop("JETRAG_MMR_DISABLE", None)

            saved = _apply_baseline_env()
            self.assertEqual(os.environ["JETRAG_RERANKER_ENABLED"], "false")
            self.assertEqual(os.environ["JETRAG_MMR_DISABLE"], "1")

            _restore_env(saved)
            self.assertEqual(os.environ["JETRAG_RERANKER_ENABLED"], "true")
            self.assertNotIn("JETRAG_MMR_DISABLE", os.environ)
        finally:
            # 원상 복구 — 다른 테스트 영향 차단
            if prev_rerank is None:
                os.environ.pop("JETRAG_RERANKER_ENABLED", None)
            else:
                os.environ["JETRAG_RERANKER_ENABLED"] = prev_rerank
            if prev_mmr is None:
                os.environ.pop("JETRAG_MMR_DISABLE", None)
            else:
                os.environ["JETRAG_MMR_DISABLE"] = prev_mmr


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
