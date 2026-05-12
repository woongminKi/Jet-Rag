"""BGE-reranker-v2-m3 via Hugging Face Inference Providers — cross-encoder reranker.

W25 D14+1 (S2) — 검색 성능 향상 plan §S2 ship.
RRF top-50 후보 → reranker 가 (query, chunk) cross-encoder 점수 산출 → 재정렬.

- Endpoint: `https://router.huggingface.co/hf-inference/models/BAAI/bge-reranker-v2-m3/pipeline/sentence-similarity`
- Request body: `{"inputs": {"source_sentence": <query>, "sentences": [<chunk1>, ...]}}`
- Response: `[float, ...]` — query 와 각 chunk 의 cross-encoder relevance.
  - sigmoid 미적용 raw logit 일 수 있음 (절대값 의미 X, ordering 만 유효).
- BGE-M3 패턴 재사용 (httpx + retry + transient 분류).
- LRU cache: key=(query_nfc, chunk_id), value=score — 같은 query 재호출 시 HF 호출 0.

설계 원칙:
- 한 번의 HF API 호출에 모든 candidates (≤50) 전달 — latency / 비용 최소.
- transient 실패 시 caller 가 RRF fallback 결정 (silent degradation 회피).
- chunk text 가 너무 길면 truncate (BGE-reranker max_length 512 토큰 ≈ 1500자 한국어).
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

from app.config import get_settings

logger = logging.getLogger(__name__)

# W25 D14+1 D5 — text-classification pipeline + [SEP] separator.
# 이전 (sentence-similarity pipeline) 은 cross-encoder 미작동 (W25 D14+1 E 정량 회귀
# -50% 발견). curl 직접 호출 검증으로 text-classification + "{q} [SEP] {p}" 형식이
# query-dependent ranking 정상 작동 확인. score 0~1 sigmoid 적용된 cross-encoder.
_URL = (
    "https://router.huggingface.co/hf-inference/"
    "models/BAAI/bge-reranker-v2-m3/pipeline/text-classification"
)
_MAX_ATTEMPTS = 3
_BASE_BACKOFF_SECONDS = 5.0
_REQUEST_TIMEOUT = 60.0
# 429/503 에 서버가 Retry-After 를 주면 그 값을 우선 — 단 무한 대기 방지 위해 클램프.
_MAX_RETRY_AFTER_SECONDS = 60.0
# BGE-reranker-v2-m3 max_length = 512 토큰. 한국어 1자 ≈ 1.5~2 토큰 → 보수적 1200자.
_MAX_PASSAGE_CHARS = 1200
# LRU — query 재호출 시 (페이지네이션·카드 mount 등) HF 호출 0.
# (query, chunk_id) 쌍 단위. 메모리 ~ 4096 × (str+float) ≈ 수백 KB.
_RERANK_CACHE_MAXSIZE = 4096

T = TypeVar("T")


class BGERerankerHFProvider:
    """HF Inference API 의 BGE-reranker-v2-m3 cross-encoder 호출 어댑터."""

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.hf_api_token:
            raise RuntimeError(
                "HF_API_TOKEN 이 설정되지 않았습니다. .env 에 토큰을 추가하세요."
            )
        self._headers = {"Authorization": f"Bearer {settings.hf_api_token}"}
        self._client = httpx.Client(timeout=_REQUEST_TIMEOUT)
        self._cache: OrderedDict[tuple[str, str], float] = OrderedDict()
        self._cache_lock = threading.Lock()
        # 직전 rerank 호출의 cache hit 비율 (메트릭용)
        self._last_cache_hits: int = 0
        self._last_cache_misses: int = 0

    def rerank(
        self, query: str, candidates: list[tuple[str, str]]
    ) -> list[float]:
        """query 와 candidates 의 cross-encoder relevance scores 반환.

        Args:
            query: 검색어 (NFC 정규화 권장).
            candidates: [(chunk_id, chunk_text), ...] — 순서가 응답 score 와 1:1.

        Returns:
            [score_1, score_2, ...] — candidates 와 같은 순서, 같은 길이.

        Raises:
            httpx.HTTPStatusError / httpx.HTTPError: HF API 호출 실패 (caller 가
                `is_transient_hf_error()` 로 분류 후 fallback 결정).
            RuntimeError: 응답 schema 불일치.
        """
        if not candidates:
            return []

        n = len(candidates)
        scores: list[float | None] = [None] * n
        miss_indices: list[int] = []
        miss_passages: list[str] = []

        # cache lookup
        with self._cache_lock:
            for i, (chunk_id, text) in enumerate(candidates):
                key = (query, chunk_id)
                cached = self._cache.get(key)
                if cached is not None:
                    self._cache.move_to_end(key)  # MRU
                    scores[i] = cached
                else:
                    miss_indices.append(i)
                    miss_passages.append(_truncate_passage(text))

        self._last_cache_hits = n - len(miss_indices)
        self._last_cache_misses = len(miss_indices)

        if not miss_indices:
            return [s for s in scores if s is not None]  # 전부 cache hit

        # HF API 호출 — miss 만
        miss_scores = self._call_hf(query, miss_passages)

        # cache 갱신 + scores 채우기
        with self._cache_lock:
            for idx, score in zip(miss_indices, miss_scores):
                chunk_id = candidates[idx][0]
                key = (query, chunk_id)
                self._cache[key] = score
                scores[idx] = score
                while len(self._cache) > _RERANK_CACHE_MAXSIZE:
                    self._cache.popitem(last=False)

        # type narrowing — miss 다 채워졌으니 None 없음
        return [s if s is not None else 0.0 for s in scores]

    def _call_hf(self, query: str, passages: list[str]) -> list[float]:
        """HF Inference API 호출 (retry 포함).

        W25 D14+1 D5 — text-classification + [SEP] schema:
            inputs = ["query [SEP] passage1", "query [SEP] passage2", ...]
            response = [{"label": "LABEL_0", "score": float}, ...]

        score 0~1 — cross-encoder relevance (sigmoid 적용).
        """
        inputs = [f"{query} [SEP] {p}" for p in passages]

        def call() -> list[float]:
            resp = self._client.post(
                _URL,
                headers=self._headers,
                json={"inputs": inputs},
            )
            return _parse_response(resp, expected=len(passages))

        return _with_retry(
            call, label=f"bge-reranker.rerank(n={len(passages)})"
        )

    def clear_cache(self) -> None:
        """테스트 전용 — rerank LRU 비움."""
        with self._cache_lock:
            self._cache.clear()
            self._last_cache_hits = 0
            self._last_cache_misses = 0


# ---------------------- helpers ----------------------


def _truncate_passage(text: str) -> str:
    """BGE-reranker max_length 보호 — 너무 긴 chunk 는 앞부분만 사용."""
    if len(text) <= _MAX_PASSAGE_CHARS:
        return text
    return text[:_MAX_PASSAGE_CHARS]


def _parse_response(resp: httpx.Response, *, expected: int) -> list[float]:
    """W25 D14+1 D5 — text-classification 응답 파싱.

    HF Inference API 의 text-classification pipeline 은 input 1건당:
    - 단일 label: `[{"label": "LABEL_0", "score": 0.99}]`
    - top_k>1: `[{"label": "LABEL_0", "score": 0.99}, {"label": "LABEL_1", "score": 0.01}]`

    batch input N건 응답 형식 (관찰):
    - flat list: `[{...}, {...}, ...]` (각 input 의 top-1 label 만)
    - nested: `[[{...}], [{...}], ...]` (각 input 의 결과 list)

    두 형식 모두 처리. score 만 추출 (label 무시 — single-class regression-like).
    """
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise RuntimeError(
            f"reranker 응답 형식 오류: {type(data).__name__}"
        )

    # batch size 별 응답 형식 정규화:
    # - 작은 batch (~< 10): flat list `[{label,score}, ...]` (len=N)
    # - 큰 batch (>= 10 추정): nested `[[{...}, {...}, ...]]` (outer len=1, inner len=N)
    # outer len 이 expected 와 다르고 inner list 가 expected 길이면 unwrap.
    if len(data) != expected and len(data) == 1 and isinstance(data[0], list):
        data = data[0]
    if len(data) != expected:
        raise RuntimeError(
            f"reranker 응답 길이 불일치: got={len(data)}, expect={expected}"
        )

    out: list[float] = []
    for i, item in enumerate(data):
        # flat: dict — `{"label":..., "score":...}`
        # 또는 inner list 1건 — `[{"label":..., "score":...}]`
        if isinstance(item, dict):
            score = item.get("score")
        elif isinstance(item, list) and item and isinstance(item[0], dict):
            score = item[0].get("score")
        else:
            raise RuntimeError(
                f"item[{i}] 형식 오류: {type(item).__name__}"
            )
        if not isinstance(score, (int, float)):
            raise RuntimeError(
                f"item[{i}].score 타입 불일치: {type(score).__name__}"
            )
        out.append(float(score))
    return out


# ---------------------- retry ----------------------


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
_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, _RETRYABLE_EXCEPTIONS):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS_CODES
    return False


def is_transient_reranker_error(exc: Exception) -> bool:
    """search.py 의 fallback 분기용 — bgem3_hf_embedding 와 동일 분류 정책.

    True:  transient (5xx/429/네트워크) → reranker 끄고 RRF 결과 그대로 사용
    False: 영구 실패 (4xx/응답 파싱) → 운영 알림용 — 단 검색 자체는 RRF 로 진행
           (reranker 실패가 검색 차단까지 가서는 안 됨)
    """
    return _is_retryable(exc)


# ---------------------- 싱글톤 ----------------------


@lru_cache(maxsize=1)
def get_reranker_provider() -> BGERerankerHFProvider:
    """프로세스당 단일 인스턴스 — `httpx.Client` 누수 방지 (BGE-M3 패턴 동일)."""
    return BGERerankerHFProvider()


def _parse_retry_after(exc: Exception) -> float | None:
    """HTTP 429/503 응답의 `Retry-After` 헤더를 초 단위로 파싱 (bgem3_hf_embedding 복사).

    - `httpx.HTTPStatusError` 가 아니거나 헤더 없으면 None.
    - delta-seconds 정수 우선, RFC 7231 HTTP-date 도 허용. 음수·파싱 실패는 None.
    - 상한 `_MAX_RETRY_AFTER_SECONDS` 클램프.
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
            # 서버가 Retry-After 를 주면 존중. 없으면 기존 지수 백오프 + jitter.
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
