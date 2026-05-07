"""S0 D4 (2026-05-07) — vision 비용 cap 가드.

master plan §6 S0 D4 + §7.4 정합:

    if doc_cost  > doc_budget_usd  → BudgetStatus(allowed=False, scope='doc')
    if daily_cost > daily_budget_usd → BudgetStatus(allowed=False, scope='daily')
    else                              → BudgetStatus(allowed=True)

호출 시점
    인제스트의 vision API 호출 직전 (페이지 단위). cap 도달 시 vision 호출
    skip + `documents.flags.vision_budget_exceeded=true` set + 인제스트는
    graceful 진행 (실패 X — base_result 그대로 chunks 적재).

설계 원칙
    - 외부 의존성 0 — supabase client 직접 사용 (vision_cache 와 동일 패턴)
    - DB 부재 / 마이그 014 미적용 시 graceful — `allowed=True` (cap 미적용)
      운영 graceful + 단위 테스트 mock-free 가능 (vision_metrics·vision_cache 패턴 답습)
    - ENV `JETRAG_BUDGET_GUARD_DISABLE=1` → 모든 호출 즉시 allowed=True
      (회귀 안전망 + 디버깅 + S2 본 ship 전 토글 가능)
    - SUM 결과 캐싱 X — 비용 0.x 초 vs DB 누적량 정확성 트레이드오프 → 정확성 우선.
      무료 페르소나 A 1일 5 doc 가정 시 SUM 부하는 무시 가능.

스코프 정의 (D4 ship)
    - doc: 단일 doc 의 vision_usage_log 누적 (success=true 만).
    - daily: 오늘 (UTC midnight ~ now) 의 vision_usage_log 누적 (success=true 만).
      D5 에서 24h sliding window 추가 예정 (master plan §6 S0 D5 — vision 24h
      누적 cap 자가 차단). 본 D4 의 daily 는 calendar day (UTC midnight 리셋).

반환값 일관성
    - allowed=True : 인제스트는 vision 호출 진행
    - allowed=False : vision 호출 skip + flags 마킹
    - reason 은 한국어 — 사용자 노출 가능한 톤 (UI 가 그대로 표시 가능)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Literal

logger = logging.getLogger(__name__)

# ENV — 운영 회복 / 디버깅용 비활성 토글. master plan §7.4 default 채택 ("1" = ON).
# "1" / "0" 만 인식. 이외 값은 default ON (보수적).
_DISABLE_ENV_KEY = "JETRAG_BUDGET_GUARD_DISABLE"

# 첫 1회만 warn (이후 debug) — vision_cache / vision_metrics 패턴.
_first_warn_logged: bool = False

# scope literal — JSON flags 에 그대로 저장됨.
BudgetScope = Literal["doc", "daily"]


@dataclass(frozen=True)
class BudgetStatus:
    """가드 결과 — 인제스트가 즉시 분기 가능한 형태."""

    allowed: bool
    used_usd: float  # 누적 사용량 (USD). 0 = 미측정 (graceful).
    cap_usd: float  # 적용된 cap (USD). settings 의 doc_/daily_ 값.
    scope: BudgetScope  # 'doc' or 'daily' — 어느 한도 검사인지.
    reason: str  # 한국어 — 로그 + UI 표시 가능 톤.


def is_disabled() -> bool:
    """ENV 토글. 단위 테스트 / 디버깅 시 "1" 로 set 하면 모든 가드 통과."""
    return os.environ.get(_DISABLE_ENV_KEY, "0") == "1"


def check_doc_budget(
    *,
    doc_id: str,
    cap_usd: float,
) -> BudgetStatus:
    """단일 doc 의 vision 누적 비용이 cap_usd 초과했는지.

    DB 부재 / 마이그 014 미적용 / SUM 실패 시 graceful — allowed=True.
    """
    if is_disabled():
        return BudgetStatus(
            allowed=True,
            used_usd=0.0,
            cap_usd=cap_usd,
            scope="doc",
            reason="가드 비활성 (ENV)",
        )
    if not doc_id:
        return BudgetStatus(
            allowed=True,
            used_usd=0.0,
            cap_usd=cap_usd,
            scope="doc",
            reason="doc_id 미지정 (단독 이미지 호출)",
        )

    used = _sum_doc_cost(doc_id)
    if used is None:
        return BudgetStatus(
            allowed=True,
            used_usd=0.0,
            cap_usd=cap_usd,
            scope="doc",
            reason="DB 조회 실패 — 가드 graceful (allowed)",
        )
    if used > cap_usd:
        return BudgetStatus(
            allowed=False,
            used_usd=used,
            cap_usd=cap_usd,
            scope="doc",
            reason=(
                f"문서당 비용 한도 초과 "
                f"(${used:.4f} > ${cap_usd:.4f}) — vision 보강 일부 생략"
            ),
        )
    return BudgetStatus(
        allowed=True,
        used_usd=used,
        cap_usd=cap_usd,
        scope="doc",
        reason="문서 한도 내",
    )


def check_daily_budget(*, cap_usd: float) -> BudgetStatus:
    """오늘 (UTC midnight ~ now) 의 vision 누적 비용이 cap_usd 초과했는지.

    DB 부재 / 마이그 014 미적용 / SUM 실패 시 graceful — allowed=True.
    """
    if is_disabled():
        return BudgetStatus(
            allowed=True,
            used_usd=0.0,
            cap_usd=cap_usd,
            scope="daily",
            reason="가드 비활성 (ENV)",
        )

    used = _sum_daily_cost()
    if used is None:
        return BudgetStatus(
            allowed=True,
            used_usd=0.0,
            cap_usd=cap_usd,
            scope="daily",
            reason="DB 조회 실패 — 가드 graceful (allowed)",
        )
    if used > cap_usd:
        return BudgetStatus(
            allowed=False,
            used_usd=used,
            cap_usd=cap_usd,
            scope="daily",
            reason=(
                f"일일 비용 한도 초과 "
                f"(${used:.4f} > ${cap_usd:.4f}) — vision 보강 일부 생략"
            ),
        )
    return BudgetStatus(
        allowed=True,
        used_usd=used,
        cap_usd=cap_usd,
        scope="daily",
        reason="일일 한도 내",
    )


def check_combined(
    *,
    doc_id: str,
    doc_cap_usd: float,
    daily_cap_usd: float,
) -> BudgetStatus:
    """doc + daily 동시 검사 — 어느 한 쪽이라도 초과하면 allowed=False.

    우선순위: doc 검사 먼저 (doc_id 가 있을 때만). 둘 다 통과해야 allowed=True.
    호출자는 단 1회 호출로 인제스트 분기 가능.
    """
    if is_disabled():
        return BudgetStatus(
            allowed=True,
            used_usd=0.0,
            cap_usd=doc_cap_usd,
            scope="doc",
            reason="가드 비활성 (ENV)",
        )

    if doc_id:
        doc_status = check_doc_budget(doc_id=doc_id, cap_usd=doc_cap_usd)
        if not doc_status.allowed:
            return doc_status

    daily_status = check_daily_budget(cap_usd=daily_cap_usd)
    if not daily_status.allowed:
        return daily_status

    # 둘 다 통과 — 가장 정보량 많은 쪽 (daily) 반환.
    return daily_status


# ----------------------- DB 헬퍼 -----------------------


def _sum_doc_cost(doc_id: str) -> float | None:
    """vision_usage_log 의 doc_id 단위 SUM(estimated_cost). 실패 시 None."""
    try:
        from app.db import get_supabase_client

        client = get_supabase_client()
        # 014 마이그 후 컬럼 모두 존재. estimated_cost NULL row 는 sum 에서 자동 제외.
        # supabase-py 는 SUM RPC 가 없어 SELECT estimated_cost 로 가져와 Python 집계.
        resp = (
            client.table("vision_usage_log")
            .select("estimated_cost,success")
            .eq("doc_id", doc_id)
            .eq("success", True)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 — DB 부재 / 014 미적용 graceful
        _warn_first(f"budget_guard doc SUM 실패 (graceful): {exc}")
        return None

    return _sum_cost_rows(resp.data or [])


def _sum_daily_cost() -> float | None:
    """오늘 UTC midnight ~ now 의 vision_usage_log SUM(estimated_cost). 실패 시 None."""
    try:
        from app.db import get_supabase_client

        client = get_supabase_client()
        midnight = _utc_midnight_iso()
        resp = (
            client.table("vision_usage_log")
            .select("estimated_cost,success")
            .gte("called_at", midnight)
            .eq("success", True)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 — DB 부재 graceful
        _warn_first(f"budget_guard daily SUM 실패 (graceful): {exc}")
        return None

    return _sum_cost_rows(resp.data or [])


def _sum_cost_rows(rows: list[dict]) -> float:
    """row 리스트 → estimated_cost 합 (None / 잘못된 값은 무시)."""
    total = 0.0
    for r in rows:
        cost = r.get("estimated_cost")
        if cost is None:
            continue
        try:
            total += float(cost)
        except (TypeError, ValueError):
            continue
    return total


def _utc_midnight_iso() -> str:
    """오늘 UTC 자정 (ISO8601). gte 필터에 사용."""
    now = datetime.now(timezone.utc)
    midnight = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)
    return midnight.isoformat()


def _warn_first(msg: str) -> None:
    """첫 1회만 warn — 마이그 014 미적용 환경 노이즈 방지."""
    global _first_warn_logged
    if not _first_warn_logged:
        _first_warn_logged = True
        logger.warning(
            "%s — 마이그 014(vision_usage_log_enhanced) 적용 후 자동 회복.", msg
        )
    else:
        logger.debug(msg)


def _reset_first_warn_for_test() -> None:
    """단위 테스트용 — 첫 warn flag 초기화."""
    global _first_warn_logged
    _first_warn_logged = False
