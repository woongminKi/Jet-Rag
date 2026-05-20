"""Supabase JWT 로컬 검증 (D1, plan §1·§11).

설계 (D1-Q1/Q2 결정):
- 로컬 검증 (PyJWT). 원격 검증 RTT 0 → KPI #10 (P95 2.5s) 영향 ~0ms.
- Supabase access token 은 `aud="authenticated"` + `sub`(user UUID) + `exp` 를 담는다.
- **알고리즘 분기**:
    - 대칭(HS256/HS384/HS512): `SUPABASE_JWT_SECRET` shared secret 로 검증.
    - 비대칭(ES256/ES384/ES512/RS256/RS384/RS512): `SUPABASE_JWKS_URL` 의 공개키로 검증.
      PyJWKClient 가 process 단위 cache 를 갖고 token 의 `kid` 와 매칭되는 공개키를 fetch.
      `kid` 미스 시 자동으로 JWKS 재조회 (PyJWT 내장).

검증 항목:
- 서명 (대칭 secret 또는 JWKS 공개키)
- `exp` 만료 (PyJWT 가 자동 — leeway 0)
- `aud == "authenticated"` (Supabase 발급 토큰 고정값)

실패는 전부 `JWTValidationError` 로 단일화 — dependency 가 401 로 변환 (한국어 detail).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import jwt
from jwt import PyJWKClient

from app.config import Settings

# Supabase access token 의 고정 audience claim. 로그인 토큰은 항상 이 값.
_SUPABASE_AUDIENCE = "authenticated"

# 본 모듈이 로컬 검증을 지원하는 대칭 알고리즘 화이트리스트.
_SYMMETRIC_ALGORITHMS = frozenset({"HS256", "HS384", "HS512"})

# 비대칭 알고리즘 화이트리스트 — JWKS 공개키 경로로 검증.
# ES256 = Supabase ECC (P-256) 기본. RS256 은 일부 마이그레이션 프로젝트 호환용.
_ASYMMETRIC_ALGORITHMS = frozenset(
    {"ES256", "ES384", "ES512", "RS256", "RS384", "RS512"}
)


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


@lru_cache(maxsize=4)
def _get_jwks_client(jwks_url: str) -> PyJWKClient:
    """JWKS URL 별 PyJWKClient 싱글톤. process 수명 동안 1회 생성 + 재사용.

    PyJWKClient 가 자체 lifecycle 캐시(JWKS 응답)를 보유하므로 매 요청 GET 호출 X.
    kid 가 캐시에 없으면 PyJWKClient 가 자동으로 JWKS 를 재조회한다.
    """
    return PyJWKClient(jwks_url, cache_keys=True)


def _resolve_symmetric_key(settings: Settings) -> str:
    """대칭 알고리즘용 shared secret 해석. 미설정 시 fail-fast."""
    secret = settings.supabase_jwt_secret
    if not secret:
        # auth_enabled=true 인데 secret 미설정 = 운영 설정 오류. fail-fast 로 401.
        raise JWTValidationError("JWT secret 이 설정되지 않았습니다 (서버 설정 오류).")
    return secret


def _resolve_asymmetric_key(token: str, settings: Settings) -> Any:
    """비대칭 알고리즘용 공개키 해석 — JWKS endpoint 에서 token 의 `kid` 매칭 키 fetch.

    실패(URL 미설정/네트워크/`kid` 미매칭/JWKS 형식 오류)는 전부 `JWTValidationError` 흡수.
    """
    jwks_url = settings.supabase_jwks_url
    if not jwks_url:
        # 비대칭 알고리즘 설정인데 JWKS URL 미지정 = 운영 설정 오류. fail-fast.
        raise JWTValidationError(
            "JWKS URL 이 설정되지 않았습니다 (비대칭 JWT 검증 불가)."
        )
    client = _get_jwks_client(jwks_url)
    try:
        signing_key = client.get_signing_key_from_jwt(token)
    except jwt.PyJWKClientError as exc:
        # kid 미매칭 / JWKS 응답 파싱 실패 / fetch 실패 등 전부.
        raise JWTValidationError("JWKS 공개키 조회에 실패했습니다.") from exc
    except Exception as exc:
        # urllib 네트워크 예외 등 PyJWKClient 가 흡수하지 못한 모든 IO 실패.
        raise JWTValidationError("JWKS 공개키 조회에 실패했습니다.") from exc
    return signing_key.key


def _resolve_signing_key(token: str, settings: Settings) -> Any:
    """검증 키 해석 — 알고리즘에 따라 대칭/비대칭 경로 분기.

    호출부(`verify_jwt`)는 키 타입을 알 필요 없이 PyJWT 에 그대로 전달한다.
    """
    algorithm = settings.supabase_jwt_algorithm
    if algorithm in _SYMMETRIC_ALGORITHMS:
        return _resolve_symmetric_key(settings)
    if algorithm in _ASYMMETRIC_ALGORITHMS:
        return _resolve_asymmetric_key(token, settings)
    # 화이트리스트 외 알고리즘 (none/PS256 등) — 운영 fail-fast.
    raise JWTValidationError(
        f"JWT 알고리즘 '{algorithm}' 은 지원되지 않습니다."
    )


def verify_jwt(token: str, settings: Settings) -> VerifiedToken:
    """JWT 문자열을 검증하고 `VerifiedToken` 반환. 실패 시 `JWTValidationError`.

    서명·만료·audience 를 모두 검증한다. PyJWT 의 세분화된 예외를 단일 예외로 흡수.
    대칭(HS256) 토큰은 `SUPABASE_JWT_SECRET`, 비대칭(ES256 등)은 JWKS 공개키로 검증.
    """
    if not token:
        raise JWTValidationError("토큰이 비어 있습니다.")

    signing_key = _resolve_signing_key(token, settings)
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
