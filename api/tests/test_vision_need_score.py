"""S1.5 D3 — vision_need_score 휴리스틱 v2 단위 테스트.

검증 범위 (master plan §6 S1.5 D3 + work-log D2 결정 7건)
- D1 호환 부 — entity regex / table-like / text_density 임계 / signal_kinds
- D3 OR rule — entity 제외 / table 0.3 / image_area / text_quality / caption
- D3 신호 — image_area_ratio / text_quality / caption_score
- D3 table v2 fallback — single-span line 안 다중 공백·탭 분리
- compute_score — entity 가중치 0 (deprecated) 검증

본 테스트는 fitz 호출 0 — page_dict mock 만 사용. 외부 의존성·DB 0.
"""

from __future__ import annotations

import os
import unittest

# 모듈 import 단계에서 환경 변수 요구 회피 (다른 테스트 파일과 동일 패턴)
os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")

from app.services.vision_need_score import (  # noqa: E402
    DEFAULT_WEIGHTS,
    compute_score,
    needs_vision,
    needs_vision_breakdown,
    score_page,
)


# A4 면적 (pt²) — PyMuPDF 기본 단위
_A4_AREA = 595.0 * 842.0  # ≈ 501,490 pt²


def _make_page_dict(
    lines: list[list[str]],
    *,
    image_blocks: list[tuple[float, float, float, float]] | None = None,
) -> dict:
    """간단한 page_dict mock — block 1개에 line 별 span list 를 packing.

    image_blocks: bbox tuple list. 면적 계산용 image block 추가 (PyMuPDF type=1).
    """
    spans_lines = []
    for line_spans in lines:
        spans = [{"text": text, "size": 10.0} for text in line_spans]
        spans_lines.append({"spans": spans})
    blocks: list[dict] = [
        {"type": 0, "lines": spans_lines},
    ]
    for bbox in image_blocks or []:
        blocks.append({"type": 1, "bbox": list(bbox)})
    return {"blocks": blocks}


class EntityRegexTest(unittest.TestCase):
    """(a) entity regex — 표·그림·식 reference 패턴 (D2 deprecated 권고에도
    regex 자체는 보존 — 분석·디버깅용)."""

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
    """(b) table-like — line 당 ≥3 span 비율 score (D3 임계 0.3)."""

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
        # D3 OR rule trigger 검증 — table_like 임계 0.3 충분히 초과
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


class TableV2FallbackTest(unittest.TestCase):
    """D3 — table v2 fallback — single-span 안 다중 공백/탭 분리.

    한국어 PDF 의 cell 이 한 span 으로 합쳐지는 PyMuPDF 한계 보강 (work-log D2 §3.4
    데이터센터 p.40 false negative 회복).
    """

    def test_single_span_multispace_split(self) -> None:
        # span 1개 안에 다중 공백으로 5 column — fallback 으로 column 후보
        page = _make_page_dict(
            [
                ["순번    구분    면적(㎡)    예산    비고"],
                ["1       A동      300       100      x"],
                ["2       B동      450       200      y"],
                ["3       C동      520       250      z"],
            ]
        )
        score = score_page(page, page_num=1, page_area_pt2=_A4_AREA)
        # 4 line 모두 fallback hit → table_score ≈ 1.0
        self.assertGreater(score.table_like_score, 0.5)

    def test_single_span_tab_split(self) -> None:
        # span 1개 안에 tab 구분 → fallback hit
        page = _make_page_dict(
            [
                ["순번\t구분\t면적\t예산"],
                ["1\tA\t100\t50"],
                ["2\tB\t200\t75"],
            ]
        )
        score = score_page(page, page_num=1, page_area_pt2=_A4_AREA)
        self.assertGreater(score.table_like_score, 0.5)

    def test_single_span_normal_text_no_split(self) -> None:
        # 일반 본문 (단일 공백만) 은 fallback 가도 column 추정 0 → table_score 0
        page = _make_page_dict(
            [
                ["이것은 일반 한국어 본문입니다 길게 풀어 적은 단락"],
                ["여전히 일반 문단 입니다 다중 공백 없음"],
            ]
        )
        score = score_page(page, page_num=1, page_area_pt2=_A4_AREA)
        self.assertEqual(score.table_like_score, 0.0)


class TextDensityTest(unittest.TestCase):
    """(c) text_density — chars / pt² 임계 1e-3 유지 (D2 결정 #2)."""

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


class NeedsVisionOrRuleTest(unittest.TestCase):
    """D3 OR rule — entity 제외, 5 신호 중 단일 hit 도 트리거."""

    def test_low_density_alone_triggers(self) -> None:
        self.assertTrue(
            needs_vision(
                text_density=0.5e-3,
                table_like_score=0.0,
                page_area_pt2=_A4_AREA,
            )
        )

    def test_image_area_high_triggers(self) -> None:
        # image_area 0.5 ≥ 0.30 — 단독으로 OR rule trigger
        self.assertTrue(
            needs_vision(
                text_density=2e-3,  # 안전 (1e-3 초과)
                table_like_score=0.0,
                image_area_ratio=0.5,
                page_area_pt2=_A4_AREA,
            )
        )

    def test_table_high_triggers_at_03(self) -> None:
        # D2 결정 #3 — 임계 0.5 → 0.3. 0.4 도 trigger.
        self.assertTrue(
            needs_vision(
                text_density=2e-3,
                table_like_score=0.4,
                page_area_pt2=_A4_AREA,
            )
        )

    def test_table_below_03_no_trigger(self) -> None:
        self.assertFalse(
            needs_vision(
                text_density=2e-3,
                table_like_score=0.25,
                page_area_pt2=_A4_AREA,
            )
        )

    def test_text_quality_low_triggers(self) -> None:
        # text_quality 0.3 ≤ 0.40 — 단독 trigger
        self.assertTrue(
            needs_vision(
                text_density=2e-3,
                table_like_score=0.0,
                text_quality=0.3,
                page_area_pt2=_A4_AREA,
            )
        )

    def test_caption_score_triggers(self) -> None:
        # caption 0.3 ≥ 0.20 — 단독 trigger
        self.assertTrue(
            needs_vision(
                text_density=2e-3,
                table_like_score=0.0,
                caption_score=0.3,
                page_area_pt2=_A4_AREA,
            )
        )

    def test_all_signals_safe_no_trigger(self) -> None:
        self.assertFalse(
            needs_vision(
                text_density=2e-3,
                table_like_score=0.1,
                image_area_ratio=0.05,
                text_quality=0.95,
                caption_score=0.05,
                page_area_pt2=_A4_AREA,
            )
        )

    def test_breakdown_returns_all_keys(self) -> None:
        bd = needs_vision_breakdown(
            text_density=2e-3,
            table_like_score=0.0,
            page_area_pt2=_A4_AREA,
        )
        self.assertEqual(
            set(bd.keys()),
            {"low_density", "table_like", "image_area", "text_quality_low", "caption"},
        )


class EntityDeprecatedTest(unittest.TestCase):
    """D2 결정 #5 — entity 신호 deprecated 검증 (OR rule 제외 + composite 가중치 0).

    score_page 단위에선 entity_hits 가 OR rule trigger 에 영향 없음을 확인.
    text_quality / caption / image / density / table 모두 안전이면 entity_hits 만으로
    needs_vision = False 여야 함.
    """

    def test_entity_only_does_not_trigger_via_score_page(self) -> None:
        # 충분히 dense + table 0 + image 0 + quality 1.0 + caption 0
        # entity 만 hit (Figure 1) — D3 OR rule 에선 trigger X
        line_a = ["Figure 1: " + "x" * 1000]
        page = _make_page_dict([line_a for _ in range(5)])
        score = score_page(page, page_num=1, page_area_pt2=_A4_AREA)
        # entity 는 측정됨 (regex 보존)
        self.assertGreater(score.entity_hits, 0)
        # 그러나 OR rule trigger 에 entity 항목 없음
        self.assertNotIn("entity", score.signal_kinds())
        # 다른 신호도 안전이라면 needs_vision = False
        self.assertFalse(score.needs_vision)

    def test_compute_score_entity_zero_weight(self) -> None:
        # entity_hits 가 1 이상이라도 다른 신호 0 이면 composite = 0
        score = compute_score(
            text_density=2e-3,
            table_like_score=0.0,
            image_area_ratio=0.0,
            text_quality=1.0,
            caption_score=0.0,
            entity_hits=10,
            page_area_pt2=_A4_AREA,
        )
        self.assertEqual(score, 0.0)

    def test_default_weights_entity_density_zero(self) -> None:
        self.assertEqual(DEFAULT_WEIGHTS["entity_density"], 0.0)


class NeedsVisionAggregationTest(unittest.TestCase):
    """OR rule 합산 — 모든 신호 안전 시 false."""

    def test_no_signal_no_vision(self) -> None:
        # dense + non-table + entity 0 → needs_vision False
        normal_line = ["일반 본문입니다 " * 50]
        page = _make_page_dict([normal_line for _ in range(5)])
        score = score_page(page, page_num=1, page_area_pt2=_A4_AREA)
        self.assertFalse(score.needs_vision)
        self.assertEqual(score.signal_kinds(), [])

    def test_zero_area_skips_density_signal(self) -> None:
        # page_area_pt2 = 0 (이상치 보호) → density 신호 미발화. caption 키워드도
        # 없는 일반 본문 → 다른 신호도 모두 안전 → needs_vision = False (D3 OR rule).
        page = _make_page_dict([["일반 한국어 본문 단락입니다."]])
        score = score_page(page, page_num=1, page_area_pt2=0.0)
        self.assertEqual(score.text_density, 0.0)
        self.assertNotIn("low_density", score.signal_kinds())
        self.assertFalse(score.needs_vision)

    def test_caption_keyword_with_zero_area_triggers_caption(self) -> None:
        # page_area_pt2 = 0 라도 caption 신호는 area 와 무관 → trigger 가능
        page = _make_page_dict([["[표 1] 데이터센터 현황"]])
        score = score_page(page, page_num=1, page_area_pt2=0.0)
        self.assertNotIn("low_density", score.signal_kinds())
        self.assertIn("caption", score.signal_kinds())
        self.assertTrue(score.needs_vision)


class ImageAreaSignalTest(unittest.TestCase):
    """D3 신호 (d) — image_area_ratio."""

    def test_image_block_area_above_threshold(self) -> None:
        # image bbox 가 페이지 면적의 60% — 0.30 임계 초과
        # A4: 595 * 842. 60% 대응 bbox 면적 ≈ 300,894
        # bbox = (0, 0, 595, 506) → 595 * 506 = 301,070
        line = ["짧은 본문"]
        page = _make_page_dict([line], image_blocks=[(0, 0, 595, 506)])
        score = score_page(page, page_num=1, page_area_pt2=_A4_AREA)
        self.assertGreaterEqual(score.image_area_ratio, 0.30)
        self.assertIn("image_area", score.signal_kinds())

    def test_image_block_area_below_threshold(self) -> None:
        # image bbox 가 페이지 면적의 10% — 0.30 임계 미만
        line = ["dense " * 500]
        page = _make_page_dict(
            [line for _ in range(5)],
            image_blocks=[(0, 0, 200, 250)],  # 50,000 pt² ≈ 10%
        )
        score = score_page(page, page_num=1, page_area_pt2=_A4_AREA)
        self.assertLess(score.image_area_ratio, 0.30)
        self.assertNotIn("image_area", score.signal_kinds())

    def test_no_image_block_zero_ratio(self) -> None:
        page = _make_page_dict([["normal text"]])
        score = score_page(page, page_num=1, page_area_pt2=_A4_AREA)
        self.assertEqual(score.image_area_ratio, 0.0)


class TextQualitySignalTest(unittest.TestCase):
    """D3 신호 (e) — text_quality (1=정상, 0=깨짐)."""

    def test_normal_korean_high_quality(self) -> None:
        page = _make_page_dict([["정상적인 한국어 본문입니다."]])
        score = score_page(page, page_num=1, page_area_pt2=_A4_AREA)
        self.assertGreaterEqual(score.text_quality, 0.95)

    def test_normal_english_high_quality(self) -> None:
        page = _make_page_dict([["Normal English text 123."]])
        score = score_page(page, page_num=1, page_area_pt2=_A4_AREA)
        self.assertGreaterEqual(score.text_quality, 0.95)

    def test_pua_chars_low_quality(self) -> None:
        # PUA (Private Use Area) U+E000~U+F8FF — printable 아님으로 간주
        broken = "\ue000\ue001\ue002\ue003\ue004\ue005\ue006\ue007"
        page = _make_page_dict([[broken]])
        score = score_page(page, page_num=1, page_area_pt2=_A4_AREA)
        self.assertLessEqual(score.text_quality, 0.40)
        self.assertIn("text_quality_low", score.signal_kinds())


class CaptionSignalTest(unittest.TestCase):
    """D3 신호 (f) — caption_score."""

    def test_caption_lines_above_threshold(self) -> None:
        # 5 line 중 2 line 이 caption-like → 0.4 ≥ 0.20
        page = _make_page_dict(
            [
                ["[표 1] 데이터센터 현황"],
                ["일반 본문 " * 30],
                ["<그림 2> 시스템 구성도"],
                ["일반 본문 " * 30],
                ["일반 본문 " * 30],
            ]
        )
        score = score_page(page, page_num=1, page_area_pt2=_A4_AREA)
        self.assertGreaterEqual(score.caption_score, 0.20)
        self.assertIn("caption", score.signal_kinds())

    def test_no_caption_lines_zero_score(self) -> None:
        page = _make_page_dict(
            [
                ["보고서 본문 시작"],
                ["여기에는 캡션 없음"],
                ["일반 단락"],
            ]
        )
        score = score_page(page, page_num=1, page_area_pt2=_A4_AREA)
        self.assertEqual(score.caption_score, 0.0)


class CompositeScoreTest(unittest.TestCase):
    """compute_score — 가중 합산 정합성."""

    def test_score_clamped_zero_to_one(self) -> None:
        score = compute_score(
            text_density=0.0,
            table_like_score=1.0,
            image_area_ratio=1.0,
            text_quality=0.0,
            caption_score=1.0,
            entity_hits=10,
            page_area_pt2=_A4_AREA,
        )
        # 모든 신호 max — 가중 합 = 1.00 (entity 0 제외해도 정확히 1.0 도달)
        self.assertGreaterEqual(score, 0.95)
        self.assertLessEqual(score, 1.00)

    def test_score_all_safe_zero(self) -> None:
        score = compute_score(
            text_density=2e-3,
            table_like_score=0.0,
            image_area_ratio=0.0,
            text_quality=1.0,
            caption_score=0.0,
            entity_hits=0,
            page_area_pt2=_A4_AREA,
        )
        # density 가 1e-3 의 정확히 2배 → density_signal = 0
        self.assertEqual(score, 0.0)


if __name__ == "__main__":
    unittest.main()
