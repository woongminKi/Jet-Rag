"""BGE-M3 via Hugging Face Inference Providers (2026 router) — `EmbeddingProvider` 구현.

- dense 1024 차원만 반환. `sparse_json` 은 빈 dict
  (HF Inference API 가 BGE-M3 의 lexical weights 를 노출하지 않아 현실적으로 확보 불가.
   hybrid 검색용 sparse 확보 방안은 W3 재검토 예정 — Postgres FTS 로 대체하는 방향 유력)
- 2026-04 시점 유효한 endpoint:
    https://router.huggingface.co/hf-inference/models/BAAI/bge-m3/pipeline/feature-extraction
  (구 endpoint `api-inference.huggingface.co/models/…` 은 404 반환)
- cold start (503 "model is loading") 1회 발생 가능 — 긴 delay 로 3회 retry
"""

from __future__ import annotations

import logging
import random
import time
from typing import Callable, TypeVar

import httpx

from app.adapters.embedding import EmbeddingResult
from app.config import get_settings

logger = logging.getLogger(__name__)

_URL = (
    "https://router.huggingface.co/hf-inference/"
    "models/BAAI/bge-m3/pipeline/feature-extraction"
)
_DENSE_DIM = 1024
_MAX_ATTEMPTS = 3
_BASE_BACKOFF_SECONDS = 5.0  # BGE-M3 cold start 5~20s 가 흔함
_REQUEST_TIMEOUT = 60.0

T = TypeVar("T")


class BGEM3HFEmbeddingProvider:
    """`EmbeddingProvider` Protocol 구현체."""

    dense_dim: int = _DENSE_DIM

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.hf_api_token:
            raise RuntimeError(
                "HF_API_TOKEN 이 설정되지 않았습니다. .env 에 토큰을 추가하세요."
            )
        self._headers = {"Authorization": f"Bearer {settings.hf_api_token}"}
        self._client = httpx.Client(timeout=_REQUEST_TIMEOUT)

    # ---------------------- public API ----------------------

    def embed(self, text: str) -> EmbeddingResult:
        def call() -> list[float]:
            resp = self._client.post(
                _URL, headers=self._headers, json={"inputs": text}
            )
            return _parse_single_response(resp)

        vec = _with_retry(call, label="bge-m3.embed")
        return EmbeddingResult(dense=vec, sparse={})

    def embed_batch(self, texts: list[str]) -> list[EmbeddingResult]:
        if not texts:
            return []

        def call() -> list[list[float]]:
            resp = self._client.post(
                _URL, headers=self._headers, json={"inputs": texts}
            )
            return _parse_batch_response(resp, expected=len(texts))

        vectors = _with_retry(
            call, label=f"bge-m3.embed_batch(n={len(texts)})"
        )
        return [EmbeddingResult(dense=v, sparse={}) for v in vectors]


# ---------------------- 응답 파싱 ----------------------


def _parse_single_response(resp: httpx.Response) -> list[float]:
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list) or not data:
        raise RuntimeError(f"예상치 못한 BGE-M3 응답: {type(data).__name__}")
    # 단일 입력 — 1D 벡터
    if isinstance(data[0], (int, float)):
        if len(data) != _DENSE_DIM:
            raise RuntimeError(
                f"차원 불일치: 받은={len(data)}, 기대={_DENSE_DIM}"
            )
        return [float(x) for x in data]
    # 드문 케이스: 배치 형태로 1개 반환
    if isinstance(data[0], list):
        return [float(x) for x in data[0]]
    raise RuntimeError(f"예상치 못한 내부 타입: {type(data[0]).__name__}")


def _parse_batch_response(
    resp: httpx.Response, *, expected: int
) -> list[list[float]]:
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list) or len(data) != expected:
        raise RuntimeError(
            f"배치 응답 길이 불일치: got={type(data).__name__}"
            f"({len(data) if isinstance(data, list) else '-'}), expect={expected}"
        )
    out: list[list[float]] = []
    for i, v in enumerate(data):
        if not isinstance(v, list) or len(v) != _DENSE_DIM:
            raise RuntimeError(
                f"item[{i}] 차원 불일치: len={len(v) if isinstance(v, list) else type(v).__name__}"
            )
        out.append([float(x) for x in v])
    return out


# ---------------------- retry ----------------------

# transient network/server 에러 — 재시도 가치 있음.
# RemoteProtocolError 가 Day 3 smoke 에서 발견된 ConnectionTerminated 의 매핑.
_RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
    httpx.ReadError,
    httpx.WriteError,
)
# HTTP 5xx + 429 (rate limit) — 서버 측 일시 문제.
_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, _RETRYABLE_EXCEPTIONS):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS_CODES
    return False


def _with_retry(fn: Callable[[], T], *, label: str) -> T:
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            # 4xx 인증/요청 오류, 응답 파싱 오류 (RuntimeError) 등 비-transient 는 즉시 실패
            if not _is_retryable(exc):
                logger.warning(
                    "%s 비-transient 실패 (attempt=%d, retry 안 함): %s",
                    label,
                    attempt,
                    exc,
                )
                break
            if attempt == _MAX_ATTEMPTS:
                break
            delay = _BASE_BACKOFF_SECONDS * (2 ** (attempt - 1)) + random.uniform(0, 1.0)
            logger.warning(
                "%s transient 실패 (attempt=%d/%d, %.1fs 후 재시도): %s",
                label,
                attempt,
                _MAX_ATTEMPTS,
                delay,
                exc,
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc
