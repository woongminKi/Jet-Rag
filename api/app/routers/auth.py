"""auth 영역 라우터 — 호출자 식별 + Realtime 용 토큰 forward.

GET /auth/me — 호출자 인증 상태 + Realtime 구독용 access_token.

설계:
- auth_enabled=false: 항상 authorized=true (single-user MVP).
- auth_enabled=true: get_current_user 가 JWT 를 검증해 호출자 식별. 인증된 모든 user 는
  authorized=true. (초대 코드 게이트는 W31 follow-up 에서 제거 — 공개 가입 정책)
- access_token: get_current_user 가 검증한 토큰(Authorization Bearer 또는 Supabase auth
  쿠키)을 그대로 forward. 프론트 Supabase Realtime 클라이언트가 setAuth(token) 으로
  주입해 publication 구독 시 RLS 통과 (D2, plan §5).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.auth import CurrentUser, get_current_user
from app.auth.cookie_token import derive_project_ref, extract_access_token
from app.config import Settings, get_settings

router = APIRouter(prefix="/auth", tags=["auth"])


class AuthMeResponse(BaseModel):
    """GET /auth/me — 호출자 인증 상태 + Realtime 용 access_token (D2, plan §5).

    Supabase Realtime 클라이언트가 `supabase.realtime.setAuth(token)` 로 JWT 주입한 뒤
    publication 구독해야 D2 RLS 정책 (ingest_jobs SELECT JOIN documents) 통과.
    auth_enabled=false / token 추출 실패 시 access_token=None.
    """

    authorized: bool
    user_id: str
    email: str | None = None
    access_token: str | None = None


@router.get("/me", response_model=AuthMeResponse)
def auth_me(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> AuthMeResponse:
    """호출자 식별 + Realtime 용 토큰 forward.

    authorized 정의:
    - auth_enabled=false: 항상 true (single-user MVP).
    - auth_enabled=true: get_current_user 가 통과시킨 모든 인증된 user 는 true.
    """
    if not settings.auth_enabled:
        return AuthMeResponse(
            authorized=True,
            user_id=current_user.user_id,
            email=current_user.email,
            access_token=None,
        )

    access_token = _extract_request_token(request, settings)

    return AuthMeResponse(
        authorized=True,
        user_id=current_user.user_id,
        email=current_user.email,
        access_token=access_token,
    )


def _extract_request_token(request: Request, settings: Settings) -> str | None:
    """현 요청의 access_token 추출 (D2 — plan §5).

    우선순위: Authorization Bearer → Supabase auth 쿠키. get_current_user 와 동일한
    순서로 정확한 동일 토큰 반환을 보장한다. 어떤 실패도 None — 호출부가 graceful 분기.
    """
    header = request.headers.get("Authorization")
    if header and header.startswith("Bearer "):
        token = header[len("Bearer ") :].strip()
        if token:
            return token

    cookies = getattr(request, "cookies", None)
    if not cookies:
        return None
    project_ref = derive_project_ref(settings.supabase_url)
    if not project_ref:
        return None
    return extract_access_token(dict(cookies), project_ref)
