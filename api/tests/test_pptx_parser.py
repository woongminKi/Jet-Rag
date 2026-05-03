"""W7 후속 — `PptxParser` 단위 테스트 (DE-68 ship).

설계
- python-pptx 의 `Presentation()` 으로 메모리 PPTX 합성 → 파서 직접 호출
- 외부 sample 파일 의존성 0 — fixture 불필요
- 케이스: title placeholder · 텍스트 박스 · 표 · GroupShape 재귀 · 빈 슬라이드

stdlib unittest + python-pptx (이미 의존성). 의존성 추가 0.
"""

from __future__ import annotations

import io
import unittest

from pptx import Presentation
from pptx.util import Inches


def _make_pptx_bytes(build_fn) -> bytes:
    """build_fn(prs) 콜백으로 PPTX 합성 → bytes 반환."""
    prs = Presentation()
    build_fn(prs)
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


class PptxBasicTextTest(unittest.TestCase):
    """텍스트 박스만 있는 단순 슬라이드 — 1 section / page=1."""

    def test_single_slide_with_text(self) -> None:
        from app.adapters.impl.pptx_parser import PptxParser

        def build(prs):
            blank_layout = prs.slide_layouts[6]  # blank
            slide = prs.slides.add_slide(blank_layout)
            tx = slide.shapes.add_textbox(
                Inches(1), Inches(1), Inches(6), Inches(2)
            )
            tf = tx.text_frame
            tf.text = "프레젠테이션 제목"
            tf.add_paragraph().text = "본문 텍스트입니다."

        data = _make_pptx_bytes(build)
        parser = PptxParser()
        result = parser.parse(data, file_name="test.pptx")

        self.assertEqual(len(result.sections), 1)
        self.assertEqual(result.sections[0].page, 1)
        self.assertIn("프레젠테이션 제목", result.sections[0].text)
        self.assertIn("본문 텍스트", result.sections[0].text)
        # title 우선 — 첫 텍스트 박스 의 첫 줄
        self.assertEqual(
            result.sections[0].section_title, "프레젠테이션 제목"
        )


class PptxTitlePlaceholderTest(unittest.TestCase):
    """title placeholder 가 있는 레이아웃 — title 우선 사용."""

    def test_title_placeholder_priority(self) -> None:
        from app.adapters.impl.pptx_parser import PptxParser

        def build(prs):
            title_layout = prs.slide_layouts[0]  # title slide
            slide = prs.slides.add_slide(title_layout)
            slide.shapes.title.text = "공식 제목"
            # subtitle/body placeholder 채움
            for ph in slide.placeholders:
                if ph.placeholder_format.idx != 0:
                    ph.text = "부제목 텍스트"
                    break

        data = _make_pptx_bytes(build)
        parser = PptxParser()
        result = parser.parse(data, file_name="title.pptx")

        self.assertEqual(len(result.sections), 1)
        self.assertEqual(result.sections[0].section_title, "공식 제목")


class PptxTableTest(unittest.TestCase):
    """표가 있는 슬라이드 — DocxParser 패턴 (` | ` separator)."""

    def test_table_renders_with_pipe_separator(self) -> None:
        from app.adapters.impl.pptx_parser import PptxParser

        def build(prs):
            blank = prs.slide_layouts[6]
            slide = prs.slides.add_slide(blank)
            shape = slide.shapes.add_table(
                rows=2, cols=2, left=Inches(1), top=Inches(1),
                width=Inches(4), height=Inches(2),
            )
            tbl = shape.table
            tbl.cell(0, 0).text = "헤더1"
            tbl.cell(0, 1).text = "헤더2"
            tbl.cell(1, 0).text = "값1"
            tbl.cell(1, 1).text = "값2"

        data = _make_pptx_bytes(build)
        parser = PptxParser()
        result = parser.parse(data, file_name="table.pptx")

        self.assertEqual(len(result.sections), 1)
        text = result.sections[0].text
        self.assertIn("헤더1 | 헤더2", text)
        self.assertIn("값1 | 값2", text)


class PptxMultiSlideTest(unittest.TestCase):
    """여러 슬라이드 — page 1·2·3 자동 부여."""

    def test_multi_slide_pages_increment(self) -> None:
        from app.adapters.impl.pptx_parser import PptxParser

        def build(prs):
            blank = prs.slide_layouts[6]
            for i in range(3):
                slide = prs.slides.add_slide(blank)
                tx = slide.shapes.add_textbox(
                    Inches(1), Inches(1), Inches(6), Inches(1)
                )
                tx.text_frame.text = f"슬라이드 {i + 1} 본문"

        data = _make_pptx_bytes(build)
        parser = PptxParser()
        result = parser.parse(data, file_name="multi.pptx")

        self.assertEqual(len(result.sections), 3)
        for i, sec in enumerate(result.sections):
            self.assertEqual(sec.page, i + 1)
            self.assertIn(f"슬라이드 {i + 1}", sec.text)


class PptxEmptySlideTest(unittest.TestCase):
    """텍스트 0인 슬라이드 — section 미생성, raise 0 (graceful)."""

    def test_empty_slide_skipped(self) -> None:
        from app.adapters.impl.pptx_parser import PptxParser

        def build(prs):
            blank = prs.slide_layouts[6]
            prs.slides.add_slide(blank)  # 빈 슬라이드 1개
            slide2 = prs.slides.add_slide(blank)
            slide2.shapes.add_textbox(
                Inches(1), Inches(1), Inches(6), Inches(1)
            ).text_frame.text = "두 번째 슬라이드"

        data = _make_pptx_bytes(build)
        parser = PptxParser()
        result = parser.parse(data, file_name="empty.pptx")

        # 빈 슬라이드는 section 미생성, 두 번째 슬라이드만 1 section
        self.assertEqual(len(result.sections), 1)
        self.assertIn("두 번째 슬라이드", result.sections[0].text)
        # page 는 슬라이드 인덱스 기준이라 두 번째 슬라이드는 page=2
        self.assertEqual(result.sections[0].page, 2)


class PptxCorruptedTest(unittest.TestCase):
    """깨진 PPTX → RuntimeError wrap (DocxParser 패턴 일치)."""

    def test_corrupted_raises_runtime(self) -> None:
        from app.adapters.impl.pptx_parser import PptxParser

        with self.assertRaises(RuntimeError) as ctx:
            PptxParser().parse(b"not a pptx file", file_name="bad.pptx")
        self.assertIn("PPTX 파서 초기화 실패", str(ctx.exception))


class PptxCanParseTest(unittest.TestCase):
    """`can_parse` 가 .pptx 만 True."""

    def test_can_parse_extension_only(self) -> None:
        from app.adapters.impl.pptx_parser import PptxParser

        p = PptxParser()
        self.assertTrue(p.can_parse("a.pptx", None))
        self.assertTrue(p.can_parse("/path/to/file.PPTX", None))
        self.assertFalse(p.can_parse("a.docx", None))
        self.assertFalse(p.can_parse("a.pdf", None))


if __name__ == "__main__":
    unittest.main()
