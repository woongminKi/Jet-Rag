"""S3 D4 — BGE-reranker 결과 in-memory LRU cache (planner v0.1 §B).

목적
----
같은 ``(query, candidate chunk_ids)`` 조합에 대해 BGE-reranker HF API 호출을
1회로 묶어 latency / quota 폭주를 방지. 재호출 시 LLM/HF 호출 0 회.

설계 원칙
---------
- **OrderedDict LRU 500건** — D3 ``query_decomposer.py:91-110`` 패턴 답습.
  ``move_to_end`` 로 hit 시 갱신, ``popitem(last=False)`` 로 oldest evict.
- **키 안정화** — chunk_ids 는 ``sorted(...)`` 후 SHA1 hex(16자) 로 압축.
  순서 무관 같은 chunk 집합 → 같은 키. query 는 ``lower().strip()`` 정규화.
- **운영 토글** — ``JETRAG_RERANKER_CACHE_DISABLE=1`` 시 cache bypass
  (디버깅 / 회귀 비교 용).
- **외부 의존성 0** — 표준 라이브러리만 (``hashlib`` / ``collections.OrderedDict``).
- **score 객체 보호** — 저장 시 dict 복사. 호출자가 반환 dict 를 mutate 해도
  cache 내부 보존 (mutation safety).

호출 계약
---------
- ``lookup(query, chunk_ids)``:
    cache hit 시 ``dict[chunk_id, float]`` (저장 당시 score map 의 복사본),
    miss 시 None. hit 시 LRU 갱신.
- ``store(query, chunk_ids, scores)``:
    저장 후 LRU 500 초과 시 oldest 제거.
- ``_reset_for_test()``:
    단위 테스트 용 — cache 전체 초기화.

회귀 영향
--------
- 외부 API / DB 호출 0.
- D3 패턴 동일 — 회귀 위험 최소.
"""

from __future__ import annotations

import hashlib
import os
from collections import OrderedDict
from typing import Iterable

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------
_CACHE_MAX_SIZE = 500
_KEY_HEX_LEN = 16  # SHA1 16자 (64bit) → 충돌 확률 무시 가능, 메모리 절약.

# ENV — 운영 토글 (D3 패턴).
_ENV_CACHE_DISABLE = "JETRAG_RERANKER_CACHE_DISABLE"

# 캐시 본체 — process-singleton OrderedDict.
# 키: tuple[str, str] = (query_normalized, chunk_ids_sha1_hex)
# 값: dict[chunk_id, float]
_cache: "OrderedDict[tuple[str, str], dict[str, float]]" = OrderedDict()


def lookup(query: str, chunk_ids: Iterable[str]) -> dict[str, float] | None:
    """LRU 조회 — hit 시 score map 복사본 반환, miss 시 None.

    ``JETRAG_RERANKER_CACHE_DISABLE=1`` 시 항상 None (cache bypass).

    Parameters
    ----------
    query:
        사용자 query — ``lower().strip()`` 정규화 후 키 구성.
    chunk_ids:
        후보 chunk_id iterable — ``sorted(...)`` 후 SHA1 압축.

    Returns
    -------
    dict[chunk_id, float] | None — hit 시 LRU 갱신 후 dict 복사본,
    miss / disable 시 None.
    """
    if _is_cache_disabled():
        return None
    key = _build_key(query, chunk_ids)
    if key not in _cache:
        return None
    _cache.move_to_end(key)
    # mutation safety — 호출자가 결과 dict 를 변경해도 cache 내부 보존.
    return dict(_cache[key])


def store(
    query: str,
    chunk_ids: Iterable[str],
    scores: dict[str, float],
) -> None:
    """LRU 저장 — 500 초과 시 oldest 제거.

    ``JETRAG_RERANKER_CACHE_DISABLE=1`` 시 no-op. score dict 는 복사 후 저장.
    """
    if _is_cache_disabled():
        return
    key = _build_key(query, chunk_ids)
    _cache[key] = dict(scores)
    _cache.move_to_end(key)
    while len(_cache) > _CACHE_MAX_SIZE:
        _cache.popitem(last=False)


def _build_key(query: str, chunk_ids: Iterable[str]) -> tuple[str, str]:
    """``(query_normalized, sha1_hex16(sorted_chunk_ids))`` — 순서 무관 정규화.

    chunk_ids 가 같은 집합이면 입력 순서 무관 같은 키 → cache hit 률 향상.
    """
    normalized_query = query.lower().strip()
    sorted_ids = sorted(chunk_ids)
    joined = "\n".join(sorted_ids).encode("utf-8")
    digest = hashlib.sha1(joined).hexdigest()[:_KEY_HEX_LEN]
    return (normalized_query, digest)


def _is_cache_disabled() -> bool:
    """``JETRAG_RERANKER_CACHE_DISABLE=1`` → True. 그 외 False (default cache ON)."""
    return os.environ.get(_ENV_CACHE_DISABLE, "0").strip() == "1"


def _reset_for_test() -> None:
    """단위 테스트 전용 — cache 전체 초기화."""
    _cache.clear()


__all__ = [
    "lookup",
    "store",
    "_reset_for_test",
]
