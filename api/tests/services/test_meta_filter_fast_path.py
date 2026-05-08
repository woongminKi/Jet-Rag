"""S3 D2 — meta_filter_fast_path detector + executor 단위 테스트 (planner v0.1 §C).

검증 범위
---------
- 룰 detector ``is_meta_only`` — planner §C 8 케이스 중 fast path 4 + RAG 2.
- 회귀 가드 — doc-suffix 없는 단순 명사 query 는 None (기존 mock 테스트 query 보호).

본 테스트는 외부 API 호출 0 — 룰 매칭만. 실 Supabase SELECT 실행 (`run`) 은 mock.
"""

from __future__ import annotations

import os
import unittest
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

# 모듈 import 단계에서 환경 변수 요구 회피 (다른 테스트 파일과 동일 패턴)
os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")

from app.services import meta_filter_fast_path  # noqa: E402
from app.services.meta_filter_fast_path import (  # noqa: E402
    MetaFilterPlan,
    is_meta_only,
)


class IsMetaOnlyDetectorTest(unittest.TestCase):
    """planner v0.1 §C 표 — fast path 4 케이스 / RAG fallback 2 케이스."""

    # -----------------------------------------------------------------
    # planner §C 케이스 #1 — `#투자` → tag-only
    # -----------------------------------------------------------------
    def test_tag_only_fires(self) -> None:
        plan = is_meta_only("#투자")
        self.assertIsNotNone(plan)
        assert plan is not None  # for type-checker
        self.assertEqual(plan.tags, ("투자",))
        self.assertIsNone(plan.date_range)
        self.assertIsNone(plan.title_ilike)
        self.assertEqual(plan.matched_kind, "tag")

    # -----------------------------------------------------------------
    # planner §C 케이스 #2 — "어제 받은 문서" → 상대 날짜 + suffix
    # -----------------------------------------------------------------
    def test_relative_date_with_suffix_fires(self) -> None:
        # `received` 단어 없이 "어제 문서" 도 동일 동작 — 명령형 stopword 만 잔존이면 fast path.
        # planner 명세 § "받은" 은 동사 — 의문 동사구가 아니므로 fast path 진입 가능해야 함.
        # 실제 룰: 잔여 토큰이 의문 동사구 0 + (suffix or 날짜·태그 가드) 면 진입.
        plan = is_meta_only("어제 받은 문서", today=date(2026, 5, 9))
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertIsNotNone(plan.date_range)
        # "문서" suffix 매칭 → title_ilike 채워짐
        self.assertIsNotNone(plan.title_ilike)
        self.assertIn("date", plan.matched_kind)

    # -----------------------------------------------------------------
    # planner §C 케이스 #3 — "2025년 3월 회의록" → 월 + suffix
    # -----------------------------------------------------------------
    def test_year_month_with_suffix_fires(self) -> None:
        plan = is_meta_only("2025년 3월 회의록")
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertIsNotNone(plan.date_range)
        from_dt, to_dt = plan.date_range
        self.assertEqual(from_dt, datetime(2025, 3, 1, tzinfo=timezone.utc))
        self.assertEqual(to_dt, datetime(2025, 4, 1, tzinfo=timezone.utc))
        self.assertEqual(plan.title_ilike, "회의록")

    # -----------------------------------------------------------------
    # planner §C 케이스 #4 — "프로젝트 X 기획서" → suffix 단독
    # -----------------------------------------------------------------
    def test_doc_suffix_only_fires(self) -> None:
        plan = is_meta_only("프로젝트 X 기획서")
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertIsNone(plan.date_range)
        self.assertEqual(plan.tags, ())
        self.assertIsNotNone(plan.title_ilike)
        self.assertIn("기획서", plan.title_ilike or "")
        self.assertEqual(plan.matched_kind, "title")

    # -----------------------------------------------------------------
    # planner §C 케이스 #5 — "왜 이 펀드가 손실났나" → 의문 동사구 → None
    # -----------------------------------------------------------------
    def test_causal_question_returns_none(self) -> None:
        plan = is_meta_only("왜 이 펀드가 손실났나")
        self.assertIsNone(plan, "인과 의문 query 는 RAG path — fast path None 기대")

    # -----------------------------------------------------------------
    # planner §C 케이스 #6 — "#투자 수익률 어떻게 계산" → 태그 있어도 의문 → None
    # -----------------------------------------------------------------
    def test_tag_with_question_verb_returns_none(self) -> None:
        plan = is_meta_only("#투자 수익률 어떻게 계산")
        self.assertIsNone(plan, "태그가 있어도 의문 동사구('어떻게') 잔존 시 RAG path")


class RegressionGuardTest(unittest.TestCase):
    """회귀 가드 — 기존 search 단위 테스트 q 값이 fast path 에 잡히지 않는지 검증."""

    def test_short_english_token_returns_none(self) -> None:
        for q in ("t", "test", "x"):
            self.assertIsNone(is_meta_only(q), f"q='{q}' 는 RAG path 기대")

    def test_korean_noun_only_returns_none(self) -> None:
        # 기존 회귀 query 들 — doc-suffix 없는 한글 명사구는 RAG path 로 보호.
        for q in ("결론", "시트", "테스트", "테스트쿼리", "없는단어"):
            self.assertIsNone(is_meta_only(q), f"q='{q}' 는 RAG path 기대")

    def test_korean_noun_phrase_returns_none(self) -> None:
        # "소나타 시트 종류" 같은 doc-suffix 없는 명사구도 RAG.
        self.assertIsNone(is_meta_only("소나타 시트 종류"))
        self.assertIsNone(is_meta_only("공사대금 합의해지"))


class RunExecutorTest(unittest.TestCase):
    """`run(plan)` — Supabase chain 호출 형태 mock 검증."""

    def _chain(self, rows: list[dict]) -> MagicMock:
        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.is_.return_value = chain
        chain.gte.return_value = chain
        chain.lt.return_value = chain
        chain.contains.return_value = chain
        chain.ilike.return_value = chain
        chain.order.return_value = chain
        chain.limit.return_value = chain
        chain.execute.return_value.data = rows
        return chain

    def test_tag_plan_calls_contains(self) -> None:
        plan = MetaFilterPlan(tags=("투자",), matched_kind="tag")
        chain = self._chain(
            [{"id": "d1", "title": "투자 문서", "doc_type": "pdf",
              "tags": ["투자"], "summary": None, "created_at": "2025-04-01T00:00:00Z"}]
        )
        client = MagicMock()
        client.table.return_value = chain
        with patch.object(
            meta_filter_fast_path, "get_supabase_client", return_value=client,
        ):
            rows = meta_filter_fast_path.run(plan, user_id="user-1")
        self.assertEqual(len(rows), 1)
        chain.contains.assert_called_with("tags", ["투자"])
        chain.limit.assert_called_with(20)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
