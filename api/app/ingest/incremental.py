"""W25 D14 B sprint — incremental vision reingest 파이프라인.

기존 `run_pipeline()` 의 한계:
- chunks 전부 삭제 → 새로 적재 (full reingest)
- random 503 으로 정답 페이지 누락 시 답변 회귀 위험 (Sprint 3 G-S 데이터센터 PDF p.6 회귀)
- 매 시도마다 비용 + 시간 (Sprint 4 fail 시 chunks 0 영구 위험)

본 모듈의 incremental 흐름:
1. 기존 chunks 의 vision 처리 페이지 set 추출 (section_title `(vision) p.N` 매칭)
2. PDF storage download → PyMuPDF open → total_pages
3. 누락 페이지 list = (1..total) - vision 처리 페이지
4. 누락 페이지만 vision 호출 (extract.py 의 `_enrich_pdf_with_vision()` 의 sweep 패턴 재사용
   하되, **누락 페이지 list 만 처리**)
5. 새 vision sections 을 ChunkRecord 로 변환 (chunk_idx max + 1 부터)
6. load stage 호출 (chunks insert, dense_vec NULL)
7. embed stage 호출 — NULL dense_vec 만 BGE-M3 embed (기존 embed.py 변경 0)

특징:
- 기존 chunks 보존 (실패해도 검색 가능 상태 유지)
- vision 호출 = 누락 페이지만 (비용 / 시간 ↓↓)
- chunk_filter / content_gate / tag_summarize / doc_embed / dedup 안 호출 (이미 적용됨)
- 사용자 PDF 자체가 변경된 case 는 부정확 — full reingest 권고
"""

from __future__ import annotations

import logging
import os
from typing import Any

import fitz

from app.adapters.impl.image_parser import ImageParser
from app.adapters.impl.supabase_storage import SupabaseBlobStorage
from app.adapters.parser import ExtractedSection
from app.adapters.vectorstore import ChunkRecord
from app.config import get_settings
from app.db import get_supabase_client
from app.ingest.jobs import fail_job, finish_job, start_job
from app.ingest.stages.embed import run_embed_stage
from app.ingest.stages.load import run_load_stage

logger = logging.getLogger(__name__)

_VISION_ENRICH_TITLE_PREFIX = "(vision) p."  # extract.py 의 vision section_title 패턴
_SCAN_RENDER_DPI = 150
_MAX_SWEEPS = int(os.environ.get("JETRAG_PDF_VISION_ENRICH_MAX_SWEEPS", "3"))

_image_parser = ImageParser()


def _vision_processed_pages(supabase, doc_id: str) -> set[int]:
    """기존 chunks 중 vision 처리된 페이지 set."""
    resp = (
        supabase.table("chunks")
        .select("page,section_title")
        .eq("doc_id", doc_id)
        .execute()
    )
    pages: set[int] = set()
    for r in resp.data or []:
        title = r.get("section_title") or ""
        if title.startswith(_VISION_ENRICH_TITLE_PREFIX) and r.get("page"):
            pages.add(int(r["page"]))
    return pages


def _max_chunk_idx(supabase, doc_id: str) -> int:
    """기존 chunks 의 max chunk_idx (없으면 -1)."""
    resp = (
        supabase.table("chunks")
        .select("chunk_idx")
        .eq("doc_id", doc_id)
        .order("chunk_idx", desc=True)
        .limit(1)
        .execute()
    )
    rows = resp.data or []
    return int(rows[0]["chunk_idx"]) if rows else -1


def _vision_pages_with_sweep(
    pdf_data: bytes,
    *,
    pages: list[int],  # 1-indexed
    file_name: str,
    image_parser: ImageParser,
    doc_id: str | None = None,
    sha256: str | None = None,
) -> tuple[list[ExtractedSection], list[str]]:
    """누락 페이지 list 만 vision 호출 + sweep. 성공한 페이지의 sections 반환.

    extract.py 의 `_enrich_pdf_with_vision()` 의 sweep 패턴 재사용.

    Phase 1 S0 D2 — sha256 전달 시 ImageParser 가 vision_page_cache lookup → hit 시
    호출 0. incremental 이 누락 페이지만 처리하므로 보통 cache miss 가 default 지만,
    중복 reingest / 동시성 race 시 hit 가능.
    """
    sections: list[ExtractedSection] = []
    warnings: list[str] = []
    if not pages:
        return sections, warnings

    try:
        doc = fitz.open(stream=pdf_data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"incremental_vision: PDF 열기 실패: {exc}")
        return sections, warnings

    try:
        # 1-indexed → 0-indexed for PyMuPDF
        pending = [p - 1 for p in pages if 0 < p <= len(doc)]
        for sweep_idx in range(1, _MAX_SWEEPS + 1):
            if not pending:
                break
            if sweep_idx > 1:
                logger.info(
                    "incremental_vision sweep %d/%d: 누락 %d 재시도 %s (file=%s)",
                    sweep_idx, _MAX_SWEEPS, len(pending),
                    [p + 1 for p in pending], file_name,
                )
            failed: list[int] = []
            for page_idx in pending:
                try:
                    page = doc[page_idx]
                    pix = page.get_pixmap(dpi=_SCAN_RENDER_DPI)
                    png = pix.tobytes("png")
                    page_result = image_parser.parse(
                        png,
                        file_name=f"{file_name}#page{page_idx + 1}.png",
                        source_type="pdf_vision_enrich",
                        doc_id=doc_id,
                        page=page_idx + 1,
                        sha256=sha256,
                    )
                    for sec in page_result.sections:
                        base_title = (sec.section_title or "").strip()
                        enriched_title = (
                            f"{_VISION_ENRICH_TITLE_PREFIX}{page_idx + 1} {base_title}".strip()
                            if base_title
                            else f"{_VISION_ENRICH_TITLE_PREFIX}{page_idx + 1}"
                        )
                        sections.append(
                            ExtractedSection(
                                text=sec.text,
                                page=page_idx + 1,
                                section_title=enriched_title,
                                bbox=None,
                            )
                        )
                    warnings.extend(page_result.warnings)
                except Exception as exc:  # noqa: BLE001
                    failed.append(page_idx)
                    if sweep_idx == _MAX_SWEEPS:
                        warnings.append(
                            f"incremental_vision: page {page_idx + 1} 실패 "
                            f"(sweep {sweep_idx}/{_MAX_SWEEPS} 최종): {exc}"
                        )
                    logger.warning(
                        "incremental_vision page %d 실패 (sweep %d/%d): %s",
                        page_idx + 1, sweep_idx, _MAX_SWEEPS, exc,
                    )
            pending = failed

        if pending:
            warnings.append(
                f"incremental_vision: {_MAX_SWEEPS} sweep 후에도 누락: "
                f"{[p + 1 for p in pending]}"
            )
    finally:
        doc.close()

    return sections, warnings


def _sections_to_chunks(
    sections: list[ExtractedSection],
    *,
    doc_id: str,
    start_chunk_idx: int,
) -> list[ChunkRecord]:
    """vision sections → ChunkRecord (dense_vec NULL — embed stage 가 채움)."""
    out: list[ChunkRecord] = []
    for offset, sec in enumerate(sections):
        out.append(
            ChunkRecord(
                doc_id=doc_id,
                chunk_idx=start_chunk_idx + offset,
                text=sec.text,
                page=sec.page,
                section_title=sec.section_title,
                bbox=sec.bbox,
                char_range=(0, len(sec.text)),
                metadata={"vision_incremental": True},
            )
        )
    return out


def run_incremental_vision_pipeline(
    job_id: str,
    doc_id: str,
) -> dict[str, Any]:
    """incremental reingest entrypoint — 기존 chunks 보존 + 누락 페이지만 vision 처리.

    Returns dict: { processed_pages, skipped_pages, chunks_inserted, total_pages, warnings }
    """
    try:
        start_job(job_id, stage="extract")

        client = get_supabase_client()
        doc = (
            client.table("documents")
            .select("doc_type,storage_path,sha256")
            .eq("id", doc_id)
            .limit(1)
            .execute()
        )
        if not doc.data:
            raise RuntimeError(f"documents 레코드 없음: {doc_id}")
        doc_row = doc.data[0]
        if doc_row["doc_type"] != "pdf":
            raise RuntimeError(
                f"incremental vision 은 PDF 만 지원 (doc_type={doc_row['doc_type']!r})"
            )

        file_name = os.path.basename(doc_row["storage_path"])
        # Phase 1 S0 D2 — vision_page_cache lookup 키.
        doc_sha256 = doc_row.get("sha256")
        storage = SupabaseBlobStorage(bucket=get_settings().supabase_storage_bucket)
        pdf_data = storage.get(doc_row["storage_path"])

        # 누락 페이지 detect
        with fitz.open(stream=pdf_data, filetype="pdf") as fdoc:
            total_pages = len(fdoc)
        processed = _vision_processed_pages(client, doc_id)
        all_pages = set(range(1, total_pages + 1))
        missing = sorted(all_pages - processed)

        logger.info(
            "incremental_vision: doc=%s total=%d processed=%d missing=%d %s",
            doc_id, total_pages, len(processed), len(missing), missing[:20],
        )

        if not missing:
            logger.info("incremental_vision: 누락 0 — skip (doc=%s)", doc_id)
            finish_job(job_id)
            return {
                "processed_pages": sorted(processed),
                "skipped_pages": [],
                "newly_processed_pages": [],
                "chunks_inserted": 0,
                "total_pages": total_pages,
                "warnings": [],
            }

        # 누락 페이지 vision 호출 (sweep)
        sections, warnings = _vision_pages_with_sweep(
            pdf_data,
            pages=missing,
            file_name=file_name,
            image_parser=_image_parser,
            doc_id=doc_id,
            sha256=doc_sha256,
        )

        # ChunkRecord 변환
        start_idx = _max_chunk_idx(client, doc_id) + 1
        chunks = _sections_to_chunks(
            sections, doc_id=doc_id, start_chunk_idx=start_idx
        )
        loaded = run_load_stage(job_id, chunks=chunks) if chunks else 0
        embedded = run_embed_stage(job_id, doc_id=doc_id) if loaded > 0 else 0

        # 다시 누락 detect (sweep 후 여전히 실패한 페이지)
        new_processed = _vision_processed_pages(client, doc_id)
        newly = sorted(new_processed - processed)
        still_missing = sorted(set(missing) - new_processed)

        logger.info(
            "incremental_vision done: doc=%s newly=%d still_missing=%d loaded=%d embedded=%d",
            doc_id, len(newly), len(still_missing), loaded, embedded,
        )

        finish_job(job_id)
        return {
            "processed_pages": sorted(new_processed),
            "skipped_pages": still_missing,
            "newly_processed_pages": newly,
            "chunks_inserted": loaded,
            "embedded": embedded,
            "total_pages": total_pages,
            "warnings": warnings,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "incremental_vision pipeline failed: job=%s doc=%s", job_id, doc_id
        )
        try:
            fail_job(job_id, error_msg=str(exc))
        except Exception:
            logger.exception("fail_job 실패")
        raise
