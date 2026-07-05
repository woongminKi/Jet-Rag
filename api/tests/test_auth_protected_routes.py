"""D1 — 보호 라우트 인증 게이트 회귀 테스트 (plan §7).

수익화 W1 정책:
- 익명(토큰 없음)은 GET 을 통과한다 (owner fallback — 게이트가 401 을 던지지 않음).
- 익명 POST/쓰기 7 endpoint 는 401 "로그인이 필요합니다."
- 유효 JWT 사용자는 게이트 통과.
- /health, / 는 공개.

전략 — 외부 의존성 0 (실 HTTP 0):
- auth_enabled=true 로 `get_settings` 를 dependency_overrides 로 교체.
- 익명 GET 통과 검증은 핸들러 본문 진입을 피한다:
  - /search, /answer 는 q 필수 — q 생략 시 게이트 통과 후 422 검증 종료
    (HF/Supabase 호출 0). 게이트가 먼저 걸렸다면 401 이었을 것.
  - /documents, /stats, /stats/trend 는 module-level `get_supabase_client()` 를
    직접 호출 (dependency_overrides 불가) — 라우터 namespace 에 MagicMock patch
    → 네트워크 0. 단언은 "401 아님" (게이트 미차단) 만.
- 쓰기 endpoint: 토큰 없음 → 401 (require_authenticated_user 게이트).

lifespan warmup/sweep 은 no-op patch (외부 호출 방지, test_search_503 선례).
실행: `python -m unittest tests.test_auth_protected_routes`
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")

from app.auth import CurrentUser
from app.auth.dependencies import get_current_user
from app.config import Settings, get_settings

_AUTH_USER_ID = "22222222-2222-2222-2222-222222222222"
_DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001"

# 익명 GET — owner fallback 으로 게이트 통과 (read-only 허용).
# q 필수 endpoint: q 생략 → 게이트 통과 후 422 (핸들러 본문 미진입 — 외부 호출 0).
_ANONYMOUS_GET_PASS_422 = [
    "/search",
    "/answer",
]
# supabase 클라이언트 사용 endpoint: get_supabase_client 를 mock 후 "401 아님" 만 단언.
_ANONYMOUS_GET_PASS_DB = [
    "/documents",
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

    def test_anonymous_get_passes_gate_validation_422(self) -> None:
        """익명 GET (/search, /answer) — 게이트 통과 후 q 누락 422.

        게이트가 익명을 차단했다면 401 — 422 는 게이트 통과 + 핸들러 본문 미진입
        (외부 HTTP 0) 을 동시에 증명.
        """
        with self.TestClient(self.app) as client:
            for path in _ANONYMOUS_GET_PASS_422:
                with self.subTest(path=path):
                    resp = client.get(path)
                    self.assertEqual(
                        resp.status_code, 422,
                        f"{path} 익명 GET 이 422 아님 ({resp.status_code}) — "
                        "401 이면 게이트가 잘못 차단한 것"
                    )

    def test_anonymous_get_passes_gate_db_routes(self) -> None:
        """익명 GET (/documents, /stats 계열) — 게이트가 401 을 던지지 않음.

        핸들러가 module-level get_supabase_client() 를 직접 호출하므로
        (dependency_overrides 불가) 라우터 namespace 에 MagicMock patch → 네트워크 0.
        mock 데이터 형태로 인한 5xx 는 무관 — 단언 대상은 게이트(401 여부)뿐.
        """
        with (
            patch("app.routers.documents.get_supabase_client", new=MagicMock()),
            patch("app.routers.stats.get_supabase_client", new=MagicMock()),
        ):
            with self.TestClient(self.app, raise_server_exceptions=False) as client:
                for path in _ANONYMOUS_GET_PASS_DB:
                    with self.subTest(path=path):
                        resp = client.get(path)
                        self.assertNotEqual(
                            resp.status_code, 401,
                            f"{path} 익명 GET 이 401 을 반환 — 게이트가 잘못 차단함"
                        )

    def test_write_without_token_returns_401(self) -> None:
        """쓰기 endpoint — 익명은 require_authenticated_user 게이트에서 401."""
        with self.TestClient(self.app) as client:
            for method, path in _WRITE_ENDPOINTS_401:
                with self.subTest(method=method, path=path):
                    resp = client.post(path)
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
