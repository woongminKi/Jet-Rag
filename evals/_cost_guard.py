"""cost 가드레일 helper — 누적 cost 80% / 100% 임계 alert.

motivation
----------
2026-05-10 RAGAS n=30 재측정 sprint 에서 누적 cost ~$0.31 (승인 $0.30) 으로
+0.3% 초과 발생. 향후 cost-incurring 작업의 사전 차단 + 80% 도달 알림 인프라.

사용 패턴 (per-row cost-incurring loop):
    guard = CostGuard(cap_usd=0.30, est_per_unit=0.003)
    for i, item in enumerate(items, start=1):
        # 사전 체크 — break or alert
        action = guard.before_unit(unit_n=1)
        if action == GuardAction.BREAK:
            print(f"[cost-cap] {guard.summary()} → break, ship partial", file=sys.stderr)
            break
        elif action == GuardAction.ALERT:
            print(f"[cost-cap] ⚠ {guard.summary()}", file=sys.stderr)
        # 측정 / 호출
        do_unit(item)
        # 누적 갱신 (실측 비용 또는 추정)
        guard.add_actual(estimated_unit_cost(item))

cap_usd=None 시 비활성 (기존 동작 유지). 측정 도구에서 opt-in.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# 사전 alert 임계 (cap 의 80% 도달 시 stderr 알림)
_ALERT_THRESHOLD = 0.80


class GuardAction(str, Enum):
    """before_unit 의 권고 액션."""

    PROCEED = "proceed"  # 정상 진행
    ALERT = "alert"      # 80%+ 도달, alert 출력 후 진행
    BREAK = "break"      # 다음 unit 진행 시 cap 초과 — break


@dataclass
class CostGuard:
    """누적 cost 추적 + cap 비교.

    - cap_usd=None 시 비활성 (always PROCEED).
    - est_per_unit: unit 1 회의 추정 cost (USD). 사전 cap 도달 예측에 사용.
    - actual_total: 누적 실측/추정 cost (USD).
    - alert_fired: ALERT 1 회 발생 후 재발 차단 (반복 noise 회피).
    """

    cap_usd: float | None = None
    est_per_unit: float = 0.0
    actual_total: float = 0.0
    alert_fired: bool = False

    def is_disabled(self) -> bool:
        return self.cap_usd is None or self.cap_usd <= 0

    def before_unit(self, unit_n: int = 1) -> GuardAction:
        """다음 unit 진행 전 cap 체크. 100% 초과 예측 시 BREAK, 80%+ 도달 시 ALERT.

        unit_n: 다음 step 에서 호출할 unit 수 (default 1).
        부동소수점 정밀도 회피용 epsilon (1e-9) 적용.
        """
        if self.is_disabled():
            return GuardAction.PROCEED
        cap = self.cap_usd  # type: ignore[assignment]
        projected = self.actual_total + (self.est_per_unit * max(unit_n, 0))
        # FP 비교 — projected > cap + 작은 마진
        if projected > cap + 1e-9:
            return GuardAction.BREAK
        # 80% 임계 — actual_total + epsilon ≥ cap × threshold
        if (self.actual_total + 1e-9) >= cap * _ALERT_THRESHOLD and not self.alert_fired:
            self.alert_fired = True
            return GuardAction.ALERT
        return GuardAction.PROCEED

    def add_actual(self, cost_usd: float) -> None:
        """unit 실제 (또는 추정) cost 누적."""
        if cost_usd < 0:
            cost_usd = 0.0
        self.actual_total += cost_usd

    def summary(self) -> str:
        if self.is_disabled():
            return "cost-guard disabled"
        cap = self.cap_usd  # type: ignore[assignment]
        pct = (self.actual_total / cap * 100) if cap > 0 else 0.0
        return f"누적 ${self.actual_total:.4f} / cap ${cap:.4f} ({pct:.1f}%)"
