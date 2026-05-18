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


if __name__ == "__main__":
    unittest.main()
