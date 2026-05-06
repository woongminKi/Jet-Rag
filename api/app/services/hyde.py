"""W25 D14+1 D4 — HyDE (Hypothetical Document Embedding).

흐름:
1. 사용자 query 를 LLM 에 전달 → query 에 답할 만한 가상 문단 1개 생성
2. (query + hypothetical_doc) concat → BGE-M3 임베딩
3. 그 임베딩으로 dense path 검색

장점: 짧은 키워드 query → 긴 자연어 doc 매칭이 더 정확해짐.
단점: latency +1~2초 (LLM 호출). cache 필수.

opt-in ENV: `JETRAG_HYDE_ENABLED=true` (default false).
실패 시 원본 query 임베딩으로 fallback (silent degradation 회피 — query_parsed 에 표기).

Phase 1 S0 D2-A — 호출처를 GeminiLLMProvider 직접 의존에서 `LLMProvider` Protocol
의존으로 일반화. cache key 에 model_id 포함 — 모델 변경 시 stale cache 회귀 차단.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict

from app.adapters.llm import ChatMessage, LLMProvider

logger = logging.getLogger(__name__)

_HYDE_PROMPT = """다음 검색 query 에 답할 만한 한국어 본문 1문단 (3~5 문장) 을 작성해주세요.

[제약]
- 한국어로만 작성 (영어 단어 X)
- 사실 추정 가능한 자연스러운 본문 형태 (실제 문서에 등장할 만한 표현)
- 본문만 출력 (다른 설명·따옴표·라벨 X)

[query]
{query}

[가상 본문 1문단]"""

# (model_id, query) → hypothetical doc cache (LRU). 같은 query 반복 호출 시 LLM 호출 0.
# D2-A 보강 — 모델 변경 시 같은 query 가 다른 결과를 반환할 수 있어 model_id 를 키에
# 포함해 stale cache 방지. 또한 provider/모델 다중 운영 시점에도 격리 보장.
_CACHE_MAXSIZE = 256
_cache: OrderedDict[tuple[str, str], str] = OrderedDict()
_cache_lock = threading.Lock()


def _model_id(llm: LLMProvider) -> str:
    """LLMProvider 인스턴스에서 cache 분리용 model 식별자 추출.

    GeminiLLMProvider 는 `_model` 속성 보유 (private). OpenAI 등 향후 어댑터도
    동일 컨벤션 권고. 미보유 시 클래스명으로 fallback (이전 동작과 동등 격리).
    """
    return str(getattr(llm, "_model", llm.__class__.__name__))


def generate_hypothetical_doc(
    llm: LLMProvider, query: str
) -> str:
    """query → 가상 본문 (LLM). 실패 시 raise.

    Cache hit 시 LLM 호출 0. cache key = (model_id, query) — 모델 변경 시 격리.
    """
    key = (_model_id(llm), query)
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None:
            _cache.move_to_end(key)
            return cached

    prompt = _HYDE_PROMPT.format(query=query)
    response = llm.complete(
        [ChatMessage(role="user", content=prompt)],
        temperature=0.3,
    )
    hyp = response.strip()
    # 라벨 / 따옴표 정리
    for prefix in ("가상 본문:", "본문:", "답변:"):
        if hyp.startswith(prefix):
            hyp = hyp[len(prefix):].strip()
    hyp = hyp.strip("'\"`「」『』")

    with _cache_lock:
        _cache[key] = hyp
        while len(_cache) > _CACHE_MAXSIZE:
            _cache.popitem(last=False)
    return hyp


def clear_cache() -> None:
    """테스트 전용 — HyDE LRU 비움."""
    with _cache_lock:
        _cache.clear()
