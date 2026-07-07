# api/app/routers/payments.py
"""수익화 W5-6 — 카카오페이 정기결제 라우터.

- /payments/subscribe/* : 로그인 유저 구독 등록·승인·해지 (require_authenticated_user 게이트).
- /billing/run          : 배치 진입점(외부 cron fallback). shared secret gate — admin 라우터에
                          두지 않는 이유는 require_admin 이 owner JWT 없는 cron 호출자를 403 하기 때문.
                          주 경로는 scripts/billing_charge.py (Railway cron). 이 endpoint 는 수동/외부 트리거.
"""
from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel

from app.auth import CurrentUserDep, LEGACY_DEFAULT_USER, require_authenticated_user
from app.config import Settings, get_settings
from app.services import billing

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/payments",
    tags=["payments"],
    dependencies=[Depends(require_authenticated_user)],
)

# cron endpoint — 별도 라우터(인증 dependency 없음, secret gate 로만 보호).
cron_router = APIRouter(prefix="/billing", tags=["billing-cron"])


class ReadyResponse(BaseModel):
    redirect_url: str


class StatusResponse(BaseModel):
    status: str


class BillingRunResponse(BaseModel):
    charged: int
    failed: int
    canceled: int


def _ensure_enabled(settings: Settings) -> None:
    if not settings.kakaopay_secret_key or not settings.billing_key_encryption_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="결제 기능이 비활성 상태입니다. 잠시 후 다시 시도해 주세요.",
        )


@router.post("/subscribe/ready", response_model=ReadyResponse)
def subscribe_ready(
    current_user: CurrentUserDep = LEGACY_DEFAULT_USER,
    settings: Settings = Depends(get_settings),
) -> ReadyResponse:
    """결제창 준비 — redirect_url 로 프론트가 사용자를 KakaoPay 결제창으로 보낸다."""
    _ensure_enabled(settings)
    try:
        result = billing.start_subscription(current_user.user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("subscribe ready 실패 (user=%s): %s", current_user.user_id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="결제창 생성에 실패했습니다. 잠시 후 다시 시도해 주세요.",
        ) from exc
    return ReadyResponse(redirect_url=result.redirect_url)


@router.post("/subscribe/approve", response_model=StatusResponse)
def subscribe_approve(
    pg_token: str = Query(..., min_length=1),
    current_user: CurrentUserDep = LEGACY_DEFAULT_USER,
    settings: Settings = Depends(get_settings),
) -> StatusResponse:
    """결제 승인 — KakaoPay 가 approval_url 로 redirect 하며 append 한 pg_token 으로 승인."""
    _ensure_enabled(settings)
    try:
        billing.approve_subscription(current_user.user_id, pg_token)
    except billing.SubscriptionNotPendingError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="진행 중인 결제 요청이 없습니다. 다시 시도해 주세요.",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.warning("subscribe approve 실패 (user=%s): %s", current_user.user_id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="결제 승인에 실패했습니다. 다시 시도해 주세요.",
        ) from exc
    return StatusResponse(status="active")


@router.post("/subscribe/cancel", response_model=StatusResponse)
def subscribe_cancel(
    current_user: CurrentUserDep = LEGACY_DEFAULT_USER,
    settings: Settings = Depends(get_settings),
) -> StatusResponse:
    """구독 해지 — 즉시 Free 강등(데이터 보존). KakaoPay SID inactive."""
    _ensure_enabled(settings)
    try:
        billing.cancel_subscription(current_user.user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("subscribe cancel 실패 (user=%s): %s", current_user.user_id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="구독 해지에 실패했습니다. 잠시 후 다시 시도해 주세요.",
        ) from exc
    return StatusResponse(status="canceled")


@cron_router.post("/run", response_model=BillingRunResponse)
def billing_run(
    x_billing_cron_secret: str = Header(default=""),
    settings: Settings = Depends(get_settings),
) -> BillingRunResponse:
    """배치 진입점 — 만료 자동결제 + 7일 grace sweep. shared secret gate."""
    if not settings.billing_cron_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="billing cron 이 비활성 상태입니다 (JETRAG_BILLING_CRON_SECRET 미설정).",
        )
    if not hmac.compare_digest(x_billing_cron_secret, settings.billing_cron_secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="cron secret 불일치"
        )
    _ensure_enabled(settings)
    charge = billing.charge_due_subscriptions()
    sweep = billing.sweep_past_due()
    return BillingRunResponse(
        charged=charge.charged, failed=charge.failed, canceled=sweep.canceled
    )
