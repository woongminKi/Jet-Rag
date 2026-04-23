"""GET /stats — 전체 시스템 통계 (검증 UX).

- 브라우저 /docs 에서 한 번 눌러보면 총 문서·청크·jobs 상태를 한눈에
- 단일 사용자 MVP 기준이라 `documents.user_id = DEFAULT_USER_ID` 필터만 적용
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import get_settings
from app.db import get_supabase_client

router = APIRouter(tags=["stats"])

# 한국 시간대 — 단일 사용자 MVP 기준이라 하드코딩
KST = timezone(timedelta(hours=9))


class DocumentsStats(BaseModel):
    total: int
    by_doc_type: dict[str, int]
    by_source_channel: dict[str, int]
    extract_skipped: int
    total_size_bytes: int
    added_this_month: int  # KST 이번 달 1일 00:00 이후 추가된 문서 수
    added_last_7d: int  # KST 기준 최근 7일(=168시간) 내 추가된 문서 수


class JobsStats(BaseModel):
    total: int
    by_status: dict[str, int]
    failed_sample: list[dict]  # 최근 실패 5건 요약 (에러 디버그용)


class TagCount(BaseModel):
    tag: str
    count: int


class StatsResponse(BaseModel):
    documents: DocumentsStats
    chunks_total: int
    jobs: JobsStats
    popular_tags: list[TagCount]  # 사용 빈도 top-10
    generated_at: str


@router.get("/stats", response_model=StatsResponse)
def stats() -> StatsResponse:
    supabase = get_supabase_client()
    user_id = get_settings().default_user_id

    # ---- documents ----
    docs = (
        supabase.table("documents")
        .select("doc_type, source_channel, size_bytes, flags, tags, created_at")
        .eq("user_id", user_id)
        .is_("deleted_at", "null")
        .execute()
        .data
        or []
    )

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

    return StatsResponse(
        documents=DocumentsStats(
            total=len(docs),
            by_doc_type=by_doc_type,
            by_source_channel=by_source_channel,
            extract_skipped=extract_skipped,
            total_size_bytes=total_size,
            added_this_month=added_this_month,
            added_last_7d=added_last_7d,
        ),
        chunks_total=chunks_total,
        jobs=JobsStats(
            total=len(jobs),
            by_status=by_status,
            failed_sample=failed_sample,
        ),
        popular_tags=popular_tags,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------- helpers ----------------------


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
