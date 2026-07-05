"""D1 멀티유저 Auth 패키지 — JWT 검증 + FastAPI dependency.

- `jwt_verify`: Supabase JWT 로컬 검증 (HS256, PyJWT). 알고리즘/키 소스 ENV 분기.
- `dependencies`: `get_current_user` FastAPI dependency + `require_auth` router-level 게이트.

production 무중단 원칙 (plan §4): `auth_enabled=false` (default) 면 검증 skip 하고
`CurrentUser(user_id=settings.default_user_id)` fallback. ENV 1줄로 true 전환.
"""

from __future__ import annotations

from app.auth.dependencies import (
    _LEGACY_DEFAULT_USER_ID,
    CurrentUser,
    CurrentUserDep,
    get_current_user,
    require_admin,
    require_auth,
    require_authenticated_user,
)
from app.auth.jwt_verify import JWTValidationError, VerifiedToken, verify_jwt

# 핸들러 직접 호출(단위 테스트) 시 Annotated dependency 의 default 로 쓰는 fallback user.
# 모듈 로드 시점 1회 생성 (frozen dataclass — 안전 공유).
LEGACY_DEFAULT_USER = CurrentUser(user_id=_LEGACY_DEFAULT_USER_ID)

__all__ = [
    "CurrentUser",
    "CurrentUserDep",
    "LEGACY_DEFAULT_USER",
    "get_current_user",
    "require_auth",
    "require_admin",
    "require_authenticated_user",
    "JWTValidationError",
    "VerifiedToken",
    "verify_jwt",
]
