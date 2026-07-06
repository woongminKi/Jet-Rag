"""수익화 W2 — per-user 일일 rate limit.

익명 데모(ip 키)·로그인 사용자(user_id 키) 모두에 일일 상한을 걸어
Gemini 유료 키 전환 후 비용 폭주·남용을 방어한다. 카운터는 DB(usage_counters,
마이그 021) 에 원자적으로 증가 — 재시작·다중 워커 안전. W3-4 미터링이 재사용.

정책
- auth_enabled=false(로컬 dev): 전면 skip — 기존 동작·테스트 보존.
- cap<=0: 무제한 (회복 토글).
- increment-then-check: RPC 가 +1 후 새 count 반환, count>cap 이면 429.
- fail-open: RPC/DB 실패 시 통과(로그 warning). DB blip 으로 정상 사용자 차단 회피.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable

from fastapi import Depends, HTTPException, Request, status

from app.auth.dependencies import CurrentUser, get_current_user
from app.config import Settings, get_settings
from app.db import get_supabase_client

logger = logging.getLogger(__name__)

_METRIC_ANSWERS = "answers"
_METRIC_DOCS = "docs"


def _client_ip(request: Request) -> str:
    """프록시(Railway) 뒤 실제 클라이언트 IP. X-Forwarded-For 첫 항목 우선.

    getattr 방어 — 단위 테스트의 fake request 처럼 .client 없는 객체도 graceful.
    """
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    client = getattr(request, "client", None)
    return getattr(client, "host", None) or "unknown"


def build_user_key(current_user: CurrentUser, request: Request) -> str:
    """rate limit 카운터 키. 로그인=user_id / 익명 데모=ip:<주소>.

    익명 데모는 전부 owner_user_id 를 공유하므로 user_id 로 세면 OWNER 본인과
    뭉친다 — IP 로 분리해 익명 남용만 격리 카운트한다.
    """
    if current_user.is_authenticated:
        return current_user.user_id
    return f"ip:{_client_ip(request)}"


def _cap_for_metric(metric: str, settings: Settings) -> int:
    if metric == _METRIC_ANSWERS:
        return settings.rate_limit_answers_per_day
    if metric == _METRIC_DOCS:
        return settings.rate_limit_docs_per_day
    return 0  # 알 수 없는 metric → 무제한 (fail-open)


def enforce_rate_limit(
    metric: str,
    request: Request,
    current_user: CurrentUser,
    settings: Settings,
) -> None:
    """metric 의 일일 카운터를 1 증가시키고 상한 초과 시 429.

    부수효과: usage_counters 카운터 +1 (auth_enabled=true & cap>0 일 때만).
    """
    if not settings.auth_enabled:
        return  # 로컬 dev — 기존 동작 보존.
    cap = _cap_for_metric(metric, settings)
    if cap <= 0:
        return  # 무제한 (회복 토글).

    user_key = build_user_key(current_user, request)
    period_date = datetime.now(timezone.utc).date().isoformat()
    try:
        resp = get_supabase_client().rpc(
            "increment_usage_counter",
            {
                "p_user_key": user_key,
                "p_metric": metric,
                "p_period_date": period_date,
            },
        ).execute()
        new_count = resp.data
    except Exception as exc:  # noqa: BLE001 — DB 실패는 fail-open
        logger.warning("rate_limit RPC 실패 — fail-open (metric=%s): %s", metric, exc)
        return

    if isinstance(new_count, int) and new_count > cap:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"일일 사용 한도({cap}회)를 초과했습니다. "
                "내일 다시 시도하시거나 Pro 로 업그레이드해 주세요."
            ),
        )


def check_rate_limit(metric: str) -> Callable[..., None]:
    """라우터 레벨 rate limit 게이트 팩토리.

    사용: `@router.get(..., dependencies=[Depends(check_rate_limit("answers"))])`
    """

    def _dependency(
        request: Request,
        current_user: CurrentUser = Depends(get_current_user),
        settings: Settings = Depends(get_settings),
    ) -> None:
        enforce_rate_limit(metric, request, current_user, settings)

    return _dependency
