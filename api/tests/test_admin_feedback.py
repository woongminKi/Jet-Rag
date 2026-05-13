"""S1 D4 ship — `/admin/feedback/stats` endpoint 단위 테스트.

검증 포인트:
- classify_comment 룰 — 4 카테고리 + 우선순위 (source > search > answer > other)
- 정상 경로: answer_feedback row → daily/rating/categories/recent_comments 매핑
- 빈 결과 (row 0건) → daily 는 days 일 수 만큼 0 row, categories 4 키 모두 0,
  satisfaction_rate=None
- 마이그 011 미적용 (DB raise) → graceful: error_code='migrations_pending'
- 빈 코멘트 / 공백만 → 분류·노출 X (recent_comments 에서 제외)
- 최근 코멘트 cap 10 — created_at desc 정렬 입력 가정
- range parsing — 7d/14d/30d 별 daily 길이

stdlib unittest + mock only. Supabase env 없이도 실행됨.
test_admin_queries.py 와 동일 패턴.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch


def _client_with_rows(rows: list[dict]) -> MagicMock:
    """Supabase client mock — answer_feedback SELECT 결과만 컨트롤."""
    client = MagicMock()
    table = MagicMock()
    select = MagicMock()
    gte = MagicMock()
    order = MagicMock()
    resp = MagicMock()
    resp.data = rows
    order.execute.return_value = resp
    gte.order.return_value = order
    select.gte.return_value = gte
    table.select.return_value = select
    client.table.return_value = table
    return client


def _client_raise(exc: Exception) -> MagicMock:
    """Supabase client mock — table().execute() raise (마이그 011 미적용 시뮬)."""
    client = MagicMock()
    client.table.side_effect = exc
    return client


def _row(
    *,
    created_at: str,
    helpful: bool,
    query: str = "테스트 query",
    comment: str | None = None,
) -> dict:
    return {
        "created_at": created_at,
        "helpful": helpful,
        "comment": comment,
        "query": query,
    }


class ClassifyCommentRulesTest(unittest.TestCase):
    """classify_comment 룰 — 키워드 매칭 + 우선순위 검증."""

    def test_source_issue_keywords(self) -> None:
        from app.routers.admin import classify_comment

        for text in (
            "출처가 이상해요",
            "근거 없는 답변이에요",
            "어디서 가져온 자료인지 모르겠어요",
            "인용된 페이지가 잘못됐어요",
            "이상한 자료를 인용했어요",
        ):
            self.assertEqual(classify_comment(text), "source_issue", msg=text)

    def test_search_issue_keywords(self) -> None:
        from app.routers.admin import classify_comment

        for text in (
            "검색이 안돼요",
            "찾을 수 없는 내용이에요",
            "관련 없는 chunk 가 나와요",
            "검색 결과가 이상합니다",
            "원하는 내용이 나오지 않아요",
        ):
            self.assertEqual(classify_comment(text), "search_issue", msg=text)

    def test_answer_issue_keywords(self) -> None:
        from app.routers.admin import classify_comment

        for text in (
            "답변이 부족해요",
            "정확하지 않은 답변",
            "잘못된 정보를 알려줘요",
            "틀린 답변입니다",
            "오답이에요",
            "환각이 심합니다",
        ):
            self.assertEqual(classify_comment(text), "answer_issue", msg=text)

    def test_priority_source_over_search(self) -> None:
        """source + search 키워드 동시 → source_issue 우선."""
        from app.routers.admin import classify_comment

        # '검색' + '출처' 둘 다 매칭. source 우선.
        self.assertEqual(
            classify_comment("검색 결과의 출처가 이상해요"),
            "source_issue",
        )

    def test_priority_search_over_answer(self) -> None:
        """search + answer 키워드 동시 → search_issue 우선."""
        from app.routers.admin import classify_comment

        # '검색' + '답변' 둘 다 매칭. search 우선.
        self.assertEqual(
            classify_comment("검색이 답변보다 먼저 잘못됐어요"),
            "search_issue",
        )

    def test_other_fallback(self) -> None:
        from app.routers.admin import classify_comment

        for text in (
            "그냥 별로네요",
            "음...",
            "good",
            "더 빨랐으면 좋겠어요",
        ):
            self.assertEqual(classify_comment(text), "other", msg=text)

    def test_empty_or_whitespace(self) -> None:
        from app.routers.admin import classify_comment

        self.assertEqual(classify_comment(""), "other")
        self.assertEqual(classify_comment("   "), "other")
        self.assertEqual(classify_comment("\n\t"), "other")


class AdminFeedbackStatsHappyPathTest(unittest.TestCase):
    """정상 경로 — row 매핑 + daily zero-fill + 카테고리 분포 검증."""

    def test_basic_mapping(self) -> None:
        from app.routers import admin as admin_module
        from app.routers.admin import KST

        # 2026-05-14 P3 fix — `now_utc` 기반 상대 시각은 KST 자정 직후 (00:00~00:29)
        # 환경에서 row 시각이 KST 자정 경계를 넘어 yesterday bucket 으로 분리되는
        # 회귀가 발생. 모든 row 를 KST today 정오 (UTC 03:00) 기준으로 고정 → timezone
        # 경계 무관 deterministic.
        base_kst_noon = datetime.now(KST).replace(
            hour=12, minute=0, second=0, microsecond=0,
        )
        base_utc = base_kst_noon.astimezone(timezone.utc)
        rows = [
            # 👍 + 코멘트 없음
            _row(created_at=base_utc.isoformat(), helpful=True, query="휠 사이즈"),
            # 👎 + source_issue 코멘트
            _row(
                created_at=(base_utc - timedelta(minutes=10)).isoformat(),
                helpful=False,
                query="환경 인증",
                comment="출처가 이상해요",
            ),
            # 👎 + answer_issue 코멘트
            _row(
                created_at=(base_utc - timedelta(minutes=20)).isoformat(),
                helpful=False,
                query="규정 조항",
                comment="틀린 답변이에요",
            ),
        ]
        client = _client_with_rows(rows)
        with patch.object(admin_module, "get_supabase_client", return_value=client):
            resp = admin_module.admin_feedback_stats(range="7d")

        self.assertEqual(resp.range, "7d")
        self.assertEqual(resp.total_feedback, 3)
        # 👍 1 / 👎 2 → 1/3 ≈ 0.3333
        self.assertEqual(resp.rating_distribution["up"], 1)
        self.assertEqual(resp.rating_distribution["down"], 2)
        self.assertAlmostEqual(resp.satisfaction_rate or 0.0, 1 / 3, places=3)
        # daily — 7일치 row, 마지막은 오늘 (up=1, down=2).
        self.assertEqual(len(resp.daily), 7)
        self.assertEqual(resp.daily[-1].up, 1)
        self.assertEqual(resp.daily[-1].down, 2)
        self.assertEqual(resp.daily[-1].total, 3)
        # 카테고리 4 키 모두 노출, source 1 / answer 1 / search 0 / other 0
        self.assertEqual(set(resp.comment_categories.keys()),
                         {"search_issue", "answer_issue", "source_issue", "other"})
        self.assertEqual(resp.comment_categories["source_issue"], 1)
        self.assertEqual(resp.comment_categories["answer_issue"], 1)
        self.assertEqual(resp.comment_categories["search_issue"], 0)
        self.assertEqual(resp.comment_categories["other"], 0)
        self.assertEqual(resp.comment_count, 2)
        # recent_comments — 코멘트 있는 2건만 노출.
        self.assertEqual(len(resp.recent_comments), 2)
        # 첫 코멘트 (가장 최근) — source_issue.
        self.assertEqual(resp.recent_comments[0].category, "source_issue")
        self.assertEqual(resp.recent_comments[0].rating, "down")
        self.assertIsNone(resp.error_code)

    def test_empty_rows_zero_fill(self) -> None:
        """row 0건 — daily 7 row 모두 0, categories 4 키 모두 0, satisfaction=None."""
        from app.routers import admin as admin_module

        client = _client_with_rows([])
        with patch.object(admin_module, "get_supabase_client", return_value=client):
            resp = admin_module.admin_feedback_stats(range="7d")

        self.assertEqual(resp.total_feedback, 0)
        self.assertIsNone(resp.satisfaction_rate)
        self.assertEqual(resp.rating_distribution, {"up": 0, "down": 0})
        self.assertEqual(len(resp.daily), 7)
        for bucket in resp.daily:
            self.assertEqual(bucket.up, 0)
            self.assertEqual(bucket.down, 0)
            self.assertEqual(bucket.total, 0)
        self.assertEqual(len(resp.comment_categories), 4)
        self.assertTrue(all(v == 0 for v in resp.comment_categories.values()))
        self.assertEqual(resp.recent_comments, [])
        self.assertEqual(resp.comment_count, 0)
        self.assertIsNone(resp.error_code)

    def test_range_parsing(self) -> None:
        """range=14d / 30d → daily 길이 정합."""
        from app.routers import admin as admin_module

        for r, expected in (("7d", 7), ("14d", 14), ("30d", 30)):
            client = _client_with_rows([])
            with patch.object(admin_module, "get_supabase_client", return_value=client):
                resp = admin_module.admin_feedback_stats(range=r)
            self.assertEqual(resp.range, r)
            self.assertEqual(len(resp.daily), expected)


class CommentEdgeCaseTest(unittest.TestCase):
    """코멘트 빈/공백/cap 검증."""

    def test_empty_comment_excluded(self) -> None:
        """None / 공백만 코멘트 → categories·recent 모두 미반영."""
        from app.routers import admin as admin_module

        now_utc = datetime.now(timezone.utc)
        rows = [
            _row(created_at=now_utc.isoformat(), helpful=True, comment=None),
            _row(
                created_at=(now_utc - timedelta(minutes=1)).isoformat(),
                helpful=False,
                comment="   ",
            ),
            _row(
                created_at=(now_utc - timedelta(minutes=2)).isoformat(),
                helpful=False,
                comment="\n\t",
            ),
        ]
        client = _client_with_rows(rows)
        with patch.object(admin_module, "get_supabase_client", return_value=client):
            resp = admin_module.admin_feedback_stats(range="7d")
        self.assertEqual(resp.total_feedback, 3)
        # 코멘트 0건이라 categories 4 키 모두 0, recent 0건.
        self.assertTrue(all(v == 0 for v in resp.comment_categories.values()))
        self.assertEqual(resp.recent_comments, [])
        self.assertEqual(resp.comment_count, 0)

    def test_recent_comments_cap_10(self) -> None:
        """코멘트 15건이라도 recent_comments 는 10건만 (categories 는 15건 모두 카운트)."""
        from app.routers import admin as admin_module

        now_utc = datetime.now(timezone.utc)
        rows = [
            _row(
                created_at=(now_utc - timedelta(minutes=i)).isoformat(),
                helpful=False,
                query=f"q{i}",
                comment=f"검색 결과 {i}",  # search_issue
            )
            for i in range(15)
        ]
        client = _client_with_rows(rows)
        with patch.object(admin_module, "get_supabase_client", return_value=client):
            resp = admin_module.admin_feedback_stats(range="7d")
        self.assertEqual(resp.total_feedback, 15)
        self.assertEqual(resp.comment_count, 15)
        self.assertEqual(resp.comment_categories["search_issue"], 15)
        self.assertEqual(len(resp.recent_comments), 10)


class MigrationsPendingTest(unittest.TestCase):
    """DB raise (마이그 011 미적용) → graceful 응답."""

    def test_db_raise_graceful(self) -> None:
        from app.routers import admin as admin_module

        client = _client_raise(RuntimeError("relation answer_feedback does not exist"))
        with patch.object(admin_module, "get_supabase_client", return_value=client):
            resp = admin_module.admin_feedback_stats(range="7d")

        self.assertEqual(resp.error_code, "migrations_pending")
        self.assertEqual(resp.daily, [])
        self.assertEqual(resp.recent_comments, [])
        self.assertEqual(resp.total_feedback, 0)
        self.assertEqual(resp.comment_count, 0)
        self.assertIsNone(resp.satisfaction_rate)
        # 분포 dict 는 fallback 시에도 항상 키 노출 (frontend 0건 표기 단순화).
        self.assertEqual(resp.rating_distribution, {"up": 0, "down": 0})
        self.assertEqual(
            set(resp.comment_categories.keys()),
            {"search_issue", "answer_issue", "source_issue", "other"},
        )


if __name__ == "__main__":
    unittest.main()
