"""Phase 1 S0 D1 — `app.adapters.factory` 단위 테스트.

검증 포인트
- default provider = gemini (ENV 미설정)
- JETRAG_LLM_PROVIDER=openai 인데 OPENAI_API_KEY 부재 → gemini fallback
- JETRAG_LLM_MODEL_<PURPOSE> ENV override 가 default 보다 우선
- 알 수 없는 provider 는 ValueError
- provider=openai (key 있음) 은 LLM/Vision 모두 NotImplementedError

stdlib unittest + mock only — google-genai 실제 호출 없음.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

# 다른 테스트와 동일 — import 단계 ENV 체크 회피용 더미.
os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")
os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")


class _FactoryEnvIsolation(unittest.TestCase):
    """ENV 가 다른 테스트로 누수되지 않도록 setUp/tearDown 에서 cleanup."""

    _MANAGED_KEYS = (
        "JETRAG_LLM_PROVIDER",
        "JETRAG_LLM_MODEL_TAG",
        "JETRAG_LLM_MODEL_SUMMARY",
        "JETRAG_LLM_MODEL_ANSWER",
        "OPENAI_API_KEY",
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


class DefaultProviderTest(_FactoryEnvIsolation):
    def test_default_provider_gemini(self) -> None:
        from app.adapters import factory

        self.assertEqual(factory._resolve_provider(), "gemini")

    def test_default_llm_model_for_tag(self) -> None:
        from app.adapters import factory

        self.assertEqual(
            factory._resolve_llm_model("gemini", "tag"),
            factory._GEMINI_DEFAULT_MODELS["tag"],
        )


class OpenAIFallbackTest(_FactoryEnvIsolation):
    def test_openai_fallback_when_key_missing(self) -> None:
        """provider=openai 인데 OPENAI_API_KEY 부재 → gemini fallback + warn."""
        from app.adapters import factory

        os.environ["JETRAG_LLM_PROVIDER"] = "openai"
        # OPENAI_API_KEY 미설정 (setUp 에서 pop)

        with self.assertLogs("app.adapters.factory", level="WARNING") as cm:
            resolved = factory._resolve_provider()

        self.assertEqual(resolved, "gemini")
        self.assertTrue(
            any("Gemini fallback" in r.getMessage() for r in cm.records),
            f"warn 로그에 fallback 명시 기대 — got {[r.getMessage() for r in cm.records]}",
        )

    def test_openai_with_key_does_not_fallback(self) -> None:
        """provider=openai 이고 key 있으면 fallback X (그대로 openai 반환)."""
        from app.adapters import factory

        os.environ["JETRAG_LLM_PROVIDER"] = "openai"
        os.environ["OPENAI_API_KEY"] = "sk-test-dummy"

        self.assertEqual(factory._resolve_provider(), "openai")


class PurposeModelOverrideTest(_FactoryEnvIsolation):
    def test_purpose_model_override_via_env(self) -> None:
        """JETRAG_LLM_MODEL_TAG ENV 가 default 를 덮어씀."""
        from app.adapters import factory

        os.environ["JETRAG_LLM_MODEL_TAG"] = "gemini-2.0-flash-lite"
        self.assertEqual(
            factory._resolve_llm_model("gemini", "tag"),
            "gemini-2.0-flash-lite",
        )

    def test_other_purpose_unaffected_by_override(self) -> None:
        """한 purpose 의 override 가 다른 purpose 에 영향 X."""
        from app.adapters import factory

        os.environ["JETRAG_LLM_MODEL_TAG"] = "gemini-2.0-flash-lite"
        # SUMMARY 는 default 그대로
        self.assertEqual(
            factory._resolve_llm_model("gemini", "summary"),
            factory._GEMINI_DEFAULT_MODELS["summary"],
        )


class UnknownProviderTest(_FactoryEnvIsolation):
    def test_unknown_provider_raises_in_get_llm(self) -> None:
        """알 수 없는 provider — get_llm_provider 단계에서 ValueError."""
        from app.adapters import factory

        os.environ["JETRAG_LLM_PROVIDER"] = "anthropic"
        # _resolve_provider 자체는 normalize 만 함, get_llm_provider 가 ValueError raise
        with self.assertRaises(ValueError) as ctx:
            factory.get_llm_provider("tag")
        self.assertIn("anthropic", str(ctx.exception))

    def test_unknown_provider_raises_in_get_vision(self) -> None:
        from app.adapters import factory

        os.environ["JETRAG_LLM_PROVIDER"] = "anthropic"
        with self.assertRaises(ValueError):
            factory.get_vision_captioner("image_parse")


class OpenAINotImplementedTest(_FactoryEnvIsolation):
    def test_openai_llm_raises_not_implemented(self) -> None:
        """provider=openai (key 있음) 시 LLM 어댑터는 NotImplementedError."""
        from app.adapters import factory

        os.environ["JETRAG_LLM_PROVIDER"] = "openai"
        os.environ["OPENAI_API_KEY"] = "sk-test-dummy"

        with self.assertRaises(NotImplementedError) as ctx:
            factory.get_llm_provider("tag")
        self.assertIn("v1.5", str(ctx.exception))

    def test_openai_vision_raises_not_implemented(self) -> None:
        from app.adapters import factory

        os.environ["JETRAG_LLM_PROVIDER"] = "openai"
        os.environ["OPENAI_API_KEY"] = "sk-test-dummy"

        with self.assertRaises(NotImplementedError) as ctx:
            factory.get_vision_captioner("image_parse")
        self.assertIn("v1.5", str(ctx.exception))


class GeminiInstantiationTest(_FactoryEnvIsolation):
    """Gemini Provider/Captioner 인스턴스화는 model 인자 전달 검증.

    실제 google-genai client 는 lazy init 이라 import 만으로는 외부 호출 없음.
    """

    def test_get_llm_provider_passes_model(self) -> None:
        from app.adapters import factory

        os.environ["JETRAG_LLM_MODEL_ANSWER"] = "gemini-2.5-flash-test-override"

        with patch("app.adapters.impl.gemini_llm.GeminiLLMProvider") as mock_cls:
            factory.get_llm_provider("answer")
        mock_cls.assert_called_once_with(model="gemini-2.5-flash-test-override")

    def test_get_vision_captioner_returns_gemini(self) -> None:
        from app.adapters import factory

        with patch("app.adapters.impl.gemini_vision.GeminiVisionCaptioner") as mock_cls:
            factory.get_vision_captioner("image_parse")
        mock_cls.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
