"""W25 D14 Sprint B — 인제스트 ETA (대략적 남은 시간) 계산.

설계 원칙
- 외부 API 비용 0 (Supabase Postgres `ingest_logs` 만 사용)
- 임베딩 본 파이프라인 무영향 (read-only, batch-status/active 응답 시점 호출)
- N+1 회피: 1쿼리로 최근 N건 ingest_logs 가져와 Python 측에서 stage별 median 집계
- in-memory cache 5분 TTL — median 은 자주 안 바뀌므로 cache hit 시 DB hit 0
- cold start fallback (ingest_logs 0건) 시 hardcoded 추정값 사용

한계
- 첫 ingest 시 정확도 낮음 (fallback 기반)
- vision_enrich 페이지 수 변동 큰 PDF 는 ETA 분산 큼 (extract median 만으로 추정)
- median 계산 sample 은 최근 succeeded 500건 — 시간 가중 평균은 미사용 (단순화)
"""

from __future__ import annotations

import logging
import time
from statistics import median
from threading import Lock

logger = logging.getLogger(__name__)

# 인제스트 stage 순서 (pipeline.py 와 동기. web STAGE_ORDER 는 chunk_filter 누락하지만
# 실제 백엔드 stage 는 9개로, ETA 계산은 실 동작 기반.)
STAGE_ORDER: tuple[str, ...] = (
    "extract",
    "chunk",
    "chunk_filter",
    "content_gate",
    "tag_summarize",
    "load",
    "embed",
    "doc_embed",
    "dedup",
)

# cold start fallback (ingest_logs 0건 또는 stage 별 sample 부족 시).
# 일반적인 한국어 PDF (10~50 페이지) 기준 보수적 추정 (실제보다 약간 길게).
_FALLBACK_STAGE_MS: dict[str, int] = {
    "extract": 5000,
    "chunk": 2000,
    "chunk_filter": 1000,
    "content_gate": 500,
    "tag_summarize": 3000,
    "load": 2000,
    "embed": 10000,
    "doc_embed": 1500,
    "dedup": 500,
}

_CACHE_TTL_SECONDS = 300  # 5분
_SAMPLE_LIMIT = 500       # ingest_logs 최근 succeeded sample (1쿼리)
_ACTIVE_JOB_STATUSES = ("queued", "running")

_cache_lock = Lock()
_cache: dict[str, object] = {
    "medians": None,    # dict[stage, float ms] | None
    "expires_at": 0.0,  # time.monotonic() 기준
}


def reset_cache() -> None:
    """단위 테스트 용 — cache 초기화."""
    with _cache_lock:
        _cache["medians"] = None
        _cache["expires_at"] = 0.0


def _fetch_stage_medians_ms(supabase) -> dict[str, float]:
    """ingest_logs 최근 succeeded N건에서 stage별 median(duration_ms) 일괄 계산.

    1쿼리 (N+1 회피). status='succeeded' 인 row 만 — failed/started 는 timing 의미 없음.
    """
    try:
        resp = (
            supabase.table("ingest_logs")
            .select("stage, duration_ms")
            .eq("status", "succeeded")
            .order("id", desc=True)
            .limit(_SAMPLE_LIMIT)
            .execute()
        )
        rows = resp.data or []
    except Exception as exc:  # noqa: BLE001 — DB 일시 장애 시 fallback 으로 떨어뜨림
        logger.warning("ingest_logs sampling 실패 — fallback 사용: %s", exc)
        return {}

    by_stage: dict[str, list[int]] = {}
    for row in rows:
        stage_name = row.get("stage")
        duration = row.get("duration_ms")
        if stage_name and isinstance(duration, int) and duration > 0:
            by_stage.setdefault(stage_name, []).append(duration)

    medians_ms: dict[str, float] = {}
    for stage_name, durations in by_stage.items():
        if len(durations) >= 3:  # 최소 3 sample 이상이면 median 의미 있음
            medians_ms[stage_name] = float(median(durations))
    return medians_ms


def _get_stage_medians_ms(supabase) -> dict[str, float]:
    """5분 TTL cache. 동시 호출 race 회피용 lock."""
    now = time.monotonic()
    with _cache_lock:
        cached_medians = _cache.get("medians")
        cached_expires = _cache.get("expires_at")
        if (
            cached_medians is not None
            and isinstance(cached_expires, float)
            and now < cached_expires
        ):
            return cached_medians  # type: ignore[return-value]

    medians = _fetch_stage_medians_ms(supabase)

    with _cache_lock:
        _cache["medians"] = medians
        _cache["expires_at"] = now + _CACHE_TTL_SECONDS

    return medians


def _stage_ms(stage_name: str, medians: dict[str, float]) -> int:
    """stage 별 시간 — DB median 우선, 없으면 fallback."""
    if stage_name in medians:
        return int(medians[stage_name])
    return _FALLBACK_STAGE_MS.get(stage_name, 1000)


def compute_remaining_ms(
    supabase,
    *,
    job_status: str,
    current_stage: str | None,
) -> int | None:
    """Job 의 남은 시간 추정 (ms).

    - completed/failed/cancelled → None (ETA 의미 없음)
    - queued → 전체 stages 합산
    - running + current_stage 알면 → current 부터 마지막까지 합산 (current 포함, 보수적)
    - running + current_stage 없으면 → 전체 합산
    """
    if job_status not in _ACTIVE_JOB_STATUSES:
        return None

    medians = _get_stage_medians_ms(supabase)

    if job_status == "queued" or current_stage is None:
        return sum(_stage_ms(s, medians) for s in STAGE_ORDER)

    try:
        current_idx = STAGE_ORDER.index(current_stage)
    except ValueError:
        # 알 수 없는 stage — 전체 합산 fallback
        return sum(_stage_ms(s, medians) for s in STAGE_ORDER)

    remaining_stages = STAGE_ORDER[current_idx:]
    return sum(_stage_ms(s, medians) for s in remaining_stages)
