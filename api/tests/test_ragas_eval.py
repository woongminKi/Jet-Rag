"""W25 D14 — /answer/eval-ragas 회귀 보호.

RAGAS evaluate 자체는 LLM judge 호출 (외부 의존성) 이라 mock — 캐시 hit/miss 동작과
graceful skip (마이그 012 미적용 / 의존성 누락) 만 검증.

Phase 1 S0 D2-A — JETRAG_LLM_PROVIDER=openai 시 RagasUnavailable raise (judge
LangChain wrapper OpenAI 분기 미구현) 회귀 차단.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch


class RagasEvalProviderEnvTest(unittest.TestCase):
    """Phase 1 S0 D2-A — JETRAG_LLM_PROVIDER=openai 시 RagasUnavailable raise."""

    def setUp(self) -> None:
        self._saved_provider = os.environ.pop("JETRAG_LLM_PROVIDER", None)

    def tearDown(self) -> None:
        if self._saved_provider is not None:
            os.environ["JETRAG_LLM_PROVIDER"] = self._saved_provider
        else:
            os.environ.pop("JETRAG_LLM_PROVIDER", None)

    def test_openai_provider_raises_unavailable(self) -> None:
        """provider=openai → judge OpenAI 미구현 → RagasUnavailable graceful skip."""
        from app.services.ragas_eval import RagasUnavailable, evaluate_single

        os.environ["JETRAG_LLM_PROVIDER"] = "openai"
        with self.assertRaises(RagasUnavailable) as ctx:
            evaluate_single(query="태양계", answer="답변", contexts=["ctx1"])
        self.assertIn("OpenAI", str(ctx.exception))

    def test_unknown_provider_raises_unavailable(self) -> None:
        """provider=anthropic → 알 수 없는 provider → RagasUnavailable."""
        from app.services.ragas_eval import RagasUnavailable, evaluate_single

        os.environ["JETRAG_LLM_PROVIDER"] = "anthropic"
        with self.assertRaises(RagasUnavailable) as ctx:
            evaluate_single(query="태양계", answer="답변", contexts=["ctx1"])
        self.assertIn("anthropic", str(ctx.exception))

    def test_default_gemini_does_not_raise_at_provider_check(self) -> None:
        """default (provider 미설정) = gemini → provider 분기 통과 (RagasUnavailable 메시지 검증).

        실제 LLM judge 호출은 mock — GEMINI_API_KEY 부재 시 RagasUnavailable
        직전 단계까지 도달해야 정상. 메시지에 "OpenAI 분기 미구현" / "알 수 없는 provider"
        가 없으면 분기 통과 증명.
        """
        from app.services.ragas_eval import RagasUnavailable, evaluate_single

        os.environ.pop("JETRAG_LLM_PROVIDER", None)
        # RAGAS judge 의존성 import 단계에서 raise 되도록 ImportError mock — 분기 통과 후
        # 다음 단계인 import 에서 RagasUnavailable 이 발생.
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}, clear=False):
            with self.assertRaises(RagasUnavailable) as ctx:
                evaluate_single(query="태양계", answer="답변", contexts=["ctx1"])
            # provider 분기 메시지가 아닌 다른 단계 (의존성/key) 의 메시지여야 함.
            msg = str(ctx.exception)
            self.assertNotIn("OpenAI 분기 미구현", msg)
            self.assertNotIn("알 수 없는 provider", msg)


if __name__ == "__main__":
    unittest.main()
