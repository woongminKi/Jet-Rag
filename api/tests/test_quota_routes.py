"""수익화 W3 — 402 게이트 route 통합 + GET /me/plan.

429 route 테스트(test_rate_limit_routes.py)와 동일 전략 — 게이트가 핸들러 진입 전
short-circuit 하므로 검색/LLM I/O 0.
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
from app.services.quota import PlanLimits


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
        rate_limit_answers_per_day=50,
        rate_limit_docs_per_day=30,
        quota_enforcement_enabled=True,
    )
    base.update(over)
    return Settings(**base)


_FREE = PlanLimits(code="free", max_documents=10, answers_per_day=5)


def _counter_client(count: int) -> MagicMock:
    client = MagicMock()
    client.rpc.return_value.execute.return_value.data = count
    return client


class QuotaRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.authed = CurrentUser(user_id="uid-1", is_authenticated=True)
        app.dependency_overrides[get_current_user] = lambda: self.authed
        app.dependency_overrides[get_settings] = lambda: _settings()
        self.client = TestClient(app, raise_server_exceptions=False)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_answer_over_plan_cap_returns_402(self) -> None:
        with patch(
            "app.services.rate_limit.get_supabase_client",
            return_value=_counter_client(6),
        ), patch("app.services.quota.get_effective_plan", return_value=_FREE):
            resp = self.client.get("/answer", params={"q": "테스트 질문"})
        self.assertEqual(resp.status_code, 402)
        self.assertIn("업그레이드", resp.json()["detail"])

    def test_upload_at_doc_retention_cap_returns_402(self) -> None:
        files = {"file": ("t.pdf", b"%PDF-1.4 test", "application/pdf")}
        with patch(
            "app.services.rate_limit.get_supabase_client",
            return_value=_counter_client(1),
        ), patch("app.services.quota.get_effective_plan", return_value=_FREE), patch(
            "app.services.quota.count_active_documents", return_value=10
        ):
            resp = self.client.post("/documents", files=files)
        self.assertEqual(resp.status_code, 402)
        self.assertIn("문서 한도", resp.json()["detail"])


class MePlanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.authed = CurrentUser(user_id="uid-1", is_authenticated=True)
        app.dependency_overrides[get_current_user] = lambda: self.authed
        app.dependency_overrides[get_settings] = lambda: _settings()
        self.client = TestClient(app, raise_server_exceptions=False)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_returns_plan_and_usage(self) -> None:
        with patch(
            "app.routers.me.quota.get_effective_plan", return_value=_FREE
        ), patch(
            "app.routers.me.quota.get_todays_count", return_value=3
        ), patch(
            "app.routers.me.quota.count_active_documents", return_value=7
        ):
            resp = self.client.get("/me/plan")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["plan_code"], "free")
        self.assertEqual(body["answers_per_day"], 5)
        self.assertEqual(body["answers_used_today"], 3)
        self.assertEqual(body["documents_count"], 7)
        self.assertEqual(body["max_documents"], 10)

    def test_anonymous_gets_401(self) -> None:
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            user_id="owner", is_authenticated=False
        )
        resp = self.client.get("/me/plan")
        self.assertEqual(resp.status_code, 401)

    def test_plan_lookup_failure_returns_503(self) -> None:
        with patch("app.routers.me.quota.get_effective_plan", return_value=None):
            resp = self.client.get("/me/plan")
        self.assertEqual(resp.status_code, 503)


if __name__ == "__main__":
    unittest.main()
