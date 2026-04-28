"""python-hwpx 기반 HWPX 문서 파서.

W2 명세 v0.3 §3.C — 페르소나 A 의 주요 포맷. HWPX 는 한글 2014+ 의 기본 저장 포맷
(ZIP 컨테이너 + Contents/section*.xml).

설계
- `TextExtractor` 는 ZipFile / 경로를 받음 → bytes 는 BytesIO 로 감싸 ZipFile 구성
- section 단위로 단락을 순회해 `ExtractedSection` 누적
- HWPX 는 PDF 와 달리 page 개념이 없음 → `ExtractedSection.page` 는 항상 None
- 단락/섹션 단위 부분 실패 허용 (`warnings`)

`section_title` 정책
- `hwpx.SectionInfo.name` 은 ZIP 내부 XML 경로 (`Contents/sectionN.xml`) 라 사용자에게 노출하면
  메타데이터 노이즈만 됨 → sentinel 패턴이면 None 처리
- 진짜 의미의 "섹션 제목" 은 본문의 heading 단락 (W3+ 에서 paragraph_property 분석 도입 시 충족)
- 명세 §1.2 의 KPI "section_title ≥ 30%" 는 W4 이후 측정 대상

기획서 §10.3 그라데이션 — 한국 공공·기업 자료 다수가 HWPX. 추출 실패는 graceful skip 이 아니라
파이프라인 fail 로 가야 사용자가 인지 가능 → `parse()` 에서 raise.
"""

from __future__ import annotations

import io
import logging
import zipfile
from pathlib import PurePosixPath

import hwpx

from app.adapters.parser import ExtractedSection, ExtractionResult

logger = logging.getLogger(__name__)


class HwpxParser:
    source_type = "hwpx"

    def can_parse(self, file_name: str, mime_type: str | None) -> bool:
        ext = PurePosixPath(file_name).suffix.lower()
        if ext == ".hwpx":
            return True
        # HWPX 의 표준 MIME 타입은 정착되지 않음 → 확장자 우선.
        # 일부 클라이언트가 application/zip 으로 전송하는 케이스는 무시 (DOCX/PPTX 와 충돌)
        return False

    def parse(self, data: bytes, *, file_name: str) -> ExtractionResult:
        sections: list[ExtractedSection] = []
        warnings: list[str] = []
        raw_parts: list[str] = []

        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise RuntimeError(
                f"HWPX 열기 실패 (zip 형식 아님): {file_name}: {exc}"
            ) from exc

        try:
            try:
                extractor = hwpx.TextExtractor(zf)
            except Exception as exc:
                raise RuntimeError(
                    f"HWPX 파서 초기화 실패: {file_name}: {exc}"
                ) from exc

            try:
                for sec in extractor.iter_sections():
                    section_title = _normalize_section_title(sec.name)
                    try:
                        for para in extractor.iter_paragraphs(sec):
                            try:
                                text = extractor.paragraph_text(para.element).strip()
                            except Exception as exc:  # noqa: BLE001
                                msg = (
                                    f"section[{sec.index}] paragraph[{para.index}] "
                                    f"추출 실패: {exc}"
                                )
                                warnings.append(msg)
                                logger.warning("%s (file=%s)", msg, file_name)
                                continue
                            if not text:
                                continue
                            sections.append(
                                ExtractedSection(
                                    text=text,
                                    page=None,  # HWPX 는 page 개념 없음
                                    section_title=section_title,
                                    bbox=None,
                                )
                            )
                            raw_parts.append(text)
                    except Exception as exc:  # noqa: BLE001 — section 단위 부분 실패 허용
                        msg = (
                            f"section[{sec.index}] '{sec.name}' 단락 순회 실패: {exc}"
                        )
                        warnings.append(msg)
                        logger.warning("%s (file=%s)", msg, file_name)
            finally:
                extractor.close()
        finally:
            zf.close()

        return ExtractionResult(
            source_type=self.source_type,
            sections=sections,
            raw_text="\n\n".join(raw_parts),
            warnings=warnings,
        )


def _normalize_section_title(raw: str | None) -> str | None:
    """`hwpx.SectionInfo.name` 을 사용자 메타데이터로 적합한 형태로 정규화.

    `Contents/sectionN.xml` 같은 ZIP 내부 경로는 사용자 의미가 없어 None 으로 마스킹.
    """
    if not raw:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    if cleaned.startswith("Contents/section") and cleaned.endswith(".xml"):
        return None
    return cleaned
