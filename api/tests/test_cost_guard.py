"""evals/_cost_guard.py 단위 테스트.

검증 범위
- disabled (cap_usd=None / 0) → 항상 PROCEED
- 80% 도달 시 ALERT 1회 발화 (재발 차단)
- 100% 초과 예측 시 BREAK
- summary string 형식 (누적 / cap / pct)
- add_actual 음수 graceful

stdlib unittest only — 외부 의존성 0.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_EVALS_DIR = Path(__file__).resolve().parents[2] / "evals"
sys.path.insert(0, str(_EVALS_DIR))


class CostGuardDisabledTest(unittest.TestCase):
    def test_none_cap_always_proceeds(self) -> None:
        from _cost_guard import CostGuard, GuardAction

        g = CostGuard(cap_usd=None, est_per_unit=1.0)
        for _ in range(5):
            self.assertEqual(g.before_unit(), GuardAction.PROCEED)
            g.add_actual(0.5)

    def test_zero_cap_treated_as_disabled(self) -> None:
        from _cost_guard import CostGuard, GuardAction

        g = CostGuard(cap_usd=0.0, est_per_unit=1.0)
        self.assertEqual(g.before_unit(), GuardAction.PROCEED)

    def test_summary_when_disabled(self) -> None:
        from _cost_guard import CostGuard

        g = CostGuard(cap_usd=None)
        self.assertEqual(g.summary(), "cost-guard disabled")


class CostGuardAlertTest(unittest.TestCase):
    def test_alert_fires_at_80pct(self) -> None:
        from _cost_guard import CostGuard, GuardAction

        g = CostGuard(cap_usd=1.0, est_per_unit=0.1)
        # 8 unit 누적 → 0.8 = 80% → 다음 step 시 ALERT
        for _ in range(8):
            self.assertEqual(g.before_unit(), GuardAction.PROCEED)
            g.add_actual(0.1)
        self.assertEqual(g.before_unit(), GuardAction.ALERT)

    def test_alert_fires_only_once(self) -> None:
        from _cost_guard import CostGuard, GuardAction

        g = CostGuard(cap_usd=1.0, est_per_unit=0.05)
        # 80% 도달
        for _ in range(8):
            g.add_actual(0.1)
        # 첫 호출: ALERT
        self.assertEqual(g.before_unit(), GuardAction.ALERT)
        # 다음 호출: PROCEED (재발 X)
        g.add_actual(0.05)
        self.assertEqual(g.before_unit(), GuardAction.PROCEED)


class CostGuardBreakTest(unittest.TestCase):
    def test_break_when_projected_exceeds_cap(self) -> None:
        from _cost_guard import CostGuard, GuardAction

        g = CostGuard(cap_usd=0.30, est_per_unit=0.05)
        # 0.30 - 0.05 = 0.25 누적 까지 OK
        for _ in range(5):
            self.assertEqual(g.before_unit(), GuardAction.PROCEED)
            g.add_actual(0.05)
        # 누적 0.25, est 0.05 → projected 0.30 = cap → PROCEED (= 아니라 ALERT 가능)
        action = g.before_unit()
        # 0.25 / 0.30 = 83% → ALERT
        self.assertEqual(action, GuardAction.ALERT)
        g.add_actual(0.05)  # 0.30
        # 누적 0.30, est 0.05 → projected 0.35 > 0.30 → BREAK
        self.assertEqual(g.before_unit(), GuardAction.BREAK)

    def test_break_with_unit_n_multiplier(self) -> None:
        from _cost_guard import CostGuard, GuardAction

        g = CostGuard(cap_usd=1.0, est_per_unit=0.1)
        # 누적 0.5, est 0.1 × 6 unit = 0.6 → projected 1.1 > 1.0 → BREAK
        g.actual_total = 0.5
        self.assertEqual(g.before_unit(unit_n=6), GuardAction.BREAK)
        # 5 unit 만 = 0.5 → projected 1.0 = cap → PROCEED 또는 ALERT
        action = g.before_unit(unit_n=5)
        self.assertIn(action, (GuardAction.PROCEED, GuardAction.ALERT))


class CostGuardSummaryTest(unittest.TestCase):
    def test_summary_format(self) -> None:
        from _cost_guard import CostGuard

        g = CostGuard(cap_usd=0.30)
        g.actual_total = 0.18
        s = g.summary()
        self.assertIn("$0.1800", s)
        self.assertIn("$0.3000", s)
        self.assertIn("60.0%", s)

    def test_negative_cost_treated_as_zero(self) -> None:
        from _cost_guard import CostGuard

        g = CostGuard(cap_usd=1.0)
        g.add_actual(-5.0)  # 음수 — graceful 0
        self.assertEqual(g.actual_total, 0.0)


if __name__ == "__main__":
    unittest.main()
