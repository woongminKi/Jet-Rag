"""W3 Day 2 Phase 3 — `/search` 의 SLO 측정 인프라 (in-memory ring buffer).

용도:
    - search.py 가 응답 직전 `record_search(...)` 로 이벤트 기록
    - stats.py 가 `get_search_slo()` 로 p50/p95/avg/fallback 분포 조회

설계 원칙:
    - **단일 사용자 MVP** — 운영 부하가 낮아 최근 500건 윈도우만 유지 (메모리 < 100KB)
    - **stdlib only** — `collections.deque` + `threading.Lock` + `statistics`
    - **thread-safe** — uvicorn worker 1 + FastAPI 의 sync 라우터를 threadpool 에서 실행하므로
      `_ring.append` / 스냅샷 모두 `Lock` 으로 보호
    - **외부 메트릭 시스템 도입 전 임시 대체** — Prometheus/OpenTelemetry 미도입 단계의 가시성 확보

future-proof:
    W6+ 사용자 자산이 누적되면 ring buffer → DB 영속화로 마이그레이션 (W4-Q-16).
    현재는 프로세스 재시작 시 휘발 — Day 2 의 PGroonga 마이그레이션 후 P95 측정에는 충분.

알려진 한계 (W3 P3 F-4):
    마이그레이션 적용 (003 → 004) 시점에 ring buffer 가 두 RPC 의 sample 을 혼재 측정.
    p95/avg 에 003 잔존 sample 이 가중되어 PGroonga 효과 측정이 오염될 수 있음.
    완화: uvicorn 재시작 시 자연 reset. 마이그레이션 직후 측정 시 서버 재시작 권장.
"""

from __future__ import annotations

import statistics
import threading
from collections import Counter, deque
from typing import Literal

# 운영 부하 단일 사용자 환경 — 최근 500건이면 5분 분량 (10 QPS 가정).
# 늘릴 때는 메모리 영향 검토 (이벤트 1건 ≈ 200B → 500건 ≈ 100KB).
_RING_MAXLEN = 500

# fallback 분류 — search.py 와 1:1 매칭. 새 값 추가 시 stats.py 의 응답 모델 docstring 도 갱신.
FallbackReason = Literal["transient_5xx", "permanent_4xx"]
_FALLBACK_VALUES: tuple[str, ...] = ("transient_5xx", "permanent_4xx")
_NONE_KEY = "none"

_ring: deque[dict] = deque(maxlen=_RING_MAXLEN)
_lock = threading.Lock()


def record_search(
    *,
    took_ms: int,
    dense_hits: int,
    sparse_hits: int,
    fused: int,
    has_dense: bool,
    fallback_reason: str | None,
    embed_cache_hit: bool = False,
) -> None:
    """`/search` 1회 처리 결과를 ring buffer 에 적재.

    호출 위치: `search()` 함수의 모든 응답 직전 (200 + 503 raise 직전 모두).

    `fallback_reason`:
        - None: dense path 정상 — sparse-only fallback 미진입
        - "transient_5xx": HF API transient 실패 → sparse-only fallback 으로 200 응답
        - "permanent_4xx": HF API 영구 실패 → 503 raise 예정 (가시성 위해 record 만)

    `embed_cache_hit`: W4-Q-3 — `embed_query` LRU 의 hit 여부.
        - True: 동일 query 재호출이라 HF API 호출 0회 (warm path)
        - False: cache miss (cold) 또는 fallback 진입 (dense path 미실행)
        - default False: 503 raise 분기 등 backward compat 안전값.
    """
    event = {
        "took_ms": int(took_ms),
        "dense_hits": int(dense_hits),
        "sparse_hits": int(sparse_hits),
        "fused": int(fused),
        "has_dense": bool(has_dense),
        "fallback_reason": fallback_reason,
        "embed_cache_hit": bool(embed_cache_hit),
    }
    with _lock:
        _ring.append(event)


def get_search_slo() -> dict:
    """현재 ring buffer 스냅샷에서 SLO 통계 계산.

    sample_count == 0 인 경우 모든 백분위/평균 필드는 None — 프론트는 "측정 데이터 없음" 표기.
    fallback_breakdown 은 항상 3개 키 (`transient_5xx`, `permanent_4xx`, `none`) 노출 — 0 이라도.
    """
    with _lock:
        # ring 스냅샷 — 락 보유 시간 최소화 위해 list copy 후 즉시 release
        snapshot = list(_ring)

    sample_count = len(snapshot)

    fallback_breakdown: dict[str, int] = {key: 0 for key in (*_FALLBACK_VALUES, _NONE_KEY)}
    if sample_count == 0:
        return {
            "p50_ms": None,
            "p95_ms": None,
            "sample_count": 0,
            "avg_dense_hits": None,
            "avg_sparse_hits": None,
            "avg_fused": None,
            "fallback_count": 0,
            "fallback_breakdown": fallback_breakdown,
            "cache_hit_count": 0,
            "cache_hit_rate": None,
        }

    took_samples = sorted(e["took_ms"] for e in snapshot)
    p50 = _percentile_nearest_rank(took_samples, 0.50)
    p95 = _percentile_nearest_rank(took_samples, 0.95)

    avg_dense = round(statistics.fmean(e["dense_hits"] for e in snapshot), 2)
    avg_sparse = round(statistics.fmean(e["sparse_hits"] for e in snapshot), 2)
    avg_fused = round(statistics.fmean(e["fused"] for e in snapshot), 2)

    reasons = Counter(
        (e["fallback_reason"] if e["fallback_reason"] is not None else _NONE_KEY)
        for e in snapshot
    )
    for key in fallback_breakdown:
        fallback_breakdown[key] = int(reasons.get(key, 0))
    fallback_count = sum(fallback_breakdown[key] for key in _FALLBACK_VALUES)

    # W4-Q-3 — `embed_query` LRU cache hit 비율. backward compat: 키 없는 옛 이벤트는 False 간주.
    cache_hit_count = sum(1 for e in snapshot if e.get("embed_cache_hit"))
    cache_hit_rate = round(cache_hit_count / sample_count, 4)

    return {
        "p50_ms": p50,
        "p95_ms": p95,
        "sample_count": sample_count,
        "avg_dense_hits": avg_dense,
        "avg_sparse_hits": avg_sparse,
        "avg_fused": avg_fused,
        "fallback_count": fallback_count,
        "fallback_breakdown": fallback_breakdown,
        "cache_hit_count": cache_hit_count,
        "cache_hit_rate": cache_hit_rate,
    }


def reset() -> None:
    """테스트 전용 — ring buffer 비움. 운영 코드에서 호출하지 말 것."""
    with _lock:
        _ring.clear()


# ---------------------- helpers ----------------------


def _percentile_nearest_rank(sorted_samples: list[int], q: float) -> int:
    """nearest-rank 백분위 — stats.py 의 `_bucket_stats` 와 동일 공식.

    n=1 이면 그 값 반환. q=0.95, n=20 이면 index=int(0.95*19)=18 → 상위 5%.
    """
    n = len(sorted_samples)
    idx = int(q * (n - 1))
    return int(sorted_samples[idx])
