"""python-pptx 기반 PPTX 프레젠테이션 파서 (W7 후속 — DE-68).

배경
- W4-Q-9 sniff (`work-log/2026-05-02 W4-Q-9 sniff DOCX·PPTX 라이브러리 평가.md`) 결과
  python-pptx 1.x 의 `Presentation.slides` + `shape.has_text_frame` 으로 충분 판정.
- 페르소나 A 의 회의 발표·교육 자료 빈도 ↑ → 사용자 자료 업로드 시점에 ship.

설계
- 슬라이드 1개 = `ExtractedSection` 1개 (raw_text 는 슬라이드 내부 모든 텍스트 join)
  · DOCX 와 달리 슬라이드 단위 스토리텔링 — chunking 직전 단계에서 슬라이드 경계 보존이
    검색 품질에 유리 (W3 청킹 정책의 page 분할 효과 동일)
- `page` = slide_index + 1 (1-based, search UI 의 "p.N" 표기와 호환)
- `section_title` = title placeholder 우선 → 첫 번째 텍스트 shape fallback
- 표 (Shape with `has_table`): DocxParser `_table_to_text` 와 동일 ` | ` separator
- 그림(picture) / chart 의 caption 텍스트는 추출 X — Vision 어댑터 스코프 (DE-68 후속)

graceful degrade
- python-pptx 가 corrupted PPTX 에서 raise → RuntimeError wrap (DocxParser 패턴)
- 슬라이드/shape 단위 부분 실패는 warnings 누적, 다음 슬라이드 계속
"""

from __future__ import annotations

import io
import logging
from pathlib import PurePosixPath

from pptx import Presentation
from pptx.util import Inches  # noqa: F401 — 향후 bbox 계산 시 사용

from app.adapters.parser import ExtractedSection, ExtractionResult

logger = logging.getLogger(__name__)


class PptxParser:
    source_type = "pptx"

    def can_parse(self, file_name: str, mime_type: str | None) -> bool:
        ext = PurePosixPath(file_name).suffix.lower()
        return ext == ".pptx"

    def parse(self, data: bytes, *, file_name: str) -> ExtractionResult:
        sections: list[ExtractedSection] = []
        warnings: list[str] = []
        raw_parts: list[str] = []

        try:
            prs = Presentation(io.BytesIO(data))
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"PPTX 파서 초기화 실패: {file_name}: {exc}"
            ) from exc

        for slide_idx, slide in enumerate(prs.slides):
            try:
                slide_title = _extract_slide_title(slide)
                slide_text_parts = _extract_slide_text(slide, warnings=warnings)
                if not slide_text_parts:
                    continue
                slide_text = "\n".join(slide_text_parts)
                sections.append(
                    ExtractedSection(
                        text=slide_text,
                        page=slide_idx + 1,
                        section_title=slide_title,
                        bbox=None,
                    )
                )
                raw_parts.append(slide_text)
            except Exception as exc:  # noqa: BLE001 — 슬라이드 단위 부분 실패 허용
                msg = f"PPTX slide {slide_idx + 1} 추출 실패: {exc}"
                warnings.append(msg)
                logger.warning("%s (file=%s)", msg, file_name)
                continue

        return ExtractionResult(
            source_type=self.source_type,
            sections=sections,
            raw_text="\n\n".join(raw_parts),
            warnings=warnings,
        )


def _extract_slide_title(slide) -> str | None:
    """슬라이드 title placeholder 우선, 없으면 첫 번째 텍스트 shape 의 첫 줄."""
    try:
        title_shape = slide.shapes.title
        if title_shape is not None and title_shape.has_text_frame:
            text = (title_shape.text_frame.text or "").strip()
            if text:
                return text.splitlines()[0].strip()
    except Exception:  # noqa: BLE001 — 일부 레이아웃은 .title 미존재
        pass

    return _first_text_in_shapes(slide.shapes)


def _first_text_in_shapes(shapes_iter) -> str | None:
    """shape 컬렉션에서 첫 텍스트의 첫 줄 (GroupShape 재귀)."""
    for shape in shapes_iter:
        if hasattr(shape, "shapes"):
            nested = _first_text_in_shapes(shape.shapes)
            if nested:
                return nested
            continue
        if not getattr(shape, "has_text_frame", False):
            continue
        try:
            text = (shape.text_frame.text or "").strip()
        except Exception:  # noqa: BLE001
            continue
        if text:
            return text.splitlines()[0].strip()
    return None


def _extract_slide_text(slide, *, warnings: list[str]) -> list[str]:
    """슬라이드 내부 모든 텍스트(텍스트 박스 + 표 + GroupShape 재귀) 추출.

    - text_frame: 단락 단위 join (paragraph 사이 줄바꿈 보존)
    - table: ` | ` separator + 행 단위 줄바꿈 (DocxParser 패턴 재사용)
    - GroupShape: `.shapes` 재귀 — 디자인 PPT 의 흔한 그룹 구조
    - picture / chart 의 캡션: 무시 (Vision 어댑터 스코프, 후속)
    """
    parts: list[str] = []
    _walk_shapes(slide.shapes, parts=parts, warnings=warnings)
    return parts


def _walk_shapes(shapes_iter, *, parts: list[str], warnings: list[str]) -> None:
    """shape 컬렉션을 재귀 순회 — GroupShape 내부 텍스트 박스도 회수."""
    for shape in shapes_iter:
        try:
            # GroupShape — 자식 shape 재귀. msopptx 의 MSO_SHAPE_TYPE.GROUP = 6 비교는
            # 라이브러리 import 회피 위해 .shapes 속성 존재 여부로 duck-typing.
            if hasattr(shape, "shapes"):
                _walk_shapes(shape.shapes, parts=parts, warnings=warnings)
                continue
            if getattr(shape, "has_text_frame", False):
                tf_text = (shape.text_frame.text or "").strip()
                if tf_text:
                    parts.append(tf_text)
            elif getattr(shape, "has_table", False):
                table_text = _table_to_text(shape.table)
                if table_text:
                    parts.append(table_text)
        except Exception as exc:  # noqa: BLE001 — shape 단위 실패 허용
            warnings.append(f"PPTX shape 추출 실패: {exc}")
            continue


def _table_to_text(table) -> str:
    """표를 chunk_filter table_noise 룰이 마킹 가능한 형태로 텍스트화.

    DocxParser `_table_to_text` 동일 패턴 — 각 행 `cell1 | cell2 | ...`,
    행 사이 `\\n`. 빈 셀은 공란.
    """
    rows_text: list[str] = []
    for row in table.rows:
        try:
            cells = [(c.text or "").strip() for c in row.cells]
            if any(cells):
                rows_text.append(" | ".join(cells))
        except Exception:  # noqa: BLE001 — 행 단위 실패 허용
            continue
    return "\n".join(rows_text)
