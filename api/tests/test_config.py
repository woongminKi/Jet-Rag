"""v1.5 W-0 (2026-05-18) — `app.config.Settings` 신규 필드 회귀 가드.

`deepinfra_api_token` 추가 시 기존 `Settings(...)` 직접 구성 (필드 default 없이) 가
깨지지 않는지 smoke. M0-a W-14 `stale_ingest_job_hours` 와 동일 패턴 — 필드 default
보유 → 기존 테스트 호환.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.config import Settings, get_settings


class TestSettingsBackwardCompat(unittest.TestCase):
    def test_direct_construction_without_deepinfra_token_kwarg(self) -> None:
        """기존 테스트가 `deepinfra_api_token` 없이 Settings(...) 구성해도 깨지지 않음."""
        s = Settings(
            supabase_url="",
            supabase_key="",
            supabase_service_role_key="",
            supabase_storage_bucket="documents",
            gemini_api_key="",
            hf_api_token="",
            default_user_id="00000000-0000-0000-0000-000000000001",
            doc_budget_usd=0.10,
            daily_budget_usd=0.50,
            sliding_24h_budget_usd=0.50,
            budget_krw_per_usd=1380.0,
            vision_need_score_enabled=True,
            vision_page_cap_per_doc=50,
        )
        # default "" 가 자동 적용.
        self.assertEqual(s.deepinfra_api_token, "")

    def test_direct_construction_with_deepinfra_token_kwarg(self) -> None:
        """`deepinfra_api_token` 명시 전달도 동작."""
        s = Settings(
            supabase_url="",
            supabase_key="",
            supabase_service_role_key="",
            supabase_storage_bucket="documents",
            gemini_api_key="",
            hf_api_token="",
            default_user_id="00000000-0000-0000-0000-000000000001",
            doc_budget_usd=0.10,
            daily_budget_usd=0.50,
            sliding_24h_budget_usd=0.50,
            budget_krw_per_usd=1380.0,
            vision_need_score_enabled=True,
            vision_page_cap_per_doc=50,
            deepinfra_api_token="test-token-abc",
        )
        self.assertEqual(s.deepinfra_api_token, "test-token-abc")

    def test_get_settings_reads_deepinfra_env(self) -> None:
        """`DEEPINFRA_API_TOKEN` ENV 값을 `get_settings()` 가 읽어옴."""
        get_settings.cache_clear()
        try:
            with patch.dict(os.environ, {"DEEPINFRA_API_TOKEN": "env-token-xyz"}):
                s = get_settings()
                self.assertEqual(s.deepinfra_api_token, "env-token-xyz")
        finally:
            get_settings.cache_clear()

    def test_get_settings_missing_deepinfra_env_defaults_to_empty(self) -> None:
        """ENV 미설정 시 default "" — RuntimeError 는 스크립트 진입부에서만."""
        get_settings.cache_clear()
        try:
            env = dict(os.environ)
            env.pop("DEEPINFRA_API_TOKEN", None)
            with patch.dict(os.environ, env, clear=True):
                s = get_settings()
                self.assertEqual(s.deepinfra_api_token, "")
        finally:
            get_settings.cache_clear()


class RateLimitSettingsTest(unittest.TestCase):
    """수익화 W2 — rate limit 상한 ENV parse."""

    def _clear(self) -> None:
        for k in ("JETRAG_RATE_LIMIT_ANSWERS_PER_DAY", "JETRAG_RATE_LIMIT_DOCS_PER_DAY"):
            os.environ.pop(k, None)
        get_settings.cache_clear()

    def test_defaults(self) -> None:
        self._clear()
        try:
            s = get_settings()
            self.assertEqual(s.rate_limit_answers_per_day, 50)
            self.assertEqual(s.rate_limit_docs_per_day, 30)
        finally:
            self._clear()

    def test_env_override(self) -> None:
        self._clear()
        os.environ["JETRAG_RATE_LIMIT_ANSWERS_PER_DAY"] = "5"
        os.environ["JETRAG_RATE_LIMIT_DOCS_PER_DAY"] = "3"
        try:
            s = get_settings()
            self.assertEqual(s.rate_limit_answers_per_day, 5)
            self.assertEqual(s.rate_limit_docs_per_day, 3)
        finally:
            self._clear()

    def test_zero_means_unlimited_passthrough(self) -> None:
        # 0/음수는 그대로 저장 — enforce 단계가 무제한으로 해석 (회복 토글).
        self._clear()
        os.environ["JETRAG_RATE_LIMIT_ANSWERS_PER_DAY"] = "0"
        try:
            s = get_settings()
            self.assertEqual(s.rate_limit_answers_per_day, 0)
        finally:
            self._clear()


class QuotaSettingsTest(unittest.TestCase):
    """수익화 W3 — quota enforcement 토글 parse."""

    def _clear(self) -> None:
        os.environ.pop("JETRAG_QUOTA_ENFORCEMENT_ENABLED", None)
        get_settings.cache_clear()

    def test_default_true(self) -> None:
        self._clear()
        try:
            self.assertTrue(get_settings().quota_enforcement_enabled)
        finally:
            self._clear()

    def test_env_false(self) -> None:
        self._clear()
        os.environ["JETRAG_QUOTA_ENFORCEMENT_ENABLED"] = "false"
        try:
            self.assertFalse(get_settings().quota_enforcement_enabled)
        finally:
            self._clear()


class EmailIngestSettingsTest(unittest.TestCase):
    """수익화 W4 — 이메일 인제스트 ENV parse."""

    def _clear(self) -> None:
        for k in ("JETRAG_EMAIL_WEBHOOK_SECRET", "JETRAG_EMAIL_INGEST_DOMAIN"):
            os.environ.pop(k, None)
        get_settings.cache_clear()

    def test_defaults(self) -> None:
        self._clear()
        try:
            s = get_settings()
            self.assertEqual(s.email_webhook_secret, "")
            self.assertEqual(s.email_ingest_domain, "in.woong-s.com")
        finally:
            self._clear()

    def test_env_override(self) -> None:
        self._clear()
        os.environ["JETRAG_EMAIL_WEBHOOK_SECRET"] = "s3cret"
        os.environ["JETRAG_EMAIL_INGEST_DOMAIN"] = "mail.example.com"
        try:
            s = get_settings()
            self.assertEqual(s.email_webhook_secret, "s3cret")
            self.assertEqual(s.email_ingest_domain, "mail.example.com")
        finally:
            self._clear()


class KakaoPayConfigTest(unittest.TestCase):
    """수익화 W5-6 — 카카오페이 결제 ENV 파싱."""

    _KEYS = (
        "JETRAG_PAYMENT_PROVIDER",
        "JETRAG_KAKAOPAY_SECRET_KEY",
        "JETRAG_KAKAOPAY_CID",
        "JETRAG_BILLING_KEY_ENCRYPTION_KEY",
        "JETRAG_BILLING_CRON_SECRET",
        "JETRAG_BILLING_REDIRECT_BASE",
    )

    def _clear(self) -> None:
        for key in self._KEYS:
            os.environ.pop(key, None)
        get_settings.cache_clear()

    def test_defaults(self) -> None:
        self._clear()
        try:
            s = get_settings()
            self.assertEqual(s.payment_provider, "kakaopay")
            self.assertEqual(s.kakaopay_secret_key, "")
            self.assertEqual(s.kakaopay_cid, "TCSUBSCRIP")
            self.assertEqual(s.billing_key_encryption_key, "")
            self.assertEqual(s.billing_cron_secret, "")
            self.assertEqual(s.billing_redirect_base, "https://jetrag.woong-s.com")
        finally:
            self._clear()

    def test_env_override(self) -> None:
        overrides = {
            "JETRAG_PAYMENT_PROVIDER": "toss",
            "JETRAG_KAKAOPAY_SECRET_KEY": "sk_test",
            "JETRAG_KAKAOPAY_CID": "CID_PROD_1234",
            "JETRAG_BILLING_KEY_ENCRYPTION_KEY": "fernkey",
            "JETRAG_BILLING_CRON_SECRET": "cronsec",
            "JETRAG_BILLING_REDIRECT_BASE": "https://example.test",
        }
        os.environ.update(overrides)
        get_settings.cache_clear()
        try:
            s = get_settings()
            self.assertEqual(s.payment_provider, "toss")
            self.assertEqual(s.kakaopay_secret_key, "sk_test")
            self.assertEqual(s.kakaopay_cid, "CID_PROD_1234")
            self.assertEqual(s.billing_key_encryption_key, "fernkey")
            self.assertEqual(s.billing_cron_secret, "cronsec")
            self.assertEqual(s.billing_redirect_base, "https://example.test")
        finally:
            self._clear()


if __name__ == "__main__":
    unittest.main()
