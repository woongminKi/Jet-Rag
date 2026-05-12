"""S4-A P1 — `evals/run_s4_a_d4_breakdown_eval.py` cross_doc re-merge 라운드로빈 단위 테스트.

검증 대상: ``_round_robin_cross_doc_chunks(target_items) -> list[(alias, chunk_idx)]``
- 각 target doc 의 matched_chunks 를 RRF desc 로 정렬
- doc 순서는 alias 사전순 (결정적 — run-to-run churn 0)
- rank 0 부터 라운드로빈 인터리브
- alias_map 미등록 doc_id item 은 skip (C 결정)

stdlib unittest only — Supabase/search 호출 0.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_EVALS_DIR = Path(__file__).resolve().parents[2] / "evals"
sys.path.insert(0, str(_EVALS_DIR))


def _doc_id_for(alias: str) -> str:
    from run_s4_a_d4_breakdown_eval import _ALIAS_MAP

    return _ALIAS_MAP[alias].doc_id


class RoundRobinCrossDocChunksTest(unittest.TestCase):
    def test_interleaves_two_docs_by_rank(self) -> None:
        from run_s4_a_d4_breakdown_eval import _round_robin_cross_doc_chunks

        # 운영내규 < 직제규정 (alias 사전순) → 운영내규 가 rank 마다 먼저.
        items = [
            {
                "doc_id": _doc_id_for("직제규정"),
                "matched_chunks": [
                    {"chunk_idx": 58, "rrf_score": 0.9},
                    {"chunk_idx": 59, "rrf_score": 0.7},
                ],
            },
            {
                "doc_id": _doc_id_for("운영내규"),
                "matched_chunks": [
                    {"chunk_idx": 22, "rrf_score": 0.8},
                    {"chunk_idx": 21, "rrf_score": 0.6},
                ],
            },
        ]
        merged = _round_robin_cross_doc_chunks(items)
        self.assertEqual(
            merged,
            [
                ("운영내규", 22),
                ("직제규정", 58),
                ("운영내규", 21),
                ("직제규정", 59),
            ],
        )

    def test_uneven_lengths_drain_longer_doc(self) -> None:
        from run_s4_a_d4_breakdown_eval import _round_robin_cross_doc_chunks

        items = [
            {
                "doc_id": _doc_id_for("law2"),
                "matched_chunks": [
                    {"chunk_idx": 10, "rrf_score": 0.9},
                    {"chunk_idx": 27, "rrf_score": 0.8},
                    {"chunk_idx": 29, "rrf_score": 0.7},
                ],
            },
            {
                "doc_id": _doc_id_for("law3"),
                "matched_chunks": [{"chunk_idx": 13, "rrf_score": 0.95}],
            },
        ]
        merged = _round_robin_cross_doc_chunks(items)
        # law2 < law3 (사전순) → rank0: law2(10), law3(13); rank1: law2(27); rank2: law2(29)
        self.assertEqual(
            merged,
            [("law2", 10), ("law3", 13), ("law2", 27), ("law2", 29)],
        )

    def test_within_doc_sorted_by_rrf_desc(self) -> None:
        from run_s4_a_d4_breakdown_eval import _round_robin_cross_doc_chunks

        items = [
            {
                "doc_id": _doc_id_for("law2"),
                # 일부러 RRF 역순으로 입력 → 내부에서 desc 재정렬되어야
                "matched_chunks": [
                    {"chunk_idx": 5, "rrf_score": 0.1},
                    {"chunk_idx": 99, "rrf_score": 0.99},
                ],
            },
        ]
        merged = _round_robin_cross_doc_chunks(items)
        self.assertEqual(merged, [("law2", 99), ("law2", 5)])

    def test_skips_unregistered_doc_id(self) -> None:
        from run_s4_a_d4_breakdown_eval import _round_robin_cross_doc_chunks

        items = [
            {"doc_id": "00000000-0000-0000-0000-000000000000",
             "matched_chunks": [{"chunk_idx": 1, "rrf_score": 0.9}]},
            {"doc_id": _doc_id_for("law3"),
             "matched_chunks": [{"chunk_idx": 13, "rrf_score": 0.8}]},
        ]
        merged = _round_robin_cross_doc_chunks(items)
        self.assertEqual(merged, [("law3", 13)])

    def test_empty_items_returns_empty(self) -> None:
        from run_s4_a_d4_breakdown_eval import _round_robin_cross_doc_chunks

        self.assertEqual(_round_robin_cross_doc_chunks([]), [])

    def test_deterministic_doc_order_independent_of_item_order(self) -> None:
        """target_items 입력 순서가 달라도 결과 동일 — churn 0 보장."""
        from run_s4_a_d4_breakdown_eval import _round_robin_cross_doc_chunks

        a = {"doc_id": _doc_id_for("운영내규"),
             "matched_chunks": [{"chunk_idx": 22, "rrf_score": 0.8}]}
        b = {"doc_id": _doc_id_for("직제규정"),
             "matched_chunks": [{"chunk_idx": 58, "rrf_score": 0.9}]}
        self.assertEqual(
            _round_robin_cross_doc_chunks([a, b]),
            _round_robin_cross_doc_chunks([b, a]),
        )


if __name__ == "__main__":
    unittest.main()
