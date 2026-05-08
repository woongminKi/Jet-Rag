"""문서 업로드 및 인제스트 상태 조회 엔드포인트.

기획서 참조
- §10.2 인제스트 파이프라인 전체 플로우
- §10.8 Tier 1 중복 감지 (SHA-256)
- §10.11 SLO: 수신 응답 < 2초
- §11.3 입력 게이트 단계 A (확장자 화이트리스트, 크기 50MB)
"""

from __future__ import annotations

import hashlib
import logging
import time
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Literal

import httpx
import trafilatura
from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field

from app.config import get_settings
from app.db import get_supabase_client
from app.ingest import (
    create_job,
    get_latest_job_for_doc,
    list_logs_for_job,
    run_full_ingest,
    run_pipeline,
)
from app.ingest.eta import compute_remaining_ms
from app.routers._input_gate import HEAD_BYTES, validate_magic
from app.routers._url_gate import recheck_dns_consistency, validate_url_safety
from app.services.ingest_mode import INGEST_MODES, IngestMode, resolve_page_cap

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])

# 기획서 §11.3 단계 A
_ALLOWED_EXTENSIONS: dict[str, str] = {
    ".pdf": "pdf",
    ".hwp": "hwp",
    ".hwpx": "hwpx",
    ".docx": "docx",
    ".pptx": "pptx",
    ".jpg": "image",
    ".jpeg": "image",
    ".png": "image",
    ".heic": "image",
    ".txt": "txt",
    ".md": "md",
}
_MAX_SIZE_BYTES = 50 * 1024 * 1024  # 50MB
_CHUNK_SIZE = 64 * 1024              # 스트리밍 read chunk
# pending path 네임스페이스. 단일 사용자 MVP 동안 "default" 고정.
# W5 멀티유저 도입 시 실제 user_id 로 치환 → Supabase Storage RLS 정책이 prefix 기반으로
# 자연스럽게 대응. documents.user_id (UUID) 와 별개의 path 라벨.
_PENDING_PATH_NAMESPACE = "default"
_SourceChannel = Literal["drag-drop", "os-share", "clipboard", "url", "camera", "api"]

# S2 D3 — 운영 모드 default. UI/router 양쪽이 같은 default 를 본다.
_DEFAULT_INGEST_MODE: IngestMode = "default"


def _validate_ingest_mode(raw: str | None) -> IngestMode:
    """입력 mode 검증 — invalid 시 400. None/빈 문자열 → default 반환."""
    if raw is None or raw == "":
        return _DEFAULT_INGEST_MODE
    if raw not in INGEST_MODES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"지원되지 않는 모드입니다: {raw!r} "
                f"(허용: {', '.join(INGEST_MODES)})"
            ),
        )
    return raw  # type: ignore[return-value]


def _flags_with_ingest_mode(existing: dict | None, mode: IngestMode) -> dict:
    """기존 flags 보존 + ingest_mode 만 갱신 (S2 D3)."""
    out = dict(existing or {})
    out["ingest_mode"] = mode
    return out


# ============================================================
# Response schemas
# ============================================================
class UploadResponse(BaseModel):
    doc_id: str
    job_id: str | None
    duplicated: bool


class JobStatus(BaseModel):
    job_id: str
    status: str
    current_stage: str | None
    attempts: int
    error_msg: str | None
    queued_at: str
    started_at: str | None
    finished_at: str | None
    # W25 D14 Sprint B — 대략적 남은 시간(ms). queued/running 시만 추정값,
    # completed/failed/cancelled 는 None. ingest_logs.duration_ms median 기반
    # (5분 TTL cache + fallback hardcoded). 첫 ingest 시 정확도 낮음 — 사용자 표시
    # 시 "약 N분 N초 남음" 보수적 표기 권장.
    estimated_remaining_ms: int | None = None
    # W25 D14 — stage 안 sub-step 진행 (예: vision_enrich 페이지 12/41).
    # {current, total, unit} 형식. NULL 시 stage 라벨만 표시.
    stage_progress: dict | None = None


class DocumentStatusResponse(BaseModel):
    doc_id: str
    job: JobStatus | None
    logs: list[dict] | None = None


class ReingestResponse(BaseModel):
    doc_id: str
    job_id: str
    chunks_deleted: int


class ReingestMissingResponse(BaseModel):
    """W25 D14 B sprint — incremental vision reingest 응답.

    full reingest 와 달리 chunks 보존 + 누락 페이지만 vision 처리.
    """
    doc_id: str
    job_id: str
    total_pages: int
    missing_pages_before: list[int]  # 호출 시점 누락 페이지
    note: str = (
        "incremental vision reingest — 누락 페이지만 처리 후 백그라운드 진행. "
        "결과는 GET /documents/{id}/status 로 폴링."
    )


class UrlUploadRequest(BaseModel):
    url: str = Field(..., description="수집할 페이지 URL (http/https 만 허용).")
    title: str | None = Field(
        None,
        description="제공 안 할 시 trafilatura 메타·OG title·hostname 순으로 자동 추정.",
    )
    source_channel: _SourceChannel = "url"
    # S2 D3 — 운영 모드 (fast/default/precise). default=default. invalid 시 400.
    mode: str = Field(
        "default",
        description="운영 모드: 'fast' | 'default' | 'precise'.",
    )


_URL_FETCH_TIMEOUT_SECONDS = 10
_URL_FETCH_USER_AGENT = (
    "Mozilla/5.0 (compatible; Jet-Rag/1.0; +https://github.com/woongminKi/Jet-Rag)"
)


class DocumentListItem(BaseModel):
    id: str
    title: str
    doc_type: str
    source_channel: str
    size_bytes: int
    content_type: str
    tags: list[str]
    summary: str | None
    flags: dict
    chunks_count: int
    latest_job_status: str | None
    latest_job_stage: str | None
    created_at: str


class DocumentListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[DocumentListItem]


class BatchStatusItem(BaseModel):
    doc_id: str
    job: JobStatus | None  # None = 해당 doc_id 의 ingest_jobs row 없음 (또는 doc 자체 없음)


class BatchStatusResponse(BaseModel):
    items: list[BatchStatusItem]


_BATCH_STATUS_MAX_IDS = 50


class ActiveDocItem(BaseModel):
    """W25 D14 Sprint 0 — /ingest 새로고침 후 진행 현황 복원용.

    placeholder 1줄 카드 정보 (UploadItem 의 file_name·sizeBytes·docId·jobId 매핑).
    """
    doc_id: str
    file_name: str
    size_bytes: int
    job: JobStatus  # 항상 존재 (queued/running/failed)


class ActiveDocsResponse(BaseModel):
    items: list[ActiveDocItem]


_ACTIVE_DOC_DEFAULT_HOURS = 24
_ACTIVE_DOC_MAX_HOURS = 168  # 7일
_ACTIVE_DOC_STATUSES = ("queued", "running", "failed")


# W25 D14 — stage_progress 컬럼 (마이그레이션 010) 미적용 환경에서 SELECT 가
# APIError 42703 ("column does not exist") 으로 실패할 때, 첫 1회만 시도 후
# 컬럼 빼고 재시도. 이후 호출은 자동으로 컬럼 미포함 → 매 요청 500 폭주 회피.
# 마이그레이션 적용 후 백엔드 재시작 시 자동 회복.
_INGEST_JOBS_BASE_COLUMNS = (
    "id, doc_id, status, current_stage, attempts, error_msg, "
    "queued_at, started_at, finished_at"
)
_stage_progress_select_enabled = True


def _ingest_jobs_select_columns() -> str:
    if _stage_progress_select_enabled:
        return _INGEST_JOBS_BASE_COLUMNS + ", stage_progress"
    return _INGEST_JOBS_BASE_COLUMNS


def _disable_stage_progress_select(reason: Exception) -> None:
    global _stage_progress_select_enabled
    if _stage_progress_select_enabled:
        _stage_progress_select_enabled = False
        logger.warning(
            "ingest_jobs.stage_progress SELECT 첫 실패 — 이후 컬럼 미포함 "
            "(마이그레이션 010 적용 후 백엔드 재시작 시 회복): %s",
            reason,
        )


def reset_stage_progress_select_enabled() -> None:
    """단위 테스트 용 — 모듈 flag 리셋."""
    global _stage_progress_select_enabled
    _stage_progress_select_enabled = True


class DocumentDetailResponse(BaseModel):
    """`/doc/[id]` 경량판 (W2 §3.M, F′-α2) 의 단건 종합 응답.

    title·doc_type·tags·summary·flags 같은 메타와 인제스트 진행 상태를 한 번에 노출.
    chunks 본문 리스트는 W4 본격판에서 추가.
    """
    id: str
    title: str
    doc_type: str
    source_channel: str
    size_bytes: int
    content_type: str
    tags: list[str]
    summary: str | None
    flags: dict
    chunks_count: int
    latest_job: JobStatus | None
    created_at: str
    received_ms: int | None
    source_url: str | None  # `flags.source_url` 추출 — URL 인제스트 문서만


# ============================================================
# GET /documents — 리스트
# ============================================================
@router.get("", response_model=DocumentListResponse)
def list_documents(
    limit: int = Query(20, ge=1, le=100, description="한 페이지 최대 반환 건수"),
    offset: int = Query(0, ge=0, description="건너뛸 건수 (페이지네이션)"),
    include_failed: bool = Query(
        False,
        alias="include_failed",
        description="True 면 flags.failed 인 문서도 포함 (디버깅 용)",
    ),
) -> DocumentListResponse:
    """최신순 문서 리스트. 각 항목에 chunks 개수 + 최신 ingest_jobs 상태 포함.

    브라우저 `/docs` (Swagger UI) 에서 Try it out 으로 즉시 확인 가능한 검증 UX.

    failed 정책 (B 안 Hybrid)
    - 기본은 `flags.failed` 가 True 인 문서 제외 — 사용자에게는 "정상 인제스트된 것만" 보임
    - PostgREST 의 jsonb 연산자는 `flags->>failed = 'true'` 매칭 외에 NULL 인 row 도
      함께 통과시키는 OR 조합이 까다로워, MVP 규모에선 Python 측에서 단순 필터링
    - `include_failed=true` 로 호출하면 디버깅용으로 전체 노출
    """
    supabase = get_supabase_client()
    settings = get_settings()

    docs_resp = (
        supabase.table("documents")
        .select(
            "id, title, doc_type, source_channel, size_bytes, content_type, "
            "tags, summary, flags, created_at"
        )
        .eq("user_id", settings.default_user_id)
        .is_("deleted_at", "null")
        .order("created_at", desc=True)
        .execute()
    )
    all_docs = docs_resp.data or []

    if include_failed:
        filtered_docs = all_docs
    else:
        filtered_docs = [
            d for d in all_docs if not (d.get("flags") or {}).get("failed")
        ]

    total = len(filtered_docs)
    page_docs = filtered_docs[offset : offset + limit]

    items: list[DocumentListItem] = []
    for doc in page_docs:
        doc_id = doc["id"]

        chunks_resp = (
            supabase.table("chunks")
            .select("id", count="exact")
            .eq("doc_id", doc_id)
            .execute()
        )
        chunks_count = chunks_resp.count or 0

        latest_job_resp = (
            supabase.table("ingest_jobs")
            .select("status, current_stage")
            .eq("doc_id", doc_id)
            .order("queued_at", desc=True)
            .limit(1)
            .execute()
        )
        latest_job = latest_job_resp.data[0] if latest_job_resp.data else None

        items.append(
            DocumentListItem(
                id=doc_id,
                title=doc["title"],
                doc_type=doc["doc_type"],
                source_channel=doc["source_channel"],
                size_bytes=doc["size_bytes"],
                content_type=doc["content_type"],
                tags=list(doc.get("tags") or []),
                summary=doc.get("summary"),
                flags=dict(doc.get("flags") or {}),
                chunks_count=chunks_count,
                latest_job_status=latest_job["status"] if latest_job else None,
                latest_job_stage=latest_job.get("current_stage") if latest_job else None,
                created_at=doc["created_at"],
            )
        )

    return DocumentListResponse(total=total, limit=limit, offset=offset, items=items)


# ============================================================
# POST /documents
# ============================================================
@router.post(
    "",
    response_model=UploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    source_channel: _SourceChannel = Form("api"),
    title: str | None = Form(None),
    mode: str = Form(_DEFAULT_INGEST_MODE),
) -> UploadResponse:
    """수신 ≤ 2초 SLO. Storage upload 는 BG `run_full_ingest` 위임.

    수신 단계 흐름 (응답 전):
      1) 확장자 화이트리스트 → 거절 시 400
      2) chunk-streaming 으로 SHA-256 + size counter (50MB 한도) + 첫 head 매직바이트 검증
      3) Tier 1 dedup (SHA-256) — 중복이면 즉시 응답 (Storage upload 없음)
      4) `documents` insert with `storage_path = "pending/{namespace}/{uuid}{ext}"` placeholder
      5) `ingest_jobs` insert + BG 큐잉 (raw bytes 전달) → 202 Accepted

    BG 단계 (응답 후): `run_full_ingest` 가 Storage upload + path update + 8-stage 파이프라인.
    """
    started_at = time.perf_counter()
    file_name = file.filename or "untitled"

    # S2 D3 — 운영 모드 검증 (확장자 검증 직전, SLO 영향 0).
    ingest_mode: IngestMode = _validate_ingest_mode(mode)

    # ---- 입력 게이트 단계 A: 확장자 화이트리스트 ----
    ext = PurePosixPath(file_name).suffix.lower()
    doc_type = _ALLOWED_EXTENSIONS.get(ext)
    if doc_type is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"지원되지 않는 확장자입니다: {ext or '(없음)'}",
        )

    # ---- chunk streaming: SHA-256 + size counter + 매직바이트 (조기 검증) ----
    hasher = hashlib.sha256()
    buf = bytearray()
    magic_validated = False
    while True:
        chunk = await file.read(_CHUNK_SIZE)
        if not chunk:
            break
        hasher.update(chunk)
        buf.extend(chunk)
        if len(buf) > _MAX_SIZE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"파일 크기 상한(50MB) 초과",
            )
        # head 가 충분히 모이면 즉시 매직바이트 검증 (조기 reject 로 메모리 절약)
        if not magic_validated and len(buf) >= HEAD_BYTES:
            validate_magic(ext=ext, raw_head=bytes(buf[:HEAD_BYTES]))
            magic_validated = True

    # 작은 파일 (< HEAD_BYTES) 은 스트림 종료 후 한 번 검증
    if not magic_validated:
        validate_magic(ext=ext, raw_head=bytes(buf))

    raw = bytes(buf)
    size = len(raw)
    if size == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="빈 파일입니다.",
        )
    sha256 = hasher.hexdigest()
    content_type = file.content_type or "application/octet-stream"
    settings = get_settings()
    supabase = get_supabase_client()

    # ---- Tier 1 dedup ----
    existing = (
        supabase.table("documents")
        .select("id, flags")
        .eq("user_id", settings.default_user_id)
        .eq("sha256", sha256)
        .is_("deleted_at", "null")
        .limit(1)
        .execute()
    )
    if existing.data:
        existing_doc = existing.data[0]
        existing_doc_id = existing_doc["id"]
        existing_flags = existing_doc.get("flags") or {}

        # 이전 인제스트가 실패한 row → 재업로드 = 재시도 의도. POST /reingest 와 동일.
        # Storage upload 도 실패했을 가능성이 있어 run_full_ingest (멱등 upsert) 로 통합.
        if existing_flags.get("failed"):
            _reset_doc_for_reingest(supabase, existing_doc_id)
            received_ms = int((time.perf_counter() - started_at) * 1000)
            # S2 D3 — 새 mode 를 flags 에 보존 + page_cap 결정.
            page_cap_override = resolve_page_cap(ingest_mode, settings)
            (
                supabase.table("documents")
                .update(
                    {
                        "received_ms": received_ms,
                        "flags": _flags_with_ingest_mode({}, ingest_mode),
                    }
                )
                .eq("id", existing_doc_id)
                .execute()
            )
            job = create_job(doc_id=existing_doc_id)
            background_tasks.add_task(
                run_full_ingest,
                job_id=job.id,
                doc_id=existing_doc_id,
                raw=raw,
                sha256=sha256,
                ext=ext,
                content_type=content_type,
                page_cap_override=page_cap_override,
            )
            return UploadResponse(
                doc_id=existing_doc_id,
                job_id=job.id,
                duplicated=False,
            )

        # 정상 중복 — 새 row 생성 X
        return UploadResponse(
            doc_id=existing_doc_id,
            job_id=None,
            duplicated=True,
        )

    # ---- 신규 — pending path 로 documents insert (Storage upload 는 BG 가 담당) ----
    doc_uuid = uuid.uuid4().hex
    pending_path = f"pending/{_PENDING_PATH_NAMESPACE}/{doc_uuid}{ext}"
    # W25 D14 — 한국어 NFD/NFC 불일치 회피 (macOS Finder 가 NFD 로 파일명 보냄)
    # → ilike/검색 query (NFC) 와 byte 매칭 fail. 인제스트 단에서 NFC 통일.
    doc_title = unicodedata.normalize("NFC", title or PurePosixPath(file_name).stem)
    received_ms = int((time.perf_counter() - started_at) * 1000)
    # S2 D3 — mode → flags + page_cap 결정.
    page_cap_override = resolve_page_cap(ingest_mode, settings)
    doc_row = (
        supabase.table("documents")
        .insert(
            {
                "user_id": settings.default_user_id,
                "title": doc_title,
                "doc_type": doc_type,
                "source_channel": source_channel,
                "storage_path": pending_path,
                "sha256": sha256,
                "size_bytes": size,
                "content_type": content_type,
                "received_ms": received_ms,
                "flags": _flags_with_ingest_mode({}, ingest_mode),
            }
        )
        .execute()
    )
    doc_id = doc_row.data[0]["id"]

    # ---- ingest_jobs + BG (Storage upload + path update + pipeline 위임) ----
    job = create_job(doc_id=doc_id)
    background_tasks.add_task(
        run_full_ingest,
        job_id=job.id,
        doc_id=doc_id,
        raw=raw,
        sha256=sha256,
        ext=ext,
        content_type=content_type,
        page_cap_override=page_cap_override,
    )
    return UploadResponse(doc_id=doc_id, job_id=job.id, duplicated=False)


# ============================================================
# POST /documents/url
# ============================================================
@router.post(
    "/url",
    response_model=UploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_url(
    background_tasks: BackgroundTasks,
    payload: UrlUploadRequest,
) -> UploadResponse:
    """URL 수집. SSRF 검증 → fetch → 기존 BG 흐름 (`run_full_ingest`) 재사용.

    수신 ≤ 2초 SLO 는 fetch timeout (10s) 으로 제한적이지만, 정상 사이트는 통상 ≤ 2초 응답.
    명세 v0.3 §3.E. doc_type='url', `flags.source_url` 에 원본 URL 보존.
    """
    started_at = time.perf_counter()

    # S2 D3 — 운영 모드 검증 (SSRF 직전, SLO 영향 0).
    ingest_mode: IngestMode = _validate_ingest_mode(payload.mode)

    # SSRF 검증 (multi-IP round-robin 차단 포함). resolved IP 집합 캐시 → recheck 입력.
    safe, reason, resolved_ips = validate_url_safety(payload.url)
    if not safe:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"안전하지 않은 URL: {reason}",
        )

    # Fetch
    try:
        async with httpx.AsyncClient(
            timeout=_URL_FETCH_TIMEOUT_SECONDS,
            follow_redirects=True,
        ) as client:
            resp = await client.get(
                payload.url, headers={"User-Agent": _URL_FETCH_USER_AGENT}
            )
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"URL 응답 에러: {exc.response.status_code}",
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"URL 조회 실패: {exc}",
        ) from exc

    # DNS rebinding 방어 — fetch 사이에 DNS 가 사설 IP 로 회전됐는지 재검증.
    # 검증 시점 (T1) → fetch (T2) 사이 IP 변경되면 fetch 결과 폐기.
    rebind_ok, rebind_reason = recheck_dns_consistency(payload.url, resolved_ips)
    if not rebind_ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"안전하지 않은 URL: {rebind_reason}",
        )

    html_bytes = resp.content
    size = len(html_bytes)
    if size == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="URL 응답 본문이 비어있습니다.",
        )
    if size > _MAX_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"HTML 크기 상한(50MB) 초과: {size} bytes",
        )

    # content_type — semicolon 뒤 charset 등 제거
    raw_ct = resp.headers.get("content-type", "text/html")
    content_type = raw_ct.split(";")[0].strip() or "text/html"

    sha256 = hashlib.sha256(html_bytes).hexdigest()
    settings = get_settings()
    supabase = get_supabase_client()

    # ---- Tier 1 dedup ----
    existing = (
        supabase.table("documents")
        .select("id, flags")
        .eq("user_id", settings.default_user_id)
        .eq("sha256", sha256)
        .is_("deleted_at", "null")
        .limit(1)
        .execute()
    )
    if existing.data:
        existing_doc = existing.data[0]
        existing_doc_id = existing_doc["id"]
        existing_flags = existing_doc.get("flags") or {}

        if existing_flags.get("failed"):
            _reset_doc_for_reingest(supabase, existing_doc_id)
            received_ms = int((time.perf_counter() - started_at) * 1000)
            # S2 D3 — 새 mode 를 flags 에 보존 + page_cap 결정. 기존 source_url 도 유지.
            page_cap_override = resolve_page_cap(ingest_mode, settings)
            preserved_flags = dict(existing_flags)
            # _reset_doc_for_reingest 가 flags 를 빈 dict 로 reset 했지만 source_url 은
            # POST /documents/url 의 dedup 분기 이전 SELECT 한 existing_flags 에서 보존.
            new_flags: dict = {}
            if "source_url" in preserved_flags:
                new_flags["source_url"] = preserved_flags["source_url"]
            new_flags["ingest_mode"] = ingest_mode
            (
                supabase.table("documents")
                .update({"received_ms": received_ms, "flags": new_flags})
                .eq("id", existing_doc_id)
                .execute()
            )
            job = create_job(doc_id=existing_doc_id)
            background_tasks.add_task(
                run_full_ingest,
                job_id=job.id,
                doc_id=existing_doc_id,
                raw=html_bytes,
                sha256=sha256,
                ext=".html",
                content_type=content_type,
                page_cap_override=page_cap_override,
            )
            return UploadResponse(
                doc_id=existing_doc_id, job_id=job.id, duplicated=False
            )

        return UploadResponse(
            doc_id=existing_doc_id, job_id=None, duplicated=True
        )

    # 제목 추정 — payload → trafilatura 메타 → URL hostname
    title = payload.title
    if not title:
        try:
            metadata = trafilatura.extract_metadata(
                html_bytes.decode("utf-8", errors="replace")
            )
            if metadata and metadata.title:
                title = metadata.title.strip()
        except Exception:  # noqa: BLE001
            logger.exception("trafilatura.extract_metadata 실패 (title fallback 사용)")
    if not title:
        from urllib.parse import urlparse as _urlparse

        title = _urlparse(payload.url).hostname or "untitled URL"

    # W25 D14 — title NFC 정규화 (한국어 NFD/NFC 불일치 회피)
    title = unicodedata.normalize("NFC", title)

    # ---- documents insert (pending path + flags.source_url + ingest_mode) ----
    doc_uuid = uuid.uuid4().hex
    pending_path = f"pending/{_PENDING_PATH_NAMESPACE}/{doc_uuid}.html"
    received_ms = int((time.perf_counter() - started_at) * 1000)
    page_cap_override = resolve_page_cap(ingest_mode, settings)
    doc_row = (
        supabase.table("documents")
        .insert(
            {
                "user_id": settings.default_user_id,
                "title": title,
                "doc_type": "url",
                "source_channel": payload.source_channel,
                "storage_path": pending_path,
                "sha256": sha256,
                "size_bytes": size,
                "content_type": content_type,
                "received_ms": received_ms,
                "flags": {
                    "source_url": payload.url,
                    "ingest_mode": ingest_mode,
                },
            }
        )
        .execute()
    )
    doc_id = doc_row.data[0]["id"]

    job = create_job(doc_id=doc_id)
    background_tasks.add_task(
        run_full_ingest,
        job_id=job.id,
        doc_id=doc_id,
        raw=html_bytes,
        sha256=sha256,
        ext=".html",
        content_type=content_type,
        page_cap_override=page_cap_override,
    )
    return UploadResponse(doc_id=doc_id, job_id=job.id, duplicated=False)


# ============================================================
# POST /documents/{doc_id}/reingest
# ============================================================
@router.post(
    "/{doc_id}/reingest",
    response_model=ReingestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def reingest_document(
    doc_id: str,
    background_tasks: BackgroundTasks,
    mode: str | None = Query(
        None,
        description=(
            "운영 모드 (fast/default/precise). 미지정 시 기존 flags.ingest_mode "
            "재사용 (없으면 'default')."
        ),
    ),
) -> ReingestResponse:
    """기존 doc 의 chunks/메타를 reset 하고 같은 storage_path 로 파이프라인 재실행.

    용례: Day 4 데이터처럼 dense_vec / doc_embedding 이 NULL 인 기존 문서를
    Day 5 임베딩 스테이지를 포함한 현재 파이프라인으로 재처리.

    - 진행 중 job (queued/running) 이 있으면 409 로 거부
    - 기존 chunks 전부 삭제 + documents.tags/summary/flags/doc_embedding NULL reset
      (단 flags.ingest_mode 는 보존 — _reset_doc_for_reingest 가 처리)
    - 새 ingest_jobs row 추가 (이전 jobs/logs 는 history 로 보존)
    - storage_path · sha256 등 원본 식별자는 유지 — Storage 재업로드 X

    S2 D3 — `?mode=` 쿼리로 운영 모드 변경 가능. 미지정 시 기존 mode 보존.
    """
    supabase = get_supabase_client()
    settings = get_settings()

    existing = (
        supabase.table("documents")
        .select("id, flags")
        .eq("id", doc_id)
        .is_("deleted_at", "null")
        .limit(1)
        .execute()
    )
    if not existing.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="문서를 찾을 수 없습니다.",
        )
    existing_flags = dict(existing.data[0].get("flags") or {})

    latest = get_latest_job_for_doc(doc_id)
    if latest and latest.status in ("queued", "running"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"진행 중인 작업이 있습니다 (job={latest.id}, status={latest.status}). 완료 후 다시 시도하세요.",
        )

    # S2 D3 — mode 결정: 명시 > 기존 flags > default. invalid 시 400.
    if mode is None:
        prior_mode = existing_flags.get("ingest_mode")
        ingest_mode: IngestMode = (
            prior_mode if prior_mode in INGEST_MODES else _DEFAULT_INGEST_MODE
        )
    else:
        ingest_mode = _validate_ingest_mode(mode)

    chunks_deleted = _reset_doc_for_reingest(supabase, doc_id)

    # _reset 후 새 mode 명시 — 호출자 책임 (S2 D3 _reset 정책).
    supabase.table("documents").update(
        {"flags": _flags_with_ingest_mode({}, ingest_mode)}
    ).eq("id", doc_id).execute()

    page_cap_override = resolve_page_cap(ingest_mode, settings)
    job = create_job(doc_id=doc_id)
    background_tasks.add_task(
        run_pipeline, job.id, doc_id, page_cap_override=page_cap_override,
    )

    return ReingestResponse(
        doc_id=doc_id, job_id=job.id, chunks_deleted=chunks_deleted
    )


# ============================================================
# POST /documents/{doc_id}/reingest-missing  (W25 D14 B sprint)
# ============================================================
@router.post(
    "/{doc_id}/reingest-missing",
    response_model=ReingestMissingResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def reingest_missing_vision(
    doc_id: str,
    background_tasks: BackgroundTasks,
    mode: str | None = Query(
        None,
        description=(
            "운영 모드 (fast/default/precise). 미지정 시 기존 flags.ingest_mode "
            "재사용 (없으면 'default')."
        ),
    ),
) -> ReingestMissingResponse:
    """incremental vision reingest — 기존 chunks 보존 + 누락 페이지만 vision 처리.

    full reingest 의 한계 (chunks 전부 삭제 → random 503 으로 정답 페이지 누락 시
    답변 회귀 / Sprint 4 처럼 fail 시 chunks 0 사태) 회피.

    동작:
    - 현재 chunks 의 vision 처리 페이지 (section_title `(vision) p.N` 매칭) 추출
    - PDF 의 누락 페이지만 vision 호출 (sweep 적용) → 새 chunks insert
    - chunk_idx 는 max(existing) + 1 부터 (충돌 회피)
    - dense_vec 은 NULL → embed stage 가 BGE-M3 로 채움
    - chunk_filter / content_gate / tag_summarize / doc_embed / dedup 안 호출
      (이미 적용된 메타 보존)

    PDF 자체가 변경된 경우는 부정확 — full reingest 권장.
    """
    supabase = get_supabase_client()
    settings = get_settings()

    existing = (
        supabase.table("documents")
        .select("id,doc_type,flags")
        .eq("id", doc_id)
        .is_("deleted_at", "null")
        .limit(1)
        .execute()
    )
    if not existing.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="문서를 찾을 수 없습니다.",
        )
    if existing.data[0]["doc_type"] != "pdf":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="incremental vision reingest 는 PDF 만 지원합니다.",
        )
    existing_flags = dict(existing.data[0].get("flags") or {})

    latest = get_latest_job_for_doc(doc_id)
    if latest and latest.status in ("queued", "running"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"진행 중인 작업이 있습니다 (job={latest.id}, status={latest.status}).",
        )

    # S2 D3 — mode 결정 (incremental). 명시 > 기존 > default.
    if mode is None:
        prior_mode = existing_flags.get("ingest_mode")
        ingest_mode: IngestMode = (
            prior_mode if prior_mode in INGEST_MODES else _DEFAULT_INGEST_MODE
        )
    else:
        ingest_mode = _validate_ingest_mode(mode)

    # 호출 시점 누락 페이지 계산 (응답 immediate)
    from app.ingest.incremental import (
        _vision_processed_pages,
        run_incremental_vision_pipeline,
    )
    from app.adapters.impl.supabase_storage import SupabaseBlobStorage
    import fitz

    doc_row = (
        supabase.table("documents")
        .select("storage_path")
        .eq("id", doc_id)
        .limit(1)
        .execute()
        .data[0]
    )
    storage = SupabaseBlobStorage(bucket=settings.supabase_storage_bucket)
    pdf_data = storage.get(doc_row["storage_path"])
    with fitz.open(stream=pdf_data, filetype="pdf") as fdoc:
        total_pages = len(fdoc)
    processed = _vision_processed_pages(supabase, doc_id)
    missing = sorted(set(range(1, total_pages + 1)) - processed)

    # S2 D3 — mode 가 변경되었으면 flags 갱신 + page_cap 결정.
    if existing_flags.get("ingest_mode") != ingest_mode:
        supabase.table("documents").update(
            {"flags": _flags_with_ingest_mode(existing_flags, ingest_mode)}
        ).eq("id", doc_id).execute()
    page_cap_override = resolve_page_cap(ingest_mode, settings)

    job = create_job(doc_id=doc_id)
    background_tasks.add_task(
        run_incremental_vision_pipeline,
        job.id,
        doc_id,
        page_cap_override=page_cap_override,
    )

    return ReingestMissingResponse(
        doc_id=doc_id,
        job_id=job.id,
        total_pages=total_pages,
        missing_pages_before=missing,
    )


# ============================================================
# 내부 헬퍼
# ============================================================
def _reset_doc_for_reingest(supabase, doc_id: str) -> int:
    """chunks 전체 삭제 + documents 의 재계산 대상 필드 reset.

    POST /documents 의 failed 자동 reingest 분기와 POST /documents/{id}/reingest
    가 공통으로 사용한다. 새 ingest_jobs row 생성과 BackgroundTasks 큐잉은
    호출자가 책임진다 (응답 형태가 다르기 때문).

    S2 D3 — `flags.ingest_mode` 는 보존 (호출자가 새 mode 로 덮어쓸 수 있도록).
    이전에는 flags 를 빈 dict 로 reset → reingest 시 mode 정보 손실. 호출자가
    새 mode 를 명시하지 않아도 이전 mode 가 유지되도록 보존.
    """
    chunks_count_resp = (
        supabase.table("chunks")
        .select("id", count="exact")
        .eq("doc_id", doc_id)
        .execute()
    )
    chunks_deleted = chunks_count_resp.count or 0
    if chunks_deleted > 0:
        supabase.table("chunks").delete().eq("doc_id", doc_id).execute()

    # S2 D3 — flags.ingest_mode 보존 (다른 시그널은 reset).
    existing_resp = (
        supabase.table("documents")
        .select("flags")
        .eq("id", doc_id)
        .limit(1)
        .execute()
    )
    existing_flags = (
        dict((existing_resp.data or [{}])[0].get("flags") or {})
    )
    preserved_flags: dict = {}
    if "ingest_mode" in existing_flags:
        preserved_flags["ingest_mode"] = existing_flags["ingest_mode"]

    supabase.table("documents").update(
        {
            "tags": [],
            "summary": None,
            "flags": preserved_flags,
            "doc_embedding": None,
        }
    ).eq("id", doc_id).execute()

    return chunks_deleted


# ============================================================
# GET /documents/batch-status
# ============================================================
@router.get("/active", response_model=ActiveDocsResponse)
def list_active_documents(
    hours: int = Query(
        default=_ACTIVE_DOC_DEFAULT_HOURS,
        ge=1,
        le=_ACTIVE_DOC_MAX_HOURS,
        description=f"최근 N시간 (max {_ACTIVE_DOC_MAX_HOURS}=7일)",
    ),
) -> ActiveDocsResponse:
    """진행 중·실패 doc 자동 표시 (W25 D14 Sprint 0).

    /ingest 페이지가 새로고침되어도 처리 현황 카드가 유지되도록, 최근 N시간 내
    status IN ('queued','running','failed') 인 doc 을 일괄 반환.

    - completed 는 제외 (이미 검색·문서 리스트로 노출)
    - cancelled 도 제외 (사용자가 명시적 종료)
    - 정렬: queued_at desc (최신 먼저)
    """
    supabase = get_supabase_client()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

    # W25 D14 — status filter 를 SQL 단에서 적용하면 같은 doc 에 어제 failed + 오늘
    # completed 양쪽 row 가 있을 때 failed 만 SELECT → 잘못 stale 로 indicator 노출.
    # 모든 status 를 가져와 doc_id 별 latest 만 추출 후, latest 의 status 가 active
    # (queued/running/failed) 인 doc 만 응답에 포함. completed/cancelled 가 latest 면 자연 제외.
    def _query():
        return (
            supabase.table("ingest_jobs")
            .select(_ingest_jobs_select_columns())
            .gte("queued_at", cutoff)
            .order("queued_at", desc=True)
            .execute()
        )

    try:
        jobs_resp = _query()
    except Exception as exc:  # noqa: BLE001
        if _stage_progress_select_enabled and "stage_progress" in str(exc):
            _disable_stage_progress_select(exc)
            jobs_resp = _query()
        else:
            raise
    job_rows = jobs_resp.data or []

    # doc_id 별 latest job (queued_at desc 정렬이라 첫 row 가 latest)
    latest_by_doc: dict[str, dict] = {}
    for row in job_rows:
        doc_id = row["doc_id"]
        if doc_id not in latest_by_doc:
            latest_by_doc[doc_id] = row

    # latest 의 status 가 active 인 doc 만 유지
    latest_by_doc = {
        doc_id: row
        for doc_id, row in latest_by_doc.items()
        if row.get("status") in _ACTIVE_DOC_STATUSES
    }

    if not latest_by_doc:
        return ActiveDocsResponse(items=[])

    docs_resp = (
        supabase.table("documents")
        .select("id, title, size_bytes")
        .in_("id", list(latest_by_doc.keys()))
        .execute()
    )
    doc_meta = {d["id"]: d for d in (docs_resp.data or [])}

    items: list[ActiveDocItem] = []
    for doc_id, row in latest_by_doc.items():
        meta = doc_meta.get(doc_id)
        if meta is None:
            continue  # 이상 케이스: ingest_jobs 는 있는데 documents row 없음
        items.append(
            ActiveDocItem(
                doc_id=doc_id,
                file_name=meta.get("title") or doc_id,
                size_bytes=meta.get("size_bytes") or 0,
                job=JobStatus(
                    job_id=row["id"],
                    status=row["status"],
                    current_stage=row.get("current_stage"),
                    attempts=row.get("attempts", 0),
                    error_msg=row.get("error_msg"),
                    queued_at=row["queued_at"],
                    started_at=row.get("started_at"),
                    finished_at=row.get("finished_at"),
                    estimated_remaining_ms=compute_remaining_ms(
                        supabase,
                        job_status=row["status"],
                        current_stage=row.get("current_stage"),
                        stage_progress=row.get("stage_progress"),
                    ),
                    stage_progress=row.get("stage_progress"),
                ),
            )
        )
    return ActiveDocsResponse(items=items)


@router.get("/batch-status", response_model=BatchStatusResponse)
def batch_status(
    ids: str = Query(
        ...,
        description="콤마 구분 doc_id 리스트 (max 50). 예: ?ids=uuid1,uuid2",
    ),
) -> BatchStatusResponse:
    """여러 doc_id 의 latest job status 를 한 번에 조회 (W2 §3.H, W1 §6 이월).

    프론트 폴러가 doc_id 단위 N회 → batch 단위 1회로 호출 횟수 절감.
    1 SQL 로 모든 jobs 가져온 뒤 Python 측에서 doc_id 별 latest 만 추출.
    """
    doc_ids = [s.strip() for s in ids.split(",") if s.strip()]
    if not doc_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ids 가 비어있습니다.",
        )
    if len(doc_ids) > _BATCH_STATUS_MAX_IDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"한 번에 최대 {_BATCH_STATUS_MAX_IDS}개 (요청: {len(doc_ids)})",
        )

    supabase = get_supabase_client()

    def _query():
        return (
            supabase.table("ingest_jobs")
            .select(_ingest_jobs_select_columns())
            .in_("doc_id", doc_ids)
            .order("queued_at", desc=True)
            .execute()
        )

    try:
        jobs_resp = _query()
    except Exception as exc:  # noqa: BLE001
        if _stage_progress_select_enabled and "stage_progress" in str(exc):
            _disable_stage_progress_select(exc)
            jobs_resp = _query()
        else:
            raise
    rows = jobs_resp.data or []

    # 각 doc_id 의 첫 번째 row (queued_at 최대) 가 latest
    latest_by_doc: dict[str, dict] = {}
    for row in rows:
        doc_id = row["doc_id"]
        if doc_id not in latest_by_doc:
            latest_by_doc[doc_id] = row

    items: list[BatchStatusItem] = []
    for doc_id in doc_ids:
        row = latest_by_doc.get(doc_id)
        if row is None:
            items.append(BatchStatusItem(doc_id=doc_id, job=None))
            continue
        items.append(
            BatchStatusItem(
                doc_id=doc_id,
                job=JobStatus(
                    job_id=row["id"],
                    status=row["status"],
                    current_stage=row.get("current_stage"),
                    attempts=row.get("attempts", 0),
                    error_msg=row.get("error_msg"),
                    queued_at=row["queued_at"],
                    started_at=row.get("started_at"),
                    finished_at=row.get("finished_at"),
                    estimated_remaining_ms=compute_remaining_ms(
                        supabase,
                        job_status=row["status"],
                        current_stage=row.get("current_stage"),
                        stage_progress=row.get("stage_progress"),
                    ),
                    stage_progress=row.get("stage_progress"),
                ),
            )
        )

    return BatchStatusResponse(items=items)


# ============================================================
# GET /documents/{doc_id} — `/doc/[id]` 경량판 (W2 §3.M)
# ============================================================
@router.get("/{doc_id}", response_model=DocumentDetailResponse)
def get_document(doc_id: str) -> DocumentDetailResponse:
    """단건 종합 조회 — `/doc/[id]` 페이지가 한 번에 필요한 메타·태그·요약·진행 상태."""
    supabase = get_supabase_client()
    doc_resp = (
        supabase.table("documents")
        .select(
            "id, title, doc_type, source_channel, size_bytes, content_type, "
            "tags, summary, flags, created_at, received_ms"
        )
        .eq("id", doc_id)
        .is_("deleted_at", "null")
        .limit(1)
        .execute()
    )
    rows = doc_resp.data or []
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="문서를 찾을 수 없습니다.",
        )
    doc = rows[0]

    chunks_resp = (
        supabase.table("chunks")
        .select("id", count="exact")
        .eq("doc_id", doc_id)
        .execute()
    )
    chunks_count = chunks_resp.count or 0

    job = get_latest_job_for_doc(doc_id)
    latest_job = (
        JobStatus(
            job_id=job.id,
            status=job.status,
            current_stage=job.current_stage,
            attempts=job.attempts,
            error_msg=job.error_msg,
            queued_at=job.queued_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
        )
        if job
        else None
    )

    flags = dict(doc.get("flags") or {})
    source_url_raw = flags.get("source_url")
    source_url = source_url_raw if isinstance(source_url_raw, str) else None

    return DocumentDetailResponse(
        id=doc["id"],
        title=doc["title"],
        doc_type=doc["doc_type"],
        source_channel=doc["source_channel"],
        size_bytes=doc["size_bytes"],
        content_type=doc["content_type"],
        tags=list(doc.get("tags") or []),
        summary=doc.get("summary"),
        flags=flags,
        chunks_count=chunks_count,
        latest_job=latest_job,
        created_at=doc["created_at"],
        received_ms=doc.get("received_ms"),
        source_url=source_url,
    )


# ============================================================
# GET /documents/{doc_id}/status
# ============================================================
@router.get("/{doc_id}/status", response_model=DocumentStatusResponse)
def get_document_status(
    doc_id: str,
    include_logs: bool = Query(False, alias="include_logs"),
) -> DocumentStatusResponse:
    supabase = get_supabase_client()
    existing = (
        supabase.table("documents")
        .select("id")
        .eq("id", doc_id)
        .limit(1)
        .execute()
    )
    if not existing.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="문서를 찾을 수 없습니다.",
        )

    job = get_latest_job_for_doc(doc_id)
    job_payload = (
        JobStatus(
            job_id=job.id,
            status=job.status,
            current_stage=job.current_stage,
            attempts=job.attempts,
            error_msg=job.error_msg,
            queued_at=job.queued_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
        )
        if job
        else None
    )
    logs = list_logs_for_job(job.id) if include_logs and job else None

    return DocumentStatusResponse(doc_id=doc_id, job=job_payload, logs=logs)
