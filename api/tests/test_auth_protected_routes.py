"""D1 — 보호 라우트 인증 게이트 회귀 테스트 (plan §7).

수익화 W1 정책:
- 익명(토큰 없음)은 GET(/documents, /search, /answer, /stats) 를 통과한다
  (owner fallback — dependency_overrides 로 외부 호출 차단).
- 익명 POST/쓰기 7 endpoint 는 401 "로그인이 필요합니다."
- 유효 JWT 사용자는 게이트 통과.
- /health, / 는 공개.

전략 — 외부 의존성 0:
- auth_enabled=true 로 `get_settings` 를 dependency_overrides 로 교체.
- 쓰기 endpoint: 토큰 없음 → 401 (require_authenticated_user 게이트).
- 유효 user 주입(get_current_user override) → 게이트 통과 검증.
  핸들러 본문 도달을 피하기 위해 잘못된 쿼리(검증 422) 로 게이트는 통과하되
  본문 진입 전 종료시킨다.

lifespan warmup/sweep 은 no-op patch (외부 호출 방지, test_search_503 선례).
실행: `python -m unittest tests.test_auth_protected_routes`
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")

from app.auth import CurrentUser
from app.auth.dependencies import get_current_user
from app.config import Settings, get_settings

_AUTH_USER_ID = "22222222-2222-2222-2222-222222222222"
_DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001"

# 익명 GET — owner fallback 으로 통과 (read-only 허용).
_ANONYMOUS_GET_PASS = [
    "/documents",
    "/search?q=hello",
    "/answer?q=hello",
    "/stats",
    "/stats/trend",
]

# 쓰기 endpoint — 익명은 401 (require_authenticated_user 게이트).
_WRITE_ENDPOINTS_401 = [
    # documents write (4곳)
    ("POST", "/documents"),
    ("POST", "/documents/url"),
    ("POST", "/documents/some-doc-id/reingest"),
    ("POST", "/documents/some-doc-id/reingest-missing"),
    # answer write (3곳) — feedback / eval
    ("POST", "/answer/feedback"),
    ("POST", "/answer/eval-ragas"),
    ("POST", "/search/eval-precision"),
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

    def test_anonymous_get_passes_gate(self) -> None:
        """익명 GET — owner fallback 통과 (read-only 허용). 외부 호출 차단으로 503/422 허용."""
        with self.TestClient(self.app) as client:
            for path in _ANONYMOUS_GET_PASS:
                with self.subTest(path=path):
                    resp = client.get(path)
                    self.assertNotEqual(
                        resp.status_code, 401,
                        f"{path} 익명 GET 이 401 을 반환 — 게이트가 잘못 차단함 ({resp.status_code})"
                    )

    def test_write_without_token_returns_401(self) -> None:
        """쓰기 endpoint — 익명은 require_authenticated_user 게이트에서 401."""
        with self.TestClient(self.app) as client:
            for method, path in _WRITE_ENDPOINTS_401:
                with self.subTest(method=method, path=path):
                    if method == "POST":
                        resp = client.post(path)
                    elif method == "DELETE":
                        resp = client.delete(path)
                    else:
                        resp = client.request(method, path)
                    self.assertEqual(
                        resp.status_code, 401,
                        f"{method} {path} 가 401 아님 ({resp.status_code})"
                    )
                    self.assertEqual(
                        resp.json().get("detail"), "로그인이 필요합니다.",
                        f"{method} {path} detail 불일치"
                    )

    def test_invalid_token_returns_401(self) -> None:
        with self.TestClient(self.app) as client:
            resp = client.get(
                "/documents", headers={"Authorization": "Bearer not-a-real-jwt"}
            )
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
        authed_user = CurrentUser(user_id=_AUTH_USER_ID, email="u@example.com")
        self.app.dependency_overrides[get_current_user] = lambda: authed_user
        with self.TestClient(self.app) as client:
            # /search 는 q 필수 — 미전달 시 422 (게이트 통과 후 검증 실패).
            resp = client.get("/search")
            self.assertNotEqual(resp.status_code, 401)
            self.assertEqual(resp.status_code, 422)


if __name__ == "__main__":
    unittest.main()
