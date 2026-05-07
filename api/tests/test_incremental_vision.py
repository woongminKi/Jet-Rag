"""S2 D1 (2026-05-08) — incremental vision sweep 의 needs_vision hook 회귀 보호.

`_vision_pages_with_sweep` 의 vision_need_score 통합 — needs_vision False 페이지는
ImageParser 호출 회피 + sweep retry 대상 X + ENV `JETRAG_VISION_NEED_SCORE_ENABLED=false`
시 모든 페이지 호출 (S1.5 이전 동작 100% 보존).

DB 의존성 0 — `_vision_pages_with_sweep` 는 fitz + ImageParser 만 사용. mock 으로 격리.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

# 모듈 import 단계의 ENV 요구 회피 (다른 테스트 파일과 동일 패턴)
os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")

import fitz  # noqa: E402

from app.adapters.parser import ExtractedSection, ExtractionResult  # noqa: E402
from app.config import Settings  # noqa: E402
from app.ingest import incremental as inc_mod  # noqa: E402


def _make_pdf_bytes(num_pages: int = 3) -> bytes:
    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {i + 1} body. 본문.")
    out = doc.tobytes()
    doc.close()
    return out


def _stub_parser(per_page_sections: list[list[ExtractedSection]]) -> MagicMock:
    parser = MagicMock()
    parser.parse.side_effect = [
        ExtractionResult(
            source_type="image",
            sections=secs,
            raw_text=" ".join(s.text for s in secs),
            warnings=[],
        )
        for secs in per_page_sections
    ]
    return parser


class TestIncrementalVisionNeedScoreHook(unittest.TestCase):
    """`_vision_pages_with_sweep` 의 needs_vision hook 회귀 차단."""

    def test_needs_vision_false_skips_image_parser(self) -> None:
        # missing pages = [1, 2, 3]. page 1 = False (skip), page 2,3 = True (호출).
        data = _make_pdf_bytes(3)
        decisions = {1: False, 2: True, 3: True}
        per_page = [
            [ExtractedSection(text=f"vision p.{i + 2}", page=None, section_title=None)]
            for i in range(2)  # page 2,3 만 호출
        ]
        parser = _stub_parser(per_page)
        with patch.object(
            inc_mod, "_page_needs_vision",
            side_effect=lambda page, *, page_num, file_name: decisions.get(page_num, True),
        ):
            sections, warnings = inc_mod._vision_pages_with_sweep(
                data,
                pages=[1, 2, 3],
                file_name="test.pdf",
                image_parser=parser,
            )
        # ImageParser 호출 = page 2 + page 3 = 2회
        self.assertEqual(parser.parse.call_count, 2)
        # sections 도 page 2,3 만
        self.assertEqual(len(sections), 2)
        pages_seen = {s.page for s in sections}
        self.assertEqual(pages_seen, {2, 3})
        # warnings 에 누락 알림 X (skip 은 정상 동작)
        self.assertFalse(any("sweep 후에도 누락" in w for w in warnings))

    def test_needs_vision_false_not_in_sweep_retry(self) -> None:
        # page 1 = False (skip), page 2 = True (sweep 1 실패 → sweep 2 회복).
        data = _make_pdf_bytes(2)
        parser = MagicMock()
        parser.parse.side_effect = [
            RuntimeError("503 sweep 1 page 2"),  # sweep 1 page 2
            ExtractionResult(
                source_type="image",
                sections=[ExtractedSection(text="p2 ok", page=None, section_title=None)],
                raw_text="p2 ok", warnings=[],
            ),  # sweep 2 page 2
        ]
        decisions = {1: False, 2: True}
        with patch.object(
            inc_mod, "_page_needs_vision",
            side_effect=lambda page, *, page_num, file_name: decisions.get(page_num, True),
        ):
            sections, warnings = inc_mod._vision_pages_with_sweep(
                data,
                pages=[1, 2],
                file_name="test.pdf",
                image_parser=parser,
            )
        # parser 호출 = page 2 sweep 1 (실패) + page 2 sweep 2 (회복) = 2회
        # page 1 은 sweep 1 에서 needs_vision False → sweep 2 retry 대상도 아님
        self.assertEqual(parser.parse.call_count, 2)
        # page 2 만 sections 추가
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0].page, 2)
        # 누락 warning 없음 (sweep 2 회복)
        self.assertFalse(any("sweep 후에도 누락" in w for w in warnings))

    def test_env_disabled_calls_all_pages(self) -> None:
        # ENV `JETRAG_VISION_NEED_SCORE_ENABLED=false` 시 모든 페이지 호출.
        data = _make_pdf_bytes(2)
        per_page = [
            [ExtractedSection(text=f"p{i + 1}", page=None, section_title=None)]
            for i in range(2)
        ]
        parser = _stub_parser(per_page)
        mock_settings = Settings(
            supabase_url="", supabase_key="", supabase_service_role_key="",
            supabase_storage_bucket="documents", gemini_api_key="", hf_api_token="",
            default_user_id="00000000-0000-0000-0000-000000000001",
            doc_budget_usd=0.10, daily_budget_usd=0.50,
            sliding_24h_budget_usd=0.50, budget_krw_per_usd=1380.0,
            vision_need_score_enabled=False,
        )
        with patch.object(
            inc_mod, "_page_needs_vision", return_value=False,
        ), patch.object(inc_mod, "get_settings", return_value=mock_settings):
            sections, warnings = inc_mod._vision_pages_with_sweep(
                data,
                pages=[1, 2],
                file_name="test.pdf",
                image_parser=parser,
            )
        # ENV false → needs_vision 영향 0, 모든 페이지 호출
        self.assertEqual(parser.parse.call_count, 2)
        self.assertEqual(len(sections), 2)


if __name__ == "__main__":
    unittest.main()
