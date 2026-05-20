"""auth 영역 라우터 — D1 초대 코드 가입 게이트 (plan §5).

POST /auth/redeem-invite — 신규 user 가 가입 직후 초대 코드를 소진한다.
- get_current_user 로 신규 user 의 JWT 검증 (호출자 본인 UUID 확보).
- 검증: code 존재 AND used_by IS NULL AND (expires_at NULL OR expires_at > now()).
- 소진: 조건부 UPDATE (used_by IS NULL) → affected=0 이면 이미 소진 (race 방어).

graceful — invite_codes (마이그 017) 미적용 환경은 503 으로 안내 (가입 차단 방지보다
운영자 인지 우선). auth_enabled=false 면 get_current_user 가 default_user_id fallback —
로컬/CI 에서 endpoint 동작은 가능하나 실제 게이트는 production(auth_enabled=true)에서만 의미.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth import CurrentUser, get_current_user
from app.config import Settings, get_settings
from app.db import get_supabase_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# 초대 코드 길이 상한 — DB PK(TEXT) 무제한이나 입력 검증으로 비정상 페이로드 차단.
_MAX_CODE_LEN = 128


class RedeemInviteRequest(BaseModel):
    """POST /auth/redeem-invite 요청 — 초대 코드 1건."""

    code: str = Field(..., min_length=1, max_length=_MAX_CODE_LEN, description="초대 코드")


class RedeemInviteResponse(BaseModel):
    """초대 코드 소진 결과."""

    redeemed: bool
    code: str


class AuthMeResponse(BaseModel):
    """GET /auth/me — 호출자 인증/승인 상태 (OAuth 복귀 유저 게이트용, plan §1.1)."""

    authorized: bool
    user_id: str
    email: str | None = None


@router.get("/me", response_model=AuthMeResponse)
def auth_me(
    current_user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> AuthMeResponse:
    """호출자 식별 + 초대 승인 여부 (plan §1.1 OAuth 게이트).

    authorized 정의:
    - auth_enabled=false: 항상 true (single-user MVP — 게이트 없음).
    - auth_enabled=true: invite_codes 에 used_by=호출자 행이 1건 이상이면 true
      (초대 코드를 소진한 복귀 유저). 없으면 false (코드 미보유 신규).

    프론트 OAuth 콜백이 `jetrag-pending-invite` 쿠키가 없을 때(=신규 가입이 아닌
    재로그인) 이 endpoint 로 복귀 유저인지 판별한다. invite_codes 조회 실패는
    graceful — authorized=false 로 처리(차단 우선, 운영자 인지).
    """
    if not settings.auth_enabled:
        return AuthMeResponse(
            authorized=True,
            user_id=current_user.user_id,
            email=current_user.email,
        )

    authorized = False
    try:
        used = (
            get_supabase_client()
            .table("invite_codes")
            .select("code")
            .eq("used_by", current_user.user_id)
            .limit(1)
            .execute()
        )
        authorized = bool(used.data or [])
    except Exception:  # noqa: BLE001 — 마이그 017 미적용/DB 장애는 미승인 처리(차단 우선).
        logger.exception("invite_codes 승인 조회 실패 (user=%s)", current_user.user_id)
        authorized = False

    return AuthMeResponse(
        authorized=authorized,
        user_id=current_user.user_id,
        email=current_user.email,
    )


@router.post("/redeem-invite", response_model=RedeemInviteResponse)
def redeem_invite(
    payload: RedeemInviteRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> RedeemInviteResponse:
    """가입 직후 초대 코드 검증 + 소진 (plan §5).

    실패 시 4xx + 한국어 detail. 프론트는 실패 시 즉시 signOut + 안내 (D1-Q5).
    """
    code = payload.code.strip()
    if not code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="초대 코드가 비어 있습니다.",
        )

    client = get_supabase_client()

    # ---- 1. 검증 SELECT ----
    try:
        existing = (
            client.table("invite_codes")
            .select("code, used_by, expires_at")
            .eq("code", code)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 — 마이그 017 미적용 등 graceful.
        logger.exception("invite_codes 조회 실패 (마이그 017 미적용 가능)")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="초대 코드 검증을 일시적으로 사용할 수 없습니다.",
        ) from exc

    rows = existing.data or []
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="유효하지 않은 초대 코드입니다.",
        )

    row = rows[0]
    if row.get("used_by"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="이미 사용된 초대 코드입니다.",
        )
    if _is_expired(row.get("expires_at")):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="만료된 초대 코드입니다.",
        )

    # ---- 2. 소진 — 조건부 UPDATE (used_by IS NULL) 로 동시 소진 race 방어 ----
    # SELECT 와 UPDATE 사이 다른 가입자가 먼저 소진하면 affected=0 → 거부.
    used_at = datetime.now(timezone.utc).isoformat()
    try:
        updated = (
            client.table("invite_codes")
            .update({"used_by": current_user.user_id, "used_at": used_at})
            .eq("code", code)
            .is_("used_by", "null")
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("invite_codes 소진 UPDATE 실패 (code=%s)", code)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="초대 코드 처리를 일시적으로 사용할 수 없습니다.",
        ) from exc

    if not (updated.data or []):
        # SELECT 통과 후 UPDATE 가 0건 = 그 사이 다른 가입자가 소진.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="이미 사용된 초대 코드입니다.",
        )

    logger.info("초대 코드 소진 — code=%s user=%s", code, current_user.user_id)
    return RedeemInviteResponse(redeemed=True, code=code)


def _is_expired(expires_at: str | None) -> bool:
    """expires_at 만료 여부. None/파싱 실패 시 만료 아님 (graceful)."""
    if not expires_at:
        return False
    try:
        # PostgREST 는 ISO 8601 (오프셋 포함) 반환. 'Z' 는 fromisoformat 미지원이라 치환.
        parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed <= datetime.now(timezone.utc)
