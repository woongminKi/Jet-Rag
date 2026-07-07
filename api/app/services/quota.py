"""quota 모듈 — Gemini API quota 초과 감지 + 수익화 W3 플랜/사용량 조회.

W9 Day 4·6·7: is_quota_exhausted — Gemini API 초과 감지 유틸리티.
  - class name 화이트리스트 + status code attribute + 메시지 fallback 3단계.
  - stdlib only, google SDK 직접 import 없이 type name 검사로 우회.

W3 수익화: plans/subscriptions(마이그 022) 를 읽어 유효 플랜 한도 산출 (read-only).
  - enforcement 는 rate_limit.py 통합 게이트(Task 4) 담당.
  - 어떤 DB 실패든 None 반환 (fail-open — 호출측이 quota skip + warning).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from app.db import get_supabase_client

logger = logging.getLogger(__name__)

# 알려진 quota 초과 exception class name 화이트리스트.
# - ResourceExhausted: google.api_core.exceptions (gRPC 표준)
# - ClientError: google.genai.errors (HTTP wrapper) — code 검사 필요
_QUOTA_EXCEPTION_NAMES: frozenset[str] = frozenset({
    "ResourceExhausted",
    "TooManyRequests",
})


def is_quota_exhausted(error_or_msg) -> bool:
    """quota 초과 케이스 감지 — Exception object 또는 str message 모두 수용.

    감지 우선순위 (W9 Day 7 — 한계 #50 회수)
    1. exception class name 화이트리스트 — google SDK 의 표준 type
    2. exception 의 status_code/code attribute == 429
    3. 메시지 휴리스틱 (RESOURCE_EXHAUSTED / 429 / QUOTA) — fallback

    1·2 는 SDK 응답 형식과 무관하게 정확. 3 은 문자열 변경 시 회귀 가능하나,
    1·2 가 false negative 일 때 안전망 역할.
    """
    if isinstance(error_or_msg, BaseException):
        # 1) class name 직접 검사 — import 없이도 google SDK 표준 type 인식
        if type(error_or_msg).__name__ in _QUOTA_EXCEPTION_NAMES:
            return True
        # 2) HTTP-style status code attribute (google.genai.errors.ClientError 등)
        for attr in ("status_code", "code"):
            value = getattr(error_or_msg, attr, None)
            if value == 429:
                return True
        # 3) fallback — 메시지 검사
        msg = str(error_or_msg)
    else:
        msg = error_or_msg

    if not msg:
        return False
    upper = msg.upper()
    return (
        "RESOURCE_EXHAUSTED" in upper
        or "429" in msg
        or "QUOTA" in upper
    )


# ---------------------------------------------------------------------------
# W3 수익화 — 플랜(Free/Pro) 해석 + 사용량 카운트 조회
# ---------------------------------------------------------------------------

_EFFECTIVE_STATUSES = ("active", "past_due")


@dataclass(frozen=True)
class PlanLimits:
    code: str
    max_documents: int
    answers_per_day: int


@dataclass(frozen=True)
class SubscriptionView:
    plan_code: str
    status: str  # active | past_due | canceled | none(행 없음)
    current_period_end: str | None


def get_subscription_view(user_id: str) -> SubscriptionView:
    """구독 표시용 (/me/subscription). 행 없음/실패 → free·none (fail-open)."""
    try:
        rows = (
            get_supabase_client()
            .table("subscriptions")
            .select("plan_code, status, current_period_end")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
            .data
        ) or []
        if not rows:
            return SubscriptionView(plan_code="free", status="none", current_period_end=None)
        r = rows[0]
        return SubscriptionView(
            plan_code=r.get("plan_code", "free"),
            status=r.get("status", "none"),
            current_period_end=r.get("current_period_end"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("구독 조회 실패 (user=%s): %s", user_id, exc)
        return SubscriptionView(plan_code="free", status="none", current_period_end=None)


def get_effective_plan(user_id: str) -> PlanLimits | None:
    """유저의 유효 플랜 한도. 실패 시 None (fail-open).

    정책
    - 구독 행 없음 / status='canceled' → free.
    - status IN ('active', 'past_due') → 해당 plan_code (past_due = grace, W5-6 예약).
    - 어떤 DB 실패든 None 반환 (fail-open — 호출측이 quota skip + warning).
    """
    try:
        client = get_supabase_client()
        sub_rows = (
            client.table("subscriptions")
            .select("plan_code, status")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
            .data
        ) or []
        code = "free"
        if sub_rows and sub_rows[0].get("status") in _EFFECTIVE_STATUSES:
            code = sub_rows[0]["plan_code"]

        plan_rows = (
            client.table("plans")
            .select("code, max_documents, answers_per_day")
            .eq("code", code)
            .limit(1)
            .execute()
            .data
        ) or []
        if not plan_rows:
            logger.warning("plans 테이블에 code=%s 없음 — quota fail-open", code)
            return None
        row = plan_rows[0]
        return PlanLimits(
            code=row["code"],
            max_documents=int(row["max_documents"]),
            answers_per_day=int(row["answers_per_day"]),
        )
    except Exception as exc:  # noqa: BLE001 — 조회 실패는 fail-open
        logger.warning("플랜 조회 실패 — quota fail-open (user=%s): %s", user_id, exc)
        return None


def count_active_documents(user_id: str) -> int | None:
    """보유 문서 수 (deleted_at IS NULL). 실패 시 None (fail-open)."""
    try:
        # limit(1) = payload 최소화. count="exact" 는 limit 과 무관하게 전체 건수를 resp.count 로 반환.
        resp = (
            get_supabase_client()
            .table("documents")
            .select("id", count="exact")
            .eq("user_id", user_id)
            .is_("deleted_at", "null")
            .limit(1)
            .execute()
        )
        return int(resp.count or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("문서 수 카운트 실패 — quota fail-open (user=%s): %s", user_id, exc)
        return None


def get_todays_count(user_key: str, metric: str) -> int:
    """usage_counters 의 금일 카운트 (표시용 — /me/plan). 실패 시 0.

    enforcement 에 사용 금지 — 실패 시 0 이라 장애가 '사용량 없음' 으로 보인다.
    """
    try:
        today = datetime.now(timezone.utc).date().isoformat()
        rows = (
            get_supabase_client()
            .table("usage_counters")
            .select("count")
            .eq("user_key", user_key)
            .eq("metric", metric)
            .eq("period_date", today)
            .limit(1)
            .execute()
            .data
        ) or []
        return int(rows[0]["count"]) if rows else 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("금일 사용량 조회 실패 — 0 반환 (key=%s): %s", user_key, exc)
        return 0
