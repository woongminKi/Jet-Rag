"""admin 영역 통계 endpoint 모음.

- `GET /admin/queries/stats` — S1 D3 ship. `search_metrics_log` 기반 query 로그 대시보드.
- `GET /admin/feedback/stats` — S1 D4 ship (master plan §6). `answer_feedback` 기반
  사용자 평가(👍/👎) 누적 + 코멘트 룰 분류 (검색/답변/출처/그 외) 대시보드.

single-user MVP 라 별도 인증 없음 (production 진입 시 별도 sprint).

설계 메모
- 두 endpoint 모두 동일한 KST 일별 GROUP BY 패턴 + `error_code='migrations_pending'`
  graceful fallback. queries 패턴을 그대로 답변 피드백에 이식.
- 코멘트 자동 분류는 룰 기반 (LLM 호출 0, 비용 0). 1주 누적 후 D3 처럼 실 데이터로
  룰 정합성 검증. 매칭 우선순위: source_issue > search_issue > answer_issue → 그 외 other.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.db import get_supabase_client
from app.services.query_classifier import QUERY_TYPE_LABELS, classify_query_type

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

# 한국 시간대 — single-user MVP 기준 하드코딩 (`stats.py` 와 동일).
KST = timezone(timedelta(hours=9))

# range 쿼리 → 일수 변환. master plan §6 S1 D3 권고 — 7d/14d/30d 3종.
_RANGE_TO_DAYS: dict[str, int] = {"7d": 7, "14d": 14, "30d": 30}

# 실패 케이스 응답에 포함할 최근 샘플 수 — UI 가 한 화면에서 훑을 수 있는 양.
_FAILED_SAMPLES_LIMIT = 10

# 9 query_type 라벨 — `app.services.query_classifier.QUERY_TYPE_LABELS` 재사용
# (S1 D3 시점에는 `evals/auto_goldenset.py` 가 단일 source 였으나 본 모듈로 이전됨).
# 응답 schema 에 항상 9개 키 노출 (sample 0건이라도) — frontend 0건 행 표기 용이.
_QUERY_TYPE_LABELS: tuple[str, ...] = QUERY_TYPE_LABELS


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
    - error_code='classify_unavailable': **deprecated** (`evals/auto_goldenset.py` →
      `app/services/query_classifier.py` 이전 후 classifier 가 production 모듈이라
      import 실패 케이스 없음). schema 후방 호환 위해 Literal 유지.
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
        error_code=classify_error,  # 항상 None (classify_unavailable 은 deprecated)
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
    """9 라벨 분포 측정.

    9 키 모두 0 으로 초기화 — frontend 가 0건 라벨도 row 로 그릴 수 있도록.
    classifier 는 production 모듈 (`app.services.query_classifier`) 이라 import
    실패 케이스 없음 (`classify_unavailable` error_code 는 deprecated).
    """
    distribution: dict[str, int] = {label: 0 for label in _QUERY_TYPE_LABELS}
    counter: Counter[str] = Counter()
    for row in rows:
        query = (row.get("query_text") or "").strip()
        if not query:
            continue
        try:
            label = classify_query_type(query)
        except Exception as exc:  # noqa: BLE001 — classifier 자체 방어
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


# ============================================================================
# S1 D4 ship — `GET /admin/feedback/stats` (answer_feedback 통합 분석)
# ============================================================================
#
# answer_feedback (마이그 011) 기반 사용자 평가 누적 + 코멘트 룰 분류.
# DoD: 1주 누적 후 사용자 평가 누적 신호 확인 → 답변 품질 회귀 추적의 사전 자료.

# 코멘트 카테고리 4종 — 룰 기반 분류. master plan §6 S1 D4 명세.
_COMMENT_CATEGORIES: tuple[str, ...] = (
    "search_issue",  # 검색 결과 문제
    "answer_issue",  # 답변 정확도 문제
    "source_issue",  # 출처/근거 문제
    "other",  # 그 외 (분류 불가)
)

# 룰 매칭 키워드 — 한국어 사용자 코멘트 패턴 가정. 1주 누적 후 D3 처럼 실 데이터로 검증.
# 매칭 우선순위는 _classify_comment 함수 내 dict iteration 순서 + 코드 흐름이 결정.
_KEYWORDS_SOURCE_ISSUE: tuple[str, ...] = (
    "출처", "근거 없", "어디서", "인용", "페이지", "이상한 자료",
)
_KEYWORDS_SEARCH_ISSUE: tuple[str, ...] = (
    "검색", "찾을 수 없", "관련 없", "나오지 않", "chunk", "검색 결과",
)
_KEYWORDS_ANSWER_ISSUE: tuple[str, ...] = (
    "답변", "정확하지 않", "잘못", "틀린", "오답", "환각",
)

# 최근 코멘트 (코멘트 첨부된 것만) 노출 수.
_RECENT_COMMENTS_LIMIT = 10


def classify_comment(text: str) -> str:
    """사용자 코멘트를 4 카테고리 중 하나로 분류.

    매칭 우선순위 — 출처가 명시된 코멘트는 검색·답변 보다 명확한 신호이므로
    `source_issue` 가 최우선. 그 다음 검색 → 답변 → 그 외.

    빈 문자열 / 공백만 / 키워드 매칭 0건 → "other".
    """
    if not text:
        return "other"
    normalized = text.strip().lower()
    if not normalized:
        return "other"
    # 1순위: source_issue (출처/근거 명시) — 가장 구체적인 불만.
    if any(kw in normalized for kw in _KEYWORDS_SOURCE_ISSUE):
        return "source_issue"
    # 2순위: search_issue (검색 결과 문제) — chunk 가 안 잡힌 케이스.
    if any(kw in normalized for kw in _KEYWORDS_SEARCH_ISSUE):
        return "search_issue"
    # 3순위: answer_issue (답변 정확도) — chunk 는 잡혔지만 LLM 답변 자체가 틀린 케이스.
    if any(kw in normalized for kw in _KEYWORDS_ANSWER_ISSUE):
        return "answer_issue"
    return "other"


class FeedbackDailyBucket(BaseModel):
    """일별 👍/👎 카운트 — KST 기준 YYYY-MM-DD."""

    date: str  # YYYY-MM-DD (KST)
    up: int
    down: int
    total: int


class FeedbackComment(BaseModel):
    """최근 코멘트 1건 — 코멘트 첨부된 것만 노출."""

    query: str
    rating: Literal["up", "down"]
    comment: str
    category: Literal["search_issue", "answer_issue", "source_issue", "other"]
    ts: str  # ISO 8601 (UTC)


class AdminFeedbackStatsResponse(BaseModel):
    """`GET /admin/feedback/stats` 응답.

    - error_code='migrations_pending': 마이그 011 미적용 환경. 모든 집계 빈 값.
    - satisfaction_rate: 전체 sample 0건 시 None.
    - rating_distribution: 항상 2 키 (up/down) 노출.
    - comment_categories: 항상 4 키 노출 (sample 0건이라도).
    - recent_comments: 코멘트 첨부된 최근 N건. up/down 무관.
    """

    range: Literal["7d", "14d", "30d"]
    daily: list[FeedbackDailyBucket]
    rating_distribution: dict[str, int]
    satisfaction_rate: float | None
    comment_categories: dict[str, int]
    recent_comments: list[FeedbackComment]
    total_feedback: int
    comment_count: int
    error_code: str | None = None
    generated_at: str


@router.get("/feedback/stats", response_model=AdminFeedbackStatsResponse)
def admin_feedback_stats(
    range: Literal["7d", "14d", "30d"] = Query("7d", description="조회 범위"),
) -> AdminFeedbackStatsResponse:
    """`answer_feedback` 기반 사용자 평가 누적 통계.

    1. 마이그 011 미적용 환경 graceful → `error_code='migrations_pending'`
    2. 정상: 일별 GROUP BY (KST 일자) + rating 분포 + 코멘트 4 카테고리 + 최근 10건
    """
    generated_at = datetime.now(timezone.utc).isoformat()
    days = _RANGE_TO_DAYS[range]
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # ---- DB SELECT ----
    try:
        supabase = get_supabase_client()
        rows = (
            supabase.table("answer_feedback")
            .select("created_at, helpful, comment, query")
            .gte("created_at", since.isoformat())
            .order("created_at", desc=True)
            .execute()
            .data
            or []
        )
    except Exception as exc:  # noqa: BLE001 — 마이그 011 미적용 graceful
        logger.warning("admin_feedback_stats DB graceful skip: %s", exc)
        return AdminFeedbackStatsResponse(
            range=range,
            daily=[],
            rating_distribution={"up": 0, "down": 0},
            satisfaction_rate=None,
            comment_categories={k: 0 for k in _COMMENT_CATEGORIES},
            recent_comments=[],
            total_feedback=0,
            comment_count=0,
            error_code="migrations_pending",
            generated_at=generated_at,
        )

    # ---- 일별 집계 (KST) ----
    daily_buckets = _build_feedback_daily_buckets(rows, days)

    # ---- rating 분포 ----
    up_count = sum(1 for r in rows if r.get("helpful") is True)
    down_count = sum(1 for r in rows if r.get("helpful") is False)
    total = up_count + down_count
    satisfaction = round(up_count / total, 4) if total else None

    # ---- 코멘트 카테고리 + 최근 N건 ----
    categories, recent_comments = _build_comment_analysis(rows)

    return AdminFeedbackStatsResponse(
        range=range,
        daily=daily_buckets,
        rating_distribution={"up": up_count, "down": down_count},
        satisfaction_rate=satisfaction,
        comment_categories=categories,
        recent_comments=recent_comments,
        total_feedback=total,
        comment_count=sum(categories.values()),
        error_code=None,
        generated_at=generated_at,
    )


# ---------------------- feedback helpers ----------------------


def _build_feedback_daily_buckets(
    rows: list[dict], days: int
) -> list[FeedbackDailyBucket]:
    """일별 👍/👎 집계 — KST 자정 기준. 빈 날짜도 0 row 로 채움 (sparkline zero-fill).

    오래된 → 최신 순서 (queries 의 `_build_daily_buckets` 와 동일 패턴).
    """
    today_kst = datetime.now(KST).date()
    counts: dict[str, dict[str, int]] = {}
    for row in rows:
        recorded_at = _parse_recorded_at_kst(row.get("created_at"))
        if recorded_at is None:
            continue
        date_str = recorded_at.date().isoformat()
        bucket = counts.setdefault(date_str, {"up": 0, "down": 0})
        # helpful 컬럼은 NOT NULL BOOLEAN — None 방어는 로깅 차원만.
        if row.get("helpful") is True:
            bucket["up"] += 1
        elif row.get("helpful") is False:
            bucket["down"] += 1

    result: list[FeedbackDailyBucket] = []
    for i in range(days - 1, -1, -1):
        d = today_kst - timedelta(days=i)
        date_str = d.isoformat()
        bucket = counts.get(date_str, {"up": 0, "down": 0})
        result.append(
            FeedbackDailyBucket(
                date=date_str,
                up=bucket["up"],
                down=bucket["down"],
                total=bucket["up"] + bucket["down"],
            )
        )
    return result


def _build_comment_analysis(
    rows: list[dict],
) -> tuple[dict[str, int], list[FeedbackComment]]:
    """코멘트 카테고리 분포 + 최근 N건 (코멘트 첨부된 것만).

    rows 는 `created_at desc` 정렬 입력 가정 — 그대로 순회해 최근 N건 컷.
    빈 코멘트 (None / 공백만) 는 분류·노출 모두 X.
    """
    categories: dict[str, int] = {k: 0 for k in _COMMENT_CATEGORIES}
    comments: list[FeedbackComment] = []
    for row in rows:
        raw = (row.get("comment") or "").strip()
        if not raw:
            continue
        category = classify_comment(raw)
        categories[category] += 1
        if len(comments) < _RECENT_COMMENTS_LIMIT:
            helpful = row.get("helpful")
            rating: Literal["up", "down"] = "up" if helpful else "down"
            comments.append(
                FeedbackComment(
                    query=(row.get("query") or "")[:200],
                    rating=rating,
                    comment=raw[:500],
                    category=category,  # type: ignore[arg-type]
                    ts=str(row.get("created_at") or ""),
                )
            )
    return categories, comments
