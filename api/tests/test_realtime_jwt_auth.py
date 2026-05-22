"""D2 — `/auth/me` access_token 노출 + Realtime setAuth 게이트 (plan §5 / §8).

검증:
- auth_enabled=false → access_token=None (fallback)
- auth_enabled=true + Authorization Bearer 토큰 → access_token 노출
- auth_enabled=true + 토큰 미존재 (헤더·쿠키 모두 없음) → access_token=None

W31 follow-up — invite 게이트 제거 후 authorized 는 인증된 모든 user 에 대해 true.

실행: `python -m unittest tests.test_realtime_jwt_auth`
"""

from __future__ import annotations

import unittest

from app.auth import CurrentUser
from app.config import Settings
from app.routers.auth import AuthMeResponse, auth_me

_DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001"
_AUTH_USER_ID = "11111111-1111-1111-1111-111111111111"
_PROJECT_REF = "abcd1234"
_SUPABASE_URL = f"https://{_PROJECT_REF}.supabase.co"


def _settings(*, auth_enabled: bool) -> Settings:
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
        supabase_jwt_secret="secret",
        supabase_jwt_algorithm="HS256",
        owner_user_id=None,
    )


class _FakeRequest:
    """auth_me 가 읽는 headers / cookies 만 흉내."""

    def __init__(
        self,
        authorization: str | None = None,
        cookies: dict[str, str] | None = None,
    ) -> None:
        self.headers: dict[str, str] = {}
        if authorization is not None:
            self.headers["Authorization"] = authorization
        self.cookies: dict[str, str] = cookies or {}


class AuthMeAccessTokenTest(unittest.TestCase):
    def test_auth_disabled_returns_authorized_with_null_token(self) -> None:
        """auth_enabled=false → authorized=true / access_token=None."""
        user = CurrentUser(user_id=_DEFAULT_USER_ID)
        req = _FakeRequest(authorization="Bearer should-be-ignored")
        resp = auth_me(request=req, current_user=user, settings=_settings(auth_enabled=False))
        self.assertIsInstance(resp, AuthMeResponse)
        self.assertTrue(resp.authorized)
        self.assertEqual(resp.user_id, _DEFAULT_USER_ID)
        self.assertIsNone(resp.access_token)

    def test_auth_enabled_returns_bearer_token(self) -> None:
        """auth_enabled=true → authorized=true + 토큰 forward."""
        user = CurrentUser(user_id=_AUTH_USER_ID, email="u@example.com")
        req = _FakeRequest(authorization="Bearer my-jwt-token")
        resp = auth_me(request=req, current_user=user, settings=_settings(auth_enabled=True))
        self.assertTrue(resp.authorized)
        self.assertEqual(resp.access_token, "my-jwt-token")
        self.assertEqual(resp.user_id, _AUTH_USER_ID)

    def test_auth_enabled_no_token_present_returns_null_token(self) -> None:
        """authorized=true 이더라도 요청에 토큰이 전혀 없으면 access_token=None.

        실 경로는 get_current_user 가 401 차단하므로 도달 불가. 본 단위 테스트는 핸들러
        직접 호출 — 토큰 추출이 헤더·쿠키 모두 비어있을 때 None 반환 보장."""
        user = CurrentUser(user_id=_AUTH_USER_ID)
        req = _FakeRequest(authorization=None, cookies={})
        resp = auth_me(request=req, current_user=user, settings=_settings(auth_enabled=True))
        self.assertTrue(resp.authorized)
        self.assertIsNone(resp.access_token)


if __name__ == "__main__":
    unittest.main()
