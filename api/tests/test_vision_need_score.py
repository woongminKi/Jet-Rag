"""S1.5 D1 PoC — vision_need_score 휴리스틱 단위 테스트.

검증 범위 (master plan §6 S1.5 D1)
- (a) entity regex hit / miss — 표·그림·식 reference 패턴
- (b) table-like 휴리스틱 — line 당 ≥3 span 비율로 column alignment proxy
- (c) text_density 임계 1e-3 char/pt² — A4 기준 ~500 chars sparse 신호
- needs_vision OR 합산 — 단일 신호도 True 트리거
- signal_kinds() — 어느 신호가 needs_vision 을 끌어올렸는지 분해

본 테스트는 fitz 호출 0 — page_dict mock 만 사용. 외부 의존성·DB 0.
"""

from __future__ import annotations

import os
import unittest

# 모듈 import 단계에서 환경 변수 요구 회피 (다른 테스트 파일과 동일 패턴)
os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")

from app.services.vision_need_score import score_page  # noqa: E402


# A4 면적 (pt²) — PyMuPDF 기본 단위
_A4_AREA = 595.0 * 842.0  # ≈ 501,490 pt²


def _make_page_dict(lines: list[list[str]]) -> dict:
    """간단한 page_dict mock — block 1개에 line 별 span list 를 packing.

    line 의 element 는 span 의 text. 빈 문자열 span 은 자동 무시 (운영 동작 동일).
    """
    spans_lines = []
    for line_spans in lines:
        spans = [{"text": text, "size": 10.0} for text in line_spans]
        spans_lines.append({"spans": spans})
    return {
        "blocks": [
            {
                "type": 0,
                "lines": spans_lines,
            }
        ]
    }


class EntityRegexTest(unittest.TestCase):
    """(a) entity regex — 표·그림·식 reference 패턴."""

    def test_korean_table_bracket_hit(self) -> None:
        page = _make_page_dict([["[표 1] 데이터센터 현황"]])
        score = score_page(page, page_num=1, page_area_pt2=_A4_AREA)
        self.assertGreater(score.entity_hits, 0)

    def test_korean_figure_angle_bracket_hit(self) -> None:
        page = _make_page_dict([["<그림 3> 흐름도"]])
        score = score_page(page, page_num=1, page_area_pt2=_A4_AREA)
        self.assertGreater(score.entity_hits, 0)

    def test_english_figure_table_eq_hit(self) -> None:
        page = _make_page_dict(
            [["Figure 2 shows the result"], ["See Table 5 below"], ["Eq. (3)"]]
        )
        score = score_page(page, page_num=1, page_area_pt2=_A4_AREA)
        # 3개 패턴 모두 hit — 정확한 카운트보단 ≥3 만 검증 (regex 변경 robust)
        self.assertGreaterEqual(score.entity_hits, 3)

    def test_plain_text_no_hit(self) -> None:
        page = _make_page_dict(
            [["일반 본문입니다."], ["여기에는 표나 그림 reference 가 없습니다."]]
        )
        score = score_page(page, page_num=1, page_area_pt2=_A4_AREA)
        self.assertEqual(score.entity_hits, 0)


class TableLikeHeuristicTest(unittest.TestCase):
    """(b) table-like — line 당 ≥3 span 비율 score."""

    def test_multi_column_lines_high_score(self) -> None:
        # 4 line 모두 ≥3 span — table_like_score = 1.0
        page = _make_page_dict(
            [
                ["A", "B", "C"],
                ["1", "2", "3"],
                ["4", "5", "6"],
                ["7", "8", "9"],
            ]
        )
        score = score_page(page, page_num=1, page_area_pt2=_A4_AREA)
        self.assertEqual(score.table_like_score, 1.0)
        self.assertIn("table_like", score.signal_kinds())

    def test_single_span_lines_zero_score(self) -> None:
        page = _make_page_dict(
            [
                ["일반 문단 한 줄"],
                ["또 한 줄"],
                ["세 번째 줄"],
            ]
        )
        score = score_page(page, page_num=1, page_area_pt2=_A4_AREA)
        self.assertEqual(score.table_like_score, 0.0)


class TextDensityTest(unittest.TestCase):
    """(c) text_density — chars / pt² 임계 1e-3."""

    def test_low_density_triggers_needs_vision(self) -> None:
        # 페이지에 5자만 — A4 면적 ≈ 500k pt², 밀도 ≈ 1e-5 ≪ 1e-3
        page = _make_page_dict([["hello"]])
        score = score_page(page, page_num=1, page_area_pt2=_A4_AREA)
        self.assertLess(score.text_density, 1e-3)
        self.assertIn("low_density", score.signal_kinds())
        self.assertTrue(score.needs_vision)

    def test_dense_text_no_low_density_signal(self) -> None:
        # 1000자 line 5개 → 5000 chars / 500k pt² ≈ 1e-2 ≫ 1e-3
        dense_line = ["x" * 1000]
        page = _make_page_dict([dense_line for _ in range(5)])
        score = score_page(page, page_num=1, page_area_pt2=_A4_AREA)
        self.assertGreater(score.text_density, 1e-3)
        self.assertNotIn("low_density", score.signal_kinds())


class NeedsVisionAggregationTest(unittest.TestCase):
    """needs_vision OR 합산 — 단일 신호도 트리거."""

    def test_entity_only_triggers(self) -> None:
        # 충분히 dense + table 0 — entity 신호만 남도록 본문 길이 확보
        line_a = ["Figure 1: " + "x" * 1000]
        page = _make_page_dict([line_a for _ in range(5)])
        score = score_page(page, page_num=1, page_area_pt2=_A4_AREA)
        self.assertTrue(score.needs_vision)
        self.assertIn("entity", score.signal_kinds())
        # density / table_like 는 트리거되지 않아야 함 (entity 단독 검증)
        self.assertNotIn("low_density", score.signal_kinds())
        self.assertNotIn("table_like", score.signal_kinds())

    def test_no_signal_no_vision(self) -> None:
        # dense + non-table + entity 0 → needs_vision False
        normal_line = ["일반 본문입니다 " * 50]
        page = _make_page_dict([normal_line for _ in range(5)])
        score = score_page(page, page_num=1, page_area_pt2=_A4_AREA)
        self.assertFalse(score.needs_vision)
        self.assertEqual(score.signal_kinds(), [])

    def test_zero_area_skips_density_signal(self) -> None:
        # page_area_pt2 = 0 (이상치 보호) → density 신호 미발화. entity 만 평가.
        page = _make_page_dict([["[표 1]"]])
        score = score_page(page, page_num=1, page_area_pt2=0.0)
        self.assertEqual(score.text_density, 0.0)
        self.assertNotIn("low_density", score.signal_kinds())
        self.assertTrue(score.needs_vision)  # entity 단독으로 트리거


if __name__ == "__main__":
    unittest.main()
