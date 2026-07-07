"""수익화 W3 — 로그인 사용자 본인 플랜·사용량 조회.

프론트가 402 업그레이드 안내·사용량 표시에 사용. W4 이메일 인제스트
설정(/me/email-ingest)이 이 라우터에 추가된다.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth import (
    LEGACY_DEFAULT_USER,
    CurrentUserDep,
    require_authenticated_user,
)
from app.config import Settings, get_settings
from app.services import email_ingest, quota

router = APIRouter(
    prefix="/me",
    tags=["me"],
    dependencies=[Depends(require_authenticated_user)],
)


class MePlanResponse(BaseModel):
    plan_code: str
    max_documents: int
    answers_per_day: int
    answers_used_today: int
    documents_count: int


@router.get("/plan", response_model=MePlanResponse)
def me_plan(current_user: CurrentUserDep = LEGACY_DEFAULT_USER) -> MePlanResponse:
    plan = quota.get_effective_plan(current_user.user_id)
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="플랜 정보를 불러올 수 없습니다. 잠시 후 다시 시도해 주세요.",
        )
    return MePlanResponse(
        plan_code=plan.code,
        max_documents=plan.max_documents,
        answers_per_day=plan.answers_per_day,
        answers_used_today=quota.get_todays_count(current_user.user_id, "answers"),
        documents_count=quota.count_active_documents(current_user.user_id) or 0,
    )


class EmailIngestAddressResponse(BaseModel):
    address: str
    pro: bool
    plan_code: str


def _address_response(row: dict, user_id: str, settings: Settings) -> EmailIngestAddressResponse:
    plan = quota.get_effective_plan(user_id)
    return EmailIngestAddressResponse(
        address=email_ingest.build_address(row["token"], settings.email_ingest_domain),
        pro=plan is not None and plan.code == "pro",
        plan_code=plan.code if plan is not None else "unknown",
    )


@router.get("/email-ingest", response_model=EmailIngestAddressResponse)
def me_email_ingest(
    current_user: CurrentUserDep = LEGACY_DEFAULT_USER,
    settings: Settings = Depends(get_settings),
) -> EmailIngestAddressResponse:
    """본인 이메일 인제스트 주소 (없으면 발급). 수신 처리 자체는 Pro 전용 —
    Free 유저에게도 주소는 보여주되 pro=false 로 업그레이드 안내를 띄운다."""
    row = email_ingest.get_or_create_address(current_user.user_id, current_user.email)
    return _address_response(row, current_user.user_id, settings)


@router.post("/email-ingest/rotate", response_model=EmailIngestAddressResponse)
def me_email_ingest_rotate(
    current_user: CurrentUserDep = LEGACY_DEFAULT_USER,
    settings: Settings = Depends(get_settings),
) -> EmailIngestAddressResponse:
    """토큰 재발급 — 스팸·유출 대응. 구 주소 즉시 무효."""
    row = email_ingest.rotate_address(current_user.user_id, current_user.email)
    return _address_response(row, current_user.user_id, settings)
