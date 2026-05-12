"""W25 D14+1 (E) — retrieval_metrics 단위 테스트.

Recall@K / MRR / nDCG@K 계산 정확성 + edge case (빈 입력 / 정답 없음).
stdlib unittest 만 — 외부 의존성 0.
"""

from __future__ import annotations

import math
import unittest


class RecallAtKTest(unittest.TestCase):
    def test_perfect_recall(self) -> None:
        from app.services.retrieval_metrics import recall_at_k
        # 정답 [1, 2, 3] 모두 top-3 안에 있음
        self.assertEqual(recall_at_k([1, 2, 3, 4, 5], {1, 2, 3}, k=10), 1.0)

    def test_partial_recall(self) -> None:
        from app.services.retrieval_metrics import recall_at_k
        # 정답 3개 중 1개 잡힘 → 1/3
        self.assertAlmostEqual(recall_at_k([1, 99, 98], {1, 2, 3}, k=10), 1 / 3)

    def test_no_relevant_returns_zero(self) -> None:
        from app.services.retrieval_metrics import recall_at_k
        self.assertEqual(recall_at_k([1, 2, 3], set(), k=10), 0.0)

    def test_k_caps_predictions(self) -> None:
        from app.services.retrieval_metrics import recall_at_k
        # D1 정정 — Recall@K 분모는 cap K (nDCG IDCG 와 일관).
        # k=2, predicted=[1, 99, 2, 3], relevant={1,2,3}:
        # hit_score = 1.0 (chunk 1) / max_score (cap k=2) = 2.0 → 0.5
        self.assertAlmostEqual(recall_at_k([1, 99, 2, 3], {1, 2, 3}, k=2), 0.5)

    def test_empty_predictions(self) -> None:
        from app.services.retrieval_metrics import recall_at_k
        self.assertEqual(recall_at_k([], {1, 2}, k=10), 0.0)


class MRRTest(unittest.TestCase):
    def test_first_hit_at_rank_1(self) -> None:
        from app.services.retrieval_metrics import mrr
        self.assertEqual(mrr([1, 99, 98], {1, 2, 3}, k=10), 1.0)

    def test_first_hit_at_rank_3(self) -> None:
        from app.services.retrieval_metrics import mrr
        self.assertAlmostEqual(mrr([99, 98, 1], {1}, k=10), 1 / 3)

    def test_no_hit_returns_zero(self) -> None:
        from app.services.retrieval_metrics import mrr
        self.assertEqual(mrr([99, 98, 97], {1, 2}, k=10), 0.0)

    def test_hit_after_k_returns_zero(self) -> None:
        from app.services.retrieval_metrics import mrr
        # k=2 안에 정답 없음 → 0 (rank 3 의 정답 무시)
        self.assertEqual(mrr([99, 98, 1], {1}, k=2), 0.0)


class NDCGTest(unittest.TestCase):
    def test_perfect_ranking_returns_one(self) -> None:
        from app.services.retrieval_metrics import ndcg_at_k
        # 정답 [1, 2, 3] 이 top-3 에 정확히 = IDCG 와 동일 → 1.0
        self.assertAlmostEqual(ndcg_at_k([1, 2, 3, 99, 98], {1, 2, 3}, k=10), 1.0)

    def test_no_relevant_returns_zero(self) -> None:
        from app.services.retrieval_metrics import ndcg_at_k
        self.assertEqual(ndcg_at_k([1, 2, 3], set(), k=10), 0.0)

    def test_known_value_calculation(self) -> None:
        """ranking [1, 99, 2] / relevant {1, 2} / k=3 → DCG = 1/log2(2) + 0 + 1/log2(4)
        = 1.0 + 0.5 = 1.5. IDCG (정답 2개 ideal) = 1/log2(2) + 1/log2(3) ≈ 1.6309.
        nDCG ≈ 1.5 / 1.6309 ≈ 0.9197.
        """
        from app.services.retrieval_metrics import ndcg_at_k
        result = ndcg_at_k([1, 99, 2], {1, 2}, k=3)
        expected = (1.0 + 1.0 / math.log2(4)) / (1.0 + 1.0 / math.log2(3))
        self.assertAlmostEqual(result, expected, places=4)

    def test_no_hit_returns_zero(self) -> None:
        from app.services.retrieval_metrics import ndcg_at_k
        self.assertEqual(ndcg_at_k([99, 98, 97], {1, 2}, k=10), 0.0)

    def test_idcg_capped_at_k(self) -> None:
        """relevant 가 K 보다 많으면 IDCG 는 K 까지만 ideal."""
        from app.services.retrieval_metrics import ndcg_at_k
        # relevant 5개 / k=2 / ranking [1, 2, ...] 이 정답 둘 → DCG = 1 + 1/log2(3)
        # IDCG (k=2) = 1 + 1/log2(3) → nDCG = 1.0
        self.assertAlmostEqual(
            ndcg_at_k([1, 2, 99], {1, 2, 3, 4, 5}, k=2), 1.0
        )


class GradedRelevanceTest(unittest.TestCase):
    """D1 정정 — relevant + acceptable graded relevance 동작 검증."""

    def test_recall_at_k_with_acceptable(self) -> None:
        from app.services.retrieval_metrics import recall_at_k
        # relevant {1, 2}, acceptable {3}, top-3 = [1, 99, 3]
        # hit_score = 1.0 (chunk 1) + 0 + 0.5 (chunk 3) = 1.5
        # max_score (cap k=10) = 1.0+1.0+0.5 = 2.5
        # recall = 1.5 / 2.5 = 0.6
        result = recall_at_k([1, 99, 3], {1, 2}, k=10, acceptable_chunks={3})
        self.assertAlmostEqual(result, 1.5 / 2.5, places=4)

    def test_recall_acceptable_only_no_relevant(self) -> None:
        """relevant 없고 acceptable hit 만 있을 때."""
        from app.services.retrieval_metrics import recall_at_k
        # relevant {}, acceptable {3}, top-1 = [3]
        # hit_score = 0.5, max = 0.5 → 1.0
        result = recall_at_k([3, 99], set(), k=10, acceptable_chunks={3})
        self.assertAlmostEqual(result, 1.0, places=4)

    def test_recall_legacy_binary_when_acceptable_none(self) -> None:
        """acceptable_chunks=None 시 기존 binary 동작 유지 — backward compatible."""
        from app.services.retrieval_metrics import recall_at_k
        self.assertEqual(recall_at_k([1, 2, 3], {1, 2, 3}, k=10), 1.0)
        self.assertAlmostEqual(recall_at_k([1, 99], {1, 2, 3}, k=10), 1 / 3)

    def test_mrr_relevant_priority_over_acceptable(self) -> None:
        """relevant rank 가 acceptable 보다 앞이면 relevant 의 1/rank."""
        from app.services.retrieval_metrics import mrr
        # ranking [1, 3] / relevant {1} / acceptable {3} → 1.0 (rank 1 relevant)
        result = mrr([1, 3], {1}, k=10, acceptable_chunks={3})
        self.assertEqual(result, 1.0)

    def test_mrr_acceptable_only_returns_half(self) -> None:
        """relevant 0 hit, acceptable 만 hit — 0.5 / rank."""
        from app.services.retrieval_metrics import mrr
        # ranking [99, 3] / relevant {1} / acceptable {3} → 0.5 / 2 = 0.25
        result = mrr([99, 3], {1}, k=10, acceptable_chunks={3})
        self.assertAlmostEqual(result, 0.25, places=4)

    def test_ndcg_with_acceptable(self) -> None:
        """nDCG graded relevance 계산 정확성."""
        from app.services.retrieval_metrics import ndcg_at_k
        import math
        # ranking [1, 3] / relevant {1} (1.0) / acceptable {3} (0.5)
        # DCG = 1.0/log2(2) + 0.5/log2(3) = 1.0 + 0.5/log2(3)
        # IDCG = 1.0/log2(2) + 0.5/log2(3) = 동일 → nDCG=1.0
        result = ndcg_at_k([1, 3], {1}, k=10, acceptable_chunks={3})
        self.assertAlmostEqual(result, 1.0, places=4)

    def test_ndcg_acceptable_in_wrong_position(self) -> None:
        """acceptable 이 정답 위치보다 앞에 있어도 graded score 적용."""
        from app.services.retrieval_metrics import ndcg_at_k
        # ranking [3, 1] / relevant {1} (rank 2) / acceptable {3} (rank 1)
        # DCG = 0.5/log2(2) + 1.0/log2(3)
        # IDCG (ideal) = 1.0/log2(2) + 0.5/log2(3)
        # nDCG < 1.0
        result = ndcg_at_k([3, 1], {1}, k=10, acceptable_chunks={3})
        self.assertLess(result, 1.0)
        self.assertGreater(result, 0.5)


class GradedRecallFourCaseTest(unittest.TestCase):
    """S2 D5 phase 1 §6.3 도구 보강 — `_measure_baseline_retrieval` 가
    `acceptable_chunks` 를 전달했을 때 4 분기 동작 확인.

    - acceptable hit only: relevant {1} (miss) + acceptable {3} (hit)
    - relevant hit only: relevant {1} (hit) + acceptable {3} (miss)
    - both hit:          relevant {1} (hit) + acceptable {3} (hit)
    - both miss:         relevant {1} + acceptable {3} (predicted 와 disjoint)
    """

    _RELEVANT: set[int] = {1}
    _ACCEPTABLE: set[int] = {3}

    def test_acceptable_hit_only(self) -> None:
        from app.services.retrieval_metrics import recall_at_k

        # predicted=[3] → relevant miss, acceptable hit (3)
        # hit_score=0.5, max_score=1.0(rel)+0.5(accept)=1.5 → 0.5/1.5 = 1/3
        result = recall_at_k(
            [3, 99],
            self._RELEVANT,
            k=10,
            acceptable_chunks=self._ACCEPTABLE,
        )
        self.assertAlmostEqual(result, 1 / 3, places=4)
        # acceptable_chunks=None 으로 호출했더라면 0.0 (acceptable 무시)
        binary = recall_at_k([3, 99], self._RELEVANT, k=10)
        self.assertEqual(binary, 0.0)

    def test_relevant_hit_only(self) -> None:
        from app.services.retrieval_metrics import recall_at_k

        # predicted=[1] → relevant hit (1), acceptable miss
        # hit_score=1.0, max_score=1.5 → 2/3
        result = recall_at_k(
            [1, 99],
            self._RELEVANT,
            k=10,
            acceptable_chunks=self._ACCEPTABLE,
        )
        self.assertAlmostEqual(result, 2 / 3, places=4)

    def test_both_hit(self) -> None:
        from app.services.retrieval_metrics import recall_at_k

        # predicted=[1, 3] → relevant + acceptable 모두 hit
        # hit_score=1.0+0.5=1.5, max_score=1.5 → 1.0
        result = recall_at_k(
            [1, 3, 99],
            self._RELEVANT,
            k=10,
            acceptable_chunks=self._ACCEPTABLE,
        )
        self.assertAlmostEqual(result, 1.0, places=4)

    def test_both_miss(self) -> None:
        from app.services.retrieval_metrics import recall_at_k

        # predicted=[99, 98] → 둘 다 miss
        # hit_score=0, max_score=1.5 → 0.0
        result = recall_at_k(
            [99, 98],
            self._RELEVANT,
            k=10,
            acceptable_chunks=self._ACCEPTABLE,
        )
        self.assertEqual(result, 0.0)


class CrossDocTupleKeyTest(unittest.TestCase):
    """golden_v2 cross_doc relabel — chunk key 가 `(alias, chunk_idx)` 튜플인 경우.

    retrieval_metrics 는 set `in` 비교만 쓰므로 hashable key 면 형태 무관.
    int key 와 동일한 수치가 나와야 한다 (회귀 보호).
    """

    def test_recall_with_tuple_keys(self) -> None:
        from app.services.retrieval_metrics import recall_at_k

        relv = {("law2", 10), ("law3", 13)}
        accept = {("law2", 27), ("law2", 29), ("law3", 24)}
        predicted = [("law2", 10), ("law2", 99), ("law3", 24), ("law3", 13)]
        # hit_score = 1.0 (law2:10) + 0 + 0.5 (law3:24) + 1.0 (law3:13) = 2.5
        # max_score (cap k=10) = 1.0+1.0 + 0.5+0.5+0.5 = 3.5
        result = recall_at_k(predicted, relv, k=10, acceptable_chunks=accept)
        self.assertAlmostEqual(result, 2.5 / 3.5, places=4)

    def test_mrr_with_tuple_keys_relevant_priority(self) -> None:
        from app.services.retrieval_metrics import mrr

        # ranking [law2:27(accept), law2:10(relevant)] → relevant rank 2 우선? 아니다 —
        # mrr 는 "첫 hit" 기준. rank 1 이 acceptable hit → 0.5/1 = 0.5.
        relv = {("law2", 10)}
        accept = {("law2", 27)}
        result = mrr([("law2", 27), ("law2", 10)], relv, k=10, acceptable_chunks=accept)
        self.assertAlmostEqual(result, 0.5, places=4)
        # relevant 가 rank 1 이면 1.0
        result2 = mrr([("law2", 10), ("law2", 27)], relv, k=10, acceptable_chunks=accept)
        self.assertEqual(result2, 1.0)

    def test_ndcg_with_tuple_keys(self) -> None:
        from app.services.retrieval_metrics import ndcg_at_k
        import math

        # ranking [a:1(rel), a:2(accept)] / relevant {a:1} / acceptable {a:2}
        # DCG = 1.0/log2(2) + 0.5/log2(3) ; IDCG 동일 → 1.0
        relv = {("a", 1)}
        accept = {("a", 2)}
        result = ndcg_at_k([("a", 1), ("a", 2)], relv, k=10, acceptable_chunks=accept)
        self.assertAlmostEqual(result, 1.0, places=4)

    def test_int_keys_still_work(self) -> None:
        """generic 화 이후에도 int key 입력은 기존 동작 그대로 (하위 호환)."""
        from app.services.retrieval_metrics import recall_at_k, mrr, ndcg_at_k

        self.assertEqual(recall_at_k([1, 2, 3], {1, 2, 3}, k=10), 1.0)
        self.assertEqual(mrr([1, 99], {1}, k=10), 1.0)
        self.assertAlmostEqual(ndcg_at_k([1, 2, 3, 99], {1, 2, 3}, k=10), 1.0)


class AggregateMetricsTest(unittest.TestCase):
    def test_empty_input(self) -> None:
        from app.services.retrieval_metrics import aggregate_metrics
        result = aggregate_metrics([])
        self.assertEqual(result["n"], 0)
        self.assertEqual(result["recall_at_10"], 0.0)

    def test_average(self) -> None:
        from app.services.retrieval_metrics import aggregate_metrics
        result = aggregate_metrics([
            {"recall_at_10": 1.0, "mrr": 1.0, "ndcg_at_10": 1.0},
            {"recall_at_10": 0.5, "mrr": 0.5, "ndcg_at_10": 0.5},
        ])
        self.assertEqual(result["n"], 2)
        self.assertAlmostEqual(result["recall_at_10"], 0.75)
        self.assertAlmostEqual(result["mrr"], 0.75)
        self.assertAlmostEqual(result["ndcg_at_10"], 0.75)


if __name__ == "__main__":
    unittest.main()
