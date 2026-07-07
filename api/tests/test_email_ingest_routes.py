"""수익화 W4 — POST /ingest/email webhook + /me/email-ingest 라우트 테스트. 외부 I/O 0."""
from __future__ import annotations

import base64
import os
import unittest
from unittest.mock import patch

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
        email_webhook_secret="s3cret",
        email_ingest_domain="in.woong-s.com",
    )
    base.update(over)
    return Settings(**base)


_PRO = PlanLimits(code="pro", max_documents=200, answers_per_day=50)
_FREE = PlanLimits(code="free", max_documents=10, answers_per_day=5)
_ADDR = {"user_id": "uid-1", "token": "abc12345", "owner_email": "user@gmail.com"}


def _payload(**over) -> dict:
    base = {
        "to": "u-abc12345@in.woong-s.com",
        "from": "user@gmail.com",
        "subject": "보고서",
        "attachments": [
            {
                "filename": "doc.pdf",
                "content_type": "application/pdf",
                "content_base64": base64.b64encode(b"%PDF-1.4 test").decode(),
            }
        ],
    }
    base.update(over)
    return base


class EmailWebhookTest(unittest.TestCase):
    def setUp(self) -> None:
        app.dependency_overrides[get_settings] = lambda: _settings()
        self.client = TestClient(app, raise_server_exceptions=False)
        self.headers = {"X-Jetrag-Webhook-Secret": "s3cret"}

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_missing_secret_returns_401(self) -> None:
        resp = self.client.post("/ingest/email", json=_payload())
        self.assertEqual(resp.status_code, 401)

    def test_disabled_when_secret_unset_returns_503(self) -> None:
        app.dependency_overrides[get_settings] = lambda: _settings(email_webhook_secret="")
        resp = self.client.post("/ingest/email", json=_payload(), headers=self.headers)
        self.assertEqual(resp.status_code, 503)

    def test_unknown_token_ignored_with_200(self) -> None:
        with patch("app.routers.email_ingest.email_ingest.lookup_by_token", return_value=None):
            resp = self.client.post("/ingest/email", json=_payload(), headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ignored")

    def test_sender_mismatch_ignored(self) -> None:
        with patch("app.routers.email_ingest.email_ingest.lookup_by_token", return_value=_ADDR):
            resp = self.client.post(
                "/ingest/email",
                json=_payload(**{"from": "attacker@evil.com"}),
                headers=self.headers,
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ignored")

    def test_free_plan_ignored(self) -> None:
        with patch("app.routers.email_ingest.email_ingest.lookup_by_token", return_value=_ADDR), \
             patch("app.routers.email_ingest.quota.get_effective_plan", return_value=_FREE):
            resp = self.client.post("/ingest/email", json=_payload(), headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ignored")

    def test_plan_lookup_failure_fails_closed(self) -> None:
        with patch("app.routers.email_ingest.email_ingest.lookup_by_token", return_value=_ADDR), \
             patch("app.routers.email_ingest.quota.get_effective_plan", return_value=None):
            resp = self.client.post("/ingest/email", json=_payload(), headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ignored")

    def test_pro_attachment_accepted(self) -> None:
        with patch("app.routers.email_ingest.email_ingest.lookup_by_token", return_value=_ADDR), \
             patch("app.routers.email_ingest.quota.get_effective_plan", return_value=_PRO), \
             patch(
                 "app.routers.email_ingest.email_ingest.ingest_email_attachment",
                 return_value={"status": "accepted", "filename": "doc.pdf", "doc_id": "d1", "job_id": "j1"},
             ) as ing, \
             patch("app.routers.email_ingest._increment_docs_counter") as inc:
            resp = self.client.post("/ingest/email", json=_payload(), headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "processed")
        self.assertEqual(body["results"][0]["status"], "accepted")
        ing.assert_called_once()
        inc.assert_called_once()

    def test_no_attachments_ignored(self) -> None:
        with patch("app.routers.email_ingest.email_ingest.lookup_by_token", return_value=_ADDR), \
             patch("app.routers.email_ingest.quota.get_effective_plan", return_value=_PRO):
            resp = self.client.post(
                "/ingest/email", json=_payload(attachments=[]), headers=self.headers
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ignored")

    def test_invalid_to_address_ignored(self) -> None:
        resp = self.client.post(
            "/ingest/email", json=_payload(to="noreply@example.com"), headers=self.headers
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ignored")


class MeEmailIngestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.authed = CurrentUser(
            user_id="uid-1", email="user@gmail.com", is_authenticated=True
        )
        app.dependency_overrides[get_current_user] = lambda: self.authed
        app.dependency_overrides[get_settings] = lambda: _settings()
        self.client = TestClient(app, raise_server_exceptions=False)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_get_returns_address_and_plan(self) -> None:
        with patch(
            "app.routers.me.email_ingest.get_or_create_address", return_value=_ADDR
        ), patch("app.routers.me.quota.get_effective_plan", return_value=_PRO):
            resp = self.client.get("/me/email-ingest")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["address"], "u-abc12345@in.woong-s.com")
        self.assertTrue(body["pro"])

    def test_get_free_user_sees_pro_false(self) -> None:
        with patch(
            "app.routers.me.email_ingest.get_or_create_address", return_value=_ADDR
        ), patch("app.routers.me.quota.get_effective_plan", return_value=_FREE):
            resp = self.client.get("/me/email-ingest")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["pro"])

    def test_rotate_returns_new_address(self) -> None:
        new_addr = {"user_id": "uid-1", "token": "zzz99999", "owner_email": "user@gmail.com"}
        with patch(
            "app.routers.me.email_ingest.rotate_address", return_value=new_addr
        ), patch("app.routers.me.quota.get_effective_plan", return_value=_PRO):
            resp = self.client.post("/me/email-ingest/rotate")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["address"], "u-zzz99999@in.woong-s.com")

    def test_anonymous_401(self) -> None:
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            user_id="owner", is_authenticated=False
        )
        resp = self.client.get("/me/email-ingest")
        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()
