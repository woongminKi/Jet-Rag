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
from app.services import quota

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
