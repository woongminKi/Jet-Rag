"""LLM / Vision Provider 팩토리.

Phase 1 S0 D1 — master plan §6 어댑터 인터페이스 / §7 OpenAI 보험 인터페이스.
Phase 1 S0 D2-D — master plan §4 모델 매핑 정합 + 단가 테이블 분리.

원칙
- 모든 호출처는 `GeminiLLMProvider()` / `GeminiVisionCaptioner()` 를 직접 import 하지 말고
  본 팩토리를 통해 받기. ENV 변수로 provider/model 1줄 전환 가능.
- 무료 티어 default = Gemini. OpenAI 는 인터페이스 + 미래 보험 (호출 코드 X, NotImplementedError 스텁).

ENV 변수
- `JETRAG_LLM_PROVIDER` (default `gemini`) — `gemini` | `openai`
  · `openai` 인데 `OPENAI_API_KEY` 미설정이면 Gemini fallback (warn log)
- `JETRAG_LLM_MODEL_<PURPOSE>` — 특정 purpose 의 모델 override
  예) `JETRAG_LLM_MODEL_TAG=gemini-2.5-flash-lite`
- `JETRAG_VISION_MODEL_<PURPOSE>` — Vision purpose 별 모델 override (D2-D 신규)
  예) `JETRAG_VISION_MODEL_PDF_ENRICH=gemini-2.5-flash`

모델 매핑 (D2-D 정정 — 2.0 계열 deprecated 후 2.5 계열로 회복)
- tag/summary/decomposition/hyde → `gemini-2.5-flash-lite` (저렴 + 동작 검증 OK)
- answer/ragas_judge             → `gemini-2.5-flash` (안정, 동작 검증 OK)
- reasoning                      → `gemini-2.5-flash` (thinking-exp 미검증, 안전 모델 사용)
- vision (모든 purpose 공통)     → `gemini-2.5-flash` (시각 추론 정확도 우선)

D2-D 회복 사유: `gemini-2.0-flash` 가 신규 사용자에게 deprecated (404 NOT_FOUND).
2.5-flash 와 2.5-flash-lite 만 호출 가능 검증됨 (2026-05-06 실측).

단가 (USD per 1M tokens) — 2026년 1월 Gemini 공식 가격 기준.
- `_GEMINI_PRICING` dict + `get_gemini_pricing(model)` lookup.
- 알 수 없는 모델은 보수적으로 `gemini-2.5-flash` default 단가 + warn log.
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
    "synonym",
]

VisionPurpose = Literal[
    "pdf_enrich",
    "image_parse",
    "pptx_rerouting",
]

# Gemini default 모델 매핑 — D2-D 정정 (2.0 계열 deprecated, 2.5 계열로 회복).
# - tag/summary/decomposition/hyde: 짧은 입출력, 비용 최소화 → 2.5-flash-lite
# - answer/ragas_judge: 사용자 액션·평가 정확도 우선 → 2.5-flash (이전 default)
# - reasoning: thinking-exp 검증 안 됨 → 안전 모델 (2.5-flash)
_GEMINI_DEFAULT_MODELS: dict[LLMPurpose, str] = {
    "tag": "gemini-2.5-flash-lite",
    "summary": "gemini-2.5-flash-lite",
    "answer": "gemini-2.5-flash",
    "ragas_judge": "gemini-2.5-flash",
    "decomposition": "gemini-2.5-flash-lite",
    "reasoning": "gemini-2.5-flash",
    "hyde": "gemini-2.5-flash-lite",
    # M1 W-2 (S4-D) — 인제스트 단계 동의어 후보 생성. 짧은 입출력 → flash-lite.
    # ENV `JETRAG_LLM_MODEL_SYNONYM` 으로 override (기존 `_MODEL_ENV_PREFIX` 패턴 자동 처리).
    "synonym": "gemini-2.5-flash-lite",
}

# Vision default 모델 — 2.5-flash (이전 default, 동작 검증 OK).
# 2.0-flash 는 신규 사용자 deprecated.
_GEMINI_VISION_DEFAULT_MODEL: str = "gemini-2.5-flash"

# 단가 (USD per 1M tokens) — 2026년 1월 Gemini 공식 가격.
# input/output 분리, thinking 토큰은 output 단가 적용 (Gemini 정책).
# 가격 변경 시 본 dict 만 갱신하면 모든 호출처 자동 적용.
_GEMINI_PRICING: dict[str, dict[str, float]] = {
    # 현재 default — 동작 검증 OK
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50, "thinking": 2.50},
    "gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40, "thinking": 0.40},
    # 2.0 계열 — 신규 사용자 deprecated, ENV override 시만 사용
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40, "thinking": 0.40},
    "gemini-2.0-flash-lite": {"input": 0.075, "output": 0.30, "thinking": 0.30},
    "gemini-2.0-flash-thinking-exp": {"input": 0.10, "output": 0.40, "thinking": 0.40},
}

_PRICING_FALLBACK_MODEL = "gemini-2.5-flash"

_PROVIDER_ENV_KEY = "JETRAG_LLM_PROVIDER"
_MODEL_ENV_PREFIX = "JETRAG_LLM_MODEL_"
_VISION_MODEL_ENV_PREFIX = "JETRAG_VISION_MODEL_"


def get_gemini_pricing(model: str) -> dict[str, float]:
    """모델 단가 lookup — 알 수 없는 모델은 안전한 default + warn.

    `vision_usage_log.estimated_cost` 등 비용 집계가 모델 갱신 시 자동 정합되도록
    하드코딩 회피. 호출처는 dict 의 input/output/thinking 키만 사용.
    """
    if model not in _GEMINI_PRICING:
        logger.warning(
            "알 수 없는 Gemini 모델 단가 — default %s 적용: %s",
            _PRICING_FALLBACK_MODEL,
            model,
        )
        return _GEMINI_PRICING[_PRICING_FALLBACK_MODEL]
    return _GEMINI_PRICING[model]


def _resolve_provider() -> str:
    """ENV 의 provider 값 정규화 + OpenAI key 부재 시 Gemini fallback.

    알 수 없는 provider 는 그대로 반환해 호출 시점 (`get_llm_provider`) 에서
    명확한 ValueError 로 차단한다. import-time 안전성은 호출처 lazy 화로 확보
    (extract.py 등은 `_get_image_parser()` 함수 패턴 사용 — P1-1).
    """
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


def _resolve_vision_model(provider: str, purpose: VisionPurpose) -> str:
    """Vision purpose 별 모델 결정 — ENV override → provider default.

    D2-D 정정 — 현재는 모든 purpose 가 동일 모델(`gemini-2.5-flash`).
    향후 `image_parse` 를 lite 로 내리거나 `pdf_enrich` 만 thinking 으로 올리는
    분기를 도입할 때 본 함수만 갱신.
    """
    env_key = f"{_VISION_MODEL_ENV_PREFIX}{purpose.upper()}"
    override = os.environ.get(env_key)
    if override:
        return override
    if provider == "gemini":
        return _GEMINI_VISION_DEFAULT_MODEL
    raise NotImplementedError(
        f"Provider {provider!r} 의 Vision 모델 매핑이 미구현입니다. "
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

    D2-D — purpose 인자가 활성화돼 ENV (`JETRAG_VISION_MODEL_<PURPOSE>`) 로
    purpose 별 모델 override 가능. provider 분기는 LLM 과 동일.
    """
    provider = _resolve_provider()
    if provider == "gemini":
        model = _resolve_vision_model(provider, purpose)
        from app.adapters.impl.gemini_vision import GeminiVisionCaptioner
        return GeminiVisionCaptioner(model=model)
    if provider == "openai":
        raise NotImplementedError(
            "OpenAI Vision 어댑터 미구현 — v1.5 에서 추가 예정. "
            f"{_PROVIDER_ENV_KEY}=gemini 로 사용하거나 OpenAI Vision 어댑터를 작성하세요."
        )
    raise ValueError(f"알 수 없는 provider: {provider!r}")
