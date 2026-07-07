# api/tests/test_me_subscription.py
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")

from fastapi.testclient import TestClient

from app.auth.dependencies import CurrentUser, get_current_user
from app.config import Settings, get_settings
from app.main import app
from app.services.quota import SubscriptionView


def _settings() -> Settings:
    return Settings(
        supabase_url="https://x.supabase.co", supabase_key="", supabase_service_role_key="svc",
        supabase_storage_bucket="documents", gemini_api_key="", hf_api_token="",
        default_user_id="00000000-0000-0000-0000-000000000001",
        doc_budget_usd=0.1, daily_budget_usd=0.5, sliding_24h_budget_usd=0.5,
        budget_krw_per_usd=1380.0, vision_need_score_enabled=True, vision_page_cap_per_doc=50,
        auth_enabled=True, owner_user_id="00000000-0000-0000-0000-0000000000ff",
    )


class MeSubscriptionTest(unittest.TestCase):
    def setUp(self) -> None:
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            user_id="uid-1", is_authenticated=True
        )
        app.dependency_overrides[get_settings] = _settings
        self.client = TestClient(app, raise_server_exceptions=False)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_returns_subscription_view(self) -> None:
        with patch(
            "app.routers.me.quota.get_subscription_view",
            return_value=SubscriptionView(
                plan_code="pro", status="active",
                current_period_end="2026-08-07T00:00:00+00:00",
            ),
        ):
            resp = self.client.get("/me/subscription")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "active")
        self.assertEqual(body["current_period_end"], "2026-08-07T00:00:00+00:00")
        self.assertEqual(body["plan_code"], "pro")


if __name__ == "__main__":
    unittest.main()
