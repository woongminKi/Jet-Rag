"""S3 D1 — intent_router 룰 단위 테스트 (planner v0.1 §7 표 9건).

검증 범위
---------
- T1~T7 각 1건 → 신호 발화 + needs_decomposition 판정
- edge_clean_keyword — 키워드 미발화 시 단순 query
- edge_empty_query — empty/whitespace ValueError

본 테스트는 외부 API 호출 0 — 룰 매칭만. 회귀 영향 0.
"""

from __future__ import annotations

import os
import unittest

# 모듈 import 단계에서 환경 변수 요구 회피 (다른 테스트 파일과 동일 패턴)
os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")

from app.services.intent_router import IntentRouterDecision, route  # noqa: E402


class IntentRouterTriggerTest(unittest.TestCase):
    """T1~T7 각 1건 + 2 edge — 명세 §7 표."""

    # -----------------------------------------------------------------
    # T1 — cross-doc regex
    # -----------------------------------------------------------------
    def test_t1_cross_doc_regex_fires(self) -> None:
        decision = route("작년 보고서랑 올해 자료 비교해줘")
        self.assertIn("T1_cross_doc", decision.triggered_signals)
        self.assertTrue(decision.needs_decomposition)

    # -----------------------------------------------------------------
    # T2 — 비교 키워드
    # -----------------------------------------------------------------
    def test_t2_compare_keyword_fires(self) -> None:
        decision = route("두 모델의 차이가 뭐야")
        self.assertIn("T2_compare", decision.triggered_signals)
        self.assertIn("차이", decision.matched_keywords)
        self.assertTrue(decision.needs_decomposition)

    # -----------------------------------------------------------------
    # T3 — 인과 키워드
    # -----------------------------------------------------------------
    def test_t3_causal_keyword_fires(self) -> None:
        decision = route("매출이 떨어진 이유 알려줘")
        self.assertIn("T3_causal", decision.triggered_signals)
        self.assertIn("이유", decision.matched_keywords)
        self.assertTrue(decision.needs_decomposition)

    # -----------------------------------------------------------------
    # T4 — 변경점 키워드 (단독은 needs_decomposition False)
    # -----------------------------------------------------------------
    def test_t4_change_keyword_fires_without_decomposition(self) -> None:
        # "달라진" 은 T2(달라) 부분매칭 회피용으로 다른 T4 키워드 사용.
        decision = route("이번 분기 업데이트 내역 정리")
        self.assertIn("T4_change", decision.triggered_signals)
        self.assertIn("업데이트", decision.matched_keywords)
        # T4 단독 → 분해 불필요 (T2/T3 미발화 확인)
        self.assertNotIn("T2_compare", decision.triggered_signals)
        self.assertNotIn("T3_causal", decision.triggered_signals)
        self.assertFalse(decision.needs_decomposition)

    # -----------------------------------------------------------------
    # T5 — 긴 query (char ≥ 40 또는 token ≥ 12)
    # -----------------------------------------------------------------
    def test_t5_long_query_fires(self) -> None:
        # 40자 이상 + T1~T4/T6/T7 키워드 0 → T5 단독 검증.
        long_q = "데이터센터 인프라 모니터링 항목 중에서 핵심 지표만 모아 깔끔히 정리해 주세요"
        decision = route(long_q)
        self.assertIn("T5_long_query", decision.triggered_signals)
        # T5 단독은 needs_decomposition False (T6 와 결합 시만 분해)
        self.assertEqual(decision.triggered_signals, ("T5_long_query",))
        self.assertFalse(decision.needs_decomposition)

    # -----------------------------------------------------------------
    # T6 — low confidence (모호 표현 + confidence -0.3 cap)
    # -----------------------------------------------------------------
    def test_t6_low_confidence_fires_with_penalty(self) -> None:
        decision = route("그거 어떻게 됐더라")
        self.assertIn("T6_low_confidence", decision.triggered_signals)
        # T6 + 다른 신호 없으면 1.0 - 0.15*1 - 0.3 = 0.55
        self.assertAlmostEqual(decision.confidence_score, 0.55, places=6)
        self.assertFalse(decision.needs_decomposition)

    # -----------------------------------------------------------------
    # T7 — 복수 대상 (T1 미발화 + 조사 ≥ 2)
    # -----------------------------------------------------------------
    def test_t7_multi_target_fires(self) -> None:
        # 명세 — `count("랑") + count("과") >= 2`. "와" 는 카운트 X.
        # "랑" 1회 + "과" 1회 = 2 + T1 cross-doc regex 미매치 (자료/문서/보고서 부재).
        decision = route("철수랑 영희, 사과랑 배 그리고 책상과 의자")
        self.assertIn("T7_multi_target", decision.triggered_signals)
        self.assertNotIn("T1_cross_doc", decision.triggered_signals)
        self.assertTrue(decision.needs_decomposition)

    # -----------------------------------------------------------------
    # Edge — 키워드 0 매칭
    # -----------------------------------------------------------------
    def test_edge_clean_keyword_returns_no_signals(self) -> None:
        decision = route("안녕하세요")
        self.assertEqual(decision.triggered_signals, ())
        self.assertEqual(decision.matched_keywords, ())
        self.assertFalse(decision.needs_decomposition)
        self.assertEqual(decision.confidence_score, 1.0)
        self.assertEqual(decision.query_normalized, "안녕하세요")
        self.assertIsInstance(decision, IntentRouterDecision)

    # -----------------------------------------------------------------
    # Edge — empty / whitespace ValueError
    # -----------------------------------------------------------------
    def test_edge_empty_query_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            route("")
        with self.assertRaises(ValueError):
            route("   \t\n  ")


class IntentRouterCrossDocP1PatternTest(unittest.TestCase):
    """S4-A P1 — T1 보조 패턴 3종 (PAIR / PAIR2 / PLURAL) + T2 어간 regex."""

    # -----------------------------------------------------------------
    # T1 — PAIR: "NP1 (와|과|랑) NP2 …문서류명사"
    # -----------------------------------------------------------------
    def test_t1_pair_proper_noun_then_doc_noun(self) -> None:
        decision = route("기웅민 이력서와 이한주 포트폴리오의 핵심 역량은 어떻게 다른가요?")
        self.assertIn("T1_cross_doc", decision.triggered_signals)
        self.assertTrue(decision.needs_decomposition)

    def test_t1_pair_multiword_proper_noun(self) -> None:
        # "law sample2와 law sample3 두 판결" — NP2 가 공백 포함 다어절 + 뒤에 "판결".
        decision = route("law sample2와 law sample3 두 판결에서 대법원이 내린 결정은?")
        self.assertIn("T1_cross_doc", decision.triggered_signals)
        self.assertTrue(decision.needs_decomposition)

    # -----------------------------------------------------------------
    # T1 — PAIR2: "문서류명사 (와|과|랑) NP2"
    # -----------------------------------------------------------------
    def test_t1_pair2_doc_noun_then_proper_noun(self) -> None:
        decision = route("승인글 템플릿1과 템플릿3은 어떤 주제를 다루고 있나요?")
        self.assertIn("T1_cross_doc", decision.triggered_signals)
        self.assertTrue(decision.needs_decomposition)

    def test_t1_pair2_naegyu_rang(self) -> None:
        decision = route("운영내규랑 직제규정에서 위원회 역할 어떻게 달라")
        self.assertIn("T1_cross_doc", decision.triggered_signals)
        self.assertTrue(decision.needs_decomposition)

    # -----------------------------------------------------------------
    # T1 — PLURAL: "문서류명사들 (에서|에|중...)"
    # -----------------------------------------------------------------
    def test_t1_plural_jaryodeul_eseo(self) -> None:
        decision = route("법률 자료들에서 원심 파기환송 사례들 어디 있었지")
        self.assertIn("T1_cross_doc", decision.triggered_signals)
        self.assertTrue(decision.needs_decomposition)

    # -----------------------------------------------------------------
    # false-positive 가드
    # -----------------------------------------------------------------
    def test_t1_no_fp_single_doc_locative(self) -> None:
        # 단수 "자료에" + 짝 명사 부재 → T1 비발화.
        decision = route("이 자료에 환경 인증 절차 나와있어?")
        self.assertNotIn("T1_cross_doc", decision.triggered_signals)

    def test_t2_no_fp_dareun_saram_with_space(self) -> None:
        # "다른 사람" — 공백 → T2 어간 regex (다른[가지]) 비매치.
        decision = route("다른 사람 이력 보여줘")
        self.assertNotIn("T2_compare", decision.triggered_signals)

    # -----------------------------------------------------------------
    # T2 — 어간 regex: 다르게/다르지/다른가/다른지/다릅니다/상이
    # -----------------------------------------------------------------
    def test_t2_stem_dareun_ga(self) -> None:
        decision = route("개정 절차가 어떻게 다른가요")
        self.assertIn("T2_compare", decision.triggered_signals)
        self.assertTrue(decision.needs_decomposition)

    def test_t2_stem_sangi(self) -> None:
        decision = route("두 규정의 효력 발생 시점이 상이한지 확인")
        self.assertIn("T2_compare", decision.triggered_signals)
        self.assertTrue(decision.needs_decomposition)

    def test_t2_stem_dareuge(self) -> None:
        decision = route("이번에 처리 방식이 다르게 됐어")
        self.assertIn("T2_compare", decision.triggered_signals)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
