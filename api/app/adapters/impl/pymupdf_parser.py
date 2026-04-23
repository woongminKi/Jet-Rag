"""PyMuPDF (`fitz`) 기반 PDF 문서 파서.

- 블록 단위 섹션 추출 (페이지 내 문단 경계 유지)
- 페이지 단위 부분 실패 허용 (`warnings` 누적)
- 섹션 제목 감지는 Day 4.5 이후 (현 시점은 `section_title=None`)
- 스캔본(텍스트 레이어 없음) 감지는 W2 Vision 경로와 함께 구현 예정
"""

from __future__ import annotations

import logging
from pathlib import PurePosixPath

import fitz  # PyMuPDF

from app.adapters.parser import ExtractedSection, ExtractionResult

logger = logging.getLogger(__name__)


class PyMuPDFParser:
    source_type = "pdf"

    def can_parse(self, file_name: str, mime_type: str | None) -> bool:
        ext = PurePosixPath(file_name).suffix.lower()
        if ext == ".pdf":
            return True
        return mime_type == "application/pdf"

    def parse(self, data: bytes, *, file_name: str) -> ExtractionResult:
        sections: list[ExtractedSection] = []
        warnings: list[str] = []
        raw_parts: list[str] = []

        try:
            doc = fitz.open(stream=data, filetype="pdf")
        except Exception as exc:
            raise RuntimeError(f"PDF 열기 실패: {file_name}: {exc}") from exc

        try:
            for page_num, page in enumerate(doc, start=1):
                try:
                    # get_text("blocks"): (x0, y0, x1, y1, text, block_no, block_type)
                    # block_type: 0 = text, 1 = image
                    for block in page.get_text("blocks"):
                        x0, y0, x1, y1, btext, _block_no, btype = block[:7]
                        if btype != 0:
                            continue
                        clean = (btext or "").strip()
                        if not clean:
                            continue
                        sections.append(
                            ExtractedSection(
                                text=clean,
                                page=page_num,
                                section_title=None,
                                bbox=(float(x0), float(y0), float(x1), float(y1)),
                            )
                        )
                        raw_parts.append(clean)
                except Exception as exc:  # noqa: BLE001 — 페이지 단위 부분 실패 허용
                    msg = f"page {page_num} 추출 실패: {exc}"
                    warnings.append(msg)
                    logger.warning("%s (file=%s)", msg, file_name)
        finally:
            doc.close()

        return ExtractionResult(
            source_type=self.source_type,
            sections=sections,
            raw_text="\n\n".join(raw_parts),
            warnings=warnings,
        )
