"""FastAPI auth dependency (D1, plan §1·§3).

핵심 — production 무중단 (plan §4):
- `auth_enabled=false` (default): JWT 검증 skip → `CurrentUser(user_id=default_user_id)`
  fallback. 프론트가 토큰을 안 보내도 기존 단일-유저 동작 100% 보존.
- `auth_enabled=true`: `Authorization: Bearer <jwt>` 필수. 누락·검증 실패 → 401.

제공:
- `get_current_user`: 호출자 user_id 를 주입하는 dependency. 핸들러가 user_id 값이 필요하면
  시그니처에 `user: CurrentUser = Depends(get_current_user)` 추가.
- `require_auth`: router-level `dependencies=[Depends(require_auth)]` 게이트.
  반환값 불필요·인증만 강제하는 라우트용 (get_current_user 의 얇은 래퍼).
- `require_authorized_user`: invite redeem 게이트 (D2 follow-up, E4 fix).
  베타 30 cap 강제 — invite_codes 에 used_by=호출자 행이 없으면 403.
- `require_admin`: admin 라우트 게이트 (D1-Q7). OWNER_USER_ID 와 호출자 비교 → 403.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.auth.cookie_token import derive_project_ref, extract_access_token
from app.auth.jwt_verify import JWTValidationError, verify_jwt
from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

_BEARER_PREFIX = "Bearer "

# 기존 single-user MVP 의 default_user_id 리터럴 (config.get_settings 의 default 와 동일).
# 핸들러 직접 호출(단위 테스트) 시 Annotated dependency 의 fallback default 로 쓰여
# 기존 무인증 테스트가 default_user_id 컨텍스트를 그대로 유지하게 한다 (회귀 0).
# 실 요청 경로에서는 FastAPI 가 get_current_user 를 실행하므로 이 default 는 무시된다.
_LEGACY_DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001"


@dataclass(frozen=True)
class CurrentUser:
    """요청 호출자. user_id 는 격리 키 (RPC user_id_arg / documents.user_id 필터).

    - auth_enabled=false: user_id = settings.default_user_id, email = None.
    - auth_enabled=true: JWT `sub` / `email`.
    """

    user_id: str
    email: str | None = None


def _extract_bearer_token(request: Request) -> str | None:
    """`Authorization: Bearer <token>` 에서 토큰 추출. 없거나 형식 불일치면 None."""
    header = request.headers.get("Authorization")
    if not header or not header.startswith(_BEARER_PREFIX):
        return None
    token = header[len(_BEARER_PREFIX) :].strip()
    return token or None


def _extract_cookie_token(request: Request, settings: Settings) -> str | None:
    """Supabase auth 쿠키(`sb-<ref>-auth-token`)에서 access_token 추출 (plan §1.1).

    아키텍처 B — 브라우저 직접 데이터 콜이 Authorization 헤더 없이 httpOnly 쿠키만
    `credentials: 'include'` 로 보낼 때의 경로. 어떤 실패도 None (호출부가 401 변환).

    getattr 방어 — 단위 테스트의 _FakeRequest 처럼 `.cookies` 없는 객체도 graceful.
    """
    cookies = getattr(request, "cookies", None)
    if not cookies:
        return None
    project_ref = derive_project_ref(settings.supabase_url)
    if not project_ref:
        return None
    return extract_access_token(dict(cookies), project_ref)


def get_current_user(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> CurrentUser:
    """호출자 식별. auth_enabled=false 면 default_user_id fallback (무중단).

    토큰 소스 우선순위 (plan §1.1):
    1. `Authorization: Bearer <jwt>` — 서버 컴포넌트 forward / 명시 헤더.
    2. Supabase auth 쿠키 — 브라우저 직접 데이터 콜(`credentials: 'include'`).
    """
    if not settings.auth_enabled:
        # production 무중단 fallback — 기존 단일-유저 동작 보존.
        return CurrentUser(user_id=settings.default_user_id, email=None)

    token = _extract_bearer_token(request) or _extract_cookie_token(request, settings)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="인증이 필요합니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        verified = verify_jwt(token, settings)
    except JWTValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="인증이 필요합니다.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    return CurrentUser(user_id=verified.user_id, email=verified.email)


# 핸들러 시그니처용 dependency alias.
# - 실 요청: FastAPI 가 get_current_user 실행 → JWT 호출자 (또는 fallback).
# - 직접 호출(단위 테스트): default = CurrentUser(_LEGACY_DEFAULT_USER_ID) → 기존 무인증
#   동작 보존. 테스트가 다른 user 컨텍스트를 원하면 명시적으로 인자 전달.
CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


def require_auth(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """router-level 인증 게이트. get_current_user 가 이미 401 처리 — 값만 전달."""
    return current_user


def require_authorized_user(
    current_user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> CurrentUser:
    """invite redeem 게이트 — D2 follow-up E4 fix (베타 cap 강제).

    동작 정의:
    - auth_enabled=false: 통과 (single-user MVP 동작 100% 보존, DB 호출 0).
    - auth_enabled=true: `invite_codes` 에 `used_by=호출자` 행이 1건 이상이어야 통과.
        - 0건: 403 `초대 코드 redeem 이 필요합니다.` (Supabase signup 만 한 random user 차단)
        - DB 조회 예외(마이그 017 미적용/장애): 503 `초대 코드 시스템이 준비되지 않았습니다.`

    설계 결정:
    - 캐시 없이 매 request DB SELECT — invite_codes 1 row per user 라 가볍고 redeem 직후
      stale risk 0. TTL/invalidate 복잡도 회피.
    - OWNER 우회 path 불요 — OWNER 본인도 JETRAG-OWNER-001 redeem 했으니 자연 통과.
    - service_role client 로 SELECT (invite_codes RLS 정책상 service_role bypass).
    - 예외 패턴은 auth.py:90-105 `/auth/me` 와 동일 (graceful — 운영자 인지 우선).

    lazy import (app.db) — 단위 테스트가 supabase client 부재 환경에서도 dependencies
    모듈 import 가능하게 유지.
    """
    if not settings.auth_enabled:
        # single-user MVP 동작 보존 — DB 호출 없이 통과.
        return current_user

    from app.db import get_supabase_client

    try:
        used = (
            get_supabase_client()
            .table("invite_codes")
            .select("code")
            .eq("used_by", current_user.user_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 — 마이그 017 미적용/DB 장애는 graceful 503.
        logger.exception(
            "invite_codes 승인 게이트 조회 실패 (user=%s)", current_user.user_id
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="초대 코드 시스템이 준비되지 않았습니다.",
        ) from exc

    if not (used.data or []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="초대 코드 redeem 이 필요합니다.",
        )
    return current_user


def require_admin(
    current_user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> CurrentUser:
    """admin 라우트 게이트 (D1-Q7).

    동작 정의 (plan §3):
    - auth_enabled=false: 게이트 통과 (기존 single-user MVP 동작 보존).
    - auth_enabled=true + owner_user_id 미설정: 전면 403 (안전 — 운영자 미지정 시 차단).
    - auth_enabled=true + 호출자 != owner_user_id: 403.
    """
    if not settings.auth_enabled:
        return current_user

    if not settings.owner_user_id or current_user.user_id != settings.owner_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="운영자 권한이 필요합니다.",
        )
    return current_user
