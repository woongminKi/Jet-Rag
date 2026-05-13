"""M1 W-1(a) — multi-query 검색 공통 유틸 (RRF merge + 분해 풀 사이즈 상수).

배경
----
`/answer` (S3 D3) 와 `/search` (M1 W-1(a)) 모두 "원본 query 풀 + sub-query 풀들 →
chunk_id 단위 RRF (Reciprocal Rank Fusion) 합산" 패턴을 쓴다. 그 합산 로직과
풀 사이즈 상수를 한 모듈로 모아 중복을 없앤다.

설계 원칙
---------
- **순수 함수만** — DB / 외부 API / 라우터 의존 0. import 순환 회피
  (`app.routers.answer` / `app.routers.search` 를 import 하지 않는다).
- `/answer` 의 기존 `_rrf_merge_pools` 와 **동작·시그니처 완전 동일** — 순수 리팩토링.
- 풀 fetch (RPC 호출) 자체는 각 라우터가 자기 컨텍스트(HyDE·embed-cache·doc_id
  scope·ablation mode)에 맞게 따로 구현 — 본 모듈은 "이미 fetch 된 풀들" 만 받는다.

상수
----
| 상수 | 값 | 의미 |
|---|---|---|
| `DECOMP_TOP_K_ORIGINAL` | 20 | 분해 활성 시 원본 query 풀 크기 (subqueries 가 노이즈일 때 fallback) |
| `DECOMP_TOP_K_PER_SUB` | 10 | 분해 활성 시 sub-query 당 풀 크기 |
"""

from __future__ import annotations

# S3 D3 (planner v0.1 §G + 사용자 결정 Q-S3-D3-2) — 원본 query 풀이 더 큼:
# subqueries 가 노이즈일 때 원본 query 풀이 fallback 역할.
DECOMP_TOP_K_ORIGINAL = 20
DECOMP_TOP_K_PER_SUB = 10


def rrf_merge_pools(pools: list[list[dict]], *, k: int) -> list[dict]:
    """RRF (Reciprocal Rank Fusion) — 여러 풀을 chunk_id 단위 ``1/(k+rank)`` 합산.

    각 풀 내 rank 는 0-based (rrf_score 순으로 이미 정렬되어 있다고 가정).
    동일 chunk_id 가 여러 풀에 등장하면 점수 합산 → 다중 sub-query hit 가
    원본 query 단독 hit 보다 우선. 첫 등장 row 의 메타 (doc_id 등) 보존.

    NOTE: `/answer` 의 기존 `_rrf_merge_pools` 와 동작·시그니처 완전 동일
    (이 모듈로 추출만 — 동작 변화 0).
    """
    scores: dict[str, float] = {}
    base_row: dict[str, dict] = {}
    for pool in pools:
        for rank, row in enumerate(pool):
            chunk_id = row.get("chunk_id")
            if not chunk_id:
                continue
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank + 1)
            base_row.setdefault(chunk_id, row)

    # rrf_score 갱신 (UI / sources.score 와 일관성 유지) + 점수 내림차순 정렬.
    fused: list[dict] = []
    for chunk_id, score in sorted(scores.items(), key=lambda x: x[1], reverse=True):
        merged = dict(base_row[chunk_id])
        merged["rrf_score"] = score
        fused.append(merged)
    return fused


__all__ = [
    "DECOMP_TOP_K_ORIGINAL",
    "DECOMP_TOP_K_PER_SUB",
    "rrf_merge_pools",
]
