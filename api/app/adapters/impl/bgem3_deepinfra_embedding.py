"""BGE-M3 via DeepInfra OpenAI-compatible API — `EmbeddingProvider` 구현 (v1.5 W-1).

배경
----
HF Inference Providers 의 free-tier scale-to-zero cold-start (~30s) + 가끔의 503 이
KPI #10 (P95 < 3s) 게이트의 위험 요인. DeepInfra BGE-M3 는 always-warm + $0.01/M
token (페르소나 트래픽 기준 <$1/월) — 동일 모델이라 검색 회귀 0 (W-0 결정성 시험
n=100 min cosine 0.999984 PASS, ≥ 0.999 게이트).

설계
----
- HF 어댑터(`bgem3_hf_embedding`) 와 동일 시그니처·동일 캐시 동작 — `EmbeddingProvider`
  Protocol 충족. 호출 사이트 8개 무변경 (factory 분기로 swap).
- API spec: OpenAI-compatible `/v1/openai/embeddings`. Body `{"model": "BAAI/bge-m3",
  "input": text or [texts]}` → Resp `{"data": [{"embedding": [...]}, ...]}`.
- 인증: `Authorization: Bearer <DEEPINFRA_API_TOKEN>`. token 부재 시 init 단계 RuntimeError.
- retry 분류 (4xx 즉시 실패 / 5xx·429 backoff) 는 HF 어댑터와 동일 정책 — `is_transient_*`
  alias 도 동일 의미. search.py 의 fallback 분기가 의도대로 작동.
- embed_query 2단 캐시 (in-process LRU 512 + DB `embed_query_cache`) 도 동일 — 영구
  캐시는 model_id `BAAI/bge-m3` 동일이라 HF↔DeepInfra 간 entry 호환 (W-0 cosine
  0.999984 ≥ 0.999 PASS 로 swap 안전 확정).

스펙 출처
---------
- `evals/run_v1_5_w0_determinism.py` line 22~80 (API spec reference)
- `work-log/2026-05-15 HF self-host 검토 — v1.5 sprint 설계.md` §3.1
- `work-log/2026-05-18 배포 방법 검토 — Railway + HuggingFace.md` §15.1 (W-0 결과)
"""

from __future__ import annotations

import email.utils
import hashlib
import logging
import random
import threading
import time
import unicodedata
from collections import OrderedDict
from typing import Callable, TypeVar

import httpx

from app.adapters.embedding import EmbeddingResult
from app.config import get_settings

logger = logging.getLogger(__name__)

# 모델 슬러그 — HF 어댑터와 동일. embed_query_cache 의 _MODEL_ID 와도 일치해야
# 영구 캐시 entry 가 HF↔DeepInfra 간 호환 (W-0 cosine 0.999984 ≥ 0.999 PASS).
_MODEL_SLUG = "BAAI/bge-m3"
_URL = "https://api.deepinfra.com/v1/openai/embeddings"
_DENSE_DIM = 1024

# always-warm 이라 cold-start 가 없어 HF 보다 짧은 backoff 가능하지만, transient 5xx
# 는 여전히 발생 가능 — HF 와 같은 정책 유지 (운영 일관성). _MAX_RETRY_AFTER_SECONDS
# 도 동일 (악의적 헤더 방어).
_MAX_ATTEMPTS = 3
_BASE_BACKOFF_SECONDS = 5.0
_REQUEST_TIMEOUT = 60.0
_MAX_RETRY_AFTER_SECONDS = 60.0

# in-process LRU — HF 어댑터와 동일 사이즈. 페르소나 A 일일 쿼리 ~30건 × 2주 윈도우 가정.
_EMBED_CACHE_MAXSIZE = 512

T = TypeVar("T")


class BGEM3DeepInfraEmbeddingProvider:
    """`EmbeddingProvider` Protocol 구현체 — DeepInfra OpenAI-compatible 백엔드."""

    dense_dim: int = _DENSE_DIM

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.deepinfra_api_token:
            raise RuntimeError(
                "DEEPINFRA_API_TOKEN 이 설정되지 않았습니다. .env 에 토큰을 추가하세요. "
                "발급: https://deepinfra.com/dash/api_keys"
            )
        self._headers = {
            "Authorization": f"Bearer {settings.deepinfra_api_token}",
            "Content-Type": "application/json",
        }
        self._client = httpx.Client(timeout=_REQUEST_TIMEOUT)
        # HF 어댑터와 동일 LRU 구조 — `embed_query` 전용. caller mutation 방어 위해
        # cache get/put 시 list(...) 복사.
        self._embed_cache: OrderedDict[str, list[float]] = OrderedDict()
        self._embed_cache_lock = threading.Lock()
        self._embed_cache_maxsize = _EMBED_CACHE_MAXSIZE
        # 직전 `embed_query` 의 cache hit 여부·출처 — search.py 메트릭 기록용.
        # 멀티 스레드 환경에서 마지막 writer 가 덮어쓰므로 전체 비율만 신뢰 가능.
        self._last_cache_hit: bool = False
        self._last_cache_source: str = "miss"

    # ---------------------- public API ----------------------

    def embed(self, text: str) -> EmbeddingResult:
        def call() -> list[float]:
            body = {"model": _MODEL_SLUG, "input": text}
            resp = self._client.post(_URL, headers=self._headers, json=body)
            return _parse_single_response(resp)

        vec = _with_retry(call, label="bge-m3.deepinfra.embed")
        return EmbeddingResult(dense=vec, sparse={})

    def embed_query(self, text: str) -> list[float]:
        """검색 쿼리용 단일 텍스트 → 1024 dim dense vector.

        HF 어댑터와 동일한 2단 캐시 (in-process LRU → DB `embed_query_cache`) 사용.
        영구 캐시는 model_id `BAAI/bge-m3` 동일이라 HF 와 entry 공유 (W-0 cosine
        0.999984 ≥ 0.999 PASS — swap 안전).

        cache hit 여부는 `self._last_cache_hit` (LRU·영구 둘 다 True = "외부 호출 안 함"),
        `self._last_cache_source` 로 "lru"/"persistent"/"miss" 구분.
        """
        # ① in-process LRU
        with self._embed_cache_lock:
            cached = self._embed_cache.get(text)
            if cached is not None:
                self._embed_cache.move_to_end(text)
                self._last_cache_hit = True
                self._last_cache_source = "lru"
                return list(cached)

        # ② 영구 캐시 (DB) — lazy import. read/write 실패는 graceful (검색 정상 보장).
        from app.services import embed_query_cache

        text_sha256: str | None = None
        model_id: str | None = None
        try:
            text_sha256, model_id = self._cache_key(text)
            persisted = embed_query_cache.lookup(text_sha256, model_id)
        except Exception as exc:  # noqa: BLE001 — read 는 best-effort
            logger.debug("embed_query 영구 캐시 lookup 우회 (graceful): %s", exc)
            persisted = None
        if persisted is not None and len(persisted) == _DENSE_DIM:
            with self._embed_cache_lock:
                self._embed_cache[text] = list(persisted)
                while len(self._embed_cache) > self._embed_cache_maxsize:
                    self._embed_cache.popitem(last=False)
            self._last_cache_hit = True
            self._last_cache_source = "persistent"
            return list(persisted)

        # ③ miss — DeepInfra 직호출 (retry 포함)
        self._last_cache_hit = False
        self._last_cache_source = "miss"
        result = self._embed_query_uncached(text)

        # 영구 캐시 best-effort write.
        if text_sha256 is not None and model_id is not None:
            try:
                embed_query_cache.upsert(text_sha256, model_id, _DENSE_DIM, result)
            except Exception as exc:  # noqa: BLE001 — write 는 best-effort
                logger.debug("embed_query 영구 캐시 upsert 우회 (graceful): %s", exc)

        with self._embed_cache_lock:
            self._embed_cache[text] = list(result)
            while len(self._embed_cache) > self._embed_cache_maxsize:
                self._embed_cache.popitem(last=False)

        return result

    @staticmethod
    def _cache_key(text: str) -> tuple[str, str]:
        """영구 캐시 키 `(text_sha256, model_id)` — HF 어댑터와 동일 알고리즘.

        text_sha256 = sha256(NFC(text.strip())). HF↔DeepInfra 간 entry 호환 보장.
        """
        from app.services.embed_query_cache import _MODEL_ID

        normalized = unicodedata.normalize("NFC", text.strip())
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return digest, _MODEL_ID

    def _embed_query_uncached(self, text: str) -> list[float]:
        """DeepInfra API 직호출 (retry 포함) — cache miss 경로."""
        def call() -> list[float]:
            body = {"model": _MODEL_SLUG, "input": text}
            resp = self._client.post(_URL, headers=self._headers, json=body)
            return _parse_single_response(resp)

        return _with_retry(call, label="bge-m3.deepinfra.embed_query")

    def clear_embed_cache(self) -> None:
        """테스트 전용 — in-process LRU 비움. 영구 캐시(DB)는 건드리지 않음."""
        with self._embed_cache_lock:
            self._embed_cache.clear()
            self._last_cache_hit = False
            self._last_cache_source = "miss"

    def embed_batch(self, texts: list[str]) -> list[EmbeddingResult]:
        if not texts:
            return []

        def call() -> list[list[float]]:
            body = {"model": _MODEL_SLUG, "input": texts}
            resp = self._client.post(_URL, headers=self._headers, json=body)
            return _parse_batch_response(resp, expected=len(texts))

        vectors = _with_retry(
            call, label=f"bge-m3.deepinfra.embed_batch(n={len(texts)})"
        )
        return [EmbeddingResult(dense=v, sparse={}) for v in vectors]


# ---------------------- 응답 파싱 ----------------------


def _parse_single_response(resp: httpx.Response) -> list[float]:
    """OpenAI-compatible 단일 응답: `{"data": [{"embedding": [...]}], ...}`."""
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict) or "data" not in data:
        raise RuntimeError(
            "예상치 못한 DeepInfra 응답 스키마: "
            f"keys={list(data.keys()) if isinstance(data, dict) else type(data).__name__}"
        )
    items = data["data"]
    if not isinstance(items, list) or not items:
        raise RuntimeError(f"DeepInfra data 배열 비어있음: {items!r}")
    emb = items[0].get("embedding") if isinstance(items[0], dict) else None
    if not isinstance(emb, list):
        raise RuntimeError(
            f"DeepInfra embedding 타입 비정상: {type(emb).__name__}"
        )
    if len(emb) != _DENSE_DIM:
        raise RuntimeError(
            f"차원 불일치: 받은={len(emb)}, 기대={_DENSE_DIM}"
        )
    return [float(x) for x in emb]


def _parse_batch_response(
    resp: httpx.Response, *, expected: int
) -> list[list[float]]:
    """OpenAI-compatible 배치 응답: `{"data": [{"embedding": [...], "index": i}, ...]}`.

    `index` 순서 보장 — OpenAI spec 상 입력 순서를 따르지만, 안전을 위해 정렬.
    """
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict) or "data" not in data:
        raise RuntimeError(
            "예상치 못한 DeepInfra batch 응답 스키마: "
            f"keys={list(data.keys()) if isinstance(data, dict) else type(data).__name__}"
        )
    items = data["data"]
    if not isinstance(items, list) or len(items) != expected:
        raise RuntimeError(
            "배치 응답 길이 불일치: "
            f"got={type(items).__name__}({len(items) if isinstance(items, list) else '-'}), expect={expected}"
        )
    # index 기준 정렬 — spec 상 보장되지만 방어.
    try:
        sorted_items = sorted(items, key=lambda d: int(d.get("index", 0)))
    except (TypeError, ValueError):
        sorted_items = items
    out: list[list[float]] = []
    for i, item in enumerate(sorted_items):
        emb = item.get("embedding") if isinstance(item, dict) else None
        if not isinstance(emb, list) or len(emb) != _DENSE_DIM:
            raise RuntimeError(
                f"item[{i}] 차원 불일치: "
                f"len={len(emb) if isinstance(emb, list) else type(emb).__name__}"
            )
        out.append([float(x) for x in emb])
    return out


# ---------------------- retry ----------------------

# transient — HF 어댑터와 동일 분류 정책 (운영 일관성). reranker 도 같은 패턴 보유.
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


def is_transient_deepinfra_error(exc: Exception) -> bool:
    """search.py·answer.py 의 fallback 분기 alias — HF 어댑터의 `is_transient_hf_error`
    와 동일 의미.

    True:  network/server transient (5xx, 429, connection error) → sparse-only fallback
    False: 4xx 영구 실패 (401 토큰 만료 / 404 endpoint 변경 / 400 잘못된 request) →
           503 raise (silent ranking degradation 방지)
    """
    return _is_retryable(exc)


def _parse_retry_after(exc: Exception) -> float | None:
    """HTTP 429/503 응답의 `Retry-After` 헤더를 초 단위로 파싱 (HF 어댑터와 동일 로직)."""
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
            # 4xx 인증/요청 오류, 응답 파싱 오류 (RuntimeError) 등 비-transient 는 즉시 실패.
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
