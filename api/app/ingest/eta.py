"""W25 D14 Sprint B — 인제스트 ETA (대략적 남은 시간) 계산.

설계 원칙
- 외부 API 비용 0 (Supabase Postgres `ingest_logs` + `vision_usage_log` 만 사용)
- 임베딩 본 파이프라인 무영향 (read-only, batch-status/active 응답 시점 호출)
- N+1 회피: 1쿼리로 최근 N건 ingest_logs 가져와 Python 측에서 stage별 median 집계
- in-memory cache 90s TTL — median 은 자주 안 바뀌므로 cache hit 시 DB hit 0
  (E1 1차 ship: cache 는 medians + vision_p95 만. `stage_progress` 는 매 호출 신선 반영)
- cold start fallback (ingest_logs 0건) 시 hardcoded 추정값 사용. 단 stage 별
  sample <3 이면 ETA None 반환 → 첫 인제스트 misleading ETA 미노출 (E1-A5)

E1 1차 ship (2026-05-07) — sub-stage 분해 + sample<3 None + TTL 단축
- 사용자 보고: 15p PDF 16분 소요인데 화면 ETA 3분 44초. ratio 0.23 (plan §10).
- 원인: extract median 244756ms (4분) baseline + stage_progress 비율 분해만으론
  vision 페이지 수 가중·503 sweep retry burst 미반영.
- E1-A1 해결: extract + stage_progress.unit='pages' 일 때 vision_usage_log p95 latency
  × 남은 페이지로 sub-stage 분해 (in-memory, schema 무변경). text-only PDF/HWP 등
  unit≠'pages' case 는 기존 동작 그대로.
- E1-A4: 5분 TTL → 90초 — 503 wave 직후 baseline 갱신 빠름
- E1-A5: extract fallback 5000 → 120000 (15p vision 보수적). 또한 stage 별 sample <3
  시 전체 ETA None 반환 → 첫 ingest "처음에는 시간 추정이 부정확합니다" 카피 분기.

호환
- `stage_progress=None` 또는 unit≠'pages' → 기존 stage 단위 합산 동작 유지.
- 기존 단위 테스트 (sample <3 case 제외) 그대로 통과.
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
# E1-A5: extract 5000 → 120000 (15p PDF vision 503 wave 반영, 진단 §10.2 p50 244756/2)
_FALLBACK_STAGE_MS: dict[str, int] = {
    "extract": 120000,
    "chunk": 2000,
    "chunk_filter": 1000,
    "content_gate": 500,
    "tag_summarize": 5000,
    "load": 2000,
    "embed": 35000,
    "doc_embed": 1500,
    "dedup": 500,
}

# E1-A1 — vision 페이지당 추정 ms (p95 기반). vision_usage_log 미가용 시 fallback.
# 진단 §10.3 의 "503 wave 시 attempt 3 fail" 누적까지 흡수하려면 p95 가 적절.
_FALLBACK_VISION_PER_PAGE_MS = 30000  # 30s/page 보수적 — 정상 시 5~10s, 503 retry 시 30~60s
_VISION_SWEEP_BUFFER_FACTOR = 1.2     # sweep 2/3 retry 누적 보정

_CACHE_TTL_SECONDS = 90    # E1-A4: 5분 → 90초 (503 wave 직후 baseline 갱신 빠름)
_SAMPLE_LIMIT = 500        # ingest_logs 최근 succeeded sample (1쿼리)
_VISION_SAMPLE_LIMIT = 200 # vision_usage_log 최근 succeeded sample
_MIN_SAMPLES_FOR_ETA = 3   # E1-A5: stage 별 sample <3 시 전체 ETA None 반환
_ACTIVE_JOB_STATUSES = ("queued", "running")

_cache_lock = Lock()
_cache: dict[str, object] = {
    "medians": None,            # dict[stage, float ms] | None
    "vision_per_page_ms": None, # float ms (p95) | None
    "expires_at": 0.0,          # time.monotonic() 기준
}


def reset_cache() -> None:
    """단위 테스트 용 — cache 초기화."""
    with _cache_lock:
        _cache["medians"] = None
        _cache["vision_per_page_ms"] = None
        _cache["expires_at"] = 0.0


def _percentile(values: list[float], p: float) -> float:
    """간단 percentile (linear interpolation 없이 nearest-rank). statistics.quantiles 대체."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    rank = max(0, min(len(sorted_vals) - 1, int(round(p * (len(sorted_vals) - 1)))))
    return float(sorted_vals[rank])


def _fetch_stage_medians_ms(supabase) -> dict[str, float]:
    """ingest_logs 최근 succeeded N건에서 stage별 median(duration_ms) 일괄 계산.

    1쿼리 (N+1 회피). status='succeeded' 인 row 만 — failed/started 는 timing 의미 없음.
    sample <_MIN_SAMPLES_FOR_ETA 인 stage 는 키 누락 (caller 에서 fallback 또는 None 처리).
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
        if len(durations) >= _MIN_SAMPLES_FOR_ETA:
            medians_ms[stage_name] = float(median(durations))
    return medians_ms


def _fetch_vision_per_page_ms(supabase) -> float | None:
    """vision_usage_log 최근 succeeded 호출의 latency p95 (페이지당 추정용).

    503 retry/sweep burst 자연 흡수 위해 p50 대신 p95 사용 (진단 §10.2 근거).
    sample <_MIN_SAMPLES_FOR_ETA 또는 테이블 미존재(D2 미진입) → None.
    """
    try:
        resp = (
            supabase.table("vision_usage_log")
            .select("latency_ms, success")
            .eq("success", True)
            .order("called_at", desc=True)
            .limit(_VISION_SAMPLE_LIMIT)
            .execute()
        )
        rows = resp.data or []
    except Exception as exc:  # noqa: BLE001 — D2 미진입 환경 graceful
        logger.debug("vision_usage_log sampling 실패 — fallback 사용: %s", exc)
        return None

    latencies: list[float] = []
    for row in rows:
        latency = row.get("latency_ms")
        if isinstance(latency, (int, float)) and latency > 0:
            latencies.append(float(latency))
    if len(latencies) < _MIN_SAMPLES_FOR_ETA:
        return None
    return _percentile(latencies, 0.95)


def _get_cached_baselines(
    supabase,
) -> tuple[dict[str, float], float | None]:
    """90s TTL cache. 동시 호출 race 회피용 lock.

    Returns:
        (stage_medians_ms, vision_per_page_p95_ms_or_none)
    """
    now = time.monotonic()
    with _cache_lock:
        cached_medians = _cache.get("medians")
        cached_vision = _cache.get("vision_per_page_ms")
        cached_expires = _cache.get("expires_at")
        if (
            cached_medians is not None
            and isinstance(cached_expires, float)
            and now < cached_expires
        ):
            return (
                cached_medians,  # type: ignore[return-value]
                cached_vision if isinstance(cached_vision, float) else None,
            )

    medians = _fetch_stage_medians_ms(supabase)
    vision_per_page = _fetch_vision_per_page_ms(supabase)

    with _cache_lock:
        _cache["medians"] = medians
        _cache["vision_per_page_ms"] = vision_per_page
        _cache["expires_at"] = now + _CACHE_TTL_SECONDS

    return medians, vision_per_page


def _stage_ms(stage_name: str, medians: dict[str, float]) -> int:
    """stage 별 시간 — DB median 우선, 없으면 fallback."""
    if stage_name in medians:
        return int(medians[stage_name])
    return _FALLBACK_STAGE_MS.get(stage_name, 1000)


def _vision_remaining_ms(
    stage_progress: dict,
    vision_per_page_ms: float | None,
) -> int | None:
    """E1-A1 — extract 의 vision sub-stage 남은 시간.

    `stage_progress.unit == 'pages'` + `total > 0` 인 경우만 활성.
    `(total - current) × per_page_p95 × sweep_buffer_factor`
    vision_usage_log 미가용 시 _FALLBACK_VISION_PER_PAGE_MS 사용.

    None 반환 시 caller 가 sub-stage 분해 비활성 (unit≠'pages' 또는 total 무효).
    """
    if not stage_progress:
        return None
    if stage_progress.get("unit") != "pages":
        return None
    current = stage_progress.get("current")
    total = stage_progress.get("total")
    if not isinstance(current, (int, float)) or not isinstance(total, (int, float)):
        return None
    if total <= 0:
        return None
    remaining_pages = max(0.0, float(total) - float(current))
    per_page_ms = (
        vision_per_page_ms
        if vision_per_page_ms is not None
        else float(_FALLBACK_VISION_PER_PAGE_MS)
    )
    return int(remaining_pages * per_page_ms * _VISION_SWEEP_BUFFER_FACTOR)


def _current_stage_remaining_ms(
    stage_name: str,
    medians: dict[str, float],
    stage_progress: dict | None,
    vision_per_page_ms: float | None,
) -> int:
    """현재 stage 의 남은 시간 (ms).

    분기 우선순위:
    1. extract + unit='pages' (vision_enrich 활성 PDF) → vision sub-stage 분해.
       vision_remaining = (total - current) × p95 × 1.2.
       단순 p95 외삽이라 medians 와 분리.
    2. stage_progress.total>0 (vision 외 sub-step) → stage_ms × (1 - current/total).
    3. 그 외 → stage_ms 전체 (queued/cold start 보호).
    """
    full_ms = _stage_ms(stage_name, medians)

    # E1-A1 — extract 만 vision sub-stage 분해 (unit='pages' 명시 시)
    if stage_name == "extract":
        vision_remaining = _vision_remaining_ms(stage_progress, vision_per_page_ms)
        if vision_remaining is not None:
            return vision_remaining

    if not stage_progress:
        return full_ms
    current = stage_progress.get("current")
    total = stage_progress.get("total")
    if not isinstance(current, (int, float)) or not isinstance(total, (int, float)):
        return full_ms
    if total <= 0:
        return full_ms
    ratio = max(0.0, min(1.0, float(current) / float(total)))
    return int(full_ms * (1.0 - ratio))


def _has_any_baseline(medians: dict[str, float]) -> bool:
    """E1-A5 — 첫 ingest (cold start) 차단용 sentinel.

    medians 에 어느 stage 든 sample >=3 이면 True. 즉 한 번이라도 인제스트가 끝났으면
    이후 ETA 노출 활성. 한 stage 만 있어도 다른 stage 는 fallback 으로 합산 가능.

    False (cold start) → ETA None → web "처음에는 시간 추정이 부정확합니다" 카피.
    """
    return bool(medians)


def compute_remaining_ms(
    supabase,
    *,
    job_status: str,
    current_stage: str | None,
    stage_progress: dict | None = None,
) -> int | None:
    """Job 의 남은 시간 추정 (ms).

    - completed/failed/cancelled → None (ETA 의미 없음)
    - 합산 대상 stage 의 medians sample 부족 → None (E1-A5: 첫 ingest 안내 카피 분기)
    - queued → 전체 stages 합산 (`stage_progress` 무시)
    - running + current_stage 알면 → current 의 남은 비율 + 이후 stages 합산
      · extract + unit='pages' → vision_usage_log p95 × 남은 페이지 × 1.2 (E1-A1)
      · 일반 sub-progress → stage_ms × (1 - current/total)
      · 없거나 total<=0 → current 전체 (기존 보수적 동작)
    - running + current_stage 없으면 → 전체 합산
    """
    if job_status not in _ACTIVE_JOB_STATUSES:
        return None

    medians, vision_per_page_ms = _get_cached_baselines(supabase)

    # E1-A5: cold start (어느 stage 도 sample <3) → ETA None
    # → web "처음에는 시간 추정이 부정확합니다" 카피 분기
    if not _has_any_baseline(medians):
        return None

    if job_status == "queued" or current_stage is None:
        return sum(_stage_ms(s, medians) for s in STAGE_ORDER)

    try:
        current_idx = STAGE_ORDER.index(current_stage)
    except ValueError:
        # 알 수 없는 stage — 전체 합산 fallback
        return sum(_stage_ms(s, medians) for s in STAGE_ORDER)

    current_remaining = _current_stage_remaining_ms(
        current_stage, medians, stage_progress, vision_per_page_ms
    )
    later_stages = STAGE_ORDER[current_idx + 1 :]
    return current_remaining + sum(_stage_ms(s, medians) for s in later_stages)
