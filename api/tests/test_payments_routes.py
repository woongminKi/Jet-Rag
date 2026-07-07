# api/tests/test_payments_routes.py
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")

from fastapi.testclient import TestClient

from app.adapters.payment import ReadyResult
from app.auth.dependencies import CurrentUser, get_current_user
from app.config import Settings, get_settings
from app.main import app


def _settings(**over) -> Settings:
    base = dict(
        supabase_url="https://x.supabase.co", supabase_key="", supabase_service_role_key="svc",
        supabase_storage_bucket="documents", gemini_api_key="", hf_api_token="",
        default_user_id="00000000-0000-0000-0000-000000000001",
        doc_budget_usd=0.1, daily_budget_usd=0.5, sliding_24h_budget_usd=0.5,
        budget_krw_per_usd=1380.0, vision_need_score_enabled=True, vision_page_cap_per_doc=50,
        auth_enabled=True, owner_user_id="00000000-0000-0000-0000-0000000000ff",
        kakaopay_secret_key="sk_test", billing_key_encryption_key="k",
    )
    base.update(over)
    return Settings(**base)


class PaymentsRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.authed = CurrentUser(user_id="uid-1", is_authenticated=True)
        app.dependency_overrides[get_current_user] = lambda: self.authed
        app.dependency_overrides[get_settings] = lambda: _settings()
        self.client = TestClient(app, raise_server_exceptions=False)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_ready_returns_redirect_url(self) -> None:
        with patch("app.routers.payments.billing.start_subscription",
                   return_value=ReadyResult(tid="T1", redirect_url="https://k/pay")):
            resp = self.client.post("/payments/subscribe/ready")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["redirect_url"], "https://k/pay")

    def test_ready_503_when_disabled(self) -> None:
        app.dependency_overrides[get_settings] = lambda: _settings(kakaopay_secret_key="")
        resp = self.client.post("/payments/subscribe/ready")
        self.assertEqual(resp.status_code, 503)

    def test_approve_ok(self) -> None:
        with patch("app.routers.payments.billing.approve_subscription") as m:
            resp = self.client.post("/payments/subscribe/approve?pg_token=pg_x")
        self.assertEqual(resp.status_code, 200)
        m.assert_called_once_with("uid-1", "pg_x")

    def test_approve_requires_pg_token(self) -> None:
        resp = self.client.post("/payments/subscribe/approve")
        self.assertEqual(resp.status_code, 422)

    def test_cancel_ok(self) -> None:
        with patch("app.routers.payments.billing.cancel_subscription") as m:
            resp = self.client.post("/payments/subscribe/cancel")
        self.assertEqual(resp.status_code, 200)
        m.assert_called_once_with("uid-1")

    def test_anonymous_blocked(self) -> None:
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            user_id="uid-1", is_authenticated=False
        )
        resp = self.client.post("/payments/subscribe/cancel")
        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()
