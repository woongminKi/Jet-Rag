"""Phase 1 S0 D2-A — `app.services.hyde` cache key 보강 검증.

이전 (D2-A 전) cache key = query only → 모델 변경 시 같은 query 가 stale 결과 반환.
보강 후 key = (model_id, query) — 모델 격리 보장.

stdlib unittest only — LLM 실 호출 없음 (Provider mock).
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock

# import 단계 ENV 회피용 더미.
os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")
os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")


class _FakeLLM:
    """LLMProvider 모킹 — `_model` 속성 노출 + complete 결정적 응답."""

    def __init__(self, *, model: str, response: str = "hyp body 1") -> None:
        self._model = model
        self._calls = 0
        self._response = response

    def complete(self, messages, *, temperature=0.2, **_kwargs) -> str:  # noqa: ARG002
        self._calls += 1
        return self._response


class HydeCacheKeyTest(unittest.TestCase):
    def setUp(self) -> None:
        from app.services.hyde import clear_cache

        clear_cache()

    def test_same_query_different_models_separate_cache(self) -> None:
        """다른 model_id 면 같은 query 라도 LLM 호출이 각각 발생."""
        from app.services.hyde import generate_hypothetical_doc

        llm_a = _FakeLLM(model="gemini-2.5-flash", response="A 가상 본문")
        llm_b = _FakeLLM(model="gemini-2.0-flash", response="B 가상 본문")

        result_a = generate_hypothetical_doc(llm_a, "태양계")
        result_b = generate_hypothetical_doc(llm_b, "태양계")

        self.assertEqual(result_a, "A 가상 본문")
        self.assertEqual(result_b, "B 가상 본문")
        # 각 모델별 1회씩 호출
        self.assertEqual(llm_a._calls, 1)
        self.assertEqual(llm_b._calls, 1)

    def test_same_model_same_query_cached(self) -> None:
        """같은 model_id + 같은 query 면 두 번째부터 cache hit (LLM 호출 0)."""
        from app.services.hyde import generate_hypothetical_doc

        llm = _FakeLLM(model="gemini-2.5-flash", response="cached body")
        generate_hypothetical_doc(llm, "태양계")
        generate_hypothetical_doc(llm, "태양계")
        generate_hypothetical_doc(llm, "태양계")

        # 첫 호출만 LLM 호출
        self.assertEqual(llm._calls, 1)

    def test_provider_without_model_attr_uses_classname(self) -> None:
        """`_model` 속성 없는 provider 도 cache 분리 (클래스명 fallback)."""
        from app.services.hyde import generate_hypothetical_doc

        class _NoModelLLM:
            def complete(self, messages, *, temperature=0.2, **_kwargs):  # noqa: ARG002
                return "no-model body"

        llm1 = _NoModelLLM()
        llm2 = _NoModelLLM()
        result1 = generate_hypothetical_doc(llm1, "x")
        result2 = generate_hypothetical_doc(llm2, "x")
        # 같은 클래스라 cache hit (둘 다 같은 결과)
        self.assertEqual(result1, "no-model body")
        self.assertEqual(result2, "no-model body")


if __name__ == "__main__":
    unittest.main()
