"""W25 D14+1 (E) — 검색 retrieval 메트릭 (Recall@K / MRR / nDCG@K).

D1 정정 (W25 D14+1 sprint 종합):
- graded relevance 도입 — relevant (1.0) + acceptable (0.5) 두 단계
- QA 의 narrowness 발견 직접 해결 (`expected_chunk_idx_hints` strict set 한계)
- 메트릭 함수에 acceptable_chunks 옵션 인자 추가 (legacy 호출은 binary 그대로)

골든셋 schema (CSV 또는 JSON):
- relevant_chunks: 라벨러가 명시적으로 정답으로 정한 chunks (weight 1.0)
- acceptable_chunks: 사용자 의도에 적합하지만 narrow 정답은 아닌 chunks (weight 0.5)
                    BGE-M3 cosine ≥ 0.7 자동 라벨 가능 (auto_goldenset.py)

본 모듈은 list[ChunkKey] 단순 입력 받아 메트릭만 계산 — 외부 의존성 0.

ChunkKey
--------
단일-doc 골든셋은 chunk_idx (``int``) 가 키. cross_doc 골든셋(C 결정)은 정답
chunk 가 doc 간 흩어져 ``(alias, chunk_idx)`` 튜플이 키. 본 모듈은 두 형태를
구분 없이 처리한다 — ``in`` / set 비교만 쓰므로 hashable 이면 무엇이든 무관.
하위 호환 — ``int`` 입력은 기존 동작 그대로.
"""

from __future__ import annotations

import math
from typing import Iterable, Union

# 단일-doc = int chunk_idx / cross_doc = (alias, chunk_idx) 튜플.
ChunkKey = Union[int, tuple[str, int]]


def _relevance_score(
    chunk_key: ChunkKey,
    relevant_set: set[ChunkKey],
    acceptable_set: set[ChunkKey] | None = None,
) -> float:
    """chunk 의 graded relevance score.

    - relevant: 1.0
    - acceptable (relevant 와 disjoint): 0.5
    - 기타: 0.0

    acceptable_set None 시 binary relevance 와 동일.
    """
    if chunk_key in relevant_set:
        return 1.0
    if acceptable_set and chunk_key in acceptable_set:
        return 0.5
    return 0.0


def recall_at_k(
    predicted_chunks: list[ChunkKey],
    relevant_chunks: set[ChunkKey] | Iterable[ChunkKey],
    k: int = 10,
    acceptable_chunks: set[ChunkKey] | Iterable[ChunkKey] | None = None,
) -> float:
    """Recall@K — top-K 예측 중 (relevant + acceptable) 가 잡힌 비율 (graded).

    분자: top-K 내 hits 의 graded score 합 (relevant=1.0, acceptable=0.5)
    분모: 이상적 max score (relevant 1.0 × |relevant| + acceptable 0.5 × |acceptable|, cap K)

    acceptable None 시 binary recall 과 동일.
    relevant 가 비어있으면 0.0.
    """
    relevant_set = set(relevant_chunks)
    accept_set = set(acceptable_chunks) if acceptable_chunks else None
    if not relevant_set and not accept_set:
        return 0.0
    top_k = predicted_chunks[:k]
    hit_score = sum(_relevance_score(c, relevant_set, accept_set) for c in top_k)
    # 이상적 max — relevant 부터 채우고 acceptable 으로 채움 (cap K)
    sorted_relevances = (
        [1.0] * len(relevant_set)
        + ([0.5] * len(accept_set or set())) * 1
    )
    sorted_relevances = sorted(sorted_relevances, reverse=True)[:k]
    max_score = sum(sorted_relevances)
    if max_score <= 0:
        return 0.0
    return hit_score / max_score


def mrr(
    predicted_chunks: list[ChunkKey],
    relevant_chunks: set[ChunkKey] | Iterable[ChunkKey],
    k: int = 10,
    acceptable_chunks: set[ChunkKey] | Iterable[ChunkKey] | None = None,
) -> float:
    """Mean Reciprocal Rank — top-K 내 첫 hit 의 1/rank.

    relevant 우선 — relevant rank 가 acceptable 보다 앞이면 relevant 만 인정.
    relevant 0 + acceptable hit 만 있으면 0.5 weight 의 1/rank.
    """
    relevant_set = set(relevant_chunks)
    accept_set = set(acceptable_chunks) if acceptable_chunks else set()
    for i, c in enumerate(predicted_chunks[:k], start=1):
        if c in relevant_set:
            return 1.0 / i
        if c in accept_set:
            return 0.5 / i
    return 0.0


def ndcg_at_k(
    predicted_chunks: list[ChunkKey],
    relevant_chunks: set[ChunkKey] | Iterable[ChunkKey],
    k: int = 10,
    acceptable_chunks: set[ChunkKey] | Iterable[ChunkKey] | None = None,
) -> float:
    """nDCG@K (graded relevance) — DCG / IDCG.

    relevance: relevant=1.0, acceptable=0.5, 기타=0.
    DCG = Σ (rel_i / log2(i+2)), i=0..k-1.
    IDCG = 정답 chunks 가 graded score 내림차순 ideal sort 후 cap K.
    """
    relevant_set = set(relevant_chunks)
    accept_set = set(acceptable_chunks) if acceptable_chunks else None
    if not relevant_set and not accept_set:
        return 0.0
    top_k_rel = [_relevance_score(c, relevant_set, accept_set) for c in predicted_chunks[:k]]
    dcg = sum(r / math.log2(i + 2) for i, r in enumerate(top_k_rel))
    # IDCG — ideal ranking (relevant 먼저 1.0 × n_r, acceptable 0.5 × n_a, cap K)
    ideal_relevances = [1.0] * len(relevant_set) + [0.5] * len(accept_set or set())
    ideal_relevances = sorted(ideal_relevances, reverse=True)[:k]
    idcg = sum(r / math.log2(i + 2) for i, r in enumerate(ideal_relevances))
    return dcg / idcg if idcg > 0 else 0.0


def aggregate_metrics(
    per_query_results: list[dict],
) -> dict:
    """per-query 메트릭 list → 평균 집계.

    Args:
        per_query_results: [{"recall_at_10": float, "mrr": float, "ndcg_at_10": float}, ...]
    Returns:
        {"recall_at_10": mean, "mrr": mean, "ndcg_at_10": mean, "n": int}
    """
    if not per_query_results:
        return {"recall_at_10": 0.0, "mrr": 0.0, "ndcg_at_10": 0.0, "n": 0}
    n = len(per_query_results)
    return {
        "recall_at_10": sum(r["recall_at_10"] for r in per_query_results) / n,
        "mrr": sum(r["mrr"] for r in per_query_results) / n,
        "ndcg_at_10": sum(r["ndcg_at_10"] for r in per_query_results) / n,
        "n": n,
    }
