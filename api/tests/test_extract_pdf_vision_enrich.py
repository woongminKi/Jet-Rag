"""W25 D14 — `_enrich_pdf_with_vision` 회귀 차단.

단위 테스트는 Gemini API 호출 없이 ImageParser 를 mock — vision_enrich 의 sections 병합 + warnings 처리 + 부분 실패 graceful 검증.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import fitz

from app.adapters.parser import ExtractedSection, ExtractionResult
from app.ingest.stages.extract import _enrich_pdf_with_vision


def _make_pdf_bytes(num_pages: int = 3) -> bytes:
    """간단한 텍스트 PDF (vision enrich 의 page iteration 검증용)."""
    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {i + 1} body text. 본문 텍스트.")
    out = doc.tobytes()
    doc.close()
    return out


def _stub_image_parser(per_page_sections: list[list[ExtractedSection]]) -> MagicMock:
    """페이지 호출당 정해진 sections 반환하는 ImageParser stub."""
    parser = MagicMock()
    call_results = [
        ExtractionResult(
            source_type="image",
            sections=secs,
            raw_text=" ".join(s.text for s in secs),
            warnings=[],
        )
        for secs in per_page_sections
    ]
    parser.parse.side_effect = call_results
    return parser


class TestEnrichPdfWithVision(unittest.TestCase):
    def test_appends_vision_sections_with_page_meta(self):
        # 3 페이지 PDF + vision 이 페이지마다 1 section 반환
        data = _make_pdf_bytes(3)
        base = ExtractionResult(
            source_type="pdf",
            sections=[
                ExtractedSection(text="기존 PyMuPDF 본문", page=1, section_title="원본"),
            ],
            raw_text="기존 PyMuPDF raw",
            warnings=[],
        )
        per_page = [
            [ExtractedSection(text=f"vision p.{i + 1} 캡션", page=None, section_title=None)]
            for i in range(3)
        ]
        parser = _stub_image_parser(per_page)
        result = _enrich_pdf_with_vision(
            data, base_result=base, file_name="test.pdf", image_parser=parser
        )

        # PyMuPDF sections 보존 + vision sections 추가 (3 페이지)
        self.assertEqual(len(result.sections), 1 + 3)
        self.assertEqual(result.sections[0].section_title, "원본")
        # 추가 sections 의 page + section_title 확인
        for i in range(3):
            sec = result.sections[1 + i]
            self.assertEqual(sec.page, i + 1)
            self.assertTrue(sec.section_title.startswith(f"(vision) p.{i + 1}"))
            self.assertEqual(sec.text, f"vision p.{i + 1} 캡션")
        # raw_text 결합
        self.assertIn("기존 PyMuPDF raw", result.raw_text)
        self.assertIn("vision p.1 캡션", result.raw_text)
        # parser 호출 횟수 = 페이지 수
        self.assertEqual(parser.parse.call_count, 3)

    def test_per_page_failure_graceful(self):
        # 한 페이지 vision 실패해도 다른 페이지는 진행 + warning 추가
        data = _make_pdf_bytes(2)
        base = ExtractionResult(
            source_type="pdf", sections=[], raw_text="", warnings=[]
        )
        parser = MagicMock()
        # 첫 페이지 성공, 두 번째 페이지 raise
        parser.parse.side_effect = [
            ExtractionResult(
                source_type="image",
                sections=[ExtractedSection(text="ok", page=None, section_title=None)],
                raw_text="ok",
                warnings=[],
            ),
            RuntimeError("Vision API timeout"),
        ]
        result = _enrich_pdf_with_vision(
            data, base_result=base, file_name="test.pdf", image_parser=parser
        )
        # 첫 페이지 section 만 추가됨
        self.assertEqual(len(result.sections), 1)
        # warning 에 두 번째 페이지 실패 명시
        self.assertTrue(any("page 2 실패" in w for w in result.warnings))

    def test_max_pages_cap(self):
        # cap 보다 많은 페이지 PDF — 첫 cap 페이지만 처리 + warning
        from app.ingest.stages import extract as ext_mod

        data = _make_pdf_bytes(8)
        base = ExtractionResult(
            source_type="pdf", sections=[], raw_text="", warnings=[]
        )
        parser = _stub_image_parser(
            [[ExtractedSection(text=f"p{i + 1}", page=None, section_title=None)] for i in range(8)]
        )

        with patch.object(ext_mod, "_VISION_ENRICH_MAX_PAGES", 3):
            result = _enrich_pdf_with_vision(
                data, base_result=base, file_name="test.pdf", image_parser=parser
            )

        # cap 3 만 처리
        self.assertEqual(parser.parse.call_count, 3)
        self.assertEqual(len(result.sections), 3)
        # warning 에 cap 명시
        self.assertTrue(any("8페이지 중 첫 3페이지" in w for w in result.warnings))

    def test_pdf_open_failure_returns_base_result(self):
        # 잘못된 PDF bytes — open 실패 시 base_result 그대로 + warning
        base = ExtractionResult(
            source_type="pdf",
            sections=[ExtractedSection(text="원본", page=1, section_title="orig")],
            raw_text="원본 raw",
            warnings=[],
        )
        parser = MagicMock()
        result = _enrich_pdf_with_vision(
            b"not a pdf",
            base_result=base,
            file_name="bad.pdf",
            image_parser=parser,
        )
        # 원본 sections 보존
        self.assertEqual(len(result.sections), 1)
        self.assertEqual(result.sections[0].section_title, "orig")
        # warning 에 enrich 실패 명시
        self.assertTrue(any("vision_enrich: PDF 열기 실패" in w for w in result.warnings))
        # parser 미호출
        self.assertEqual(parser.parse.call_count, 0)


if __name__ == "__main__":
    unittest.main()
