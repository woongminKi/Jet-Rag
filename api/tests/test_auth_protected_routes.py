"""D1 — 보호 라우트 인증 게이트 회귀 테스트 (plan §7).

대상: documents / search / answer / stats 라우터의 router-level require_auth +
공개 라우트(/health, /). admin OWNER 게이트는 test_admin_gate 에서 별도.

전략 — 외부 의존성 0:
- auth_enabled=true 로 `get_settings` 를 dependency_overrides 로 교체.
- 토큰 없음/잘못된 토큰 → 401. 이 경로는 dependency 단에서 차단되어 핸들러 본문
  (Supabase/HF) 에 도달하지 않으므로 외부 호출 0.
- 유효 user 주입(get_current_user override) → 게이트 통과 검증. 핸들러 본문 도달을
  피하기 위해 잘못된 쿼리(검증 422) 로 게이트는 통과하되 본문 진입 전 종료시킨다.
- /health, / 는 공개 — auth_enabled=true 라도 200/3xx.

lifespan warmup/sweep 은 no-op patch (외부 호출 방지, test_search_503 선례).
실행: `python -m unittest tests.test_auth_protected_routes`
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")

from app.auth import CurrentUser
from app.auth.dependencies import get_current_user, require_authorized_user
from app.config import Settings, get_settings

_AUTH_USER_ID = "22222222-2222-2222-2222-222222222222"
_DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001"

# router-level require_auth 가 걸린 보호 라우트 (인증 없으면 401).
_PROTECTED_GET = [
    "/documents",
    "/search?q=hello",
    "/answer?q=hello",
    "/stats",
    "/stats/trend",
]


def _auth_enabled_settings() -> Settings:
    return Settings(
        supabase_url="",
        supabase_key="",
        supabase_service_role_key="",
        supabase_storage_bucket="documents",
        gemini_api_key="",
        hf_api_token="dummy-test-token",
        default_user_id=_DEFAULT_USER_ID,
        doc_budget_usd=0.1,
        daily_budget_usd=0.5,
        sliding_24h_budget_usd=0.5,
        budget_krw_per_usd=1380.0,
        vision_need_score_enabled=True,
        vision_page_cap_per_doc=50,
        auth_enabled=True,
        supabase_jwt_secret="test-secret",
        supabase_jwt_algorithm="HS256",
        owner_user_id=None,
    )


class ProtectedRoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # lifespan warmup/sweep no-op → TestClient 진입 시 외부 호출 0.
        cls._patchers = [
            patch("app.main._warmup_bgem3", new=AsyncMock(return_value=None)),
            patch("app.main._sweep_stale_ingest_jobs", new=AsyncMock(return_value=None)),
        ]
        for p in cls._patchers:
            p.start()

        from fastapi.testclient import TestClient

        from app.main import app

        cls.app = app
        cls.TestClient = TestClient

    @classmethod
    def tearDownClass(cls) -> None:
        for p in cls._patchers:
            p.stop()

    def setUp(self) -> None:
        # auth_enabled=true 강제 — dependency override.
        self.app.dependency_overrides[get_settings] = _auth_enabled_settings

    def tearDown(self) -> None:
        self.app.dependency_overrides.clear()

    def test_protected_get_without_token_returns_401(self) -> None:
        with self.TestClient(self.app) as client:
            for path in _PROTECTED_GET:
                with self.subTest(path=path):
                    resp = client.get(path)
                    self.assertEqual(
                        resp.status_code, 401, f"{path} 가 401 아님 ({resp.status_code})"
                    )
                    self.assertEqual(resp.json().get("detail"), "인증이 필요합니다.")

    def test_protected_get_with_invalid_token_returns_401(self) -> None:
        with self.TestClient(self.app) as client:
            resp = client.get(
                "/documents", headers={"Authorization": "Bearer not-a-real-jwt"}
            )
            self.assertEqual(resp.status_code, 401)

    def test_upload_without_token_returns_401(self) -> None:
        # POST /documents 도 보호 — 본문 도달 전 401.
        with self.TestClient(self.app) as client:
            resp = client.post("/documents", files={"file": ("x.txt", b"hi")})
            self.assertEqual(resp.status_code, 401)

    def test_health_is_public(self) -> None:
        with self.TestClient(self.app) as client:
            resp = client.get("/health")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json(), {"status": "ok"})

    def test_root_is_public(self) -> None:
        with self.TestClient(self.app) as client:
            resp = client.get("/", follow_redirects=False)
            # / → /docs redirect (3xx). 인증 게이트에 막히지 않음.
            self.assertIn(resp.status_code, (200, 307, 308))

    def test_valid_user_passes_gate(self) -> None:
        # get_current_user override → 인증 게이트 통과. 잘못된 쿼리(q 누락)로 핸들러 본문
        # 진입 전 422 검증 단계에서 종료 → Supabase 호출 0. 401 이 아님을 확인.
        # D2 follow-up — require_authorized_user 도 override 로 invite redeem 게이트 우회
        # (실 Supabase 미설정 환경에서 invite_codes SELECT 시도 시 503 변환 risk 차단).
        authed_user = CurrentUser(user_id=_AUTH_USER_ID, email="u@example.com")
        self.app.dependency_overrides[get_current_user] = lambda: authed_user
        self.app.dependency_overrides[require_authorized_user] = lambda: authed_user
        with self.TestClient(self.app) as client:
            # /search 는 q 필수 — 미전달 시 422 (게이트 통과 후 검증 실패).
            resp = client.get("/search")
            self.assertNotEqual(resp.status_code, 401)
            self.assertEqual(resp.status_code, 422)


if __name__ == "__main__":
    unittest.main()
