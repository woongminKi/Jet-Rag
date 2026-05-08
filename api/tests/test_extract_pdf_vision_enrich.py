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


class TestVisionNeedScoreHook(unittest.TestCase):
    """S2 D1 (2026-05-08) — vision_need_score 운영 hook 회귀 보호.

    master plan §6 S2 D1. needs_vision False 페이지는 ImageParser.parse() 호출 0 +
    sweep retry 대상 X + ENV `JETRAG_VISION_NEED_SCORE_ENABLED=false` 시 S1.5 이전
    동작 (모든 페이지 호출) 100% 보존.
    """

    def test_needs_vision_false_skips_image_parser(self) -> None:
        # _page_needs_vision 을 monkeypatch — page 1 만 False 반환.
        from app.ingest.stages import extract as ext_mod

        data = _make_pdf_bytes(3)
        base = ExtractionResult(
            source_type="pdf", sections=[], raw_text="", warnings=[]
        )
        # page 1 = False (skip), page 2,3 = True (호출)
        decisions = {1: False, 2: True, 3: True}
        per_page = [
            [ExtractedSection(text=f"vision p.{i + 1}", page=None, section_title=None)]
            for i in range(2)  # page 2,3 만 호출되니 stub 2개
        ]
        parser = _stub_image_parser(per_page)
        with patch.object(
            ext_mod, "_page_needs_vision",
            side_effect=lambda page, *, page_num, file_name: decisions.get(page_num, True),
        ):
            result = _enrich_pdf_with_vision(
                data, base_result=base, file_name="test.pdf", image_parser=parser
            )
        # page 1 skip → ImageParser 2회 호출 (page 2,3)
        self.assertEqual(parser.parse.call_count, 2)
        # sections 도 2개 (page 1 의 vision section 없음)
        self.assertEqual(len(result.sections), 2)
        pages_seen = {s.page for s in result.sections}
        self.assertEqual(pages_seen, {2, 3})
        # warnings 에는 skip 알림 X (정상 동작)
        self.assertFalse(any("need_score" in w.lower() for w in result.warnings))

    def test_needs_vision_false_not_in_sweep_retry(self) -> None:
        # page 1 = False (skip) — sweep 2 진입해도 retry 대상 X.
        # page 2 = True 인데 sweep 1 실패 → sweep 2 성공 (sweep 정상 동작).
        from app.ingest.stages import extract as ext_mod

        data = _make_pdf_bytes(2)
        base = ExtractionResult(
            source_type="pdf", sections=[], raw_text="", warnings=[]
        )
        parser = MagicMock()
        parser.parse.side_effect = [
            RuntimeError("503 sweep 1 page 2"),  # page 2 sweep 1
            ExtractionResult(
                source_type="image",
                sections=[ExtractedSection(text="p2 ok", page=None, section_title=None)],
                raw_text="p2 ok", warnings=[],
            ),  # page 2 sweep 2 회복
        ]
        decisions = {1: False, 2: True}
        with patch.object(
            ext_mod, "_page_needs_vision",
            side_effect=lambda page, *, page_num, file_name: decisions.get(page_num, True),
        ):
            result = _enrich_pdf_with_vision(
                data, base_result=base, file_name="test.pdf", image_parser=parser
            )
        # parser 호출 = page 2 sweep 1 (실패) + page 2 sweep 2 (성공) = 2회.
        # page 1 은 sweep 1 에서 skip → sweep 2 retry 대상도 아님.
        self.assertEqual(parser.parse.call_count, 2)
        # page 2 만 sections 추가
        self.assertEqual(len(result.sections), 1)
        self.assertEqual(result.sections[0].page, 2)
        self.assertEqual(result.sections[0].text, "p2 ok")

    def test_env_disabled_calls_all_pages(self) -> None:
        # ENV `JETRAG_VISION_NEED_SCORE_ENABLED=false` 시 모든 페이지 호출.
        # _page_needs_vision 이 False 반환해도 hook 자체가 비활성 → 호출.
        from app.ingest.stages import extract as ext_mod

        data = _make_pdf_bytes(3)
        base = ExtractionResult(
            source_type="pdf", sections=[], raw_text="", warnings=[]
        )
        per_page = [
            [ExtractedSection(text=f"vision p.{i + 1}", page=None, section_title=None)]
            for i in range(3)
        ]
        parser = _stub_image_parser(per_page)

        # settings.vision_need_score_enabled=False mock
        from app.config import Settings

        # _page_needs_vision 은 항상 False 반환 (회피 시도) — 그러나 ENV 가 우선
        mock_settings = Settings(
            supabase_url="", supabase_key="", supabase_service_role_key="",
            supabase_storage_bucket="documents", gemini_api_key="", hf_api_token="",
            default_user_id="00000000-0000-0000-0000-000000000001",
            doc_budget_usd=0.10, daily_budget_usd=0.50,
            sliding_24h_budget_usd=0.50, budget_krw_per_usd=1380.0,
            vision_need_score_enabled=False,
            vision_page_cap_per_doc=50,  # S2 D2 — default
        )
        with patch.object(
            ext_mod, "_page_needs_vision", return_value=False,
        ), patch.object(ext_mod, "get_settings", return_value=mock_settings):
            result = _enrich_pdf_with_vision(
                data, base_result=base, file_name="test.pdf", image_parser=parser
            )
        # ENV false → 모든 페이지 호출 (need_score False 영향 0)
        self.assertEqual(parser.parse.call_count, 3)
        self.assertEqual(len(result.sections), 3)

    def test_score_compute_failure_falls_back_to_vision_call(self) -> None:
        # vision_need_score 가 raise → needs_vision=True 보수적 fallback.
        # _page_needs_vision 이 fitz.Page.get_text() 단계에서 raise 케이스 시뮬레이트.
        from app.ingest.stages import extract as ext_mod

        data = _make_pdf_bytes(2)
        base = ExtractionResult(
            source_type="pdf", sections=[], raw_text="", warnings=[]
        )
        per_page = [
            [ExtractedSection(text=f"vision p.{i + 1}", page=None, section_title=None)]
            for i in range(2)
        ]
        parser = _stub_image_parser(per_page)
        # 점수 모듈 직접 raise → _page_needs_vision 이 True fallback
        with patch.object(
            ext_mod, "_score_page_for_vision",
            side_effect=RuntimeError("score 계산 실패"),
        ):
            result = _enrich_pdf_with_vision(
                data, base_result=base, file_name="test.pdf", image_parser=parser
            )
        # 점수 깨져도 vision 호출 흐름 보존 — 두 페이지 모두 호출
        self.assertEqual(parser.parse.call_count, 2)
        self.assertEqual(len(result.sections), 2)


def _settings_with_page_cap(page_cap: int, *, need_score: bool = False):
    """S2 D2 단위 테스트용 Settings 인스턴스. need_score=False 가 default."""
    from app.config import Settings
    return Settings(
        supabase_url="", supabase_key="", supabase_service_role_key="",
        supabase_storage_bucket="documents", gemini_api_key="", hf_api_token="",
        default_user_id="00000000-0000-0000-0000-000000000001",
        doc_budget_usd=0.10, daily_budget_usd=0.50,
        sliding_24h_budget_usd=0.50, budget_krw_per_usd=1380.0,
        vision_need_score_enabled=need_score,
        vision_page_cap_per_doc=page_cap,
    )


class TestVisionPageCapHook(unittest.TestCase):
    """S2 D2 (2026-05-08) — page cap + cost budget 병행 회귀 보호.

    master plan §6 S2 D2. cost cap (S0 D4) 과 직교 — 둘 중 먼저 닿는 지점 stop.
    needs_vision skip 페이지는 카운터 증가 X (사용자 가치 페이지만 차감, cap
    도달 지연 정합). ENV 0 시 S2 D1 동작 100% 보존 (회복 토글).
    """

    def test_page_cap_break_mid_sweep(self) -> None:
        """called_count >= cap 시 sweep 즉시 break + warning + flag 마킹 가능 status."""
        from app.ingest.stages import extract as ext_mod

        data = _make_pdf_bytes(5)
        base = ExtractionResult(
            source_type="pdf", sections=[], raw_text="", warnings=[]
        )
        # 5 페이지 PDF, page cap=2 — page 1, 2 호출 후 page 3 진입 시 cap 도달 break.
        per_page = [
            [ExtractedSection(text=f"vision p.{i + 1}", page=None, section_title=None)]
            for i in range(5)
        ]
        parser = _stub_image_parser(per_page)
        # need_score 비활성 → 모든 페이지 호출 시도. cap=2 가 작동 검증.
        mock_settings = _settings_with_page_cap(2, need_score=False)
        with patch.object(ext_mod, "get_settings", return_value=mock_settings):
            result = _enrich_pdf_with_vision(
                data, base_result=base, file_name="test.pdf", image_parser=parser
            )
        # cap=2 — page 1, 2 호출 후 page 3 진입 시 break.
        self.assertEqual(parser.parse.call_count, 2)
        self.assertEqual(len(result.sections), 2)
        # warnings 에 page cap 도달 메시지 + skip 안내
        self.assertTrue(any("page cap 도달" in w for w in result.warnings))
        self.assertTrue(any("2/2" in w for w in result.warnings))

    def test_page_cap_with_needs_vision_skip_does_not_increment_counter(self) -> None:
        """needs_vision False 페이지는 called_count 증가 X — cap 도달 지연 정합.

        cap=2 인데 5 페이지 PDF 에서 첫 3 페이지가 needs_vision False (skip) 라면
        나머지 2 페이지만 호출되고 cap 정확히 도달 (skip 페이지가 cap 차감 X).
        """
        from app.ingest.stages import extract as ext_mod

        data = _make_pdf_bytes(5)
        base = ExtractionResult(
            source_type="pdf", sections=[], raw_text="", warnings=[]
        )
        # page 1,2,3 = needs_vision False (skip), page 4,5 = True (호출)
        decisions = {1: False, 2: False, 3: False, 4: True, 5: True}
        per_page = [
            [ExtractedSection(text=f"p{n}", page=None, section_title=None)]
            for n in (4, 5)  # 호출되는 page 만 stub
        ]
        parser = _stub_image_parser(per_page)
        mock_settings = _settings_with_page_cap(2, need_score=True)
        with patch.object(ext_mod, "get_settings", return_value=mock_settings), \
             patch.object(
                 ext_mod, "_page_needs_vision",
                 side_effect=lambda page, *, page_num, file_name: decisions.get(page_num, True),
             ):
            result = _enrich_pdf_with_vision(
                data, base_result=base, file_name="test.pdf", image_parser=parser
            )
        # page 4, 5 만 호출 — skip 3 페이지가 cap 차감 X.
        # cap=2 라 page 4 + 5 호출 후 (called=2) sweep end (page 6 없음).
        # page cap 도달 warning X (마지막 page 까지 정상 처리).
        self.assertEqual(parser.parse.call_count, 2)
        self.assertEqual(len(result.sections), 2)
        # cap 도달 warning 없어야 함 (정상 종료)
        self.assertFalse(any("page cap 도달" in w for w in result.warnings))

    def test_page_cap_zero_disables_unlimited(self) -> None:
        """ENV `JETRAG_VISION_PAGE_CAP_PER_DOC=0` 시 모든 페이지 호출 (회복 토글).

        S2 D1 이전 동작 100% 보존 — page cap hook 자체 영향 0.
        """
        from app.ingest.stages import extract as ext_mod

        data = _make_pdf_bytes(5)
        base = ExtractionResult(
            source_type="pdf", sections=[], raw_text="", warnings=[]
        )
        per_page = [
            [ExtractedSection(text=f"vision p.{i + 1}", page=None, section_title=None)]
            for i in range(5)
        ]
        parser = _stub_image_parser(per_page)
        # cap=0 → 무한 모드. need_score 도 비활성 → 모든 페이지 호출 검증.
        mock_settings = _settings_with_page_cap(0, need_score=False)
        with patch.object(ext_mod, "get_settings", return_value=mock_settings):
            result = _enrich_pdf_with_vision(
                data, base_result=base, file_name="test.pdf", image_parser=parser
            )
        # 모든 페이지 호출 — cap 영향 0
        self.assertEqual(parser.parse.call_count, 5)
        self.assertEqual(len(result.sections), 5)
        # cap 도달 warning 없음
        self.assertFalse(any("page cap 도달" in w for w in result.warnings))

    def test_page_cap_default_is_50(self) -> None:
        """ENV 미설정 시 default 50 — config 회귀 보호."""
        import importlib
        import os as _os

        from app.config import get_settings as get_settings_real

        prev = _os.environ.pop("JETRAG_VISION_PAGE_CAP_PER_DOC", None)
        try:
            # lru_cache clear — config 모듈 reload 대신 단일 함수 캐시 클리어.
            get_settings_real.cache_clear()
            settings = get_settings_real()
            self.assertEqual(settings.vision_page_cap_per_doc, 50)
        finally:
            if prev is not None:
                _os.environ["JETRAG_VISION_PAGE_CAP_PER_DOC"] = prev
            get_settings_real.cache_clear()


class TestPageCapOverride(unittest.TestCase):
    """S2 D3 (2026-05-09) — page_cap_override 회귀 보호. master plan §6 S2 D3.

    `_enrich_pdf_with_vision` 의 page_cap_override 인자가 mode 별 cap 으로
    settings.vision_page_cap_per_doc 을 override 하는지 + kill switch (settings 0)
    가 override 보다 강한지 검증. router 의 mode → page_cap 매핑은 별도 단위 테스트
    (test_ingest_mode.py) 에서 검증 — 본 클래스는 stage 측 적용 책임만.
    """

    def test_override_reduces_cap_below_settings(self) -> None:
        """T-B-04 — override=2 (fast 모드 효과) 가 settings=50 보다 강함."""
        from app.ingest.stages import extract as ext_mod

        data = _make_pdf_bytes(5)
        base = ExtractionResult(
            source_type="pdf", sections=[], raw_text="", warnings=[]
        )
        per_page = [
            [ExtractedSection(text=f"vision p.{i + 1}", page=None, section_title=None)]
            for i in range(5)
        ]
        parser = _stub_image_parser(per_page)
        # settings 의 cap 은 50 (큼) — override=2 가 우선 적용되어야 함.
        mock_settings = _settings_with_page_cap(50, need_score=False)
        with patch.object(ext_mod, "get_settings", return_value=mock_settings):
            result = _enrich_pdf_with_vision(
                data, base_result=base, file_name="test.pdf",
                image_parser=parser, page_cap_override=2,
            )
        # cap=2 (override) — page 1, 2 호출 후 break.
        self.assertEqual(parser.parse.call_count, 2)
        self.assertEqual(len(result.sections), 2)
        self.assertTrue(any("page cap 도달" in w for w in result.warnings))
        self.assertTrue(any("2/2" in w for w in result.warnings))

    def test_override_zero_unlimited_overrides_low_settings(self) -> None:
        """T-B-05 — override=0 (precise 모드) → 무한, settings cap 무시."""
        from app.ingest.stages import extract as ext_mod

        data = _make_pdf_bytes(5)
        base = ExtractionResult(
            source_type="pdf", sections=[], raw_text="", warnings=[]
        )
        per_page = [
            [ExtractedSection(text=f"vision p.{i + 1}", page=None, section_title=None)]
            for i in range(5)
        ]
        parser = _stub_image_parser(per_page)
        # settings cap=2 — 평소라면 2에서 break. precise (override=0) 가 모두 풀어줌.
        mock_settings = _settings_with_page_cap(2, need_score=False)
        with patch.object(ext_mod, "get_settings", return_value=mock_settings):
            result = _enrich_pdf_with_vision(
                data, base_result=base, file_name="test.pdf",
                image_parser=parser, page_cap_override=0,
            )
        # override=0 → 무한. 5 페이지 모두 호출.
        self.assertEqual(parser.parse.call_count, 5)
        self.assertEqual(len(result.sections), 5)
        self.assertFalse(any("page cap 도달" in w for w in result.warnings))

    def test_kill_switch_settings_zero_overrides_override(self) -> None:
        """T-B-06 — settings.vision_page_cap_per_doc=0 (전역 kill switch) → 무한.

        Q-S2-1e A 안: ENV 가 0 이면 mode/override 무관 항상 무한 (회복 토글).
        override=2 처럼 작은 값이 들어와도 settings=0 이 우선.
        """
        from app.ingest.stages import extract as ext_mod

        data = _make_pdf_bytes(5)
        base = ExtractionResult(
            source_type="pdf", sections=[], raw_text="", warnings=[]
        )
        per_page = [
            [ExtractedSection(text=f"vision p.{i + 1}", page=None, section_title=None)]
            for i in range(5)
        ]
        parser = _stub_image_parser(per_page)
        # settings=0 (kill switch) — override=2 무시되고 모든 페이지 호출.
        mock_settings = _settings_with_page_cap(0, need_score=False)
        with patch.object(ext_mod, "get_settings", return_value=mock_settings):
            result = _enrich_pdf_with_vision(
                data, base_result=base, file_name="test.pdf",
                image_parser=parser, page_cap_override=2,
            )
        self.assertEqual(parser.parse.call_count, 5)
        self.assertEqual(len(result.sections), 5)
        self.assertFalse(any("page cap 도달" in w for w in result.warnings))

    def test_override_none_preserves_s2d2_behavior(self) -> None:
        """T-B-09 — override=None → settings.vision_page_cap_per_doc 그대로 (S2 D2 호환).

        S2 D3 변경이 S2 D2 단위 테스트 (TestVisionPageCapHook) 의 기존 동작을 깨뜨리지
        않는지 명시적 회귀 보호.
        """
        from app.ingest.stages import extract as ext_mod

        data = _make_pdf_bytes(5)
        base = ExtractionResult(
            source_type="pdf", sections=[], raw_text="", warnings=[]
        )
        per_page = [
            [ExtractedSection(text=f"vision p.{i + 1}", page=None, section_title=None)]
            for i in range(5)
        ]
        parser = _stub_image_parser(per_page)
        # settings=2 — override 미지정 (S2 D2 path).
        mock_settings = _settings_with_page_cap(2, need_score=False)
        with patch.object(ext_mod, "get_settings", return_value=mock_settings):
            result = _enrich_pdf_with_vision(
                data, base_result=base, file_name="test.pdf",
                image_parser=parser,  # page_cap_override 미전달
            )
        # settings=2 가 그대로 cap — page 1,2 호출 후 break.
        self.assertEqual(parser.parse.call_count, 2)
        self.assertEqual(len(result.sections), 2)


if __name__ == "__main__":
    unittest.main()
