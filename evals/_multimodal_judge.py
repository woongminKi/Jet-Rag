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
- 본 module: helper + parsing + 실 image_fetch_fn / llm_call_fn 구현 (2026-05-11 ship)
- run_ragas_regression 통합 (--with-multimodal-judge flag): 본 sprint 동일 ship

2026-05-11 — 실 storage 통합 (work-log 2026-05-11 참고)
- `make_image_fetcher(*, bucket, dpi=150)` — Supabase storage.get + PyMuPDF page
  render. doc_id → storage_path 조회는 supabase client 로. (doc_id, page) LRU 캐시.
- `make_llm_caller(*, model="gemini-2.5-flash")` — Gemini multimodal API. JSON mode.
  vision_usage_log 자동 기록 (source_type="multimodal_judge").
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable

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


# ---------------------------------------------------------------------------
# 실 구현 — image_fetch_fn / llm_call_fn (2026-05-11 ship)
# ---------------------------------------------------------------------------
#
# 두 함수는 `make_image_fetcher()` / `make_llm_caller()` 팩토리로 생성한다.
# 팩토리 패턴 이유:
# 1) Supabase client / Gemini client 는 첫 호출 시점 lazy init (테스트가 import
#    만 해도 외부 의존이 발동하지 않도록) — 기존 helper API 가 stdlib only 인
#    invariant 를 유지.
# 2) LRU 캐시 scope 가 팩토리 인스턴스 단위 — 한 회귀 측정 run 동안 같은 (doc_id,
#    page) 재호출 시 storage / fitz 비용 절감.

_DEFAULT_RENDER_DPI = 150  # extract.py `_SCAN_RENDER_DPI` 와 동일 (일관성)
_DEFAULT_MULTIMODAL_MODEL = "gemini-2.5-flash"


def make_image_fetcher(
    *,
    bucket: str | None = None,
    dpi: int = _DEFAULT_RENDER_DPI,
) -> Callable[[str, int], bytes]:
    """Supabase storage + PyMuPDF 기반 image_fetch_fn 팩토리.

    반환 함수: `(doc_id, page) -> PNG bytes`. page 는 1-indexed (golden v2 / chunks
    .page 컬럼 규약과 동일).

    캐싱 전략 (한 측정 run 의 cost 절감):
    - PDF bytes per doc_id: `lru_cache(maxsize=16)` — 같은 doc 의 여러 vision_diagram
      row 가 동일 storage round-trip 을 피함.
    - PNG bytes per (doc_id, page): `lru_cache(maxsize=64)` — 같은 row 가 retry 될
      때 render 비용 0.

    실패 시 RuntimeError raise — `evaluate_multimodal` 의 try/except 가 score=None
    graceful 처리.
    """
    # lazy import — 단위 테스트에서 외부 의존 없이 module import 가능하도록.
    from app.adapters.impl.supabase_storage import SupabaseBlobStorage
    from app.config import get_settings
    from app.db import get_supabase_client

    settings = get_settings()
    storage_bucket = bucket or settings.supabase_storage_bucket
    storage = SupabaseBlobStorage(bucket=storage_bucket)
    client = get_supabase_client()

    @lru_cache(maxsize=16)
    def _fetch_pdf_bytes(doc_id: str) -> bytes:
        """documents.storage_path 조회 → storage.get → PDF bytes."""
        rows = (
            client.table("documents")
            .select("storage_path")
            .eq("id", doc_id)
            .limit(1)
            .execute()
            .data
        )
        if not rows:
            raise RuntimeError(f"documents row not found: doc_id={doc_id}")
        storage_path = rows[0].get("storage_path")
        if not storage_path:
            raise RuntimeError(f"storage_path is NULL: doc_id={doc_id}")
        return storage.get(storage_path)

    @lru_cache(maxsize=64)
    def _fetch_page_png(doc_id: str, page: int) -> bytes:
        """PDF bytes → PyMuPDF page render → PNG bytes. page 는 1-indexed."""
        # fitz 는 import cost 가 있어 호출 시점에 import.
        import fitz  # PyMuPDF

        pdf_data = _fetch_pdf_bytes(doc_id)
        with fitz.open(stream=pdf_data, filetype="pdf") as fdoc:
            total_pages = len(fdoc)
            if page < 1 or page > total_pages:
                raise RuntimeError(
                    f"page out of range: doc_id={doc_id} page={page} total={total_pages}"
                )
            pix = fdoc[page - 1].get_pixmap(dpi=dpi)
            return pix.tobytes("png")

    def image_fetch_fn(doc_id: str, page: int) -> bytes:
        return _fetch_page_png(doc_id, page)

    return image_fetch_fn


def make_llm_caller(
    *,
    model: str = _DEFAULT_MULTIMODAL_MODEL,
    record_usage: bool = True,
) -> Callable[[bytes, str, str], str]:
    """Gemini multimodal API 기반 llm_call_fn 팩토리.

    반환 함수: `(image_bytes, system_prompt, user_prompt) -> raw JSON str`.
    내부적으로 `gemini_vision.py` 와 동일한 client / retry 패턴을 재사용.

    `record_usage=True` (default) 시 호출 1건마다 vision_usage_log 에
    `source_type="multimodal_judge"` row 기록 — cost 누적 추적용. 단위 테스트는
    `record_usage=False` 로 우회 가능.

    실패 시 RuntimeError raise — evaluate_multimodal 의 try/except 가 score=None
    처리. doc_id / page 정보가 필요한 vision_usage_log row 는 caller (즉
    evaluate_multimodal 외부) 에서 별도 wrap 필요 — 본 함수는 generic.
    """
    from google.genai import types

    from app.adapters.impl._gemini_common import get_client, with_retry
    from app.adapters.impl.gemini_vision import _parse_usage_metadata

    client = get_client()

    def llm_call_fn(image_bytes: bytes, system_prompt: str, user_prompt: str) -> str:
        # system prompt 는 user content 앞에 텍스트 part 로 붙여 단일 turn 으로 처리.
        # response_mime_type=application/json 으로 LLM 이 JSON 만 반환.
        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text=system_prompt),
                    types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
                    types.Part.from_text(text=user_prompt),
                ],
            ),
        ]
        config = types.GenerateContentConfig(
            temperature=0.0,  # judge → deterministic
            response_mime_type="application/json",
        )

        def call() -> object:
            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
            text = response.text
            if text is None or not text.strip():
                raise RuntimeError(f"Gemini multimodal judge 응답이 비어있습니다: {response}")
            return response

        response = with_retry(call, label="multimodal_judge")

        if record_usage:
            try:
                from app.services import vision_metrics

                usage = _parse_usage_metadata(response, model=model)
                vision_metrics.record_call(
                    success=True,
                    source_type="multimodal_judge",
                    usage=usage,
                )
            except Exception as exc:  # noqa: BLE001 — usage 기록 실패는 graceful
                logger.debug("multimodal_judge usage 기록 실패 (graceful): %s", exc)

        return response.text

    return llm_call_fn
