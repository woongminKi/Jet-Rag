"""S2 D3 (2026-05-09) — `app.services.ingest_mode.resolve_page_cap` 단위 테스트.

master plan §6 S2 D3. 사용자 결정 Q-S2-1b (모드 매핑) + Q-S2-1e (A 안 우선순위).

T-B-01: fast → JETRAG_VISION_MODE_FAST_CAP (default 10)
T-B-02: default → settings.vision_page_cap_per_doc 그대로
T-B-03: precise → 0 (무한)
T-B-07: 전역 kill switch (settings.vision_page_cap_per_doc <= 0) → mode 무관 항상 0
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.config import Settings
from app.services.ingest_mode import (
    INGEST_MODES,
    IngestMode,
    resolve_page_cap,
)


def _settings(page_cap: int) -> Settings:
    """단위 테스트용 Settings — vision_page_cap_per_doc 만 의미 있음."""
    return Settings(
        supabase_url="", supabase_key="", supabase_service_role_key="",
        supabase_storage_bucket="documents", gemini_api_key="", hf_api_token="",
        default_user_id="00000000-0000-0000-0000-000000000001",
        doc_budget_usd=0.10, daily_budget_usd=0.50,
        sliding_24h_budget_usd=0.50, budget_krw_per_usd=1380.0,
        vision_need_score_enabled=True,
        vision_page_cap_per_doc=page_cap,
    )


class TestResolvePageCap(unittest.TestCase):
    def test_fast_mode_uses_env_default_10(self) -> None:
        """T-B-01 — fast → JETRAG_VISION_MODE_FAST_CAP 미지정 시 default 10."""
        # 환경변수 제거 후 default 검증
        prev = os.environ.pop("JETRAG_VISION_MODE_FAST_CAP", None)
        try:
            self.assertEqual(resolve_page_cap("fast", _settings(50)), 10)
        finally:
            if prev is not None:
                os.environ["JETRAG_VISION_MODE_FAST_CAP"] = prev

    def test_fast_mode_env_override(self) -> None:
        """T-B-01b — JETRAG_VISION_MODE_FAST_CAP=15 설정 시 그 값 사용."""
        with patch.dict(os.environ, {"JETRAG_VISION_MODE_FAST_CAP": "15"}):
            self.assertEqual(resolve_page_cap("fast", _settings(50)), 15)

    def test_default_mode_uses_settings_value(self) -> None:
        """T-B-02 — default → settings.vision_page_cap_per_doc 그대로."""
        self.assertEqual(resolve_page_cap("default", _settings(50)), 50)
        self.assertEqual(resolve_page_cap("default", _settings(20)), 20)

    def test_precise_mode_returns_zero_unlimited(self) -> None:
        """T-B-03 — precise → 0 (페이지 무한, 비용 한도까지)."""
        self.assertEqual(resolve_page_cap("precise", _settings(50)), 0)
        self.assertEqual(resolve_page_cap("precise", _settings(10)), 0)

    def test_kill_switch_overrides_all_modes(self) -> None:
        """T-B-07 — settings.vision_page_cap_per_doc <= 0 → 모든 mode 가 0.

        Q-S2-1e A 안: ENV `JETRAG_VISION_PAGE_CAP_PER_DOC=0` 일 때만 ENV 가
        전역 kill switch (mode 무관 무한).
        """
        for mode in INGEST_MODES:
            self.assertEqual(resolve_page_cap(mode, _settings(0)), 0)
            self.assertEqual(resolve_page_cap(mode, _settings(-1)), 0)

    def test_unknown_mode_raises_value_error(self) -> None:
        """범위 밖 mode 는 ValueError (router 가 이전에 400 으로 잡지만 이중 방어)."""
        with self.assertRaises(ValueError):
            resolve_page_cap("invalid", _settings(50))  # type: ignore[arg-type]

    def test_invalid_env_falls_back_to_default(self) -> None:
        """JETRAG_VISION_MODE_FAST_CAP=abc → default 10 fallback (graceful)."""
        with patch.dict(os.environ, {"JETRAG_VISION_MODE_FAST_CAP": "abc"}):
            self.assertEqual(resolve_page_cap("fast", _settings(50)), 10)

    def test_ingest_modes_constant_has_expected_set(self) -> None:
        """INGEST_MODES 상수 회귀 보호 — 새 mode 추가 시 의식적 변경 강제."""
        self.assertEqual(set(INGEST_MODES), {"fast", "default", "precise"})


if __name__ == "__main__":
    unittest.main()
