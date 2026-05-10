"""multimodal LLM judge — vision_diagram qtype Faithfulness 한계 우회.

motivation
----------
RAGAS Faithfulness (text-only) 가 vision_diagram qtype 에서 0.0~0.5 회귀 — LLM
judge 가 vision OCR text 만 보고 diagram-based claim verify 불가.
multimodal LLM (Gemini 2.5 Flash with image) 으로 페이지 이미지 + 답변 직접
비교하여 faithfulness 평가.

설계 원칙
- **dependency injection** — `image_fetch_fn(doc_id, page) -> bytes` + `llm_call_fn(image_bytes, system, user) -> str` 콜백으로 실 구현 분리 (테스트 용이)
- **graceful** — 이미지 fetch 실패 또는 LLM 실패 시 score=None
- **cost guard** — 호출 단위 cost 추정 (Gemini multimodal Flash ~$0.001~0.005 per call)

scope
----
- 본 module: helper + parsing
- 실 image fetch (storage.get + PyMuPDF page render): 별도 sprint
- run_ragas_regression 통합 (--with-multimodal-judge flag): 별도 sprint
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class MultimodalJudgmentResult:
    """multimodal judge 결과."""

    score: float | None  # 0.0~1.0, None = 평가 실패
    reasoning: str
    n_claims: int
    n_verified: int


_SYSTEM_PROMPT = """당신은 한국어 RAG 답변 검증 전문가입니다.
주어진 페이지 이미지와 답변 텍스트를 비교하여 faithfulness 를 평가하세요.

평가 기준:
- 답변의 각 claim 이 페이지 이미지 (도표/그림/표 포함) 의 정보와 일치하는가
- 이미지 에 없는 정보는 unfaithful (claim 무관 또는 hallucination)

JSON object 만 반환 (markdown fence 금지):
{
  "n_claims": <답변에서 식별된 claim 수>,
  "n_verified": <이미지로 verify 된 claim 수>,
  "reasoning": "<평가 근거 1-2 문장>"
}

score = n_verified / n_claims (호출부에서 계산).
"""


def build_judge_prompt(*, query: str, answer: str) -> str:
    """multimodal judge user prompt — image 와 함께 LLM 에 전달."""
    return f"""사용자 query: {query!r}

답변:
{answer}

위 답변을 첨부된 페이지 이미지와 비교하여 faithfulness 평가해 JSON 으로 반환하세요."""


def parse_judgment(raw: str) -> MultimodalJudgmentResult:
    """LLM JSON → MultimodalJudgmentResult.

    score = n_verified / n_claims (n_claims=0 → None).
    markdown fence 자동 제거.
    """
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned)
    try:
        d = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"LLM JSON parse 실패: {exc}\nraw: {raw[:300]}") from exc
    if not isinstance(d, dict):
        raise RuntimeError(f"LLM 응답 dict 아님: {type(d)}")
    try:
        n_claims = int(d.get("n_claims", 0))
        n_verified = int(d.get("n_verified", 0))
    except (TypeError, ValueError):
        n_claims, n_verified = 0, 0
    n_verified = max(0, min(n_verified, n_claims))  # clamp
    reasoning = str(d.get("reasoning", "")).strip()
    score: float | None
    if n_claims <= 0:
        score = None
    else:
        score = n_verified / n_claims
    return MultimodalJudgmentResult(
        score=score,
        reasoning=reasoning,
        n_claims=n_claims,
        n_verified=n_verified,
    )


def evaluate_multimodal(
    *,
    query: str,
    answer: str,
    doc_id: str,
    page: int,
    image_fetch_fn,  # callable(doc_id, page) -> bytes (PNG/JPEG)
    llm_call_fn,     # callable(image_bytes, system_prompt, user_prompt) -> str (raw JSON)
) -> MultimodalJudgmentResult:
    """multimodal judge 메인 entry — DI 패턴.

    `image_fetch_fn(doc_id, page) -> bytes`: 페이지 이미지 (PNG/JPEG bytes)
    `llm_call_fn(image, system, user) -> str`: Gemini multimodal API call
    실패 시 score=None (graceful).
    """
    if not answer or not answer.strip():
        return MultimodalJudgmentResult(
            score=0.0, reasoning="empty answer", n_claims=0, n_verified=0
        )
    try:
        image_bytes = image_fetch_fn(doc_id, page)
    except Exception as exc:  # noqa: BLE001
        logger.warning("image fetch 실패 (doc=%s, p=%s): %s", doc_id[:8], page, exc)
        return MultimodalJudgmentResult(
            score=None, reasoning=f"image_fetch_failed: {exc!r}", n_claims=0, n_verified=0
        )
    if not image_bytes:
        return MultimodalJudgmentResult(
            score=None, reasoning="empty_image", n_claims=0, n_verified=0
        )
    user_prompt = build_judge_prompt(query=query, answer=answer)
    try:
        raw = llm_call_fn(image_bytes, _SYSTEM_PROMPT, user_prompt)
    except Exception as exc:  # noqa: BLE001
        logger.warning("multimodal LLM call 실패: %s", exc)
        return MultimodalJudgmentResult(
            score=None, reasoning=f"llm_call_failed: {exc!r}", n_claims=0, n_verified=0
        )
    try:
        return parse_judgment(raw)
    except RuntimeError as exc:
        return MultimodalJudgmentResult(
            score=None, reasoning=f"parse_failed: {exc!r}", n_claims=0, n_verified=0
        )
