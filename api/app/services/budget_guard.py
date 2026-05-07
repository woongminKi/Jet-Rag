"""S0 D4~D5 + S2 D2 (2026-05-07~08) — vision 비용/페이지 cap 가드.

master plan §6 S0 D4 + D5 + S2 D2 + §7.4 정합:

    if doc_cost      > doc_budget_usd      → BudgetStatus(allowed=False, scope='doc')
    if daily_cost    > daily_budget_usd    → BudgetStatus(allowed=False, scope='daily')
    if sliding_24h   > 24h_budget_usd      → BudgetStatus(allowed=False, scope='24h_sliding')
    if called_pages >= page_cap_per_doc    → BudgetStatus(allowed=False, scope='page_cap')  # S2 D2
    else                                    → BudgetStatus(allowed=True)

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

스코프 정의
    - doc: 단일 doc 의 vision_usage_log 누적 (success=true 만).
    - daily: 오늘 (UTC midnight ~ now) 의 vision_usage_log 누적 (success=true 만).
      calendar day 리셋 — 사용자에게 "오늘/내일" 직관적.
    - 24h_sliding (D5): now() - 24h ~ now() 의 누적. calendar day 리셋과
      독립적으로 24시간 rolling window 강제 — 자정 직전 폭주 후 자정 직후
      재폭주 가능한 calendar-day 한계 보완.
    - page_cap (S2 D2): 단일 doc 안 vision API 호출 페이지 누적 카운터.
      DB SUM 불필요 — 호출 측의 in-memory 카운터를 인자로 받음 (latency 0).
      cost cap (3중) 과 직교 — 페이지 수 자체를 한도로 보호 (50p PDF · 100p 대형
      PDF 의 latency / RPM cap). 둘 중 먼저 닿는 지점에서 stop.

24h sliding 의 의미 (D5)
    daily 가 calendar 기준이면 23:59 에 daily cap 도달했어도 00:00 에 reset.
    sliding 은 "최근 24시간" 단위라 폭주 직후에는 다음 폭주를 24시간 차단.
    두 가드가 동시 적용되어도 어느 한쪽만 fail 해도 차단되므로 보수적 안전망.

page cap 의 의미 (S2 D2)
    cost cap (S0 D4) 은 비용 누적 한도라 정상 동작 흐름에서도 50~100 페이지
    PDF 1건 인제스트 시 cap 미도달 가능. page cap 은 "한 doc 당 vision call
    페이지 수" 자체를 한도로 두어 (default 50) latency / RPM 폭주를 직교
    방어. needs_vision False 페이지 (S2 D1) 는 카운터 증가 X — 사용자 가치
    있는 페이지만 cap 에 차감 (cap 도달 지연 정합).

반환값 일관성
    - allowed=True : 인제스트는 vision 호출 진행
    - allowed=False : vision 호출 skip + flags 마킹
    - reason 은 한국어 — 사용자 노출 가능한 톤 (UI 가 그대로 표시 가능)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Literal

logger = logging.getLogger(__name__)

# ENV — 운영 회복 / 디버깅용 비활성 토글. master plan §7.4 default 채택 ("1" = ON).
# "1" / "0" 만 인식. 이외 값은 default ON (보수적).
_DISABLE_ENV_KEY = "JETRAG_BUDGET_GUARD_DISABLE"

# 첫 1회만 warn (이후 debug) — vision_cache / vision_metrics 패턴.
_first_warn_logged: bool = False

# scope literal — JSON flags 에 그대로 저장됨.
# S2 D2 — `page_cap` 추가 (in-memory 카운터, cost cap 과 직교).
BudgetScope = Literal["doc", "daily", "24h_sliding", "page_cap"]

# D5 — 24h sliding window 길이. 외부 ENV 로 변경 X (의미가 24시간으로 고정).
_SLIDING_WINDOW_HOURS = 24


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


def check_24h_sliding_budget(
    *,
    cap_usd: float,
    now: datetime | None = None,
) -> BudgetStatus:
    """현재 시각 기준 -24h 누적 vision 비용 검사 (D5 sliding window).

    daily (calendar-day) 와 보완 관계:
        - daily : 오늘 자정~now 누적. 자정에 reset.
        - sliding: now-24h~now 누적. 자정 무관 rolling.

    DB 부재 / 마이그 014 미적용 / SUM 실패 시 graceful — allowed=True.

    인자 `now` 는 단위 테스트 결정성을 위한 주입점. None 이면 datetime.now(UTC).
    """
    if is_disabled():
        return BudgetStatus(
            allowed=True,
            used_usd=0.0,
            cap_usd=cap_usd,
            scope="24h_sliding",
            reason="가드 비활성 (ENV)",
        )

    used = _sum_24h_sliding_cost(now=now)
    if used is None:
        return BudgetStatus(
            allowed=True,
            used_usd=0.0,
            cap_usd=cap_usd,
            scope="24h_sliding",
            reason="DB 조회 실패 — 가드 graceful (allowed)",
        )
    if used > cap_usd:
        return BudgetStatus(
            allowed=False,
            used_usd=used,
            cap_usd=cap_usd,
            scope="24h_sliding",
            reason=(
                f"최근 24시간 비용 한도 초과 "
                f"(${used:.4f} > ${cap_usd:.4f}) — vision 보강 일부 생략"
            ),
        )
    return BudgetStatus(
        allowed=True,
        used_usd=used,
        cap_usd=cap_usd,
        scope="24h_sliding",
        reason="24시간 한도 내",
    )


def check_combined(
    *,
    doc_id: str,
    doc_cap_usd: float,
    daily_cap_usd: float,
    sliding_24h_cap_usd: float | None = None,
) -> BudgetStatus:
    """doc + daily + 24h sliding 동시 검사 — 어느 한 쪽이라도 초과하면 allowed=False.

    우선순위 (가장 좁은 범위 → 넓은 범위): doc → daily → 24h_sliding.
    셋 다 통과해야 allowed=True. 호출자는 단 1회 호출로 인제스트 분기 가능.

    `sliding_24h_cap_usd` 는 D5 신규 옵션. None 이면 sliding 검사 skip
    (하위 호환 — D4 ship 시 호출자는 daily 만 알았음). 호출자는 settings 에서
    값을 읽어 함께 전달 권고.
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

    if sliding_24h_cap_usd is not None:
        sliding_status = check_24h_sliding_budget(cap_usd=sliding_24h_cap_usd)
        if not sliding_status.allowed:
            return sliding_status
        # 가장 최근 검사 결과 (sliding) 가 정보량 최대 — 반환.
        return sliding_status

    # sliding 인자 없으면 daily 반환 (D4 호환).
    return daily_status


def check_doc_page_cap(
    *,
    called_pages: int,
    page_cap: int,
) -> BudgetStatus:
    """S2 D2 — 단일 doc 안 vision call 페이지 누적이 page_cap 도달했는지.

    DB 미접근 — 호출 측의 in-memory 카운터를 인자로 받아 즉시 비교 (latency 0).
    cost cap (S0 D4 의 doc/daily/24h_sliding) 과 직교. 호출자는 cost cap 검사
    직후에 본 함수를 호출해 두 cap 중 먼저 닿는 지점에서 stop.

    인자
        called_pages: 현재까지 vision call 한 페이지 수 (sweep 간 누적,
            needs_vision skip 페이지는 포함 X — 사용자 가치 페이지만 차감).
        page_cap: ENV `JETRAG_VISION_PAGE_CAP_PER_DOC` (default 50).
            0 이하면 무한 (회복 토글) — 항상 allowed=True.

    반환
        allowed=True : called_pages < page_cap (호출 진행)
        allowed=False: called_pages >= page_cap (이번 호출 부터 차단)
    """
    # ENV 비활성 토글 — cost cap 과 동일 정책.
    if is_disabled():
        return BudgetStatus(
            allowed=True,
            used_usd=0.0,
            cap_usd=float(page_cap),
            scope="page_cap",
            reason="가드 비활성 (ENV)",
        )
    # 무한 모드 (0 또는 음수) — S1.5 이전 동작 100% 보존.
    if page_cap <= 0:
        return BudgetStatus(
            allowed=True,
            used_usd=float(called_pages),
            cap_usd=0.0,
            scope="page_cap",
            reason="페이지 cap 무한 (ENV 0)",
        )
    if called_pages >= page_cap:
        return BudgetStatus(
            allowed=False,
            used_usd=float(called_pages),
            cap_usd=float(page_cap),
            scope="page_cap",
            reason=(
                f"문서당 vision 페이지 한도 도달 "
                f"({called_pages}/{page_cap}) — vision 보강 일부 생략"
            ),
        )
    return BudgetStatus(
        allowed=True,
        used_usd=float(called_pages),
        cap_usd=float(page_cap),
        scope="page_cap",
        reason="페이지 한도 내",
    )


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


def _sum_24h_sliding_cost(now: datetime | None = None) -> float | None:
    """now - 24h ~ now 의 vision_usage_log SUM(estimated_cost). 실패 시 None.

    인덱스: 014 마이그의 `idx_vision_usage_created (called_at)` 활용.
    """
    try:
        from app.db import get_supabase_client

        client = get_supabase_client()
        cutoff = _sliding_cutoff_iso(now=now)
        resp = (
            client.table("vision_usage_log")
            .select("estimated_cost,success")
            .gte("called_at", cutoff)
            .eq("success", True)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 — DB 부재 graceful
        _warn_first(f"budget_guard 24h_sliding SUM 실패 (graceful): {exc}")
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


def _sliding_cutoff_iso(now: datetime | None = None) -> str:
    """now - 24h (ISO8601). gte 필터에 사용. now=None 이면 datetime.now(UTC)."""
    base = now if now is not None else datetime.now(timezone.utc)
    cutoff = base - timedelta(hours=_SLIDING_WINDOW_HOURS)
    return cutoff.isoformat()


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
