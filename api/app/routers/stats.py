"""GET /stats — 전체 시스템 통계 (검증 UX).

- 브라우저 /docs 에서 한 번 눌러보면 총 문서·청크·jobs 상태를 한눈에
- 단일 사용자 MVP 기준이라 `documents.user_id = DEFAULT_USER_ID` 필터만 적용
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.config import get_settings
from app.db import get_supabase_client
from app.services import search_metrics, vision_metrics

logger = logging.getLogger(__name__)

router = APIRouter(tags=["stats"])

# 한국 시간대 — 단일 사용자 MVP 기준이라 하드코딩
KST = timezone(timedelta(hours=9))

# 기획서 §10.11 — 수신 응답 < 2초 SLO
SLO_TARGET_MS = 2000
# pdf_50p 버킷의 size 임계값 — 50MB 한도의 절반 (큰 PDF 시나리오 대표값)
PDF_50P_THRESHOLD_BYTES = 25 * 1024 * 1024


class DocumentsStats(BaseModel):
    total: int
    by_doc_type: dict[str, int]
    by_source_channel: dict[str, int]
    extract_skipped: int
    total_size_bytes: int
    added_this_month: int  # KST 이번 달 1일 00:00 이후 추가된 문서 수
    added_last_7d: int  # KST 기준 최근 7일(=168시간) 내 추가된 문서 수
    failed_count: int  # 인제스트 실패로 chunks 가 비어있는 문서 수 (집계 자체에서는 제외)


class JobsStats(BaseModel):
    total: int
    by_status: dict[str, int]
    failed_sample: list[dict]  # 최근 실패 5건 요약 (에러 디버그용)


class TagCount(BaseModel):
    tag: str
    count: int


class SloBucketStats(BaseModel):
    """`/stats.slo_buckets` 의 버킷별 측정값.

    - p95_ms: received_ms 95퍼센타일 (sample 0건이면 None)
    - sample_count: 해당 버킷에 속한 documents 수 (received_ms IS NOT NULL 만)
    - pass_rate: received_ms < 2000 인 비율 (0.0 ~ 1.0). sample 0건이면 None
    """
    p95_ms: int | None
    sample_count: int
    pass_rate: float | None


class SearchSloStats(BaseModel):
    """`/search` SLO 측정값 — W3 Day 2 Phase 3.

    `app/services/search_metrics.py` 의 in-memory ring buffer (최근 500건) 기반.
    프로세스 재시작 시 휘발 — W4-Q-16 에서 DB 영속화 검토.

    `fallback_breakdown` 키:
      - transient_5xx: HF API 일시 오류 → sparse-only fallback (200 응답)
      - permanent_4xx: HF API 영구 오류 → 503 raise (가시성 위해 record)
      - none: dense path 정상

    W4-Q-3 신규:
      - cache_hit_count: `embed_query` LRU hit 횟수 (전체 샘플 중)
      - cache_hit_rate: hit 비율 (0.0 ~ 1.0). sample 0건이면 None
    """
    p50_ms: int | None
    p95_ms: int | None
    sample_count: int
    avg_dense_hits: float | None
    avg_sparse_hits: float | None
    avg_fused: float | None
    fallback_count: int
    fallback_breakdown: dict[str, int]
    cache_hit_count: int = 0
    cache_hit_rate: float | None = None
    # W14 Day 3 (한계 #77) — mode 별 분리 측정. 같은 schema 가 mode 별로 반복.
    # 키: hybrid / dense / sparse — 항상 노출 (sample 0 이라도).
    by_mode: dict[str, dict] = {}


class IngestSloAggregate(BaseModel):
    """W12 Day 2 — 인제스트 SLO 달성률 KPI (기획서 §13.1).

    5 SLO 버킷 (pdf_50p · image · pdf_scan · hwp · url) 의 sample_count 가중 평균.
    각 버킷 pass_rate (received_ms < 2000) 를 sample 수로 가중.

    - total_samples: 5 버킷 sample_count 합 (received_ms 측정된 docs)
    - overall_pass_rate: 가중 평균 — sample 0건이면 None
    - buckets_with_samples: sample > 0 인 버킷 이름 리스트 (KPI 측정 가능 버킷)
    """
    total_samples: int
    overall_pass_rate: float | None
    buckets_with_samples: list[str]


class VisionUsageStats(BaseModel):
    """W8 Day 4 — Vision API 호출 누적 카운트 (한계 #29).
    W11 Day 1 — last_quota_exhausted_at 추가 (한계 #38 lite).

    in-memory counter (vision_metrics 모듈) 의 스냅샷. 프로세스 재시작 시 휘발.
    Gemini Flash RPD 20 무료 티어 cap 모니터링 기준.
    """
    total_calls: int
    success_calls: int
    error_calls: int
    last_called_at: str | None
    last_quota_exhausted_at: str | None = None


class ChunksStats(BaseModel):
    """W7 Day 3 — chunks 단위 가시성 (DE-65 후 1256 환경 + chunk_filter 마킹 추적).

    - total: 전체 chunks 수
    - effective: 검색 대상 (flags.filtered_reason IS NULL)
    - filtered_breakdown: 마킹 사유별 카운트 (table_noise · header_footer · empty · extreme_short)
    - filtered_ratio: 마킹 비율 (0.0 ~ 1.0)
    """
    total: int
    effective: int
    filtered_breakdown: dict[str, int]
    filtered_ratio: float


class StatsResponse(BaseModel):
    documents: DocumentsStats
    chunks_total: int  # backward compatible — chunks.total 과 동일
    chunks: ChunksStats  # W7 Day 3 신규
    jobs: JobsStats
    popular_tags: list[TagCount]  # 사용 빈도 top-10
    slo_buckets: dict[str, SloBucketStats]  # W2 §3.A: pdf_50p · image · pdf_scan · hwp · url
    ingest_slo_aggregate: IngestSloAggregate  # W12 Day 2 — 5 버킷 가중 평균 (KPI §13.1)
    search_slo: SearchSloStats  # W3 Day 2 Phase 3 — `/search` p50/p95/fallback 분포
    vision_usage: VisionUsageStats  # W8 Day 4 — Gemini Vision 호출 카운트 (한계 #29)
    generated_at: str


@router.get("/stats", response_model=StatsResponse)
def stats() -> StatsResponse:
    supabase = get_supabase_client()
    user_id = get_settings().default_user_id

    # ---- documents ----
    all_docs = (
        supabase.table("documents")
        .select(
            "doc_type, source_channel, size_bytes, flags, tags, "
            "created_at, received_ms"  # W2 §3.A SLO 측정용
        )
        .eq("user_id", user_id)
        .is_("deleted_at", "null")
        .execute()
        .data
        or []
    )

    # 인제스트 실패 문서는 모든 집계에서 제외 — failed_count 만 별도로 노출
    failed_docs = [d for d in all_docs if (d.get("flags") or {}).get("failed")]
    docs = [d for d in all_docs if not (d.get("flags") or {}).get("failed")]
    failed_count = len(failed_docs)

    now_kst = datetime.now(KST)
    month_start = now_kst.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    week_ago = now_kst - timedelta(days=7)

    by_doc_type: dict[str, int] = {}
    by_source_channel: dict[str, int] = {}
    total_size = 0
    extract_skipped = 0
    added_this_month = 0
    added_last_7d = 0
    for d in docs:
        by_doc_type[d["doc_type"]] = by_doc_type.get(d["doc_type"], 0) + 1
        by_source_channel[d["source_channel"]] = (
            by_source_channel.get(d["source_channel"], 0) + 1
        )
        total_size += d["size_bytes"] or 0
        if (d.get("flags") or {}).get("extract_skipped"):
            extract_skipped += 1

        created_at_kst = _parse_created_at_kst(d.get("created_at"))
        if created_at_kst is not None:
            if created_at_kst >= month_start:
                added_this_month += 1
            if created_at_kst >= week_ago:
                added_last_7d += 1

    tag_counter = Counter(tag for d in docs for tag in (d.get("tags") or []))
    popular_tags = [
        TagCount(tag=t, count=c) for t, c in tag_counter.most_common(10)
    ]

    # ---- chunks ----
    chunks_resp = supabase.table("chunks").select("id", count="exact").execute()
    chunks_total = chunks_resp.count or 0
    chunks_stats = _compute_chunks_stats(supabase, chunks_total)

    # ---- jobs ----
    jobs = (
        supabase.table("ingest_jobs")
        .select("status")
        .execute()
        .data
        or []
    )
    by_status: dict[str, int] = {}
    for j in jobs:
        by_status[j["status"]] = by_status.get(j["status"], 0) + 1

    failed_resp = (
        supabase.table("ingest_jobs")
        .select("id, doc_id, current_stage, error_msg, queued_at")
        .eq("status", "failed")
        .order("queued_at", desc=True)
        .limit(5)
        .execute()
    )
    failed_sample = failed_resp.data or []

    # SLO 버킷 — failed 포함 all_docs 기준. received_ms 는 수신 단계 SLO 만 반영하므로
    # 파이프라인 단계 실패 doc 도 receive 자체는 성공한 유효 sample.
    slo_buckets = _compute_slo_buckets(all_docs)
    ingest_slo_aggregate = _compute_slo_aggregate(slo_buckets)

    # `/search` ring buffer — 외부 IO 0, 락 짧게 잡고 스냅샷만 계산.
    search_slo = SearchSloStats(**search_metrics.get_search_slo())

    # W8 Day 4 — Vision 호출 누적 (in-memory counter, 외부 IO 0).
    vision_usage = VisionUsageStats(**vision_metrics.get_usage())

    return StatsResponse(
        documents=DocumentsStats(
            total=len(docs),
            by_doc_type=by_doc_type,
            by_source_channel=by_source_channel,
            extract_skipped=extract_skipped,
            total_size_bytes=total_size,
            added_this_month=added_this_month,
            added_last_7d=added_last_7d,
            failed_count=failed_count,
        ),
        chunks_total=chunks_total,
        chunks=chunks_stats,
        jobs=JobsStats(
            total=len(jobs),
            by_status=by_status,
            failed_sample=failed_sample,
        ),
        popular_tags=popular_tags,
        slo_buckets=slo_buckets,
        ingest_slo_aggregate=ingest_slo_aggregate,
        search_slo=search_slo,
        vision_usage=vision_usage,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------- helpers ----------------------


def _compute_chunks_stats(supabase, chunks_total: int) -> ChunksStats:
    """W7 Day 3 — chunks_filter 마킹 분포 가시성.

    DE-65 본 적용 후 chunks 1256 환경에서 effective vs filtered 비율 추적용.
    PostgREST `flags->>'filtered_reason'` JSONB path query 사용.
    """
    # filtered (filtered_reason IS NOT NULL) 카운트 — JSONB path 활용
    filtered_resp = (
        supabase.table("chunks")
        .select("flags", count="exact")
        .not_.is_("flags->>filtered_reason", "null")
        .execute()
    )
    filtered_rows = filtered_resp.data or []
    filtered_total = filtered_resp.count or 0

    # 사유별 카운트 — 페이지네이션 없이 가져온 flags 만 집계
    # (chunks 가 매우 커지면 별도 RPC 권장. 현재 1256 환경에서는 OK)
    breakdown: dict[str, int] = {}
    for r in filtered_rows:
        flags = r.get("flags") or {}
        reason = flags.get("filtered_reason")
        if reason:
            breakdown[reason] = breakdown.get(reason, 0) + 1

    effective = chunks_total - filtered_total
    ratio = filtered_total / chunks_total if chunks_total else 0.0
    return ChunksStats(
        total=chunks_total,
        effective=effective,
        filtered_breakdown=breakdown,
        filtered_ratio=round(ratio, 4),
    )


def _compute_slo_buckets(docs: list[dict]) -> dict[str, SloBucketStats]:
    """W2 §3.A 의 5개 SLO 버킷별 received_ms 집계.

    버킷 분류 규칙:
      - pdf_scan: doc_type=pdf AND flags.scan=true (스캔 PDF, Vision 재라우팅 대상)
      - pdf_50p:  doc_type=pdf AND size_bytes ≥ 25MB AND NOT scan (큰 PDF, SLO 한계 시나리오)
      - image:    doc_type=image
      - hwp:      doc_type ∈ {hwp, hwpx}
      - url:      doc_type=url

    소형 비스캔 PDF / docx / pptx / txt / md 는 어떤 버킷에도 포함 X — 본 5종은
    SLO 측정 대상으로 명세 v0.3 §3.A AC 에 명시된 시나리오.
    """
    buckets: dict[str, list[int]] = {
        "pdf_50p": [],
        "image": [],
        "pdf_scan": [],
        "hwp": [],
        "url": [],
    }
    for d in docs:
        ms = d.get("received_ms")
        if ms is None:
            continue  # received_ms 미측정 (W2 Day 2 이전 업로드분) 은 제외
        doc_type = d.get("doc_type")
        size = d.get("size_bytes") or 0
        flags = d.get("flags") or {}
        is_scan = bool(flags.get("scan"))

        if doc_type == "pdf":
            if is_scan:
                buckets["pdf_scan"].append(ms)
            elif size >= PDF_50P_THRESHOLD_BYTES:
                buckets["pdf_50p"].append(ms)
        elif doc_type == "image":
            buckets["image"].append(ms)
        elif doc_type in ("hwp", "hwpx"):
            buckets["hwp"].append(ms)
        elif doc_type == "url":
            buckets["url"].append(ms)

    return {name: _bucket_stats(samples) for name, samples in buckets.items()}


def _compute_slo_aggregate(
    slo_buckets: dict[str, SloBucketStats],
) -> IngestSloAggregate:
    """W12 Day 2 — 5 SLO 버킷 sample_count 가중 평균 (기획서 §13.1).

    각 버킷의 pass_rate × sample_count → 합 / total_samples.
    sample 0건 버킷은 가중 평균에서 제외.
    """
    weighted_sum = 0.0
    total_samples = 0
    buckets_with_samples: list[str] = []
    for name, bucket in slo_buckets.items():
        if bucket.sample_count > 0 and bucket.pass_rate is not None:
            weighted_sum += bucket.pass_rate * bucket.sample_count
            total_samples += bucket.sample_count
            buckets_with_samples.append(name)
    overall = (
        round(weighted_sum / total_samples, 4) if total_samples > 0 else None
    )
    return IngestSloAggregate(
        total_samples=total_samples,
        overall_pass_rate=overall,
        buckets_with_samples=buckets_with_samples,
    )


def _bucket_stats(samples: list[int]) -> SloBucketStats:
    n = len(samples)
    if n == 0:
        return SloBucketStats(p95_ms=None, sample_count=0, pass_rate=None)
    sorted_samples = sorted(samples)
    # nearest-rank p95: index = ceil(0.95 * n) - 1, 작은 n 안전하게 int(0.95 * (n-1))
    p95_idx = int(0.95 * (n - 1))
    p95 = sorted_samples[p95_idx]
    pass_count = sum(1 for ms in samples if ms < SLO_TARGET_MS)
    pass_rate = round(pass_count / n, 4)
    return SloBucketStats(p95_ms=p95, sample_count=n, pass_rate=pass_rate)


def _parse_created_at_kst(value: str | None) -> datetime | None:
    """Supabase 의 ISO 문자열(UTC, 'Z' 또는 '+00:00') 을 KST tz-aware datetime 으로 변환."""
    if not value:
        return None
    try:
        # Postgres TIMESTAMPTZ 직렬화는 보통 '+00:00' 이지만 'Z' 도 방어적으로 처리
        normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST)


# ============================================================
# W16 Day 2 — `/stats/trend` 추세 분석 endpoint
# ============================================================
# 마이그레이션 007 의 RPC 2개 (`get_search_metrics_trend` / `get_vision_usage_trend`)
# 호출. 005·006·007 미적용 시 graceful — 빈 배열 + error_code='migrations_pending'.

_VALID_RANGES: tuple[str, ...] = ("24h", "7d", "30d")
_VALID_MODES: tuple[str, ...] = ("all", "hybrid", "dense", "sparse")
_VALID_METRICS: tuple[str, ...] = ("search", "vision")


class TrendBucket(BaseModel):
    """`/stats/trend` 의 단일 시간 bucket.

    공통 필드 (bucket_start / sample_count) + metric 별 부가 필드.
    metric=search 시 p50_ms / p95_ms / fallback_count 채움.
    metric=vision 시 success_count / quota_exhausted_count 채움.
    빈 bucket (sample_count=0) 도 row 유지 — frontend 시계열 그래프 zero-fill.
    """
    bucket_start: str  # ISO timestamp (UTC)
    sample_count: int
    # metric=search
    p50_ms: int | None = None
    p95_ms: int | None = None
    fallback_count: int | None = None
    # metric=vision
    success_count: int | None = None
    quota_exhausted_count: int | None = None


class TrendResponse(BaseModel):
    """`/stats/trend` 응답.

    - error_code='migrations_pending': 005·006·007 미적용 환경에서 graceful 응답.
      buckets 가 빈 배열. frontend 가 안내 카드로 분기.
    - error_code=None: RPC 호출 성공 — buckets 에 zero-fill 된 시계열.
    """
    metric: Literal["search", "vision"]
    range: Literal["24h", "7d", "30d"]
    mode: Literal["all", "hybrid", "dense", "sparse"] | None  # metric=vision 시 None
    buckets: list[TrendBucket]
    error_code: str | None = None
    generated_at: str


@router.get("/stats/trend", response_model=TrendResponse)
def stats_trend(
    range: Literal["24h", "7d", "30d"] = Query("7d", description="시간 범위"),
    mode: Literal["all", "hybrid", "dense", "sparse"] = Query(
        "all", description="metric=search 만 적용. metric=vision 시 무시."
    ),
    metric: Literal["search", "vision"] = Query(
        "search", description="search: 검색 성능 / vision: Vision API 호출"
    ),
) -> TrendResponse:
    """W16 Day 2 — 마이그레이션 007 RPC 호출 → 시계열 aggregate 응답.

    마이그레이션 미적용 시 graceful: 빈 buckets + error_code='migrations_pending'.
    """
    generated_at = datetime.now(timezone.utc).isoformat()
    response_mode: str | None = mode if metric == "search" else None

    try:
        supabase = get_supabase_client()
        if metric == "search":
            rpc_resp = supabase.rpc(
                "get_search_metrics_trend",
                {"range_label": range, "mode_label": mode},
            ).execute()
        else:
            rpc_resp = supabase.rpc(
                "get_vision_usage_trend",
                {"range_label": range},
            ).execute()
        rows = rpc_resp.data or []
    except Exception as exc:  # noqa: BLE001 — 마이그레이션 미적용 graceful
        logger.warning("stats_trend RPC graceful skip: %s", exc)
        return TrendResponse(
            metric=metric,
            range=range,
            mode=response_mode,
            buckets=[],
            error_code="migrations_pending",
            generated_at=generated_at,
        )

    buckets = [_row_to_bucket(metric, row) for row in rows]
    return TrendResponse(
        metric=metric,
        range=range,
        mode=response_mode,
        buckets=buckets,
        error_code=None,
        generated_at=generated_at,
    )


def _row_to_bucket(metric: str, row: dict) -> TrendBucket:
    """RPC row → TrendBucket. metric 별 부가 필드 매핑."""
    bucket_start = row.get("bucket_start") or ""
    sample_count = int(row.get("sample_count") or 0)
    if metric == "search":
        return TrendBucket(
            bucket_start=str(bucket_start),
            sample_count=sample_count,
            p50_ms=int(row.get("p50_ms") or 0),
            p95_ms=int(row.get("p95_ms") or 0),
            fallback_count=int(row.get("fallback_count") or 0),
        )
    return TrendBucket(
        bucket_start=str(bucket_start),
        sample_count=sample_count,
        success_count=int(row.get("success_count") or 0),
        quota_exhausted_count=int(row.get("quota_exhausted_count") or 0),
    )
