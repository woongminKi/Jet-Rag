"""Supabase JWT 로컬 검증 (D1, plan §1·§11).

설계 (D1-Q1/Q2 결정):
- 로컬 HS256 검증 (PyJWT). 원격 검증 RTT 0 → KPI #10 (P95 2.5s) 영향 ~0ms.
- Supabase access token 은 `aud="authenticated"` + `sub`(user UUID) + `exp` 를 담는다.
- **알고리즘/키 소스 ENV 분기** (D1-Q1 잔여 대응): Supabase 프로젝트가 대칭(HS256 shared
  secret) 인지 비대칭(ECC JWKS) 인지 dashboard 확인이 필요. 현재는 HS256 경로만 동작하되,
  비대칭 전환 시 `_resolve_signing_key` 에 JWKS fetch 분기만 추가하면 검증 흐름은 동일.

검증 항목:
- 서명 (secret 기반 HS256)
- `exp` 만료 (PyJWT 가 자동 — leeway 0)
- `aud == "authenticated"` (Supabase 발급 토큰 고정값)

실패는 전부 `JWTValidationError` 로 단일화 — dependency 가 401 로 변환 (한국어 detail).
"""

from __future__ import annotations

from dataclasses import dataclass

import jwt

from app.config import Settings

# Supabase access token 의 고정 audience claim. 로그인 토큰은 항상 이 값.
_SUPABASE_AUDIENCE = "authenticated"

# 본 모듈이 로컬 검증을 지원하는 대칭 알고리즘 화이트리스트.
# 비대칭(RS256/ES256)은 JWKS 키 소스 추가 시 _resolve_signing_key 에서 분기 (D1-Q1 잔여).
_SYMMETRIC_ALGORITHMS = frozenset({"HS256", "HS384", "HS512"})


class JWTValidationError(Exception):
    """JWT 검증 실패 단일 예외. dependency 가 401 한국어 메시지로 변환."""


@dataclass(frozen=True)
class VerifiedToken:
    """검증을 통과한 JWT 의 핵심 claim.

    - user_id: `sub` claim = Supabase user UUID (격리 키).
    - email: `email` claim (선택 — 토큰에 없을 수 있음).
    """

    user_id: str
    email: str | None


def _resolve_signing_key(settings: Settings) -> str:
    """검증 키 해석 — 현재는 HS256 shared secret 만.

    D1-Q1 잔여: 비대칭(JWKS) 전환 시 algorithm 이 RS256/ES256 이면 이 함수에서
    Supabase JWKS endpoint fetch → kid 매칭 공개키 반환 분기를 추가한다 (검증 호출부 무변경).
    """
    algorithm = settings.supabase_jwt_algorithm
    if algorithm not in _SYMMETRIC_ALGORITHMS:
        # 비대칭 알고리즘은 아직 미지원 — JWKS 키 소스 추가 전까지 운영 fail-fast.
        raise JWTValidationError(
            f"JWT 알고리즘 '{algorithm}' 은 아직 지원되지 않습니다 (HS256 만 가능)."
        )
    secret = settings.supabase_jwt_secret
    if not secret:
        # auth_enabled=true 인데 secret 미설정 = 운영 설정 오류. fail-fast 로 401.
        raise JWTValidationError("JWT secret 이 설정되지 않았습니다 (서버 설정 오류).")
    return secret


def verify_jwt(token: str, settings: Settings) -> VerifiedToken:
    """JWT 문자열을 검증하고 `VerifiedToken` 반환. 실패 시 `JWTValidationError`.

    서명·만료·audience 를 모두 검증한다. PyJWT 의 세분화된 예외를 단일 예외로 흡수.
    """
    if not token:
        raise JWTValidationError("토큰이 비어 있습니다.")

    signing_key = _resolve_signing_key(settings)
    try:
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=[settings.supabase_jwt_algorithm],
            audience=_SUPABASE_AUDIENCE,
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise JWTValidationError("토큰이 만료되었습니다.") from exc
    except jwt.InvalidAudienceError as exc:
        raise JWTValidationError("토큰 audience 가 유효하지 않습니다.") from exc
    except jwt.InvalidTokenError as exc:
        # 서명 불일치·형식 오류·필수 claim 누락 등 PyJWT 의 모든 검증 실패 흡수.
        raise JWTValidationError("토큰 검증에 실패했습니다.") from exc

    user_id = claims.get("sub")
    if not user_id or not isinstance(user_id, str):
        raise JWTValidationError("토큰에 사용자 식별자(sub)가 없습니다.")

    email = claims.get("email")
    if email is not None and not isinstance(email, str):
        email = None

    return VerifiedToken(user_id=user_id, email=email)
