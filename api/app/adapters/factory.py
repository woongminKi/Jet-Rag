"""LLM / Vision Provider 팩토리.

Phase 1 S0 D1 — master plan §6 어댑터 인터페이스 / §7 OpenAI 보험 인터페이스.

원칙
- 모든 호출처는 `GeminiLLMProvider()` / `GeminiVisionCaptioner()` 를 직접 import 하지 말고
  본 팩토리를 통해 받기. ENV 변수로 provider/model 1줄 전환 가능.
- 무료 티어 default = Gemini. OpenAI 는 인터페이스 + 미래 보험 (호출 코드 X, NotImplementedError 스텁).

ENV 변수
- `JETRAG_LLM_PROVIDER` (default `gemini`) — `gemini` | `openai`
  · `openai` 인데 `OPENAI_API_KEY` 미설정이면 Gemini fallback (warn log)
- `JETRAG_LLM_MODEL_<PURPOSE>` — 특정 purpose 의 모델 override
  예) `JETRAG_LLM_MODEL_TAG=gemini-2.0-flash-lite`

모델 매핑
- D1 시점은 현재 코드의 `gemini-2.5-flash` 하드코딩을 default 보존 (회귀 0).
- master plan §4 의 `gemini-2.0-flash` / `gemini-2.0-flash-lite` 변경은 D1 종료 후 별도 commit.
- TODO 주석으로 명시.
"""

from __future__ import annotations

import logging
import os
from typing import Literal

from app.adapters.llm import LLMProvider
from app.adapters.vision import VisionCaptioner

logger = logging.getLogger(__name__)

LLMPurpose = Literal[
    "tag",
    "summary",
    "answer",
    "ragas_judge",
    "decomposition",
    "reasoning",
    "hyde",
]

VisionPurpose = Literal[
    "pdf_enrich",
    "image_parse",
    "pptx_rerouting",
]

# Gemini default 모델 매핑 (master plan §4 정합 — D1 은 default 보존).
# TODO (D1 종료 후 별도 commit): master plan §4 에 맞춰 다음으로 변경
#   tag/summary/decomposition → gemini-2.0-flash-lite
#   answer/ragas_judge/hyde   → gemini-2.0-flash
#   reasoning                 → gemini-2.0-flash-thinking-exp (S3)
_GEMINI_DEFAULT_MODELS: dict[str, str] = {
    "tag": "gemini-2.5-flash",
    "summary": "gemini-2.5-flash",
    "answer": "gemini-2.5-flash",
    "ragas_judge": "gemini-2.5-flash",
    "decomposition": "gemini-2.5-flash",
    "reasoning": "gemini-2.5-flash",
    "hyde": "gemini-2.5-flash",
}

# Vision default 모델 — 현재 GeminiVisionCaptioner 의 _DEFAULT_MODEL 과 동일.
# TODO (D1 종료 후 별도 commit): master plan §4 = gemini-2.0-flash.
_GEMINI_VISION_DEFAULT_MODEL: str = "gemini-2.5-flash"

_PROVIDER_ENV_KEY = "JETRAG_LLM_PROVIDER"
_MODEL_ENV_PREFIX = "JETRAG_LLM_MODEL_"


def _resolve_provider() -> str:
    """ENV 의 provider 값 정규화 + OpenAI key 부재 시 Gemini fallback."""
    provider = os.environ.get(_PROVIDER_ENV_KEY, "gemini").strip().lower()
    if provider == "openai" and not os.environ.get("OPENAI_API_KEY"):
        logger.warning(
            "%s=openai 인데 OPENAI_API_KEY 미설정 → Gemini fallback",
            _PROVIDER_ENV_KEY,
        )
        return "gemini"
    return provider


def _resolve_llm_model(provider: str, purpose: LLMPurpose) -> str:
    """purpose 별 모델 결정 — ENV override → provider default.

    provider=gemini 외에는 default 매핑 미정 → ENV override 만 허용.
    호출자(get_llm_provider) 가 provider 분기 안에서 호출 — 미지원 provider 는
    여기 도달 전에 처리됨.
    """
    env_key = f"{_MODEL_ENV_PREFIX}{purpose.upper()}"
    override = os.environ.get(env_key)
    if override:
        return override
    if provider == "gemini":
        return _GEMINI_DEFAULT_MODELS[purpose]
    raise NotImplementedError(
        f"Provider {provider!r} 의 모델 매핑이 미구현입니다. "
        f"{env_key} ENV 로 모델을 명시하거나 provider=gemini 를 사용하세요."
    )


def get_llm_provider(purpose: LLMPurpose) -> LLMProvider:
    """purpose 에 맞는 LLMProvider 반환.

    purpose 별로 모델이 다를 수 있으므로 호출처마다 정확한 purpose 를 명시.
    Gemini default, OpenAI 는 v1.5 어댑터 ship 시점에 활성화.
    """
    provider = _resolve_provider()
    if provider == "gemini":
        model = _resolve_llm_model(provider, purpose)
        # lazy import — 단위 테스트가 google-genai 로딩 비용 회피 가능
        from app.adapters.impl.gemini_llm import GeminiLLMProvider
        return GeminiLLMProvider(model=model)
    if provider == "openai":
        raise NotImplementedError(
            "OpenAI LLM 어댑터 미구현 — v1.5 에서 추가 예정. "
            f"{_PROVIDER_ENV_KEY}=gemini 로 사용하거나 OpenAI 어댑터를 작성하세요."
        )
    raise ValueError(f"알 수 없는 provider: {provider!r}")


def get_vision_captioner(purpose: VisionPurpose) -> VisionCaptioner:
    """purpose 에 맞는 VisionCaptioner 반환.

    현재 모든 purpose 가 Gemini Vision 1종 — purpose 인자는 향후 확장
    (예: `image_parse` → 로컬 LLaVA, `pdf_enrich` → Gemini) 대비.
    """
    provider = _resolve_provider()
    if provider == "gemini":
        from app.adapters.impl.gemini_vision import GeminiVisionCaptioner
        # D1 — 현재 GeminiVisionCaptioner _DEFAULT_MODEL 보존 (회귀 0).
        # master plan §4 모델 변경은 별도 commit.
        return GeminiVisionCaptioner()
    if provider == "openai":
        raise NotImplementedError(
            "OpenAI Vision 어댑터 미구현 — v1.5 에서 추가 예정. "
            f"{_PROVIDER_ENV_KEY}=gemini 로 사용하거나 OpenAI Vision 어댑터를 작성하세요."
        )
    raise ValueError(f"알 수 없는 provider: {provider!r}")
