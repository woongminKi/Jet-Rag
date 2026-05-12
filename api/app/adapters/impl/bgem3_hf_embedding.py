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

import email.utils
import logging
import random
import threading
import time
from collections import OrderedDict
from functools import lru_cache
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
# 429/503 에 서버가 Retry-After 를 주면 그 값을 우선 — 단 무한 대기 방지 위해 클램프.
_MAX_RETRY_AFTER_SECONDS = 60.0

# W4-Q-3 — embedding cache (in-process LRU, 의존성 0).
# 페르소나 A 일일 쿼리 ~30건 × 2주 윈도우 가정. 메모리 ≈ 512 × 1024 × 8B = 4MB.
_EMBED_CACHE_MAXSIZE = 512

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
        # W4-Q-3 — `embed_query` 전용 LRU. `OrderedDict` 의 `move_to_end` 로 MRU 갱신,
        # 초과 시 `popitem(last=False)` 로 LRU eviction. 동시성 보호 위해 Lock.
        # cache key = text 단독 (model_id `BAAI/bge-m3` 모듈 상수 — 향후 config 화 시 키에 포함 필요).
        self._embed_cache: OrderedDict[str, list[float]] = OrderedDict()
        self._embed_cache_lock = threading.Lock()
        self._embed_cache_maxsize = _EMBED_CACHE_MAXSIZE
        # 직전 `embed_query` 호출의 cache hit 여부 노출 (search.py 의 메트릭 기록용).
        # 멀티 스레드 환경에서 마지막 writer 가 덮어쓰므로 정확성은 보장 안 함 — 전체 비율은 신뢰 가능.
        self._last_cache_hit: bool = False

    # ---------------------- public API ----------------------

    def embed(self, text: str) -> EmbeddingResult:
        def call() -> list[float]:
            resp = self._client.post(
                _URL, headers=self._headers, json={"inputs": text}
            )
            return _parse_single_response(resp)

        vec = _with_retry(call, label="bge-m3.embed")
        return EmbeddingResult(dense=vec, sparse={})

    def embed_query(self, text: str) -> list[float]:
        """검색 쿼리용 단일 텍스트 → 1024 dim dense vector.

        W3 §3.A 하이브리드 검색의 dense 입력. `embed()` 와 동일 호출이지만
        sparse 미사용이라 `EmbeddingResult` 래핑을 생략, list[float] 직접 반환.
        chunks 인덱싱과 같은 모델·endpoint 사용 (검색-인덱싱 일관성).

        **W4-Q-3 LRU cache** — 동일 text 재호출 시 HF API 호출 0회.
        cache hit 여부는 `self._last_cache_hit` 로 노출 (멀티 스레드 환경에선
        전체 비율만 신뢰 가능). caller 가 vector 를 mutate 해도 cache 보존되도록
        defensive copy 반환.

        `embed()` / `embed_batch()` 는 인제스트 경로 — 동일 text 재호출 가능성 0
        이라 cache 미부착.
        """
        with self._embed_cache_lock:
            cached = self._embed_cache.get(text)
            if cached is not None:
                self._embed_cache.move_to_end(text)  # MRU 갱신
                self._last_cache_hit = True
                return list(cached)  # caller mutation 방어

        self._last_cache_hit = False
        result = self._embed_query_uncached(text)

        with self._embed_cache_lock:
            self._embed_cache[text] = list(result)
            while len(self._embed_cache) > self._embed_cache_maxsize:
                self._embed_cache.popitem(last=False)  # LRU eviction

        return result

    def _embed_query_uncached(self, text: str) -> list[float]:
        """HF API 직호출 (retry 포함) — cache miss 경로."""
        def call() -> list[float]:
            resp = self._client.post(
                _URL, headers=self._headers, json={"inputs": text}
            )
            return _parse_single_response(resp)

        return _with_retry(call, label="bge-m3.embed_query")

    def clear_embed_cache(self) -> None:
        """테스트 전용 — `embed_query` LRU 비움. 운영 코드에서 호출하지 말 것."""
        with self._embed_cache_lock:
            self._embed_cache.clear()
            self._last_cache_hit = False

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


def is_transient_hf_error(exc: Exception) -> bool:
    """search.py 의 fallback 분기 — `_is_retryable` 와 동일 분류 (외부 재사용 alias).

    True:  network/server transient (5xx, 429, connection error 등) → sparse-only fallback 허용
    False: 4xx 영구 실패 (401 토큰 만료 / 404 endpoint 변경 / 400 잘못된 request) →
           503 raise 하여 사용자에게 "검색 일시 오류" 노출. silent ranking degradation 방지.
    """
    return _is_retryable(exc)


# ---------------------- 싱글톤 헬퍼 ----------------------


@lru_cache(maxsize=1)
def get_bgem3_provider() -> BGEM3HFEmbeddingProvider:
    """프로세스당 단일 인스턴스 — `httpx.Client` 누수 방지.

    이전: search.py / ingest 스테이지가 매 호출마다 `BGEM3HFEmbeddingProvider()` 생성
        → 매번 신규 `httpx.Client` (close 안 됨) → 검색 100회 = FD leak 100건.
    이후: `lru_cache(maxsize=1)` 로 프로세스당 1개 공유. `httpx.Client` 자체가 thread-safe.
    """
    return BGEM3HFEmbeddingProvider()


def _parse_retry_after(exc: Exception) -> float | None:
    """HTTP 429/503 응답의 `Retry-After` 헤더를 초 단위로 파싱.

    - `httpx.HTTPStatusError` 가 아니거나 헤더 없으면 None (caller 가 지수 백오프).
    - delta-seconds 정수 형식 우선. RFC 7231 의 HTTP-date 형식도 허용 (현재시각과의 차).
    - 음수·파싱 실패는 None. 상한은 `_MAX_RETRY_AFTER_SECONDS` 로 클램프
      (악의적/오작동 서버의 과도한 값 방어).
    """
    if not isinstance(exc, httpx.HTTPStatusError):
        return None
    raw = exc.response.headers.get("Retry-After")
    if not raw:
        return None
    raw = raw.strip()
    try:
        seconds = float(int(raw))
    except ValueError:
        try:
            parsed = email.utils.parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        if parsed is None:
            return None
        seconds = parsed.timestamp() - time.time()
    if seconds <= 0:
        return None
    return min(seconds, _MAX_RETRY_AFTER_SECONDS)


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
            # 서버가 Retry-After 를 주면 존중 (429/503 의 cold-start·rate-limit 힌트).
            # 없으면 기존 지수 백오프 + jitter.
            retry_after = _parse_retry_after(exc)
            if retry_after is not None:
                delay = retry_after + random.uniform(0, 1.0)
            else:
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
