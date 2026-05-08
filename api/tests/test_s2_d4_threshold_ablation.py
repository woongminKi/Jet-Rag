"""S2 D4 옵션 A — vision_need_score threshold ablation 단위 테스트.

검증 범위 (master plan §6 S2 D4 옵션 A)
- ThresholdRecomputeTest (5건): 각 신호 단독 trigger / 모든 신호 OFF / 복수 신호 동시
- AblationOnlyTest (5건): A1~A5 단독 ablation — 다른 4 신호 항상 False 검증
- DatacenterP40CatchTest (1건): G-A-008 회귀 row raw signal 의 11 후보 catch 결과
- GoldenHintCrossCheckTest (2건): hint cross-check 함수 일관성 + 측정 불가 row note 검증

본 테스트는 D3 raw signal CSV / 골든셋 / DB 호출 0 — D3PageSignal mock 만 사용.
운영 모듈 vision_need_score 의 모듈 상수 변경 0 (D4 는 측정만, ablation 은 본 스크립트 격리).
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")

# evals/ 를 import path 에 추가 — ablation 스크립트 모듈 import
_REPO_ROOT = Path(__file__).resolve().parents[2]
_EVALS_PATH = _REPO_ROOT / "evals"
if str(_EVALS_PATH) not in sys.path:
    sys.path.insert(0, str(_EVALS_PATH))

from run_s2_d4_threshold_ablation import (  # noqa: E402
    D3PageSignal,
    GoldenRow,
    Threshold,
    _build_candidates,
    cross_check_hints,
    datacenter_p40_catch,
    recompute_with_thresholds,
)


# ---------------------------------------------------------------------------
# 공용 헬퍼
# ---------------------------------------------------------------------------


_A4_AREA = 595.0 * 842.0  # ≈ 501,490 pt² (D3 CSV 의 page_area_pt2 와 동치)


def _signal(
    *,
    doc: str = "test.pdf",
    page: int = 1,
    text_density: float = 5e-3,  # default = baseline 미달 (low_density 미발화)
    table_like_score: float = 0.0,
    image_area_ratio: float = 0.0,
    text_quality: float = 1.0,
    caption_score: float = 0.0,
) -> D3PageSignal:
    """D3PageSignal mock 헬퍼. default 는 모든 신호 비발화 (needs_vision=False)."""
    return D3PageSignal(
        doc=doc,
        page=page,
        page_area_pt2=_A4_AREA,
        text_density=text_density,
        table_like_score=table_like_score,
        image_area_ratio=image_area_ratio,
        text_quality=text_quality,
        caption_score=caption_score,
    )


_BASELINE = Threshold(
    name="C0_baseline", density=1e-3, table=0.30, image=0.30, quality=0.40, caption=0.20
)


# ---------------------------------------------------------------------------
# (1) ThresholdRecomputeTest — 각 신호 단독 trigger 검증
# ---------------------------------------------------------------------------


class ThresholdRecomputeTest(unittest.TestCase):
    """각 신호가 임계 만족 시 needs_vision=True / 미달 시 False / 복수 동시."""

    def test_density_alone_triggers(self) -> None:
        """text_density < 1e-3 만 만족하면 low_density 단독 발화."""
        s = _signal(text_density=5e-4)  # baseline 미만
        decisions = recompute_with_thresholds([s], _BASELINE)
        self.assertTrue(decisions[0].needs_vision)
        self.assertEqual(decisions[0].triggers, ("low_density",))

    def test_table_alone_triggers(self) -> None:
        """table_like_score >= 0.30 만 만족하면 table_like 단독 발화."""
        s = _signal(table_like_score=0.40)
        decisions = recompute_with_thresholds([s], _BASELINE)
        self.assertTrue(decisions[0].needs_vision)
        self.assertEqual(decisions[0].triggers, ("table_like",))

    def test_all_signals_off(self) -> None:
        """모든 신호가 임계 미달이면 needs_vision=False, triggers=()."""
        s = _signal(
            text_density=5e-3,  # > 1e-3 → low_density off
            table_like_score=0.10,  # < 0.30
            image_area_ratio=0.10,  # < 0.30
            text_quality=0.99,  # > 0.40
            caption_score=0.05,  # < 0.20
        )
        decisions = recompute_with_thresholds([s], _BASELINE)
        self.assertFalse(decisions[0].needs_vision)
        self.assertEqual(decisions[0].triggers, ())

    def test_multiple_signals_simultaneous(self) -> None:
        """3 신호 동시 trigger — triggers 에 모두 포함, needs_vision=True."""
        s = _signal(
            text_density=5e-4,  # low_density
            table_like_score=0.50,  # table_like
            caption_score=0.30,  # caption
        )
        decisions = recompute_with_thresholds([s], _BASELINE)
        self.assertTrue(decisions[0].needs_vision)
        self.assertSetEqual(
            set(decisions[0].triggers),
            {"low_density", "table_like", "caption"},
        )

    def test_quality_inverse_direction(self) -> None:
        """text_quality 는 ≤ 임계가 trigger (낮을수록 vision). 0.40 = 경계."""
        # text_quality_low: 0.40 이하면 trigger
        s_low = _signal(text_quality=0.30)
        s_high = _signal(text_quality=0.50)
        d_low = recompute_with_thresholds([s_low], _BASELINE)
        d_high = recompute_with_thresholds([s_high], _BASELINE)
        self.assertEqual(d_low[0].triggers, ("text_quality_low",))
        self.assertEqual(d_high[0].triggers, ())


# ---------------------------------------------------------------------------
# (2) AblationOnlyTest — A1~A5 단독 ablation (다른 4 신호 항상 False)
# ---------------------------------------------------------------------------


class AblationOnlyTest(unittest.TestCase):
    """A1~A5 후보는 한 신호만 활성, 나머지 4 신호는 trigger_*=False 라 절대 발화 X."""

    def _candidate(self, name: str) -> Threshold:
        for c in _build_candidates():
            if c.name == name:
                return c
        raise ValueError(f"후보 없음: {name}")

    def test_a1_density_only_other_signals_off(self) -> None:
        """A1 활성 시 table/image/quality/caption 강제 신호로도 needs_vision=False."""
        a1 = self._candidate("A1_density_only")
        # density 는 미달, 다른 4 신호는 모두 발화 강제
        s = _signal(
            text_density=5e-3,  # off
            table_like_score=0.99,
            image_area_ratio=0.99,
            text_quality=0.0,
            caption_score=0.99,
        )
        decisions = recompute_with_thresholds([s], a1)
        self.assertFalse(decisions[0].needs_vision)
        self.assertEqual(decisions[0].triggers, ())

    def test_a2_table_only(self) -> None:
        a2 = self._candidate("A2_table_only")
        s = _signal(
            text_density=1e-9,  # 매우 낮음 — 그래도 OFF
            table_like_score=0.40,  # 활성 — 발화
            image_area_ratio=0.99,
            text_quality=0.0,
            caption_score=0.99,
        )
        decisions = recompute_with_thresholds([s], a2)
        self.assertTrue(decisions[0].needs_vision)
        self.assertEqual(decisions[0].triggers, ("table_like",))

    def test_a3_image_only(self) -> None:
        a3 = self._candidate("A3_image_only")
        # image 만 활성, 다른 신호 강제 발화 시도해도 OFF
        s_image_off = _signal(
            text_density=1e-9,
            table_like_score=0.99,
            image_area_ratio=0.10,  # < 0.30 → off
            text_quality=0.0,
            caption_score=0.99,
        )
        d_off = recompute_with_thresholds([s_image_off], a3)
        self.assertFalse(d_off[0].needs_vision)

        s_image_on = _signal(image_area_ratio=0.50, text_density=5e-3, text_quality=1.0)
        d_on = recompute_with_thresholds([s_image_on], a3)
        self.assertEqual(d_on[0].triggers, ("image_area",))

    def test_a4_quality_only(self) -> None:
        a4 = self._candidate("A4_quality_only")
        s = _signal(text_quality=0.20, text_density=5e-3)
        d = recompute_with_thresholds([s], a4)
        self.assertEqual(d[0].triggers, ("text_quality_low",))

    def test_a5_caption_only(self) -> None:
        a5 = self._candidate("A5_caption_only")
        # 다른 신호 강제 발화 시도해도 caption 만 응답
        s = _signal(
            text_density=1e-9,
            table_like_score=0.99,
            image_area_ratio=0.99,
            text_quality=0.0,
            caption_score=0.30,  # 활성
        )
        d = recompute_with_thresholds([s], a5)
        self.assertTrue(d[0].needs_vision)
        self.assertEqual(d[0].triggers, ("caption",))


# ---------------------------------------------------------------------------
# (3) DatacenterP40CatchTest — G-A-008 raw signal 의 11 후보 catch 결과
# ---------------------------------------------------------------------------


class DatacenterP40CatchTest(unittest.TestCase):
    """데이터센터 산업 활성화 안내서 p.40 의 D3 raw signal 로 11 후보 모두 측정.

    raw signal: density 1.62e-3 / table 0 / image_area 0.009 / text_quality 0.97 /
    caption 0.067.

    각 신호별 임계 비교:
    - density 1.62e-3 < 2e-3 (C5_density_aggr) 만 trigger. < 1.5e-3 (C2) / < 1e-3 (C0,C1,C3,C4) 모두 미달.
    - table 0.0 — 모든 후보 임계 (0.20~0.40) 미달.
    - image 0.009 — 모든 후보 임계 (0.20~0.40) 미달.
    - text_quality 0.97 — 모든 후보 임계 (0.30~0.50) 초과 (= trigger 안 됨).
    - caption 0.067 — 모든 후보 임계 (0.10~0.30) 미달.

    → catch 후보는 **C5_density_aggr 단 1개** (density 임계 완화로만 catch 가능).
       다른 10 후보는 5 신호 만으로는 catch 불가 — Q2 분기 진입의 핵심 증거.
    """

    DC_DOC = "(붙임2) 2025년 데이터센터 산업 활성화 지원 사업 통합_안내서.pdf"
    EXPECTED_CATCHERS = {"C5_density_aggr"}

    def _dc_p40_signal(self) -> D3PageSignal:
        return D3PageSignal(
            doc=self.DC_DOC,
            page=40,
            page_area_pt2=_A4_AREA,
            text_density=1.62e-3,
            table_like_score=0.0,
            image_area_ratio=0.009,
            text_quality=0.97,
            caption_score=0.067,
        )

    def test_only_c5_density_aggr_catches_p40(self) -> None:
        """11 후보 중 catch=True 후보 set 이 명세 raw signal 분석과 일치."""
        signal = self._dc_p40_signal()
        catches: dict[str, bool] = {}
        for t in _build_candidates():
            decisions = recompute_with_thresholds([signal], t)
            catches[t.name] = datacenter_p40_catch(decisions)
        actual_catchers = {name for name, caught in catches.items() if caught}
        self.assertSetEqual(
            actual_catchers,
            self.EXPECTED_CATCHERS,
            f"실측 catcher={actual_catchers} vs 기대={self.EXPECTED_CATCHERS}. "
            "raw signal 또는 후보 임계 변경 시 본 expected set 도 함께 갱신 필요.",
        )


# ---------------------------------------------------------------------------
# (4) GoldenHintCrossCheckTest — cross_check_hints 함수 일관성
# ---------------------------------------------------------------------------


class GoldenHintCrossCheckTest(unittest.TestCase):
    """hint cross-check 의 doc 매칭 / page 매칭 / 측정 불가 처리."""

    def test_cross_check_basic_match(self) -> None:
        """doc title prefix + page 가 D3 페이지에 존재하면 needs_vision_at_hint 산출."""
        signals = [_signal(doc="my-test-doc.pdf", page=5, text_density=5e-4)]
        decisions = recompute_with_thresholds(signals, _BASELINE)
        golden = [
            GoldenRow(
                id="G-TEST-1", query="q", query_type="vision_diagram",
                doc_id="", expected_doc_title="my-test-doc",
                relevant_chunks=(), source_hint="p.5",
            )
        ]
        hits = cross_check_hints(golden, decisions)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].matched_d3_doc, "my-test-doc.pdf")
        self.assertEqual(hits[0].hint_page, 5)
        self.assertTrue(hits[0].needs_vision_at_hint)
        self.assertEqual(hits[0].triggers, ("low_density",))
        self.assertEqual(hits[0].note, "OK")

    def test_cross_check_unknown_page_and_doc(self) -> None:
        """page 미상 / doc 미매칭 시 needs_vision_at_hint=None + note 기록."""
        signals = [_signal(doc="other.pdf", page=1)]
        decisions = recompute_with_thresholds(signals, _BASELINE)
        golden = [
            GoldenRow(
                id="G-TEST-2-NOPAGE", query="q", query_type="table_lookup",
                doc_id="", expected_doc_title="other",
                relevant_chunks=(), source_hint="",  # page 미상
            ),
            GoldenRow(
                id="G-TEST-3-NODOC", query="q", query_type="table_lookup",
                doc_id="", expected_doc_title="nonexistent_doc_title_xyz",
                relevant_chunks=(), source_hint="p.1",
            ),
        ]
        hits = cross_check_hints(golden, decisions)
        nopage = next(h for h in hits if h.golden_id == "G-TEST-2-NOPAGE")
        nodoc = next(h for h in hits if h.golden_id == "G-TEST-3-NODOC")
        self.assertIsNone(nopage.needs_vision_at_hint)
        self.assertEqual(nopage.note, "page 미상")
        self.assertIsNone(nodoc.needs_vision_at_hint)
        self.assertEqual(nodoc.note, "doc 미매칭")


if __name__ == "__main__":
    unittest.main()
