"""Extract 스테이지 — 포맷별 원본 파일 추출 (기획서 §10.2 [4] · §10.3).

지원 포맷 (W2 + 후속 누적)
- PDF: `PyMuPDFParser` — 블록 단위 섹션·bbox·페이지 (스캔본 감지 시 ImageParser 재라우팅)
- HWPX: `HwpxParser` — section 단위 단락 (Day 3, §3.C)
- 이미지: `ImageParser` (Vision composition) — PNG/JPEG/HEIC (Day 3, §3.D)
- URL: `UrlParser` — trafilatura 본문 추출 (Day 4, §3.E)
- HWP 5.x: `Hwp5Parser` — pyhwp `hwp5txt` CLI + olefile fallback (Day 4 §3.F + DE-52)
- HWPML: `HwpmlParser` — 법제처/한컴 옛 XML 직렬화. doc_type='hwp' 그대로 두고
  raw bytes prefix sniff 로 dispatcher 가 분기 (DE-39 패턴)
- 그 외(docx/pptx/txt/md): **graceful skip** — `flags.extract_skipped=true` 마킹.
  W3 에 DOCX/PPTX 어댑터 도입 시 `/documents/{id}/reingest` 로 재처리.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import fitz  # PyMuPDF — 스캔 PDF rerouting 시 페이지를 PNG 로 렌더

from app.adapters.impl.docx_parser import DocxParser
from app.adapters.impl.hwp_parser import Hwp5Parser
from app.adapters.impl.hwpml_parser import HwpmlParser, is_hwpml_bytes
from app.adapters.impl.hwpx_parser import HwpxParser
from app.adapters.impl.image_parser import ImageParser
from app.adapters.impl.pptx_parser import PptxParser
from app.adapters.impl.pymupdf_parser import PyMuPDFParser
from app.adapters.impl.supabase_storage import SupabaseBlobStorage
from app.adapters.impl.url_parser import UrlParser
from app.adapters.parser import DocumentParser, ExtractedSection, ExtractionResult
from app.config import get_settings
from app.db import get_supabase_client
from app.ingest.jobs import skip_stage, stage

logger = logging.getLogger(__name__)

_STAGE = "extract"
_pdf_parser = PyMuPDFParser()
_hwpx_parser = HwpxParser()
_image_parser = ImageParser()
_url_parser = UrlParser()
_hwp_parser = Hwp5Parser()
_hwpml_parser = HwpmlParser()
_docx_parser = DocxParser()
_pptx_parser = PptxParser()

# doc_type → DocumentParser 디스패처.
# W5 DE-67 — DOCX 추가. W7 후속 — DE-68 PPTX ship (사용자 자료 업로드 시점).
_PARSERS_BY_DOC_TYPE: dict[str, DocumentParser] = {
    "pdf": _pdf_parser,
    "hwpx": _hwpx_parser,
    "image": _image_parser,
    "url": _url_parser,
    "hwp": _hwp_parser,
    "docx": _docx_parser,
    "pptx": _pptx_parser,
}

# 스캔 PDF 감지 임계값 — PyMuPDFParser raw_text 가 이 이하면 텍스트 레이어 부재로 간주
# (DE-36, W2 Day 3). 사용자 정의 가능하게 추후 분리 가능
_SCAN_TEXT_THRESHOLD = 50

# 스캔 PDF rerouting 시 처리할 max 페이지 수 — Vision API 비용·시간 cap
# (DE-36, 5페이지 ≈ Vision API 호출 50초). 그 이상은 warning + skip
_MAX_SCAN_PAGES = 5

# 스캔 페이지 → PNG 렌더 DPI (PyMuPDF 기본 72 → 150 으로 OCR 품질 확보)
_SCAN_RENDER_DPI = 150


def run_extract_stage(job_id: str, doc_id: str) -> ExtractionResult | None:
    """스테이지 실행. 지원 포맷이면 `ExtractionResult`, 그 외는 skip 후 `None`.

    스테이지 로그 갱신·flags 마킹까지 내부에서 처리한다. 호출자(pipeline)는 반환값이 None 이면
    다음 스테이지를 건너뛰고 job 을 completed 로 마감하면 된다.
    """
    client = get_supabase_client()
    doc = _fetch_document(client, doc_id)
    doc_type = doc["doc_type"]

    parser = _PARSERS_BY_DOC_TYPE.get(doc_type)
    if parser is None:
        _mark_unsupported_format(client, doc_id, doc_type=doc_type, flags=doc.get("flags") or {})
        skip_stage(
            job_id,
            stage=_STAGE,
            reason=f"{doc_type} 포맷은 아직 지원되지 않습니다 (후속 어댑터 도입 예정).",
        )
        return None

    file_name = os.path.basename(doc["storage_path"])
    storage = SupabaseBlobStorage(bucket=get_settings().supabase_storage_bucket)

    with stage(job_id, _STAGE):
        data = storage.get(doc["storage_path"])

        # HWP 변형 분기 — doc_type='hwp' 가 OLE2 (Hwp5Parser) 와 HWPML XML
        # (HwpmlParser) 둘 다 받음. raw bytes prefix 로 결정 (DE-39 패턴).
        if doc_type == "hwp" and is_hwpml_bytes(data[:4096]):
            logger.info(
                "HWPML(XML) 감지 → HwpmlParser 사용 (file=%s, doc_id=%s)",
                file_name,
                doc_id,
            )
            parser = _hwpml_parser

        result = parser.parse(data, file_name=file_name)

        # 스캔 PDF 재라우팅 (§3.A′) — PyMuPDF 가 텍스트 추출에 실패한 케이스
        if doc_type == "pdf" and _is_scan_pdf(result):
            logger.info(
                "스캔 PDF 감지 (raw_text=%d자, threshold=%d) → ImageParser fallback. doc_id=%s",
                len(result.raw_text.strip()),
                _SCAN_TEXT_THRESHOLD,
                doc_id,
            )
            result = _reroute_pdf_to_image(
                data, file_name=file_name, image_parser=_image_parser
            )
            _mark_scan_flag(client, doc_id, existing_flags=doc.get("flags") or {})

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


def _is_scan_pdf(result: ExtractionResult) -> bool:
    """raw_text 가 너무 빈약 → 텍스트 레이어 없는 스캔 PDF 로 판정."""
    return len(result.raw_text.strip()) <= _SCAN_TEXT_THRESHOLD


def _mark_scan_flag(client: Any, doc_id: str, *, existing_flags: dict) -> None:
    """`flags.scan = true` 마킹. doc_type 은 'pdf' 그대로 (DB CHECK 제약 준수)."""
    updated = dict(existing_flags)
    updated["scan"] = True
    client.table("documents").update({"flags": updated}).eq("id", doc_id).execute()


def _reroute_pdf_to_image(
    data: bytes, *, file_name: str, image_parser: ImageParser,
) -> ExtractionResult:
    """스캔 PDF 의 각 페이지를 PNG 로 렌더 → ImageParser.parse() 호출.

    멀티페이지는 페이지별 sections 을 누적. Vision API 비용 cap 으로 max _MAX_SCAN_PAGES.
    페이지 단위 부분 실패 허용 (warnings).

    명세 §3.A′ — doc_type='pdf' 유지 + flags.scan=true (DB CHECK 위반 회피).
    """
    sections: list[ExtractedSection] = []
    raw_parts: list[str] = []
    warnings: list[str] = []

    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise RuntimeError(
            f"스캔 PDF rerouting: PDF 열기 실패: {file_name}: {exc}"
        ) from exc

    try:
        total_pages = len(doc)
        process_count = min(total_pages, _MAX_SCAN_PAGES)
        if total_pages > _MAX_SCAN_PAGES:
            msg = (
                f"스캔 PDF {total_pages}페이지 중 첫 {_MAX_SCAN_PAGES}페이지만 처리 "
                "(Vision API 비용 cap)"
            )
            warnings.append(msg)
            logger.warning("%s (file=%s)", msg, file_name)

        for page_num in range(process_count):
            try:
                page = doc[page_num]
                pix = page.get_pixmap(dpi=_SCAN_RENDER_DPI)
                png_bytes = pix.tobytes("png")
                page_result = image_parser.parse(
                    png_bytes,
                    file_name=f"{file_name}#page{page_num + 1}.png",
                )
                for sec in page_result.sections:
                    base_title = sec.section_title or ""
                    sections.append(
                        ExtractedSection(
                            text=sec.text,
                            page=page_num + 1,
                            section_title=(
                                f"p.{page_num + 1} {base_title}".strip()
                                if base_title
                                else f"p.{page_num + 1}"
                            ),
                            bbox=None,
                        )
                    )
                if page_result.raw_text:
                    raw_parts.append(page_result.raw_text)
                warnings.extend(page_result.warnings)
            except Exception as exc:  # noqa: BLE001 — 페이지 단위 부분 실패 허용
                msg = f"page {page_num + 1} 스캔 fallback 실패: {exc}"
                warnings.append(msg)
                logger.warning("%s (file=%s)", msg, file_name)
    finally:
        doc.close()

    return ExtractionResult(
        source_type="pdf",  # 본질은 PDF, flags.scan=true 로 구분
        sections=sections,
        raw_text="\n\n".join(raw_parts),
        warnings=warnings,
    )
