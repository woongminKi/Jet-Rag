"""FastAPI auth dependency (D1, plan §1·§3 / 수익화 W1 3-way 분기).

핵심 — 데모 병행 모드 (수익화 W1):
- `auth_enabled=false` (로컬 dev): default_user_id fallback + is_authenticated=True.
  프론트가 토큰을 안 보내도 기존 단일-유저 개발 동작 100% 보존.
- `auth_enabled=true` + 토큰 없음: owner_user_id(없으면 default_user_id) fallback +
  is_authenticated=False. 익명 데모 방문자 — owner 문서 read-only 시연 허용.
  쓰기는 후속 태스크의 require_authenticated_user 가 is_authenticated=False 로 차단.
- `auth_enabled=true` + 유효 JWT: 본인 user_id + is_authenticated=True.
  완전 격리 컨텍스트 — 로그인 사용자는 자기 문서만 접근.
- `auth_enabled=true` + 무효 JWT: 401. 조용한 데모 강등 금지 (잘못된 토큰 = 명시적 실패).

제공:
- `get_current_user`: 호출자 user_id 를 주입하는 dependency. 핸들러가 user_id 값이 필요하면
  시그니처에 `user: CurrentUser = Depends(get_current_user)` 추가.
- `require_auth`: router-level `dependencies=[Depends(require_auth)]` 게이트.
  반환값 불필요·인증만 강제하는 라우트용 (get_current_user 의 얇은 래퍼).
- `require_admin`: admin 라우트 게이트 (D1-Q7). OWNER_USER_ID 와 호출자 비교 → 403.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.auth.cookie_token import derive_project_ref, extract_access_token
from app.auth.jwt_verify import JWTValidationError, verify_jwt
from app.config import Settings, get_settings

_BEARER_PREFIX = "Bearer "

# 기존 single-user MVP 의 default_user_id 리터럴 (config.get_settings 의 default 와 동일).
# 핸들러 직접 호출(단위 테스트) 시 Annotated dependency 의 fallback default 로 쓰여
# 기존 무인증 테스트가 default_user_id 컨텍스트를 그대로 유지하게 한다 (회귀 0).
# 실 요청 경로에서는 FastAPI 가 get_current_user 를 실행하므로 이 default 는 무시된다.
_LEGACY_DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001"


@dataclass(frozen=True)
class CurrentUser:
    """요청 호출자. user_id 는 격리 키 (RPC user_id_arg / documents.user_id 필터).

    - is_authenticated=False: 익명 데모 방문자 (owner read-only fallback) — 쓰기 불가.
    - is_authenticated=True: JWT 검증 통과 또는 auth_enabled=false 로컬 dev.
    """

    user_id: str
    email: str | None = None
    is_authenticated: bool = True


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
    """호출자 식별 — 데모 병행 3-way 분기 (수익화 W1).

    - auth_enabled=false: 로컬 dev / single-user — default_user 로 쓰기 포함 전체 허용.
    - 토큰 없음: 익명 데모 — owner 문서 read-only (쓰기는 require_authenticated_user 가 차단).
    - 토큰 있음: JWT 검증 → 본인 격리 컨텍스트. 무효 토큰은 401 (조용한 데모 강등 금지).
    """
    if not settings.auth_enabled:
        # 로컬 dev 무중단 — 기존 단일-유저 동작 보존. is_authenticated=True (쓰기 허용).
        return CurrentUser(user_id=settings.default_user_id, email=None)

    token = _extract_bearer_token(request) or _extract_cookie_token(request, settings)
    if token is None:
        # 익명 데모 방문자 — owner 문서 read-only. 쓰기 게이트는 후속 태스크에서 차단.
        return CurrentUser(
            user_id=settings.owner_user_id or settings.default_user_id,
            email=None,
            is_authenticated=False,
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


def forbid_demo_writes(
    settings: Settings = Depends(get_settings),
) -> None:
    """PORTFOLIO MODE C+ — 데모 readonly 가드.

    `JETRAG_DEMO_READONLY=true` 일 때 업로드/이관/feedback/eval 등 모든 write
    엔드포인트에서 503. 채용 담당자 데모는 검색·답변 GET 만 허용 → 데이터 오염
    + LLM 비용 burn 차단.

    복원 시 본 함수 + 라우터 7곳의 Depends(forbid_demo_writes) 일괄 주석.
    """
    if getattr(settings, "demo_readonly", False):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="포트폴리오 데모 모드 — 업로드/쓰기 작업이 일시 비활성입니다.",
        )


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
