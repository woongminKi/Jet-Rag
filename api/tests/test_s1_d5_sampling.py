"""S1 D5 — `evals/run_s1_d5_baseline.py` 의 sampling 결정성 회귀 보호.

실 측정 스크립트는 LLM/HF/Supabase 실 호출 (외부 의존성) → 본 단위 테스트는
sampling 로직만 격리 검증한다. seed 가 같으면 같은 결과, 다른 seed 면 분포 다름,
모집단보다 큰 sample 요청 시 cap, sample size=0 시 빈 결과, query_type 별
proportional 분배 — 다섯 케이스로 sampling 함수의 결정성을 회귀 차단.

목적
----
- D5 baseline 측정의 sampling 이 seed 로 100% 재현 가능함을 보증 (`evals/results/`
  를 git 추적 안 해도 다른 환경에서 동일 결과 도출).
- factory 우회 인스턴스화·ResponseRelevancy 캡처 로직은 외부 호출 의존 → 본 파일은
  loop·함수 단위 회귀만 보호. 실측정 정합은 사용자 명시 8단계의 4·5단계로 검증.

회귀 시나리오 (5건)
------------------
1. 같은 seed → 같은 sample (결정성)
2. 다른 seed → 다른 sample (랜덤 작동)
3. sample_size > population → 모집단 그대로 + 경고 없음 (cap)
4. sample_size = 0 → 빈 list (edge case)
5. query_type 별 stratified 분배 (분포 보존)
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# evals/ 디렉토리를 import path 에 추가 — 측정 스크립트 함수 직접 호출.
# `test_auto_goldenset.py:21` 와 동일 패턴.
_EVALS_PATH = Path(__file__).resolve().parents[2] / "evals"
sys.path.insert(0, str(_EVALS_PATH))


class SamplingDeterminismTest(unittest.TestCase):
    """`run_s1_d5_baseline.sample_golden` 의 5 회귀 시나리오."""

    def setUp(self) -> None:
        # lazy import — evals/run_s1_d5_baseline.py 의 헤더가 sys.path 보정 필요.
        from run_s1_d5_baseline import sample_golden

        self._sample_golden = sample_golden

    def _make_population(self, n: int, qt_ratio: tuple[int, int] = (7, 3)) -> list[dict]:
        """qt_ratio = (exact_fact 비율, summary 비율) — 합계가 n.

        ratio 합 != n 이면 자연 분배 (잔여는 첫 type 으로).
        """
        a, b = qt_ratio
        total = a + b
        n_a = (n * a) // total
        n_b = n - n_a
        rows: list[dict] = []
        for i in range(n_a):
            rows.append({"id": f"A-{i:03d}", "query": f"qa{i}", "query_type": "exact_fact"})
        for i in range(n_b):
            rows.append({"id": f"B-{i:03d}", "query": f"qb{i}", "query_type": "summary"})
        return rows

    def test_same_seed_same_sample(self) -> None:
        """같은 seed 면 두 호출 결과의 id 순서까지 동일."""
        pop = self._make_population(50)
        s1 = self._sample_golden(pop, sample_size=10, seed=42)
        s2 = self._sample_golden(pop, sample_size=10, seed=42)
        self.assertEqual([r["id"] for r in s1], [r["id"] for r in s2])

    def test_different_seed_diff_sample(self) -> None:
        """다른 seed 면 sample id set 이 달라야 한다 (충돌 확률 0 아니지만 50C10 충분히 큼)."""
        pop = self._make_population(50)
        s_a = self._sample_golden(pop, sample_size=10, seed=42)
        s_b = self._sample_golden(pop, sample_size=10, seed=99)
        self.assertNotEqual([r["id"] for r in s_a], [r["id"] for r in s_b])

    def test_sample_size_exceeds_population(self) -> None:
        """sample_size > len(pop) → 모집단 그대로 반환 (정렬은 보장 X)."""
        pop = self._make_population(5)
        sample = self._sample_golden(pop, sample_size=10, seed=42)
        self.assertEqual(len(sample), 5)
        self.assertEqual({r["id"] for r in sample}, {r["id"] for r in pop})

    def test_sample_size_zero(self) -> None:
        """sample_size=0 → 빈 list."""
        pop = self._make_population(50)
        sample = self._sample_golden(pop, sample_size=0, seed=42)
        self.assertEqual(sample, [])

    def test_stratified_by_query_type(self) -> None:
        """stratified=True 시 query_type 비율 보존 (모집단 70/30 → sample 도 70/30 ±1).

        모집단 100 (70 exact_fact + 30 summary) 에서 10 sample 요청 시 7/3 ±1 보장.
        """
        pop = self._make_population(100, qt_ratio=(7, 3))
        sample = self._sample_golden(pop, sample_size=10, seed=42, stratified=True)
        self.assertEqual(len(sample), 10)
        n_a = sum(1 for r in sample if r["query_type"] == "exact_fact")
        n_b = sum(1 for r in sample if r["query_type"] == "summary")
        # 70/30 비율 → 7/3 ± 1 (반올림 오차 허용)
        self.assertGreaterEqual(n_a, 6)
        self.assertLessEqual(n_a, 8)
        self.assertGreaterEqual(n_b, 2)
        self.assertLessEqual(n_b, 4)


if __name__ == "__main__":
    unittest.main()
