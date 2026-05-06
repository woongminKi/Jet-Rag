"""Gemini 기반 `VisionCaptioner` 구현체 (master plan §4 — 2.0-flash default).

기획서 §10.4 의 단일 호출 4필드 JSON 계약을 그대로 구현. 호출자는 정규화된 bytes 를
넘기는 책임 (`ImageParser` 가 다운스케일·EXIF transpose 담당, QA 검수 C-2). 본 구현은:

- google-genai SDK `inline_data` 로 이미지 1장 전달
- response_mime_type=application/json + `_PROMPT` 로 4필드 보장
- 3회 retry + 지수 백오프 (`_gemini_common.with_retry`)
- 파싱 시 type 필드는 화이트리스트, 외 케이스는 보수적으로 "기타" 분류

HEIC/HEIF 는 Gemini 가 직접 지원 (DE-17) — mime_type 만 정확히 전달하면 됨.

D2-D — 단가는 `factory.get_gemini_pricing(model)` 로 위임 → 모델 변경 시
estimated_cost 자동 정합.
"""

from __future__ import annotations

import json
import logging

from google.genai import types

from app.adapters.factory import get_gemini_pricing
from app.adapters.impl._gemini_common import get_client, with_retry
from app.adapters.vision import VisionCaption, VisionCategory

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "gemini-2.5-flash"

# 기획서 §10.4 의 단일 호출 JSON 프롬프트.
# - type 은 8종 화이트리스트 (애매하면 "기타")
# - structured 는 type 별 다른 schema:
#   · 명함 → name/title/contact
#   · 차트 → axis/series/values
#   · 표 → headers/rows
#   · 화이트보드 → action_items (W13 Day 1 — US-07 회수)
#   · 그 외 → null
# - 한국어 출력 강제
_PROMPT = """\
당신은 이미지에서 정보를 정확하게 추출하는 분석가입니다.
다음 JSON 스키마에 정확히 맞춰 한국어로 응답하세요. 다른 텍스트 없이 JSON 만 출력합니다.

{
  "type": "문서|스크린샷|메신저대화|화이트보드|명함|차트|표|기타 중 하나 (애매하면 기타)",
  "ocr_text": "이미지의 모든 텍스트를 위→아래·좌→우 순서로. 텍스트가 없으면 빈 문자열",
  "caption": "이미지의 한국어 한 문장 요약 (≤ 80자, 끝에 마침표 없이)",
  "structured": "type 별 구조화 객체 — 명함: {name, title, contact}, 차트: {axis, series, values}, 표: {headers, rows}, 화이트보드: {action_items: [\"항목1\", \"항목2\", ...]} (담당자·기한 명시 시 그대로 보존). 구조화 불가 시 null"
}
"""

_VALID_TYPES: set[VisionCategory] = {
    "문서", "스크린샷", "메신저대화", "화이트보드", "명함", "차트", "표", "기타",
}

# D2-D — 단가는 `factory._GEMINI_PRICING` dict 가 단일 출처.
# `get_gemini_pricing(model)` 로 input/output/thinking 단가 lookup.


def _estimate_cost(
    *,
    model: str,
    prompt_tokens: int,
    output_tokens: int,
    thinking_tokens: int,
) -> float:
    """모델별 단가 × 토큰. image_tokens 는 prompt_tokens 에 합산 (Gemini 정책).

    thinking 토큰은 별도 단가 dict 키 사용 — 일반 flash 는 output 과 동일하지만
    thinking-exp 등 향후 분리 단가 도입 시 자동 정합.
    """
    pricing = get_gemini_pricing(model)
    return (
        prompt_tokens * pricing["input"]
        + output_tokens * pricing["output"]
        + thinking_tokens * pricing["thinking"]
    ) / 1_000_000


# Gemini SDK ModalityTokenCount.modality 의 IMAGE 식별자 (대문자 정규화 비교).
# 실측 (google-genai 1.x): MediaModality 는 enum (TEXT/IMAGE/VIDEO/AUDIO/DOCUMENT/MODALITY_UNSPECIFIED).
_MODALITY_IMAGE = "IMAGE"


def _extract_image_tokens(metadata: object) -> int | None:
    """`prompt_tokens_details: list[ModalityTokenCount]` 에서 IMAGE modality 토큰 합산.

    SDK 버전 / 응답 형태에 따라 필드 부재 가능 → 안전 처리.
    값이 0 이거나 details 자체가 없으면 None (NULL 컬럼) 반환.
    """
    details = getattr(metadata, "prompt_tokens_details", None) or []
    total = 0
    for d in details:
        modality = getattr(d, "modality", None)
        # MediaModality enum 또는 str 둘 다 대응 — `str(enum)` 은 'MediaModality.IMAGE' 라
        # name/value 로 비교. enum 값이 'IMAGE' 문자열과 동일.
        modality_str = (
            getattr(modality, "name", None)
            or getattr(modality, "value", None)
            or (str(modality) if modality is not None else "")
        )
        if str(modality_str).upper().endswith(_MODALITY_IMAGE):
            total += int(getattr(d, "token_count", 0) or 0)
    return total or None


def _parse_usage_metadata(response: object, *, model: str) -> dict | None:
    """Gemini response.usage_metadata → record_call usage dict.

    SDK 버전에 따라 필드 부재 가능 → getattr default 0 으로 안전 처리.
    metadata 자체가 없으면 None 반환 (record_call 가 모든 컬럼 NULL 처리).

    image_tokens (P1-3 보강):
        Gemini SDK 의 `prompt_tokens_details: list[ModalityTokenCount]` 가
        IMAGE/TEXT modality 분리 제공 → IMAGE 합산만 별도 컬럼에 저장.
        `prompt_tokens` 는 SDK 의 `prompt_token_count` 그대로 (텍스트+이미지 합산).
        image_tokens 는 정보용 — 단가 계산은 prompt_tokens 단일 단가 적용.
    """
    metadata = getattr(response, "usage_metadata", None)
    if metadata is None:
        return None
    prompt_tokens = int(getattr(metadata, "prompt_token_count", 0) or 0)
    output_tokens = int(getattr(metadata, "candidates_token_count", 0) or 0)
    thinking_tokens = int(getattr(metadata, "thoughts_token_count", 0) or 0)
    image_tokens = _extract_image_tokens(metadata)
    return {
        "prompt_tokens": prompt_tokens,
        "image_tokens": image_tokens,
        "output_tokens": output_tokens,
        "thinking_tokens": thinking_tokens,
        "estimated_cost": _estimate_cost(
            model=model,
            prompt_tokens=prompt_tokens,
            output_tokens=output_tokens,
            thinking_tokens=thinking_tokens,
        ),
        "model_used": model,
    }


class GeminiVisionCaptioner:
    def __init__(self, *, model: str = _DEFAULT_MODEL) -> None:
        self._model = model

    def caption(self, image_bytes: bytes, *, mime_type: str) -> VisionCaption:
        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                    types.Part.from_text(text=_PROMPT),
                ],
            ),
        ]
        config = types.GenerateContentConfig(
            temperature=0.2,
            response_mime_type="application/json",
        )

        # P2 — retry_attempt 추적: 성공 시점의 attempt 수를 record_call 로 전파.
        # exception 시 마지막 attempt 수를 exc 속성에 첨부 (image_parser 가 읽어 record).
        attempts_used = [0]

        def call() -> object:
            attempts_used[0] += 1
            response = get_client().models.generate_content(
                model=self._model,
                contents=contents,
                config=config,
            )
            text = response.text
            if text is None or not text.strip():
                raise RuntimeError(f"Gemini Vision 응답이 비어있습니다: {response}")
            return response

        try:
            response = with_retry(call, label="gemini.vision.caption")
        except Exception as exc:
            # private 속성 — image_parser fail path 가 record_call retry_attempt 전달.
            exc._jetrag_retry_attempt = attempts_used[0]  # type: ignore[attr-defined]
            raise

        return self._parse(
            response.text,
            response=response,
            model=self._model,
            retry_attempt=attempts_used[0],
        )

    @staticmethod
    def _parse(
        text: str,
        *,
        response: object | None = None,
        model: str | None = None,
        retry_attempt: int | None = None,
    ) -> VisionCaption:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Gemini Vision JSON 파싱 실패: {exc}; 응답 앞 200자: {text[:200]!r}"
            ) from exc

        if not isinstance(data, dict):
            raise RuntimeError(
                f"Gemini Vision 응답이 dict 가 아닙니다: {type(data).__name__}; 앞 200자: {text[:200]!r}"
            )

        type_raw = data.get("type")
        type_: VisionCategory
        if type_raw in _VALID_TYPES:
            type_ = type_raw  # type: ignore[assignment]
        else:
            logger.warning("Gemini Vision type 화이트리스트 외 값 → '기타' 강제: %r", type_raw)
            type_ = "기타"

        ocr_text = data.get("ocr_text") or ""
        if not isinstance(ocr_text, str):
            ocr_text = str(ocr_text)

        caption_text = data.get("caption") or ""
        if not isinstance(caption_text, str):
            caption_text = str(caption_text)

        structured = data.get("structured")
        if not isinstance(structured, dict) or not structured:
            structured = None

        # Phase 1 S0 D1 — usage_metadata 파싱 (response 인자 옵션, 미전달 시 None).
        # P2 — retry_attempt 키 추가 (success path).
        usage = (
            _parse_usage_metadata(response, model=model or _DEFAULT_MODEL)
            if response is not None
            else None
        )
        if usage is not None and retry_attempt is not None:
            usage["retry_attempt"] = retry_attempt

        return VisionCaption(
            type=type_,
            ocr_text=ocr_text,
            caption=caption_text,
            structured=structured,
            usage=usage,
        )
