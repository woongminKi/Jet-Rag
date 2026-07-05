"""수익화 W1 — 데모 병행 모드 get_current_user 3-way 분기 테스트.

- auth_enabled=false: default_user + is_authenticated=True (로컬 dev 쓰기 보존)
- auth_enabled=true + 토큰 없음: owner fallback + is_authenticated=False (익명 데모)
- auth_enabled=true + 유효 JWT: 본인 user_id + is_authenticated=True
- auth_enabled=true + 무효 JWT: 401
실행: `python -m unittest tests.test_auth_demo_fallback`
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")

from fastapi import HTTPException

from app.auth.dependencies import CurrentUser, get_current_user
from app.auth.jwt_verify import JWTValidationError, VerifiedToken
from app.config import Settings

_OWNER_ID = "11111111-1111-1111-1111-111111111111"
_DEFAULT_ID = "00000000-0000-0000-0000-000000000001"
_JWT_USER_ID = "22222222-2222-2222-2222-222222222222"


def _settings(auth_enabled: bool, owner: str | None = _OWNER_ID) -> Settings:
    return Settings(
        supabase_url="https://example.supabase.co",
        supabase_key="",
        supabase_service_role_key="",
        supabase_storage_bucket="documents",
        gemini_api_key="",
        hf_api_token="dummy-test-token",
        default_user_id=_DEFAULT_ID,
        doc_budget_usd=0.1,
        daily_budget_usd=0.5,
        sliding_24h_budget_usd=0.5,
        budget_krw_per_usd=1380.0,
        vision_need_score_enabled=True,
        vision_page_cap_per_doc=50,
        auth_enabled=auth_enabled,
        supabase_jwt_secret="test-secret",
        supabase_jwt_algorithm="HS256",
        owner_user_id=owner,
    )


class _FakeRequest:
    def __init__(self, bearer: str | None = None) -> None:
        self.headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
        self.cookies: dict[str, str] = {}


class DemoFallbackTest(unittest.TestCase):
    def test_auth_disabled_returns_default_user_authenticated(self) -> None:
        user = get_current_user(_FakeRequest(), _settings(auth_enabled=False))
        self.assertEqual(user.user_id, _DEFAULT_ID)
        self.assertTrue(user.is_authenticated)

    def test_anonymous_falls_back_to_owner_unauthenticated(self) -> None:
        user = get_current_user(_FakeRequest(), _settings(auth_enabled=True))
        self.assertEqual(user.user_id, _OWNER_ID)
        self.assertFalse(user.is_authenticated)

    def test_anonymous_without_owner_falls_back_to_default(self) -> None:
        user = get_current_user(
            _FakeRequest(), _settings(auth_enabled=True, owner=None)
        )
        self.assertEqual(user.user_id, _DEFAULT_ID)
        self.assertFalse(user.is_authenticated)

    def test_valid_jwt_returns_caller_authenticated(self) -> None:
        with patch(
            "app.auth.dependencies.verify_jwt",
            return_value=VerifiedToken(user_id=_JWT_USER_ID, email="a@b.co"),
        ):
            user = get_current_user(
                _FakeRequest(bearer="valid-token"), _settings(auth_enabled=True)
            )
        self.assertEqual(user.user_id, _JWT_USER_ID)
        self.assertTrue(user.is_authenticated)

    def test_invalid_jwt_raises_401(self) -> None:
        with patch(
            "app.auth.dependencies.verify_jwt",
            side_effect=JWTValidationError("bad token"),
        ):
            with self.assertRaises(HTTPException) as ctx:
                get_current_user(
                    _FakeRequest(bearer="bad-token"), _settings(auth_enabled=True)
                )
        self.assertEqual(ctx.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
