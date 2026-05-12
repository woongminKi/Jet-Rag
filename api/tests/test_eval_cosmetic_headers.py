"""W-6 (C) — eval 산출 markdown 헤더의 골든셋 파일명 동적화 단위 테스트.

PRD `work-log/2026-05-12 검색 정확도 80% 달성 PRD.md` §3 W-6.

대상
- `evals/run_s3_d5_search_stack_eval.py` `_format_markdown` — `golden_v1.csv` 하드코딩 제거
- `evals/eval_retrieval_metrics.py` `_format_markdown` / `_format_multi_doc_md` —
  `golden_v0.4_sonata.csv` 하드코딩 제거

stdlib unittest only — 외부 의존성 0 (markdown 문자열 생성만 검증).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_EVALS_DIR = Path(__file__).resolve().parents[2] / "evals"
sys.path.insert(0, str(_EVALS_DIR))


class S3D5HeaderTests(unittest.TestCase):
    def test_s3_d5_header_reflects_csv_name(self) -> None:
        """`run_s3_d5_search_stack_eval._format_markdown(golden_csv_name=...)` 가
        넘긴 파일명을 그대로 헤더에 반영, 옛 `golden_v1.csv` 하드코딩 미포함."""
        from run_s3_d5_search_stack_eval import ComboSummary, _format_markdown

        s = ComboSummary(
            combo="a",
            label="RRF-only",
            n_rows=183,
            n_chunk_evaluable=176,
            avg_recall_at_10=0.68,
            avg_ndcg_at_10=0.6,
            avg_mrr=0.5,
            top1_rate=0.8,
            p95_latency_ms=230.0,
            avg_latency_ms=120.0,
            cache_hit_rate=0.0,
            invoke_rate=0.0,
            degrade_rate=0.0,
            disabled_rate=1.0,
            doc_match_fail=2,
            error_count=0,
        )
        md = _format_markdown(
            summaries=[s],
            cross_doc=None,
            n_golden=183,
            mock_reranker=False,
            cells_by_combo={"a": []},
            golden_csv_name="golden_v2.csv",
        )
        self.assertIn("golden_v2.csv", md)
        self.assertNotIn("golden_v1.csv", md)
        self.assertIn("183 row", md)


class EvalRetrievalMetricsHeaderTests(unittest.TestCase):
    def test_eval_retrieval_metrics_header_reflects_csv_name(self) -> None:
        """`eval_retrieval_metrics._format_markdown(golden_csv_name=...)` 가
        넘긴 파일명을 헤더에 반영, 옛 `golden_v0.4_sonata.csv` 하드코딩 미포함."""
        from eval_retrieval_metrics import _format_markdown

        agg = {"recall_at_10": 0.7, "mrr": 0.5, "ndcg_at_10": 0.6, "n": 10}
        per_query = [
            {"id": "q1", "query": "테스트", "recall_at_10": 0.7, "mrr": 0.5,
             "ndcg_at_10": 0.6, "took_ms": 100,
             "relevant_chunks": [1, 2], "predicted_top10": [1, 2, 3]}
        ]
        md = _format_markdown(
            per_query, agg, None, None, "doc-uuid", "golden_v1.csv"
        )
        self.assertIn("golden_v1.csv", md)
        self.assertNotIn("golden_v0.4_sonata.csv", md)

    def test_eval_retrieval_metrics_multi_doc_header_reflects_csv_name(self) -> None:
        """`eval_retrieval_metrics._format_multi_doc_md(golden_csv_name=...)` 동일 검증."""
        from eval_retrieval_metrics import _format_multi_doc_md

        agg = {
            "top1": 0.8, "top3": 0.9, "doc_mrr": 0.7,
            "chunk_recall_in_response": 0.5, "n": 10,
        }
        per_query = [
            {"id": "q1", "query": "테스트", "doc_top1": True, "doc_top3": True,
             "doc_rank": 1, "doc_mrr": 1.0, "chunk_recall_in_response": 0.5,
             "took_ms": 100},
        ]
        md = _format_multi_doc_md(
            per_query, agg, None, None, "doc-uuid", "golden_v1.csv"
        )
        self.assertIn("golden_v1.csv", md)
        self.assertNotIn("golden_v0.4_sonata.csv", md)


if __name__ == "__main__":
    unittest.main()
