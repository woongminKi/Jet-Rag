"""S5-B — `evals/run_ragas_regression.py` 단위 테스트.

검증 범위
- stratified_sample — qtype 비율 보존 + cross_doc skip + 결정적 (seed)
- aggregate / by_qtype — None 안전 처리 + n/mean/stdev/min/max
- derive_thresholds — max(statistical, industry) + 표본 부족 분기
- compare_against_baseline — JSON 임계 vs 현재 mean alert

외부 의존성 0 — HTTP / RAGAS / DB 호출 없음. 순수 데이터 변환 검증만.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

_EVALS_DIR = Path(__file__).resolve().parents[2] / "evals"
sys.path.insert(0, str(_EVALS_DIR))


def _mk_row(qid: str, qtype: str, doc_id: str = "doc-A"):
    from run_ragas_regression import GoldenRow

    return GoldenRow(
        id=qid,
        query=f"q-{qid}",
        query_type=qtype,
        doc_id=doc_id,
        expected_answer_summary="",
    )


def _mk_record(qid: str, qtype: str, **scores):
    from run_ragas_regression import RowMeasurement

    rec = RowMeasurement(
        golden_id=qid,
        query_type=qtype,
        doc_id="doc-A",
        query=f"q-{qid}",
        answer="",
        n_contexts=5,
    )
    for k, v in scores.items():
        setattr(rec, k, v)
    return rec


class StratifiedSampleTest(unittest.TestCase):
    def test_preserves_qtype_proportions_within_one(self) -> None:
        from run_ragas_regression import stratified_sample

        # 60 row: exact_fact 30 / fuzzy 15 / synonym 15 (비율 2:1:1)
        rows = (
            [_mk_row(f"E{i}", "exact_fact") for i in range(30)]
            + [_mk_row(f"F{i}", "fuzzy") for i in range(15)]
            + [_mk_row(f"S{i}", "synonym") for i in range(15)]
        )
        picked = stratified_sample(rows, n=12, seed=1)
        c = Counter(r.query_type for r in picked)
        self.assertEqual(sum(c.values()), 12)
        # 비율 ±1 허용 (round 영향)
        self.assertAlmostEqual(c["exact_fact"], 6, delta=1)
        self.assertAlmostEqual(c["fuzzy"], 3, delta=1)
        self.assertAlmostEqual(c["synonym"], 3, delta=1)

    def test_deterministic_with_same_seed(self) -> None:
        from run_ragas_regression import stratified_sample

        rows = [_mk_row(f"X{i}", "exact_fact") for i in range(20)]
        a = [r.id for r in stratified_sample(rows, n=5, seed=42)]
        b = [r.id for r in stratified_sample(rows, n=5, seed=42)]
        self.assertEqual(a, b)

    def test_different_seed_changes_picks(self) -> None:
        from run_ragas_regression import stratified_sample

        rows = [_mk_row(f"X{i}", "exact_fact") for i in range(20)]
        a = set(r.id for r in stratified_sample(rows, n=5, seed=1))
        b = set(r.id for r in stratified_sample(rows, n=5, seed=999))
        # 표본 5/20 → 충돌 가능하지만 완전 일치는 매우 드묾
        self.assertNotEqual(a, b)

    def test_skip_cross_doc_default(self) -> None:
        from run_ragas_regression import stratified_sample

        rows = [_mk_row("U1", "cross_doc", doc_id=""), _mk_row("A1", "exact_fact")]
        picked = stratified_sample(rows, n=2, seed=1, skip_cross_doc=True)
        self.assertEqual([r.id for r in picked], ["A1"])

    def test_include_cross_doc_explicit(self) -> None:
        from run_ragas_regression import stratified_sample

        rows = [_mk_row("U1", "cross_doc", doc_id=""), _mk_row("A1", "exact_fact")]
        picked = stratified_sample(rows, n=2, seed=1, skip_cross_doc=False)
        self.assertEqual({r.id for r in picked}, {"U1", "A1"})

    def test_n_larger_than_eligible_returns_all(self) -> None:
        from run_ragas_regression import stratified_sample

        rows = [_mk_row("A1", "exact_fact"), _mk_row("A2", "exact_fact")]
        picked = stratified_sample(rows, n=999, seed=1)
        self.assertEqual({r.id for r in picked}, {"A1", "A2"})

    def test_minimum_one_per_qtype_when_possible(self) -> None:
        from run_ragas_regression import stratified_sample

        # rare qtype 1 row — n 작아도 최소 1개 보장
        rows = [_mk_row(f"E{i}", "exact_fact") for i in range(30)] + [
            _mk_row("R1", "rare")
        ]
        picked = stratified_sample(rows, n=5, seed=1)
        c = Counter(r.query_type for r in picked)
        self.assertGreaterEqual(c["rare"], 1)


class AggregateTest(unittest.TestCase):
    def test_aggregate_handles_none_values(self) -> None:
        from run_ragas_regression import aggregate

        records = [
            _mk_record("A1", "exact_fact", faithfulness=0.9, answer_relevancy=0.8),
            _mk_record("A2", "exact_fact", faithfulness=0.7, answer_relevancy=None),
            _mk_record("A3", "exact_fact", faithfulness=None, answer_relevancy=0.6),
        ]
        agg = aggregate(records)
        self.assertEqual(agg["faithfulness"].n, 2)
        self.assertAlmostEqual(agg["faithfulness"].mean, 0.8)
        self.assertEqual(agg["answer_relevancy"].n, 2)
        self.assertAlmostEqual(agg["answer_relevancy"].mean, 0.7)
        # context_precision 모두 None
        self.assertEqual(agg["context_precision"].n, 0)
        self.assertIsNone(agg["context_precision"].mean)

    def test_aggregate_min_max_stdev(self) -> None:
        from run_ragas_regression import aggregate

        records = [
            _mk_record("A1", "exact_fact", faithfulness=0.9),
            _mk_record("A2", "exact_fact", faithfulness=0.7),
            _mk_record("A3", "exact_fact", faithfulness=0.5),
        ]
        agg = aggregate(records)
        f = agg["faithfulness"]
        self.assertEqual(f.n, 3)
        self.assertAlmostEqual(f.mean, 0.7)
        self.assertEqual(f.min, 0.5)
        self.assertEqual(f.max, 0.9)
        self.assertGreater(f.stdev, 0)

    def test_by_qtype_groups(self) -> None:
        from run_ragas_regression import by_qtype

        records = [
            _mk_record("A1", "exact_fact", faithfulness=0.9),
            _mk_record("A2", "exact_fact", faithfulness=0.7),
            _mk_record("F1", "fuzzy", faithfulness=0.5),
        ]
        out = by_qtype(records)
        self.assertEqual(set(out.keys()), {"exact_fact", "fuzzy"})
        self.assertAlmostEqual(out["exact_fact"]["faithfulness"].mean, 0.8)
        self.assertEqual(out["fuzzy"]["faithfulness"].mean, 0.5)


class DeriveThresholdsTest(unittest.TestCase):
    def test_uses_max_of_statistical_and_industry(self) -> None:
        from run_ragas_regression import _INDUSTRY_FLOOR, aggregate, derive_thresholds

        # baseline 모두 매우 높음 — 통계 floor 가 industry 보다 큼
        records = [
            _mk_record(
                f"A{i}",
                "exact_fact",
                faithfulness=0.95,
                answer_relevancy=0.92,
                context_precision=0.88,
            )
            for i in range(5)
        ]
        agg = aggregate(records)
        guards = derive_thresholds(agg)
        # stdev ~0 → statistical_floor ≈ mean
        self.assertGreaterEqual(
            guards["faithfulness"].recommended, _INDUSTRY_FLOOR["faithfulness"]
        )
        self.assertGreaterEqual(
            guards["context_precision"].recommended, _INDUSTRY_FLOOR["context_precision"]
        )

    def test_industry_floor_when_baseline_low(self) -> None:
        from run_ragas_regression import _INDUSTRY_FLOOR, aggregate, derive_thresholds

        # baseline 낮음 (mean 0.5, stdev 0.1) → statistical floor 0.3 → industry 더 높음
        records = [
            _mk_record(f"A{i}", "exact_fact", faithfulness=0.5 + (i - 2) * 0.05)
            for i in range(5)
        ]
        agg = aggregate(records)
        guards = derive_thresholds(agg)
        self.assertEqual(
            guards["faithfulness"].recommended, _INDUSTRY_FLOOR["faithfulness"]
        )

    def test_no_baseline_falls_back_to_industry(self) -> None:
        from run_ragas_regression import _INDUSTRY_FLOOR, aggregate, derive_thresholds

        agg = aggregate([])  # 모든 metric None
        guards = derive_thresholds(agg)
        for metric, floor in _INDUSTRY_FLOOR.items():
            self.assertEqual(guards[metric].recommended, floor)
            self.assertIsNone(guards[metric].statistical_floor)


class CompareBaselineTest(unittest.TestCase):
    def test_alert_when_below_threshold(self) -> None:
        from run_ragas_regression import aggregate, compare_against_baseline

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(
                {
                    "threshold_guard": {
                        "faithfulness": {"recommended": 0.85},
                        "answer_relevancy": {"recommended": 0.80},
                        "context_precision": {"recommended": 0.70},
                    }
                },
                f,
            )
            baseline_path = Path(f.name)

        # 회귀 사례 — faithfulness 0.7 (임계 0.85 미만)
        records = [
            _mk_record(
                "A1",
                "exact_fact",
                faithfulness=0.7,
                answer_relevancy=0.85,
                context_precision=0.75,
            )
        ]
        agg = aggregate(records)
        alerts = compare_against_baseline(agg, baseline_path)
        joined = "\n".join(alerts)
        self.assertIn("❌ faithfulness", joined)
        self.assertIn("✅ answer_relevancy", joined)
        self.assertIn("✅ context_precision", joined)

    def test_missing_baseline_returns_warning(self) -> None:
        from run_ragas_regression import aggregate, compare_against_baseline

        alerts = compare_against_baseline(
            aggregate([]),
            Path(tempfile.gettempdir()) / "definitely-not-here-9999.json",
        )
        self.assertTrue(any("baseline JSON 없음" in a for a in alerts))


if __name__ == "__main__":
    unittest.main()
