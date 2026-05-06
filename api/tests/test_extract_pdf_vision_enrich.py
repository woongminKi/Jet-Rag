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
        # 한 페이지 vision 실패해도 다른 페이지는 진행 + warning 추가.
        # 2026-05-06 D2-C — master plan §7.3: sweep default 3 → 2.
        # page 2 가 sweep 1/2 모두 실패 → 최종 누락.
        from app.ingest.stages import extract as ext_mod

        data = _make_pdf_bytes(2)
        base = ExtractionResult(
            source_type="pdf", sections=[], raw_text="", warnings=[]
        )
        parser = MagicMock()
        # 첫 페이지 성공, 두 번째 페이지 sweep 2회 모두 raise
        parser.parse.side_effect = [
            ExtractionResult(
                source_type="image",
                sections=[ExtractedSection(text="ok", page=None, section_title=None)],
                raw_text="ok",
                warnings=[],
            ),
            RuntimeError("Vision API timeout"),  # sweep 1
            RuntimeError("Vision API timeout"),  # sweep 2 (최종)
        ]
        with patch.object(ext_mod, "_VISION_ENRICH_MAX_SWEEPS", 2):
            result = _enrich_pdf_with_vision(
                data, base_result=base, file_name="test.pdf", image_parser=parser
            )
        # 첫 페이지 section 만 추가됨
        self.assertEqual(len(result.sections), 1)
        # 최종 sweep warning + 전체 누락 warning 둘 다
        self.assertTrue(any("page 2 실패" in w and "최종" in w for w in result.warnings))
        self.assertTrue(any("2 sweep 후에도 누락" in w for w in result.warnings))
        # parser 호출 횟수: 1 (page 1 sweep 1) + 2 (page 2 sweep 1/2) = 3
        self.assertEqual(parser.parse.call_count, 3)

    def test_per_page_failure_graceful_env_override_to_3(self):
        # ENV override (sweep 3) 회복 시나리오 — page 2 가 sweep 1/2/3 모두 실패.
        from app.ingest.stages import extract as ext_mod

        data = _make_pdf_bytes(2)
        base = ExtractionResult(
            source_type="pdf", sections=[], raw_text="", warnings=[]
        )
        parser = MagicMock()
        parser.parse.side_effect = [
            ExtractionResult(
                source_type="image",
                sections=[ExtractedSection(text="ok", page=None, section_title=None)],
                raw_text="ok",
                warnings=[],
            ),
            RuntimeError("Vision API timeout"),  # sweep 1
            RuntimeError("Vision API timeout"),  # sweep 2
            RuntimeError("Vision API timeout"),  # sweep 3 (최종)
        ]
        with patch.object(ext_mod, "_VISION_ENRICH_MAX_SWEEPS", 3):
            result = _enrich_pdf_with_vision(
                data, base_result=base, file_name="test.pdf", image_parser=parser
            )
        # 첫 페이지 section 만 추가됨
        self.assertEqual(len(result.sections), 1)
        # 호출 = 1 (page 1) + 3 (page 2) = 4
        self.assertEqual(parser.parse.call_count, 4)
        # 최종 sweep warning 에 3 명시
        self.assertTrue(any("3/3 최종" in w or "(sweep 3/3" in w for w in result.warnings))
        self.assertTrue(any("3 sweep 후에도 누락" in w for w in result.warnings))

    def test_sweep_recovers_failed_page(self):
        # sweep 1 에서 실패한 페이지가 sweep 2 에서 성공 — 누락 0
        data = _make_pdf_bytes(2)
        base = ExtractionResult(
            source_type="pdf", sections=[], raw_text="", warnings=[]
        )
        parser = MagicMock()
        parser.parse.side_effect = [
            # sweep 1 — page 1 성공, page 2 실패
            ExtractionResult(
                source_type="image",
                sections=[ExtractedSection(text="p1 ok", page=None, section_title=None)],
                raw_text="p1 ok", warnings=[],
            ),
            RuntimeError("503 sweep 1"),
            # sweep 2 — page 2 재시도 성공
            ExtractionResult(
                source_type="image",
                sections=[ExtractedSection(text="p2 recovered", page=None, section_title=None)],
                raw_text="p2 recovered", warnings=[],
            ),
        ]
        result = _enrich_pdf_with_vision(
            data, base_result=base, file_name="test.pdf", image_parser=parser
        )
        # 두 페이지 모두 sections 추가됨
        self.assertEqual(len(result.sections), 2)
        texts = {s.text for s in result.sections}
        self.assertIn("p1 ok", texts)
        self.assertIn("p2 recovered", texts)
        # "최종" warning 없음 (sweep 2 에서 회복)
        self.assertFalse(any("최종" in w for w in result.warnings))
        self.assertFalse(any("sweep 후에도 누락" in w for w in result.warnings))
        # parser 호출 = page 1 (sweep 1) + page 2 (sweep 1) + page 2 (sweep 2) = 3
        self.assertEqual(parser.parse.call_count, 3)

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


class TestVisionEnrichDefaults(unittest.TestCase):
    """2026-05-06 D2-C — master plan §7.3 정합 회귀 보호.

    sweep × retry 곱셈 제거 (sweep 2 × retry 1 = worst case 페이지당 2 호출).
    회귀 발생 시 ENV `JETRAG_PDF_VISION_ENRICH_MAX_SWEEPS=3` 으로 즉시 회복 가능.
    """

    def test_sweep_default_is_2(self):
        # ENV 미설정 시 module-level default = 2 (master plan §7.3).
        # importlib.reload 로 ENV 영향 격리 — 테스트 환경에서 ENV 가 설정돼 있으면 unset.
        import importlib
        import os as _os

        from app.ingest.stages import extract as ext_mod

        prev = _os.environ.pop("JETRAG_PDF_VISION_ENRICH_MAX_SWEEPS", None)
        try:
            importlib.reload(ext_mod)
            self.assertEqual(ext_mod._VISION_ENRICH_MAX_SWEEPS, 2)
        finally:
            if prev is not None:
                _os.environ["JETRAG_PDF_VISION_ENRICH_MAX_SWEEPS"] = prev
            importlib.reload(ext_mod)

    def test_sweep_env_override_to_3(self):
        # ENV 설정 시 회복 시나리오 — sweep = 3.
        import importlib
        import os as _os

        from app.ingest.stages import extract as ext_mod

        prev = _os.environ.get("JETRAG_PDF_VISION_ENRICH_MAX_SWEEPS")
        _os.environ["JETRAG_PDF_VISION_ENRICH_MAX_SWEEPS"] = "3"
        try:
            importlib.reload(ext_mod)
            self.assertEqual(ext_mod._VISION_ENRICH_MAX_SWEEPS, 3)
        finally:
            if prev is None:
                _os.environ.pop("JETRAG_PDF_VISION_ENRICH_MAX_SWEEPS", None)
            else:
                _os.environ["JETRAG_PDF_VISION_ENRICH_MAX_SWEEPS"] = prev
            importlib.reload(ext_mod)


if __name__ == "__main__":
    unittest.main()
