"""Gemini 어댑터 공통 헬퍼.

`gemini_llm.py` (텍스트) · `gemini_vision.py` (이미지) 가 모두 사용하는 client lazy init +
retry 패턴을 한 곳에 모은다. 어댑터 6종 분리 (DE-19) 시 두 어댑터의 공통 인프라.
"""

from __future__ import annotations

import logging
import os
import random
import time
from functools import lru_cache
from typing import Callable, TypeVar

from google import genai

from app.config import get_settings

logger = logging.getLogger(__name__)

# W25 D14 Sprint 4 (2026-05-05) 시도 후 원복:
#   강화 (5회/2.0s base, max delay 32s) 적용 후 reingest 실패 — HTTP/2 ConnectionTerminated
#   (long sleep 중 connection idle timeout). Sprint 5 (sweep 만 유지) 로 전환.
# 2026-05-06 D2-C — master plan §7.3 정합: default 3 → 1.
#   sweep × retry 곱셈 제거 (sweep 2 × retry 1 = worst case 페이지당 2 호출, 50p PDF
#   기준 450 → 100 으로 4.5x 절감). 503 random 은 sweep 2 가 페이지 단위로 재시도
#   보장 → retry 1 로 충분. 회귀 발생 시 ENV `JETRAG_GEMINI_RETRY=3` 으로 즉시 회복.
_MAX_ATTEMPTS = int(os.environ.get("JETRAG_GEMINI_RETRY", "1"))
_BASE_BACKOFF_SECONDS = 1.0

T = TypeVar("T")


@lru_cache
def get_client() -> genai.Client:
    settings = get_settings()
    if not settings.gemini_api_key:
        raise RuntimeError(
            "GEMINI_API_KEY 가 설정되지 않았습니다. .env 를 확인하세요."
        )
    return genai.Client(api_key=settings.gemini_api_key)


def with_retry(
    fn: Callable[[], T],
    *,
    label: str,
    max_attempts: int = _MAX_ATTEMPTS,
) -> T:
    """3회 retry + 지수 백오프 (§10.10). 마지막 실패 시 예외 그대로 raise."""
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — 외부 API 호출 실패 흡수
            last_exc = exc
            if attempt == max_attempts:
                break
            delay = _BASE_BACKOFF_SECONDS * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
            logger.warning(
                "%s 실패(attempt=%d/%d, delay=%.1fs): %s",
                label,
                attempt,
                max_attempts,
                delay,
                exc,
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc
