"""Phase 1 S0 D2-D — factory pricing dict + master plan §4 정합 검증.

검증 포인트
- get_gemini_pricing 알려진/미지 모델 lookup 동작
- _GEMINI_DEFAULT_MODELS 가 master plan §4 매핑 그대로 유지
- Vision factory 의 purpose 인자 활성화 (model 인자 전달)
- JETRAG_VISION_MODEL_<PURPOSE> ENV override 동작
- 2.0-flash 단가 적용 시 estimated_cost 계산 정확

stdlib unittest only — 실 SDK 호출 없음.
"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

# import-time ENV 회피.
os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")
os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")


class _VisionEnvIsolation(unittest.TestCase):
    """JETRAG_VISION_MODEL_* ENV 가 다른 테스트로 누수되지 않도록."""

    _MANAGED_KEYS = (
        "JETRAG_LLM_PROVIDER",
        "OPENAI_API_KEY",
        "JETRAG_VISION_MODEL_PDF_ENRICH",
        "JETRAG_VISION_MODEL_IMAGE_PARSE",
        "JETRAG_VISION_MODEL_PPTX_REROUTING",
    )

    def setUp(self) -> None:
        self._saved: dict[str, str | None] = {
            k: os.environ.pop(k, None) for k in self._MANAGED_KEYS
        }

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class PricingLookupTest(unittest.TestCase):
    """get_gemini_pricing — 알려진/미지 모델 분기."""

    def test_pricing_lookup_known_model(self) -> None:
        from app.adapters.factory import get_gemini_pricing

        pricing = get_gemini_pricing("gemini-2.0-flash")
        self.assertEqual(pricing["input"], 0.10)
        self.assertEqual(pricing["output"], 0.40)
        self.assertEqual(pricing["thinking"], 0.40)

    def test_pricing_lookup_lite_model(self) -> None:
        """2.0-flash-lite 가 2.0-flash 보다 저렴한지 검증."""
        from app.adapters.factory import get_gemini_pricing

        flash = get_gemini_pricing("gemini-2.0-flash")
        lite = get_gemini_pricing("gemini-2.0-flash-lite")
        self.assertLess(lite["input"], flash["input"])
        self.assertLess(lite["output"], flash["output"])

    def test_pricing_lookup_unknown_model_fallback(self) -> None:
        """알 수 없는 모델 — default(2.0-flash) 단가 + warn 로그."""
        from app.adapters.factory import get_gemini_pricing

        with self.assertLogs("app.adapters.factory", level="WARNING") as cm:
            pricing = get_gemini_pricing("gemini-3.0-future-x")

        self.assertEqual(pricing["input"], 0.10)
        self.assertEqual(pricing["output"], 0.40)
        self.assertTrue(
            any("알 수 없는 Gemini 모델 단가" in r.getMessage() for r in cm.records),
            f"warn 로그에 'default 적용' 기대 — got {[r.getMessage() for r in cm.records]}",
        )


class DefaultModelsMasterPlanTest(unittest.TestCase):
    """_GEMINI_DEFAULT_MODELS 가 master plan §4 매핑 그대로인지 회귀 보호."""

    def test_default_models_match_master_plan(self) -> None:
        from app.adapters.factory import _GEMINI_DEFAULT_MODELS

        expected = {
            "tag": "gemini-2.0-flash-lite",
            "summary": "gemini-2.0-flash-lite",
            "answer": "gemini-2.0-flash",
            "ragas_judge": "gemini-2.0-flash",
            "decomposition": "gemini-2.0-flash-lite",
            "reasoning": "gemini-2.0-flash-thinking-exp",
            "hyde": "gemini-2.0-flash-lite",
        }
        self.assertEqual(_GEMINI_DEFAULT_MODELS, expected)

    def test_vision_default_is_2_0_flash(self) -> None:
        from app.adapters.factory import _GEMINI_VISION_DEFAULT_MODEL

        self.assertEqual(_GEMINI_VISION_DEFAULT_MODEL, "gemini-2.0-flash")


class VisionPurposeFactoryTest(_VisionEnvIsolation):
    """get_vision_captioner(purpose) — purpose 인자 + ENV override 활성화."""

    def test_vision_model_resolves_with_purpose(self) -> None:
        """purpose 인자 dead code 였던 P1-2 fix — 실제로 model 인자 전달."""
        from app.adapters import factory

        with patch("app.adapters.impl.gemini_vision.GeminiVisionCaptioner") as mock_cls:
            factory.get_vision_captioner("pdf_enrich")
        mock_cls.assert_called_once_with(model=factory._GEMINI_VISION_DEFAULT_MODEL)

    def test_vision_env_override_per_purpose(self) -> None:
        """JETRAG_VISION_MODEL_PDF_ENRICH ENV 가 default 를 덮어씀."""
        from app.adapters import factory

        os.environ["JETRAG_VISION_MODEL_PDF_ENRICH"] = "gemini-2.0-flash-lite"

        with patch("app.adapters.impl.gemini_vision.GeminiVisionCaptioner") as mock_cls:
            factory.get_vision_captioner("pdf_enrich")
        mock_cls.assert_called_once_with(model="gemini-2.0-flash-lite")

    def test_vision_env_override_does_not_affect_other_purpose(self) -> None:
        """한 purpose 의 override 가 다른 purpose 에 영향 X."""
        from app.adapters import factory

        os.environ["JETRAG_VISION_MODEL_PDF_ENRICH"] = "gemini-2.0-flash-lite"

        with patch("app.adapters.impl.gemini_vision.GeminiVisionCaptioner") as mock_cls:
            factory.get_vision_captioner("image_parse")
        # image_parse 는 default 그대로
        mock_cls.assert_called_once_with(model=factory._GEMINI_VISION_DEFAULT_MODEL)


class EstimatedCostWith2_0FlashTest(unittest.TestCase):
    """gemini_vision._parse_usage_metadata 가 2.0-flash 단가로 정확히 계산."""

    def test_estimated_cost_with_2_0_flash(self) -> None:
        from app.adapters.impl.gemini_vision import _parse_usage_metadata

        # 1M input + 1M output 으로 단순 계산 — 단가 = 0.10 + 0.40 = $0.50
        metadata = SimpleNamespace(
            prompt_token_count=1_000_000,
            candidates_token_count=1_000_000,
            thoughts_token_count=0,
            prompt_tokens_details=[],
        )
        response = SimpleNamespace(usage_metadata=metadata)

        result = _parse_usage_metadata(response, model="gemini-2.0-flash")
        assert result is not None
        # 0.10 (input) + 0.40 (output) = 0.50 USD
        self.assertAlmostEqual(result["estimated_cost"], 0.50, places=6)

    def test_estimated_cost_with_2_0_flash_lite_cheaper(self) -> None:
        """동일 토큰 수 — lite 가 일반보다 저렴."""
        from app.adapters.impl.gemini_vision import _parse_usage_metadata

        metadata = SimpleNamespace(
            prompt_token_count=1_000_000,
            candidates_token_count=1_000_000,
            thoughts_token_count=0,
            prompt_tokens_details=[],
        )
        response = SimpleNamespace(usage_metadata=metadata)

        flash = _parse_usage_metadata(response, model="gemini-2.0-flash")
        lite = _parse_usage_metadata(response, model="gemini-2.0-flash-lite")
        assert flash is not None
        assert lite is not None
        self.assertLess(lite["estimated_cost"], flash["estimated_cost"])


class GeminiLLMModelPropertyTest(unittest.TestCase):
    """GeminiLLMProvider.model property — 응답 schema 동적 표시 지원."""

    def test_model_property_returns_init_value(self) -> None:
        from app.adapters.impl.gemini_llm import GeminiLLMProvider

        provider = GeminiLLMProvider(model="gemini-2.0-flash-lite")
        self.assertEqual(provider.model, "gemini-2.0-flash-lite")

    def test_model_property_returns_default(self) -> None:
        """model 인자 미전달 시 default(2.0-flash) 반환."""
        from app.adapters.impl.gemini_llm import GeminiLLMProvider

        provider = GeminiLLMProvider()
        self.assertEqual(provider.model, "gemini-2.0-flash")


if __name__ == "__main__":
    unittest.main()
