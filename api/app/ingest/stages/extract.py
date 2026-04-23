"""Extract 스테이지 — 포맷별 원본 파일 추출 (기획서 §10.2 [4] · §10.3).

Day 4 범위
- PDF: `PyMuPDFParser` 로 블록 단위 섹션·bbox·페이지 추출
- 그 외 포맷(hwp/hwpx/docx/pptx/image/url/txt/md): **graceful skip** — 파이프라인을 실패로 몰지
  않고 `documents.flags.extract_skipped = true` 로 표시 후 `ingest_logs` 에 skipped 로 기록.
  W2 에 각 포맷 어댑터가 추가되면 `POST /documents/{id}/reingest` (별도) 로 재처리.

Day 4 이후
- 스캔본(텍스트 레이어 없음) 감지 → Vision 경로 재라우팅
- HWP/HWPX/DOCX 어댑터 구현체 추가
"""

from __future__ import annotations

import os
from typing import Any

from app.adapters.impl.pymupdf_parser import PyMuPDFParser
from app.adapters.impl.supabase_storage import SupabaseBlobStorage
from app.adapters.parser import ExtractionResult
from app.config import get_settings
from app.db import get_supabase_client
from app.ingest.jobs import skip_stage, stage

_STAGE = "extract"
_pdf_parser = PyMuPDFParser()


def run_extract_stage(job_id: str, doc_id: str) -> ExtractionResult | None:
    """스테이지 실행. PDF 는 `ExtractionResult` 반환, 그 외는 skip 후 `None` 반환.

    스테이지 로그 갱신·flags 마킹까지 내부에서 처리한다. 호출자(pipeline)는 반환값이 None 이면
    다음 스테이지를 건너뛰고 job 을 completed 로 마감하면 된다.
    """
    client = get_supabase_client()
    doc = _fetch_document(client, doc_id)
    doc_type = doc["doc_type"]

    if doc_type != "pdf":
        _mark_unsupported_format(client, doc_id, doc_type=doc_type, flags=doc.get("flags") or {})
        skip_stage(
            job_id,
            stage=_STAGE,
            reason=f"{doc_type} 포맷은 아직 지원되지 않습니다 (W2 예정).",
        )
        return None

    file_name = os.path.basename(doc["storage_path"])
    storage = SupabaseBlobStorage(bucket=get_settings().supabase_storage_bucket)

    with stage(job_id, _STAGE):
        data = storage.get(doc["storage_path"])
        result = _pdf_parser.parse(data, file_name=file_name)

    return result


# ---------------------- internals ----------------------


def _fetch_document(client: Any, doc_id: str) -> dict:
    resp = (
        client.table("documents")
        .select("doc_type, storage_path, flags")
        .eq("id", doc_id)
        .limit(1)
        .execute()
    )
    if not resp.data:
        raise RuntimeError(f"documents 레코드를 찾을 수 없습니다: {doc_id}")
    return resp.data[0]


def _mark_unsupported_format(
    client: Any, doc_id: str, *, doc_type: str, flags: dict,
) -> None:
    updated = dict(flags)
    updated["extract_skipped"] = True
    updated["extract_skipped_reason"] = (
        f"doc_type={doc_type} 는 아직 지원되지 않는 포맷입니다 (W2 예정)."
    )
    client.table("documents").update({"flags": updated}).eq("id", doc_id).execute()
