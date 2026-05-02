"""PyMuPDF (`fitz`) 기반 PDF 문서 파서.

- 블록 단위 섹션 추출 (페이지 내 문단 경계 유지)
- 페이지 단위 부분 실패 허용 (`warnings` 누적)
- heading sticky propagate (W4 Day 2, W4-Q-17) — 한 번 잡힌 heading 을 다음
  heading 까지 모든 블록의 `section_title` 로 상속 (HwpxParser 패턴 동일).
  heading 단락 자체도 그 title 로 self-tag 되어 검색 대상에 포함 (옵션 A).
- 스캔본(텍스트 레이어 없음) 감지는 W2 Vision 경로와 함께 구현 예정

heading 휴리스틱 (W4-Q-17, 명세 §3.W4-Q-17)
- (A) **font size 비율** — block 내 max span size 가 page median size × `_HEADING_FONT_RATIO`
  이상이면 heading. page 평균은 outlier (대형 표지 폰트 60pt 등) 에 취약 → median 사용.
- (B) **inline 텍스트 패턴** — `제N조`, `부칙`, `별표`, `【판시사항】` 등. HwpxParser 패턴
  + 한국 법률 PDF 의 `【...】`/`[...]` 추가. text 길이 ≤ `_HEADING_TEXT_MAX_LEN` 일 때만 적용.
- bold flag (`flags & 16`) 는 사용자 자산 sniff 결과 거의 미사용 → 미구현. TODO 참조.
- `get_text("dict")` 호출 실패 시 `get_text("blocks")` 로 fallback (graceful degrade).
- page median 이 0 이면 (텍스트 추출 실패 등) font 휴리스틱 skip, 텍스트 패턴만 사용.
"""

from __future__ import annotations

import logging
import re
from pathlib import PurePosixPath
from statistics import median

import fitz  # PyMuPDF

from app.adapters.parser import ExtractedSection, ExtractionResult

logger = logging.getLogger(__name__)


# heading 판별 — font size 비율 임계
# 1.15 = page median 대비 15% 이상 큰 글꼴이면 heading 후보 (sniff: sonata 9pt→21pt 2.3x,
# law sample3 10.1pt→12pt 1.19x). 1.10 은 본문 내 강조 텍스트가 false positive,
# 1.20 은 law sample3 의 12pt heading miss → 1.15 가 적정.
_HEADING_FONT_RATIO = 1.15

# heading 판별 — 텍스트 inline 패턴 (HwpxParser + 한국 법률 PDF 패턴 추가)
_HEADING_TEXT_PATTERN = re.compile(
    r"^(제\s*\d+\s*[조항장절편관]|부칙|별표\s*\d*|별첨\s*\d*"
    r"|【[^】]{1,30}】|\[[^\]]{1,30}\]"
    r"|Chapter\s*\d*|Section\s*\d*)([\s(].*)?$"
)

# 텍스트 패턴 적용 최대 길이 — prefix-only false positive 차단
_HEADING_TEXT_MAX_LEN = 80

# PyMuPDF dict 모드의 block type
# 0 = text block, 1 = image block
_BLOCK_TYPE_TEXT = 0


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

        # heading sticky propagate — doc 전체 sticky (HwpxParser 와 동일 정책).
        # 페이지 경계를 넘어도 다음 heading 만나기 전까지 직전 title 상속.
        current_title: str | None = None

        try:
            for page_num, page in enumerate(doc, start=1):
                try:
                    page_dict = _get_page_dict(page)
                except Exception as exc:  # noqa: BLE001 — dict 실패 시 blocks fallback
                    msg = (
                        f"page {page_num} dict 추출 실패 → blocks fallback: {exc}"
                    )
                    warnings.append(msg)
                    logger.warning("%s (file=%s)", msg, file_name)
                    page_dict = None

                try:
                    if page_dict is not None:
                        new_title = _extract_dict_blocks(
                            page_dict,
                            page_num=page_num,
                            current_title=current_title,
                            sections=sections,
                            raw_parts=raw_parts,
                        )
                        current_title = new_title
                    else:
                        # graceful degrade — heading 미감지, 본문만 추출
                        _extract_legacy_blocks(
                            page,
                            page_num=page_num,
                            current_title=current_title,
                            sections=sections,
                            raw_parts=raw_parts,
                        )
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


def _get_page_dict(page: fitz.Page) -> dict:
    """`page.get_text("dict")` wrapper — 테스트에서 mock 가능."""
    return page.get_text("dict")


def _extract_dict_blocks(
    page_dict: dict,
    *,
    page_num: int,
    current_title: str | None,
    sections: list[ExtractedSection],
    raw_parts: list[str],
) -> str | None:
    """dict 모드 블록 순회 + heading sticky propagate.

    Returns: page 처리 후의 `current_title` (다음 page 로 sticky 전파).
    """
    page_median = _page_median_size(page_dict)

    for block in page_dict.get("blocks", []):
        if block.get("type", 0) != _BLOCK_TYPE_TEXT:
            continue
        text = _block_text(block).strip()
        if not text:
            continue

        block_max = _block_max_size(block)
        if _is_heading_block(block_max, page_median, text):
            current_title = text

        bbox = block.get("bbox")
        sections.append(
            ExtractedSection(
                text=text,
                page=page_num,
                section_title=current_title,
                bbox=tuple(float(x) for x in bbox) if bbox else None,
            )
        )
        raw_parts.append(text)

    return current_title


def _extract_legacy_blocks(
    page: fitz.Page,
    *,
    page_num: int,
    current_title: str | None,
    sections: list[ExtractedSection],
    raw_parts: list[str],
) -> None:
    """`get_text("blocks")` fallback — heading 미감지, 직전 sticky title 만 상속.

    dict 모드 호출이 실패한 페이지에서만 사용. PyMuPDF 가 표/이미지 페이지에서
    dict 가 비어 있으나 blocks 는 추출하는 케이스 대비.
    """
    for block in page.get_text("blocks"):
        x0, y0, x1, y1, btext, _block_no, btype = block[:7]
        if btype != _BLOCK_TYPE_TEXT:
            continue
        clean = (btext or "").strip()
        if not clean:
            continue
        sections.append(
            ExtractedSection(
                text=clean,
                page=page_num,
                section_title=current_title,
                bbox=(float(x0), float(y0), float(x1), float(y1)),
            )
        )
        raw_parts.append(clean)


def _page_median_size(page_dict: dict) -> float:
    """페이지의 본문 폰트 size 중앙값. 비어있으면 0.0.

    median 사용 이유 — 카탈로그성 PDF (sonata) 는 표지에 60pt 한 글자가 박혀
    평균을 끌어올림. median 은 outlier 에 robust → 본문 9pt 가 그대로 잡힘.
    """
    sizes: list[float] = []
    for block in page_dict.get("blocks", []):
        if block.get("type", 0) != _BLOCK_TYPE_TEXT:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                size = span.get("size")
                if isinstance(size, (int, float)) and size > 0:
                    sizes.append(float(size))
    if not sizes:
        return 0.0
    return float(median(sizes))


def _block_max_size(block: dict) -> float:
    """블록 내 모든 span 의 max font size. 비어있으면 0.0."""
    sizes: list[float] = []
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            size = span.get("size")
            if isinstance(size, (int, float)) and size > 0:
                sizes.append(float(size))
    if not sizes:
        return 0.0
    return max(sizes)


def _block_text(block: dict) -> str:
    """블록 내 모든 span 텍스트를 line break 로 join.

    PyMuPDF dict 의 line 단위 줄바꿈을 보존 — chunk 단계에서 다시 처리.
    """
    lines: list[str] = []
    for line in block.get("lines", []):
        spans = line.get("spans", [])
        line_text = "".join(span.get("text", "") for span in spans)
        if line_text:
            lines.append(line_text)
    return "\n".join(lines)


def _is_heading_block(
    block_max_size: float, page_median_size: float, text: str
) -> bool:
    """블록이 heading 후보인지 판정.

    (A) font size 비율 — block_max ≥ page_median × `_HEADING_FONT_RATIO` (page_median > 0)
    (B) 텍스트 inline 패턴 — 길이 ≤ `_HEADING_TEXT_MAX_LEN` 일 때만 적용

    TODO(W4+): bold flag (`flags & 16`) 휴리스틱 — 사용자 자산 sniff 결과 거의 미사용.
    추가 자산 ablation 후 도입 검토.
    """
    if (
        page_median_size > 0
        and block_max_size >= page_median_size * _HEADING_FONT_RATIO
    ):
        return True
    if len(text) <= _HEADING_TEXT_MAX_LEN and _HEADING_TEXT_PATTERN.match(text):
        return True
    return False
