# api/tests/test_billing_cron_route.py
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")

from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app
from app.services.billing import ChargeReport, SweepReport


def _settings(**over) -> Settings:
    base = dict(
        supabase_url="https://x.supabase.co", supabase_key="", supabase_service_role_key="svc",
        supabase_storage_bucket="documents", gemini_api_key="", hf_api_token="",
        default_user_id="00000000-0000-0000-0000-000000000001",
        doc_budget_usd=0.1, daily_budget_usd=0.5, sliding_24h_budget_usd=0.5,
        budget_krw_per_usd=1380.0, vision_need_score_enabled=True, vision_page_cap_per_doc=50,
        billing_cron_secret="cron_secret_x",
        kakaopay_secret_key="sk_test", billing_key_encryption_key="k",
    )
    base.update(over)
    return Settings(**base)


class BillingCronRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        app.dependency_overrides[get_settings] = lambda: _settings()
        self.client = TestClient(app, raise_server_exceptions=False)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_run_ok_with_secret(self) -> None:
        with patch("app.routers.payments.billing.charge_due_subscriptions",
                   return_value=ChargeReport(1, 0, ["u1"], [])), \
             patch("app.routers.payments.billing.sweep_past_due",
                   return_value=SweepReport(0, [])):
            resp = self.client.post("/billing/run", headers={"X-Billing-Cron-Secret": "cron_secret_x"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"charged": 1, "failed": 0, "canceled": 0})

    def test_run_401_wrong_secret(self) -> None:
        resp = self.client.post("/billing/run", headers={"X-Billing-Cron-Secret": "wrong"})
        self.assertEqual(resp.status_code, 401)

    def test_run_503_when_secret_unset(self) -> None:
        app.dependency_overrides[get_settings] = lambda: _settings(billing_cron_secret="")
        resp = self.client.post("/billing/run", headers={"X-Billing-Cron-Secret": "x"})
        self.assertEqual(resp.status_code, 503)

    def test_run_503_when_payment_keys_unset(self) -> None:
        app.dependency_overrides[get_settings] = lambda: _settings(
            kakaopay_secret_key="", billing_key_encryption_key=""
        )
        resp = self.client.post("/billing/run", headers={"X-Billing-Cron-Secret": "cron_secret_x"})
        self.assertEqual(resp.status_code, 503)


if __name__ == "__main__":
    unittest.main()
