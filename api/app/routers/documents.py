"""문서 업로드 및 인제스트 상태 조회 엔드포인트.

기획서 참조
- §10.2 인제스트 파이프라인 전체 플로우
- §10.8 Tier 1 중복 감지 (SHA-256)
- §10.11 SLO: 수신 응답 < 2초
- §11.3 입력 게이트 단계 A (확장자 화이트리스트, 크기 50MB)
"""

from __future__ import annotations

import hashlib
import time
import uuid
from pathlib import PurePosixPath
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel

from app.config import get_settings
from app.db import get_supabase_client
from app.ingest import (
    create_job,
    get_latest_job_for_doc,
    list_logs_for_job,
    run_full_ingest,
    run_pipeline,
)
from app.routers._input_gate import HEAD_BYTES, validate_magic

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


class DocumentStatusResponse(BaseModel):
    doc_id: str
    job: JobStatus | None
    logs: list[dict] | None = None


class ReingestResponse(BaseModel):
    doc_id: str
    job_id: str
    chunks_deleted: int


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
            (
                supabase.table("documents")
                .update({"received_ms": received_ms})
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
    doc_title = title or PurePosixPath(file_name).stem
    received_ms = int((time.perf_counter() - started_at) * 1000)
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
) -> ReingestResponse:
    """기존 doc 의 chunks/메타를 reset 하고 같은 storage_path 로 파이프라인 재실행.

    용례: Day 4 데이터처럼 dense_vec / doc_embedding 이 NULL 인 기존 문서를
    Day 5 임베딩 스테이지를 포함한 현재 파이프라인으로 재처리.

    - 진행 중 job (queued/running) 이 있으면 409 로 거부
    - 기존 chunks 전부 삭제 + documents.tags/summary/flags/doc_embedding NULL reset
    - 새 ingest_jobs row 추가 (이전 jobs/logs 는 history 로 보존)
    - storage_path · sha256 등 원본 식별자는 유지 — Storage 재업로드 X
    """
    supabase = get_supabase_client()

    existing = (
        supabase.table("documents")
        .select("id")
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

    latest = get_latest_job_for_doc(doc_id)
    if latest and latest.status in ("queued", "running"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"진행 중인 작업이 있습니다 (job={latest.id}, status={latest.status}). 완료 후 다시 시도하세요.",
        )

    chunks_deleted = _reset_doc_for_reingest(supabase, doc_id)

    job = create_job(doc_id=doc_id)
    background_tasks.add_task(run_pipeline, job.id, doc_id)

    return ReingestResponse(
        doc_id=doc_id, job_id=job.id, chunks_deleted=chunks_deleted
    )


# ============================================================
# 내부 헬퍼
# ============================================================
def _reset_doc_for_reingest(supabase, doc_id: str) -> int:
    """chunks 전체 삭제 + documents 의 재계산 대상 필드 reset.

    POST /documents 의 failed 자동 reingest 분기와 POST /documents/{id}/reingest
    가 공통으로 사용한다. 새 ingest_jobs row 생성과 BackgroundTasks 큐잉은
    호출자가 책임진다 (응답 형태가 다르기 때문).
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

    supabase.table("documents").update(
        {
            "tags": [],
            "summary": None,
            "flags": {},
            "doc_embedding": None,
        }
    ).eq("id", doc_id).execute()

    return chunks_deleted


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
