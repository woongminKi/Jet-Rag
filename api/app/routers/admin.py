"""GET /admin/queries/stats — 실 query 로그 시각화 대시보드 backend.

S1 D3 ship (master plan §6) — `search_metrics_log` 기반 일별 query 분포 + 9 query_type
자동 분류 + 실패 케이스 추출. 1주 누적 후 실 query 분포 확인 → S1 D5 모델 회귀 측정의
사전 자료. single-user MVP 라 별도 인증 없음 (production 진입 시 별도 sprint).

설계 메모
- `search_metrics_log` 직접 SELECT (mig 006). 일별 GROUP BY 만 필요해 RPC 신설 X.
- `query_type` 자동 분류 — `evals/auto_goldenset.py:classify_query_type` 재사용
  (기존 `tests/test_auto_goldenset.py:21` 의 sys.path 보정 패턴 동일).
- 마이그 006 미적용 환경 graceful — `error_code='migrations_pending'` (mig stats_trend 패턴).
- 실패 케이스 분류: `fallback_reason` 우선 → `permanent_4xx`/`transient_5xx`,
  그 외 `fused == 0` 이면 `no_hits`. 그 외는 success.
"""

from __future__ import annotations

import logging
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.db import get_supabase_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

# 한국 시간대 — single-user MVP 기준 하드코딩 (`stats.py` 와 동일).
KST = timezone(timedelta(hours=9))

# range 쿼리 → 일수 변환. master plan §6 S1 D3 권고 — 7d/14d/30d 3종.
_RANGE_TO_DAYS: dict[str, int] = {"7d": 7, "14d": 14, "30d": 30}

# 실패 케이스 응답에 포함할 최근 샘플 수 — UI 가 한 화면에서 훑을 수 있는 양.
_FAILED_SAMPLES_LIMIT = 10

# 9 query_type 라벨 — `auto_goldenset._QUERY_TYPE_LABELS` 와 동기.
# 응답 schema 에 항상 9개 키 노출 (sample 0건이라도) — frontend 0건 행 표기 용이.
_QUERY_TYPE_LABELS: tuple[str, ...] = (
    "exact_fact",
    "fuzzy_memory",
    "vision_diagram",
    "table_lookup",
    "numeric_lookup",
    "cross_doc",
    "summary",
    "synonym_mismatch",
    "out_of_scope",
)


# `evals/auto_goldenset.py` 의 분류 함수 lazy import — sys.path 보정 1회.
# import 실패 시 (eval 모듈 미존재) frontend 가 query_type_distribution 빈 dict 로 안내.
def _import_classify_query_type():
    """evals/ 하위의 classify_query_type 함수를 lazy import.

    test_auto_goldenset.py:21 와 동일 패턴 — `<repo>/evals/` 를 sys.path 에 1회 추가.
    """
    evals_dir = Path(__file__).resolve().parents[3] / "evals"
    if str(evals_dir) not in sys.path:
        sys.path.insert(0, str(evals_dir))
    from auto_goldenset import classify_query_type  # type: ignore

    return classify_query_type


class DailyBucket(BaseModel):
    """일별 query 카운트 — KST 기준 YYYY-MM-DD."""

    date: str  # YYYY-MM-DD (KST)
    count: int
    success_count: int
    fail_count: int


class FailedSample(BaseModel):
    """실패 샘플 1건 — 최근 N건만 노출.

    reason: `permanent_4xx` / `transient_5xx` / `no_hits`
    """

    query: str
    ts: str  # ISO 8601 (UTC)
    reason: Literal["permanent_4xx", "transient_5xx", "no_hits"]


class AdminQueriesStatsResponse(BaseModel):
    """`GET /admin/queries/stats` 응답.

    - error_code='migrations_pending': 마이그 006 미적용 환경. 모든 집계 빈 값.
    - error_code='classify_unavailable': evals 모듈 import 실패. distribution 빈 dict.
    """

    range: Literal["7d", "14d", "30d"]
    daily: list[DailyBucket]
    query_type_distribution: dict[str, int]
    failed_samples: list[FailedSample]
    total_queries: int
    success_rate: float | None  # sample 0건 시 None
    avg_latency_ms: int | None  # sample 0건 시 None
    error_code: str | None = None
    generated_at: str


@router.get("/queries/stats", response_model=AdminQueriesStatsResponse)
def admin_queries_stats(
    range: Literal["7d", "14d", "30d"] = Query("7d", description="조회 범위"),
) -> AdminQueriesStatsResponse:
    """`search_metrics_log` 기반 실 query 통계.

    1. 마이그 006 미적용 환경 graceful → `error_code='migrations_pending'`
    2. 정상: 일별 GROUP BY (KST 일자) + 9 query_type 분포 + 실패 최근 10건
    """
    generated_at = datetime.now(timezone.utc).isoformat()
    days = _RANGE_TO_DAYS[range]
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # ---- DB SELECT ----
    try:
        supabase = get_supabase_client()
        rows = (
            supabase.table("search_metrics_log")
            .select("recorded_at, took_ms, fused, fallback_reason, query_text")
            .gte("recorded_at", since.isoformat())
            .order("recorded_at", desc=True)
            .execute()
            .data
            or []
        )
    except Exception as exc:  # noqa: BLE001 — 마이그 006 미적용 graceful
        logger.warning("admin_queries_stats DB graceful skip: %s", exc)
        return AdminQueriesStatsResponse(
            range=range,
            daily=[],
            query_type_distribution={},
            failed_samples=[],
            total_queries=0,
            success_rate=None,
            avg_latency_ms=None,
            error_code="migrations_pending",
            generated_at=generated_at,
        )

    # ---- 일별 집계 (KST) ----
    daily_buckets = _build_daily_buckets(rows, days)

    # ---- query_type 분포 ----
    distribution, classify_error = _build_query_type_distribution(rows)

    # ---- 실패 샘플 ----
    failed_samples = _extract_failed_samples(rows)

    total = len(rows)
    success = sum(1 for r in rows if _row_is_success(r))
    success_rate = round(success / total, 4) if total else None

    latencies = [r.get("took_ms") for r in rows if r.get("took_ms") is not None]
    avg_latency = int(sum(latencies) / len(latencies)) if latencies else None

    return AdminQueriesStatsResponse(
        range=range,
        daily=daily_buckets,
        query_type_distribution=distribution,
        failed_samples=failed_samples,
        total_queries=total,
        success_rate=success_rate,
        avg_latency_ms=avg_latency,
        error_code=classify_error,  # None 또는 'classify_unavailable'
        generated_at=generated_at,
    )


# ---------------------- helpers ----------------------


def _row_is_success(row: dict) -> bool:
    """row 가 성공 query 인지 판단.

    - fallback_reason 이 있으면 실패 (permanent_4xx / transient_5xx).
    - fused == 0 이면 hit 0건 → no_hits 실패.
    - 그 외는 성공.
    """
    if row.get("fallback_reason"):
        return False
    fused = row.get("fused")
    return fused is not None and fused > 0


def _classify_failure_reason(row: dict) -> Literal[
    "permanent_4xx", "transient_5xx", "no_hits"
] | None:
    """실패 row 의 reason 분류. 성공 row 면 None."""
    fr = row.get("fallback_reason")
    if fr in ("permanent_4xx", "transient_5xx"):
        return fr
    fused = row.get("fused")
    if fused is not None and fused == 0:
        return "no_hits"
    return None


def _parse_recorded_at_kst(value: str | None) -> datetime | None:
    """Supabase TIMESTAMPTZ ISO 문자열 → KST tz-aware datetime."""
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST)


def _build_daily_buckets(rows: list[dict], days: int) -> list[DailyBucket]:
    """일별 집계 — KST 자정 기준. 빈 날짜도 0 row 로 채움 (sparkline zero-fill).

    오래된 → 최신 순서로 반환 (시계열 그래프 left-to-right 자연 순).
    """
    today_kst = datetime.now(KST).date()
    counts: dict[str, dict[str, int]] = {}
    for row in rows:
        recorded_at = _parse_recorded_at_kst(row.get("recorded_at"))
        if recorded_at is None:
            continue
        date_str = recorded_at.date().isoformat()
        bucket = counts.setdefault(
            date_str, {"count": 0, "success_count": 0, "fail_count": 0}
        )
        bucket["count"] += 1
        if _row_is_success(row):
            bucket["success_count"] += 1
        else:
            bucket["fail_count"] += 1

    result: list[DailyBucket] = []
    # days 일 전 ~ 오늘. 빈 날짜는 0 으로 채움.
    for i in range(days - 1, -1, -1):
        d = today_kst - timedelta(days=i)
        date_str = d.isoformat()
        bucket = counts.get(
            date_str, {"count": 0, "success_count": 0, "fail_count": 0}
        )
        result.append(
            DailyBucket(
                date=date_str,
                count=bucket["count"],
                success_count=bucket["success_count"],
                fail_count=bucket["fail_count"],
            )
        )
    return result


def _build_query_type_distribution(
    rows: list[dict],
) -> tuple[dict[str, int], str | None]:
    """9 라벨 분포 측정. classify import 실패 시 빈 dict + error_code.

    빈 dict 가 아니라 `_QUERY_TYPE_LABELS` 9 키 모두 0 으로 초기화 — frontend
    가 0건 라벨도 row 로 그릴 수 있도록.
    """
    distribution: dict[str, int] = {label: 0 for label in _QUERY_TYPE_LABELS}
    try:
        classify = _import_classify_query_type()
    except Exception as exc:  # noqa: BLE001 — evals 모듈 부재 graceful
        logger.warning("classify_query_type import 실패 — distribution skip: %s", exc)
        return {}, "classify_unavailable"

    counter: Counter[str] = Counter()
    for row in rows:
        query = (row.get("query_text") or "").strip()
        if not query:
            continue
        try:
            label = classify(query)
        except Exception as exc:  # noqa: BLE001
            logger.debug("classify 호출 실패 query=%r: %s", query[:80], exc)
            continue
        counter[label] += 1

    for label, count in counter.items():
        # 9 라벨 외 값이 와도 distribution 에 추가 (방어적).
        distribution[label] = count
    return distribution, None


def _extract_failed_samples(rows: list[dict]) -> list[FailedSample]:
    """실패 row 중 최근 N건 — `recorded_at desc` 정렬 입력 가정 (caller)."""
    samples: list[FailedSample] = []
    for row in rows:
        if len(samples) >= _FAILED_SAMPLES_LIMIT:
            break
        reason = _classify_failure_reason(row)
        if reason is None:
            continue
        samples.append(
            FailedSample(
                query=(row.get("query_text") or "")[:200],
                ts=str(row.get("recorded_at") or ""),
                reason=reason,
            )
        )
    return samples
