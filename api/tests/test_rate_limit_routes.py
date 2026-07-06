"""수익화 W2 — rate limit 라우터 게이트 통합 테스트.

전략: 상한 초과(429)는 라우터 의존성이 핸들러 진입 전에 short-circuit 하므로
검색/LLM 외부 I/O 0 로 검증 가능. 통과(under-cap) 경로는 test_rate_limit.py
단위 테스트가 커버 — 여기선 429 거절만 확인한다.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")

from fastapi.testclient import TestClient

from app.auth.dependencies import CurrentUser, get_current_user
from app.config import Settings, get_settings
from app.main import app


def _settings(**over) -> Settings:
    base = dict(
        supabase_url="https://x.supabase.co",
        supabase_key="",
        supabase_service_role_key="svc",
        supabase_storage_bucket="documents",
        gemini_api_key="",
        hf_api_token="",
        default_user_id="00000000-0000-0000-0000-000000000001",
        doc_budget_usd=0.1,
        daily_budget_usd=0.5,
        sliding_24h_budget_usd=0.5,
        budget_krw_per_usd=1380.0,
        vision_need_score_enabled=True,
        vision_page_cap_per_doc=50,
        auth_enabled=True,
        owner_user_id="00000000-0000-0000-0000-0000000000ff",
        rate_limit_answers_per_day=5,
        rate_limit_docs_per_day=3,
    )
    base.update(over)
    return Settings(**base)


def _over_cap_client() -> MagicMock:
    client = MagicMock()
    client.rpc.return_value.execute.return_value.data = 999  # cap 초과
    return client


class AnswerRateLimitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.authed = CurrentUser(user_id="uid-1", is_authenticated=True)
        app.dependency_overrides[get_current_user] = lambda: self.authed
        app.dependency_overrides[get_settings] = lambda: _settings()
        self.client = TestClient(app, raise_server_exceptions=False)
        quota_patcher = patch(
            "app.services.quota.get_effective_plan", return_value=None
        )
        quota_patcher.start()
        self.addCleanup(quota_patcher.stop)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_over_cap_returns_429_before_handler(self) -> None:
        with patch("app.services.rate_limit.get_supabase_client", return_value=_over_cap_client()):
            resp = self.client.get("/answer", params={"q": "테스트 질문"})
        self.assertEqual(resp.status_code, 429)
        self.assertIn("한도", resp.json()["detail"])


class UploadRateLimitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.authed = CurrentUser(user_id="uid-1", is_authenticated=True)
        app.dependency_overrides[get_current_user] = lambda: self.authed
        app.dependency_overrides[get_settings] = lambda: _settings()
        self.client = TestClient(app, raise_server_exceptions=False)
        quota_patcher = patch(
            "app.services.quota.get_effective_plan", return_value=None
        )
        quota_patcher.start()
        self.addCleanup(quota_patcher.stop)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_upload_over_cap_returns_429(self) -> None:
        files = {"file": ("t.pdf", b"%PDF-1.4 test", "application/pdf")}
        with patch("app.services.rate_limit.get_supabase_client", return_value=_over_cap_client()):
            resp = self.client.post("/documents", files=files)
        self.assertEqual(resp.status_code, 429)
        self.assertIn("한도", resp.json()["detail"])

    def test_upload_url_over_cap_returns_429(self) -> None:
        payload = {"url": "https://example.com/doc.pdf"}
        with patch("app.services.rate_limit.get_supabase_client", return_value=_over_cap_client()):
            resp = self.client.post("/documents/url", json=payload)
        self.assertEqual(resp.status_code, 429)
        self.assertIn("한도", resp.json()["detail"])


if __name__ == "__main__":
    unittest.main()
