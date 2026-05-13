"""W-9.5 (2026-05-14) — `run_s4_a_d4_breakdown_eval.py` 의 `--mode` ablation flag.

검증 포인트
- `_parse_args` 의 `--mode` 인자: default `hybrid` / choices `hybrid|dense|sparse` /
  invalid 값은 argparse 가 SystemExit 으로 거부.
- `_measure_one_cell(g, mode=...)` 가 search() 에 mode 그대로 전달.
- `_format_markdown(mode=...)` 가 헤더에 mode 명시.

KPI #7 ("하이브리드 우세 +5pp") 측정 인프라 — eval-side 만 변경, 운영 코드 0.
stdlib unittest only.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_EVALS_DIR = Path(__file__).resolve().parents[2] / "evals"
sys.path.insert(0, str(_EVALS_DIR))


class ModeArgparseTests(unittest.TestCase):
    """`_parse_args` 의 `--mode` 분기."""

    def test_default_mode_is_hybrid(self) -> None:
        from run_s4_a_d4_breakdown_eval import _parse_args
        ns = _parse_args([])
        self.assertEqual(ns.mode, "hybrid")

    def test_explicit_dense(self) -> None:
        from run_s4_a_d4_breakdown_eval import _parse_args
        ns = _parse_args(["--mode", "dense"])
        self.assertEqual(ns.mode, "dense")

    def test_explicit_sparse(self) -> None:
        from run_s4_a_d4_breakdown_eval import _parse_args
        ns = _parse_args(["--mode", "sparse"])
        self.assertEqual(ns.mode, "sparse")

    def test_invalid_mode_rejected(self) -> None:
        from run_s4_a_d4_breakdown_eval import _parse_args
        with self.assertRaises(SystemExit):
            _parse_args(["--mode", "neural"])


class MeasureOneCellPassesModeTests(unittest.TestCase):
    """`_measure_one_cell(mode=...)` 가 search() 에 mode 그대로 전달."""

    def _stub_golden_row(self, gid: str):
        from run_s4_a_d4_breakdown_eval import GoldenV2Row
        return GoldenV2Row(
            id=gid,
            query="테스트 query",
            query_type="exact_fact",
            doc_id="",
            expected_doc_title="",
            relevant_chunks=(0,),
            acceptable_chunks=(),
            doc_type="pdf",
            caption_dependent=False,
        )

    def _stub_search_resp(self):
        resp = MagicMock()
        resp.model_dump.return_value = {
            "items": [],
            "query_parsed": {"reranker_path": "disabled"},
            "meta": {},
        }
        return resp

    def test_dense_mode_passed_to_search(self) -> None:
        from run_s4_a_d4_breakdown_eval import _measure_one_cell

        g = self._stub_golden_row("G-T-001")
        with patch(
            "app.routers.search.search", return_value=self._stub_search_resp(),
        ) as mock_search:
            _measure_one_cell(g, mode="dense")

        self.assertEqual(mock_search.call_count, 1)
        self.assertEqual(mock_search.call_args.kwargs["mode"], "dense")

    def test_default_mode_is_hybrid(self) -> None:
        from run_s4_a_d4_breakdown_eval import _measure_one_cell

        g = self._stub_golden_row("G-T-002")
        with patch(
            "app.routers.search.search", return_value=self._stub_search_resp(),
        ) as mock_search:
            _measure_one_cell(g)  # mode 미지정

        self.assertEqual(mock_search.call_args.kwargs["mode"], "hybrid")

    def test_sparse_mode_passed_to_search(self) -> None:
        from run_s4_a_d4_breakdown_eval import _measure_one_cell

        g = self._stub_golden_row("G-T-003")
        with patch(
            "app.routers.search.search", return_value=self._stub_search_resp(),
        ) as mock_search:
            _measure_one_cell(g, mode="sparse")

        self.assertEqual(mock_search.call_args.kwargs["mode"], "sparse")


class FormatMarkdownModeHeaderTests(unittest.TestCase):
    """`_format_markdown(mode=...)` 가 헤더에 mode 명시."""

    def _stub_summary(self):
        from run_s4_a_d4_breakdown_eval import GroupSummary
        return GroupSummary(
            label="overall",
            n_rows=10,
            n_chunk_evaluable=10,
            avg_recall_at_10=0.80,
            avg_ndcg_at_10=0.70,
            avg_mrr=0.60,
            top1_rate=0.80,
            p95_latency_ms=200.0,
            avg_latency_ms=120.0,
            doc_match_fail=0,
            error_count=0,
        )

    def test_header_shows_dense_mode(self) -> None:
        from run_s4_a_d4_breakdown_eval import _format_markdown
        s = self._stub_summary()
        md = _format_markdown(
            overall=s, by_qtype=[], by_doc_type=[],
            by_caption=[], by_qtype_caption=[],
            n_golden=10, mode="dense",
        )
        self.assertIn("search mode = `dense`", md)
        self.assertIn("KPI #7 ablation", md)

    def test_header_shows_sparse_mode(self) -> None:
        from run_s4_a_d4_breakdown_eval import _format_markdown
        s = self._stub_summary()
        md = _format_markdown(
            overall=s, by_qtype=[], by_doc_type=[],
            by_caption=[], by_qtype_caption=[],
            n_golden=10, mode="sparse",
        )
        self.assertIn("search mode = `sparse`", md)

    def test_default_mode_in_header_is_hybrid(self) -> None:
        from run_s4_a_d4_breakdown_eval import _format_markdown
        s = self._stub_summary()
        md = _format_markdown(
            overall=s, by_qtype=[], by_doc_type=[],
            by_caption=[], by_qtype_caption=[],
            n_golden=10,
        )
        self.assertIn("search mode = `hybrid`", md)


if __name__ == "__main__":
    unittest.main()
