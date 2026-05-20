"""D1 — JWT 로컬 검증 단위 테스트 (plan §7).

대상: `app.auth.jwt_verify.verify_jwt` + `app.auth.dependencies.get_current_user`.

검증:
- 유효 JWT (HS256/ES256) → VerifiedToken(user_id, email)
- 만료 → JWTValidationError
- 서명 불일치 → JWTValidationError
- audience 불일치 → JWTValidationError
- sub 누락 → JWTValidationError
- JWKS URL 미설정 (비대칭) → JWTValidationError
- JWKS fetch 실패 (mock 예외) → JWTValidationError
- get_current_user: auth_enabled=false → default_user_id fallback (무중단 핵심)
- get_current_user: auth_enabled=true + 토큰 없음 → 401
- get_current_user: auth_enabled=true + 유효 토큰 → CurrentUser

외부 의존성 0 — PyJWT 로 직접 토큰 생성, Settings 직접 구성, JWKS fetch 는 mock.
실행: `python -m unittest tests.test_auth_jwt`
"""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.auth.dependencies import CurrentUser, get_current_user
from app.auth import jwt_verify
from app.auth.jwt_verify import JWTValidationError, verify_jwt
from app.config import Settings

_PROJECT_REF = "abcd1234"
_SUPABASE_URL = f"https://{_PROJECT_REF}.supabase.co"
_AUTH_COOKIE_NAME = f"sb-{_PROJECT_REF}-auth-token"

_SECRET = "test-jwt-secret-do-not-use-in-prod"
_OTHER_SECRET = "a-different-secret"
_USER_ID = "11111111-1111-1111-1111-111111111111"
_DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001"


def _make_settings(*, auth_enabled: bool, secret: str | None = _SECRET) -> Settings:
    """auth 필드만 의미 있는 최소 Settings.

    supabase_url 은 쿠키 경로(project_ref 유도)를 위해 실제 형식으로 채운다.
    `supabase_jwks_url` 은 default None — HS256 경로에는 무영향, 비대칭 테스트는 `replace` 로 주입.
    """
    return Settings(
        supabase_url=_SUPABASE_URL,
        supabase_key="",
        supabase_service_role_key="",
        supabase_storage_bucket="documents",
        gemini_api_key="",
        hf_api_token="",
        default_user_id=_DEFAULT_USER_ID,
        doc_budget_usd=0.1,
        daily_budget_usd=0.5,
        sliding_24h_budget_usd=0.5,
        budget_krw_per_usd=1380.0,
        vision_need_score_enabled=True,
        vision_page_cap_per_doc=50,
        auth_enabled=auth_enabled,
        supabase_jwt_secret=secret,
        supabase_jwt_algorithm="HS256",
        owner_user_id=None,
    )


def _make_token(
    *,
    secret: str = _SECRET,
    sub: str | None = _USER_ID,
    email: str | None = "user@example.com",
    aud: str = "authenticated",
    exp_delta: timedelta = timedelta(hours=1),
    algorithm: str = "HS256",
    include_exp: bool = True,
) -> str:
    """검증용 JWT 발급. claim 을 케이스별로 조작."""
    payload: dict = {"aud": aud}
    if sub is not None:
        payload["sub"] = sub
    if email is not None:
        payload["email"] = email
    if include_exp:
        payload["exp"] = datetime.now(timezone.utc) + exp_delta
    return jwt.encode(payload, secret, algorithm=algorithm)


class _FakeRequest:
    """get_current_user 가 읽는 request.headers / request.cookies 만 흉내."""

    def __init__(
        self,
        authorization: str | None = None,
        cookies: dict[str, str] | None = None,
    ) -> None:
        self.headers: dict[str, str] = {}
        if authorization is not None:
            self.headers["Authorization"] = authorization
        self.cookies: dict[str, str] = cookies or {}


class VerifyJwtTest(unittest.TestCase):
    def test_valid_token_returns_verified(self) -> None:
        settings = _make_settings(auth_enabled=True)
        verified = verify_jwt(_make_token(), settings)
        self.assertEqual(verified.user_id, _USER_ID)
        self.assertEqual(verified.email, "user@example.com")

    def test_token_without_email_yields_none(self) -> None:
        settings = _make_settings(auth_enabled=True)
        verified = verify_jwt(_make_token(email=None), settings)
        self.assertEqual(verified.user_id, _USER_ID)
        self.assertIsNone(verified.email)

    def test_expired_token_raises(self) -> None:
        settings = _make_settings(auth_enabled=True)
        token = _make_token(exp_delta=timedelta(hours=-1))
        with self.assertRaises(JWTValidationError):
            verify_jwt(token, settings)

    def test_wrong_signature_raises(self) -> None:
        settings = _make_settings(auth_enabled=True)
        token = _make_token(secret=_OTHER_SECRET)
        with self.assertRaises(JWTValidationError):
            verify_jwt(token, settings)

    def test_wrong_audience_raises(self) -> None:
        settings = _make_settings(auth_enabled=True)
        token = _make_token(aud="anon")
        with self.assertRaises(JWTValidationError):
            verify_jwt(token, settings)

    def test_missing_sub_raises(self) -> None:
        settings = _make_settings(auth_enabled=True)
        token = _make_token(sub=None)
        with self.assertRaises(JWTValidationError):
            verify_jwt(token, settings)

    def test_empty_token_raises(self) -> None:
        settings = _make_settings(auth_enabled=True)
        with self.assertRaises(JWTValidationError):
            verify_jwt("", settings)

    def test_missing_secret_raises(self) -> None:
        settings = _make_settings(auth_enabled=True, secret=None)
        with self.assertRaises(JWTValidationError):
            verify_jwt(_make_token(), settings)

    def test_unsupported_algorithm_raises(self) -> None:
        # PS256 은 화이트리스트(대칭/비대칭) 어디에도 없는 미지원 알고리즘 — fail-fast.
        # (RS256/ES256 은 JWKS 경로로 지원 — VerifyJwtAsymmetricTest 참고.)
        settings = replace(_make_settings(auth_enabled=True), supabase_jwt_algorithm="PS256")
        with self.assertRaises(JWTValidationError):
            verify_jwt(_make_token(), settings)


class VerifyJwtAsymmetricTest(unittest.TestCase):
    """ES256 (Supabase ECC P-256 기본) + JWKS 경로 검증.

    전략: `cryptography` 로 ephemeral keypair 생성 → private key 로 ES256 토큰 서명 →
    `PyJWKClient.get_signing_key_from_jwt` 를 `unittest.mock.patch` 로 stub 해서
    공개키만 반환. 외부 HTTP 호출 0.
    """

    @classmethod
    def setUpClass(cls) -> None:
        # 테스트 전용 ES256 keypair (P-256). PyJWKClient mock 이 이 공개키를 반환한다.
        cls._private_key = ec.generate_private_key(ec.SECP256R1())
        cls._public_key = cls._private_key.public_key()
        # PyJWT 가 검증 시 받는 키 형식은 cryptography 객체 그대로 OK.
        cls._public_key_pem = cls._public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        # PyJWKClient.get_signing_key_from_jwt 가 반환할 stub — `.key` 속성만 필요.
        cls._jwks_url = "https://example.supabase.co/auth/v1/.well-known/jwks.json"

    def setUp(self) -> None:
        # PyJWKClient lru_cache 비워 테스트 간 격리. 같은 URL 재호출 시 stub 없으면 실제 HTTP.
        jwt_verify._get_jwks_client.cache_clear()

    def _make_es256_settings(self, *, jwks_url: str | None = None) -> Settings:
        """ES256 + JWKS 설정. jwks_url 미지정 시 default 사용 (None 명시도 가능)."""
        base = _make_settings(auth_enabled=True, secret=None)
        return replace(
            base,
            supabase_jwt_algorithm="ES256",
            supabase_jwks_url=jwks_url if jwks_url is not None else self._jwks_url,
        )

    def _make_es256_token(
        self,
        *,
        exp_delta: timedelta = timedelta(hours=1),
        sub: str | None = _USER_ID,
    ) -> str:
        """ES256 토큰 발급. kid header 포함 — PyJWKClient 가 사용."""
        payload: dict = {"aud": "authenticated"}
        if sub is not None:
            payload["sub"] = sub
        payload["email"] = "user@example.com"
        payload["exp"] = datetime.now(timezone.utc) + exp_delta
        return jwt.encode(
            payload,
            self._private_key,
            algorithm="ES256",
            headers={"kid": "test-kid"},
        )

    def _stub_signing_key(self):
        """PyJWKClient.get_signing_key_from_jwt → 공개키만 담은 stub 반환."""

        class _FakeSigningKey:
            def __init__(self, key) -> None:
                self.key = key

        return _FakeSigningKey(self._public_key_pem)

    def test_valid_es256_token_returns_verified(self) -> None:
        settings = self._make_es256_settings()
        token = self._make_es256_token()
        with patch(
            "app.auth.jwt_verify.PyJWKClient.get_signing_key_from_jwt",
            return_value=self._stub_signing_key(),
        ):
            verified = verify_jwt(token, settings)
        self.assertEqual(verified.user_id, _USER_ID)
        self.assertEqual(verified.email, "user@example.com")

    def test_es256_without_jwks_url_raises(self) -> None:
        # 비대칭 알고리즘 설정인데 JWKS URL 미지정 → fail-fast (한국어 메시지).
        settings = self._make_es256_settings(jwks_url="")
        # Settings 는 supabase_jwks_url=None 으로 normalize 안 됨(빈 문자열 그대로) — falsy 검사.
        # 실제로는 get_settings() 가 빈 문자열을 None 으로 변환하지만 dataclass 직접 구성은 그대로 유지.
        settings = replace(settings, supabase_jwks_url=None)
        token = self._make_es256_token()
        with self.assertRaises(JWTValidationError) as ctx:
            verify_jwt(token, settings)
        self.assertIn("JWKS URL", str(ctx.exception))

    def test_es256_jwks_fetch_failure_raises(self) -> None:
        settings = self._make_es256_settings()
        token = self._make_es256_token()
        # PyJWKClient.get_signing_key_from_jwt 가 PyJWKClientError 던지면 → 한국어 변환.
        with patch(
            "app.auth.jwt_verify.PyJWKClient.get_signing_key_from_jwt",
            side_effect=jwt.PyJWKClientError("kid not found"),
        ):
            with self.assertRaises(JWTValidationError) as ctx:
                verify_jwt(token, settings)
        self.assertIn("JWKS", str(ctx.exception))

    def test_es256_jwks_network_failure_raises(self) -> None:
        # PyJWKClient 가 자체 흡수 못한 IO 예외 (urllib URLError 등) 도 한국어 변환.
        settings = self._make_es256_settings()
        token = self._make_es256_token()
        with patch(
            "app.auth.jwt_verify.PyJWKClient.get_signing_key_from_jwt",
            side_effect=OSError("network down"),
        ):
            with self.assertRaises(JWTValidationError):
                verify_jwt(token, settings)

    def test_expired_es256_token_raises(self) -> None:
        settings = self._make_es256_settings()
        token = self._make_es256_token(exp_delta=timedelta(hours=-1))
        with patch(
            "app.auth.jwt_verify.PyJWKClient.get_signing_key_from_jwt",
            return_value=self._stub_signing_key(),
        ):
            with self.assertRaises(JWTValidationError) as ctx:
                verify_jwt(token, settings)
        self.assertIn("만료", str(ctx.exception))


class GetCurrentUserTest(unittest.TestCase):
    def test_auth_disabled_returns_default_fallback(self) -> None:
        settings = _make_settings(auth_enabled=False)
        # 토큰 없어도 fallback — 무중단 핵심.
        user = get_current_user(_FakeRequest(), settings)
        self.assertEqual(user.user_id, _DEFAULT_USER_ID)
        self.assertIsNone(user.email)

    def test_auth_disabled_ignores_invalid_token(self) -> None:
        settings = _make_settings(auth_enabled=False)
        # 잘못된 토큰을 보내도 검증 skip → fallback.
        user = get_current_user(_FakeRequest("Bearer garbage"), settings)
        self.assertEqual(user.user_id, _DEFAULT_USER_ID)

    def test_auth_enabled_no_header_raises_401(self) -> None:
        from fastapi import HTTPException

        settings = _make_settings(auth_enabled=True)
        with self.assertRaises(HTTPException) as ctx:
            get_current_user(_FakeRequest(), settings)
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertEqual(ctx.exception.detail, "인증이 필요합니다.")

    def test_auth_enabled_malformed_header_raises_401(self) -> None:
        from fastapi import HTTPException

        settings = _make_settings(auth_enabled=True)
        # "Bearer " prefix 없는 헤더.
        with self.assertRaises(HTTPException) as ctx:
            get_current_user(_FakeRequest("Token abc"), settings)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_auth_enabled_invalid_token_raises_401(self) -> None:
        from fastapi import HTTPException

        settings = _make_settings(auth_enabled=True)
        with self.assertRaises(HTTPException) as ctx:
            get_current_user(_FakeRequest("Bearer not-a-jwt"), settings)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_auth_enabled_valid_token_returns_user(self) -> None:
        settings = _make_settings(auth_enabled=True)
        user = get_current_user(_FakeRequest(f"Bearer {_make_token()}"), settings)
        self.assertIsInstance(user, CurrentUser)
        self.assertEqual(user.user_id, _USER_ID)
        self.assertEqual(user.email, "user@example.com")

    def test_auth_enabled_cookie_token_returns_user(self) -> None:
        # Authorization 헤더 없이 Supabase auth 쿠키만 → 쿠키 경로로 검증 (plan §1.1).
        settings = _make_settings(auth_enabled=True)
        cookie_value = json.dumps({"access_token": _make_token()})
        request = _FakeRequest(cookies={_AUTH_COOKIE_NAME: cookie_value})
        user = get_current_user(request, settings)
        self.assertEqual(user.user_id, _USER_ID)
        self.assertEqual(user.email, "user@example.com")

    def test_auth_enabled_header_precedes_cookie(self) -> None:
        # 헤더 + 쿠키 동시 존재 → 헤더 우선. 쿠키는 만료 토큰이라 헤더를 안 쓰면 401.
        settings = _make_settings(auth_enabled=True)
        expired = _make_token(exp_delta=timedelta(hours=-1))
        request = _FakeRequest(
            authorization=f"Bearer {_make_token()}",
            cookies={_AUTH_COOKIE_NAME: json.dumps({"access_token": expired})},
        )
        user = get_current_user(request, settings)
        self.assertEqual(user.user_id, _USER_ID)

    def test_auth_enabled_invalid_cookie_token_raises_401(self) -> None:
        from fastapi import HTTPException

        settings = _make_settings(auth_enabled=True)
        request = _FakeRequest(
            cookies={_AUTH_COOKIE_NAME: json.dumps({"access_token": "not-a-jwt"})}
        )
        with self.assertRaises(HTTPException) as ctx:
            get_current_user(request, settings)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_auth_enabled_no_token_anywhere_raises_401(self) -> None:
        from fastapi import HTTPException

        settings = _make_settings(auth_enabled=True)
        # 헤더 없음 + 무관 쿠키만 → 토큰 소스 0 → 401.
        request = _FakeRequest(cookies={"unrelated": "x"})
        with self.assertRaises(HTTPException) as ctx:
            get_current_user(request, settings)
        self.assertEqual(ctx.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
