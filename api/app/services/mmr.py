"""S3 D4 — MMR (Maximal Marginal Relevance) 다양성 재정렬 (planner v0.1 §D).

목적
----
cross-doc query 의 top-K 결과를 ``relevance vs diversity`` 로 재정렬해
같은 doc 의 청크가 상위를 독점하지 않도록 한다. 사용자가 "A 문서랑 B 문서를
비교해줘" 라 물었을 때 두 문서가 골고루 노출되도록 보장.

수식
----
``score(c) = λ · rel(q, c) − (1 − λ) · max_{s ∈ S} sim(c, s)``

- ``rel(q, c)`` — reranker score (cache/invoked) 또는 RRF score (degraded)
- ``sim(c, s)`` — chunk embedding cosine similarity
- λ — relevance vs diversity balance (default 0.7)
- S — 이미 선택된 chunk 집합

선택 알고리즘
-------------
1. 가장 relevance 높은 chunk 1건 선택 (S ← {c0}).
2. 남은 후보 중 ``score(c)`` 최대인 chunk 선택 → S 갱신.
3. ``top_k`` 개 채울 때까지 반복.

설계 원칙
---------
- **외부 호출 0** — 호출자가 ``embeddings_by_id`` 를 미리 전달. cache miss
  (None) 인 chunk 는 ``sim=0`` 보수적 처리 — diversity term 0 → relevance 만
  반영. 별도 HF/DB 호출 0.
- **cross_doc only** — 단일 doc query 에는 호출자가 본 모듈 진입 자체 skip.
- **운영 토글** — ``JETRAG_MMR_DISABLE=1`` 시 호출자가 즉시 skip 가능.
- **결정성** — 동일 입력 → 동일 출력. 부동소수 비교 시 chunk_id 사전순 tie-break.
- **의존성 추가 0** — 표준 라이브러리만 (``math``).

회귀 영향
--------
- 외부 API / DB 호출 0.
- λ=1.0 → 순수 relevance 정렬 (기존 ranking 동일).
"""

from __future__ import annotations

import math
import os
from typing import Iterable

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------
_ENV_LAMBDA = "JETRAG_MMR_LAMBDA"
_ENV_DISABLE = "JETRAG_MMR_DISABLE"

_DEFAULT_LAMBDA = 0.7

# embedding 결측 시 sim 으로 사용할 보수적 default — diversity term 0 화.
_MISSING_SIM = 0.0


def is_disabled() -> bool:
    """``JETRAG_MMR_DISABLE=1`` → True. 그 외 False (default MMR ON)."""
    return os.environ.get(_ENV_DISABLE, "0").strip() == "1"


def resolve_lambda() -> float:
    """``JETRAG_MMR_LAMBDA`` → float (default 0.7). invalid 시 default."""
    raw = os.environ.get(_ENV_LAMBDA)
    if raw is None or raw == "":
        return _DEFAULT_LAMBDA
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_LAMBDA
    # [0.0, 1.0] cap — 수식 의미가 깨지지 않도록.
    if value < 0.0 or value > 1.0:
        return _DEFAULT_LAMBDA
    return value


def rerank(
    candidate_ids: Iterable[str],
    *,
    relevance: dict[str, float],
    embeddings_by_id: dict[str, list[float]],
    top_k: int,
    lambda_: float | None = None,
) -> list[str]:
    """MMR greedy 선택 — 다양성 가중 ranking.

    Parameters
    ----------
    candidate_ids:
        후보 chunk_id (또는 doc_id 등 식별자) iterable.
    relevance:
        식별자 → relevance score (reranker / RRF). 결측 시 0.0.
    embeddings_by_id:
        식별자 → 1024-dim embedding (BGE-M3 cached). 결측 시 sim=0.
    top_k:
        반환 개수. candidate 가 부족하면 candidate 수만큼 반환.
    lambda_:
        relevance 가중 (1.0 = pure relevance, 0.0 = pure diversity).
        None 이면 ENV/default 사용.

    Returns
    -------
    list[str] — MMR 순서로 정렬된 식별자 리스트 (길이 ≤ top_k).
    """
    candidates = list(candidate_ids)
    if not candidates or top_k <= 0:
        return []

    lam = lambda_ if lambda_ is not None else resolve_lambda()

    # 1) 가장 relevance 높은 첫 chunk 선택. tie-break: id 사전순 작은 쪽 우선.
    remaining = list(candidates)
    selected: list[str] = []
    # max(key) 동률 시 첫 발견을 반환 — 결정성 위해 명시적 sort 후 max.
    sorted_by_rel = sorted(
        remaining, key=lambda cid: (-_rel(relevance, cid), cid)
    )
    first = sorted_by_rel[0]
    selected.append(first)
    remaining.remove(first)

    # 2) 남은 후보 중 mmr_score 최대인 chunk 반복 선택.
    while remaining and len(selected) < top_k:
        best_id: str | None = None
        best_score = -math.inf
        for cid in sorted(remaining):  # 사전순 순회로 tie-break 결정성.
            rel = _rel(relevance, cid)
            sim_max = _max_sim_to_selected(cid, selected, embeddings_by_id)
            mmr_score = lam * rel - (1.0 - lam) * sim_max
            if mmr_score > best_score:
                best_score = mmr_score
                best_id = cid
        if best_id is None:  # safety — 이론상 도달 불가
            break
        selected.append(best_id)
        remaining.remove(best_id)

    return selected


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------
def _rel(relevance: dict[str, float], cid: str) -> float:
    """relevance 결측 시 0.0 — degrade 분기 / RRF 0 시 자연스럽게 처리."""
    return float(relevance.get(cid, 0.0))


def _max_sim_to_selected(
    cid: str,
    selected: list[str],
    embeddings_by_id: dict[str, list[float]],
) -> float:
    """candidate 와 이미 선택된 chunks 간 cosine sim 최대값. 결측 시 0.0."""
    emb_c = embeddings_by_id.get(cid)
    if emb_c is None:
        return _MISSING_SIM
    best = _MISSING_SIM
    for sid in selected:
        emb_s = embeddings_by_id.get(sid)
        if emb_s is None:
            continue
        sim = _cosine(emb_c, emb_s)
        if sim is None:
            continue
        if sim > best:
            best = sim
    return best


def _cosine(a: list[float], b: list[float]) -> float | None:
    """두 벡터의 cosine similarity. dim mismatch / zero 벡터 시 None.

    `search.py:_cosine` 와 동일 패턴 — 의존성 0 (numpy 미사용).
    1024 dim × 작은 candidate 수면 수 ms 이내.
    """
    if len(a) != len(b):
        return None
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a <= 0.0 or norm_b <= 0.0:
        return None
    return dot / ((norm_a ** 0.5) * (norm_b ** 0.5))


__all__ = [
    "rerank",
    "is_disabled",
    "resolve_lambda",
]
