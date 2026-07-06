"""quota 모듈 단위 테스트.

- W9 Day 7: is_quota_exhausted 감지 정확도 (class name / status code / message fallback)
- W3 수익화: get_effective_plan / count_active_documents / get_todays_count (MagicMock Supabase, 외부 I/O 0)
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")


def _table_client(tables: dict[str, list[dict]]) -> MagicMock:
    """table(name) 별로 지정된 data 를 반환하는 mock. 체이닝 전부 흡수."""
    client = MagicMock()

    def _table(name: str) -> MagicMock:
        t = MagicMock()
        resp = MagicMock()
        resp.data = tables.get(name, [])
        resp.count = len(tables.get(name, []))
        # select().eq()...execute() 어떤 체인이든 마지막 execute 가 resp 반환
        t.select.return_value = t
        t.eq.return_value = t
        t.is_.return_value = t
        t.limit.return_value = t
        t.execute.return_value = resp
        return t

    client.table.side_effect = _table
    return client


class QuotaClassNameTest(unittest.TestCase):
    """1단계 — exception class name 화이트리스트."""

    def test_resource_exhausted_class_name(self) -> None:
        from app.services.quota import is_quota_exhausted

        # google.api_core.exceptions.ResourceExhausted 모방
        class ResourceExhausted(Exception):
            pass

        self.assertTrue(is_quota_exhausted(ResourceExhausted("any text")))

    def test_too_many_requests_class_name(self) -> None:
        from app.services.quota import is_quota_exhausted

        class TooManyRequests(Exception):
            pass

        self.assertTrue(is_quota_exhausted(TooManyRequests("anything")))

    def test_unknown_class_falls_through_to_message(self) -> None:
        from app.services.quota import is_quota_exhausted

        class CustomError(Exception):
            pass

        # class name 화이트리스트 미해당 + 메시지에 키워드 없음 → False
        self.assertFalse(is_quota_exhausted(CustomError("Service down")))


class QuotaStatusCodeTest(unittest.TestCase):
    """2단계 — exception attribute (status_code / code) == 429."""

    def test_status_code_429_attribute(self) -> None:
        from app.services.quota import is_quota_exhausted

        class HttpError(Exception):
            def __init__(self, status_code):
                super().__init__()
                self.status_code = status_code

        self.assertTrue(is_quota_exhausted(HttpError(429)))
        self.assertFalse(is_quota_exhausted(HttpError(500)))

    def test_code_429_attribute(self) -> None:
        from app.services.quota import is_quota_exhausted

        class ApiError(Exception):
            def __init__(self, code):
                super().__init__()
                self.code = code

        self.assertTrue(is_quota_exhausted(ApiError(429)))
        self.assertFalse(is_quota_exhausted(ApiError(404)))


class QuotaMessageFallbackTest(unittest.TestCase):
    """3단계 — 메시지 휴리스틱 (Day 4·6 기존 동작 보존)."""

    def test_message_string_input(self) -> None:
        from app.services.quota import is_quota_exhausted

        self.assertTrue(is_quota_exhausted("429 RESOURCE_EXHAUSTED"))
        self.assertTrue(is_quota_exhausted("HTTP 429 Too Many Requests"))
        self.assertTrue(is_quota_exhausted("You exceeded your quota"))
        self.assertFalse(is_quota_exhausted("Service unavailable"))
        self.assertFalse(is_quota_exhausted(""))
        self.assertFalse(is_quota_exhausted(None))  # type: ignore

    def test_exception_with_message_fallback(self) -> None:
        from app.services.quota import is_quota_exhausted

        # class name 미매칭 + attribute 없음 + 메시지에 키워드 → True
        class GenericError(Exception):
            pass

        self.assertTrue(
            is_quota_exhausted(GenericError("429 RESOURCE_EXHAUSTED detail"))
        )


class GetEffectivePlanTest(unittest.TestCase):
    def test_no_subscription_falls_back_to_free(self) -> None:
        from app.services import quota

        client = _table_client({
            "subscriptions": [],
            "plans": [{"code": "free", "max_documents": 10, "answers_per_day": 5}],
        })
        with patch.object(quota, "get_supabase_client", return_value=client):
            plan = quota.get_effective_plan("uid-1")
        self.assertIsNotNone(plan)
        self.assertEqual(plan.code, "free")
        self.assertEqual(plan.max_documents, 10)
        self.assertEqual(plan.answers_per_day, 5)

    def test_active_pro_subscription(self) -> None:
        from app.services import quota

        client = _table_client({
            "subscriptions": [{"plan_code": "pro", "status": "active"}],
            "plans": [{"code": "pro", "max_documents": 200, "answers_per_day": 50}],
        })
        with patch.object(quota, "get_supabase_client", return_value=client):
            plan = quota.get_effective_plan("uid-1")
        self.assertEqual(plan.code, "pro")

    def test_past_due_still_effective(self) -> None:
        # W5-6 grace period 예약 — past_due 는 아직 유효 플랜.
        from app.services import quota

        client = _table_client({
            "subscriptions": [{"plan_code": "pro", "status": "past_due"}],
            "plans": [{"code": "pro", "max_documents": 200, "answers_per_day": 50}],
        })
        with patch.object(quota, "get_supabase_client", return_value=client):
            self.assertEqual(quota.get_effective_plan("uid-1").code, "pro")

    def test_canceled_falls_back_to_free(self) -> None:
        from app.services import quota

        client = _table_client({
            "subscriptions": [{"plan_code": "pro", "status": "canceled"}],
            "plans": [{"code": "free", "max_documents": 10, "answers_per_day": 5}],
        })
        with patch.object(quota, "get_supabase_client", return_value=client):
            self.assertEqual(quota.get_effective_plan("uid-1").code, "free")

    def test_db_error_fails_open_none(self) -> None:
        from app.services import quota

        client = MagicMock()
        client.table.side_effect = RuntimeError("db down")
        with patch.object(quota, "get_supabase_client", return_value=client):
            self.assertIsNone(quota.get_effective_plan("uid-1"))

    def test_missing_plan_row_returns_none(self) -> None:
        from app.services import quota

        client = _table_client({"subscriptions": [], "plans": []})
        with patch.object(quota, "get_supabase_client", return_value=client):
            self.assertIsNone(quota.get_effective_plan("uid-1"))


class CountActiveDocumentsTest(unittest.TestCase):
    def test_returns_count(self) -> None:
        # resp.count(=5) 와 len(resp.data)(=1) 를 다르게 두어, 구현이 resp.count 를
        # 읽는지 실증 — limit(1) 로 data 는 잘려도 count="exact" 는 전체 건수.
        from app.services import quota

        client = MagicMock()
        t = MagicMock()
        resp = MagicMock()
        resp.data = [{"id": "a"}]
        resp.count = 5
        t.select.return_value = t
        t.eq.return_value = t
        t.is_.return_value = t
        t.limit.return_value = t
        t.execute.return_value = resp
        client.table.return_value = t
        with patch.object(quota, "get_supabase_client", return_value=client):
            self.assertEqual(quota.count_active_documents("uid-1"), 5)

    def test_db_error_returns_none(self) -> None:
        from app.services import quota

        client = MagicMock()
        client.table.side_effect = RuntimeError("db down")
        with patch.object(quota, "get_supabase_client", return_value=client):
            self.assertIsNone(quota.count_active_documents("uid-1"))


class GetTodaysCountTest(unittest.TestCase):
    def test_returns_zero_when_no_row(self) -> None:
        from app.services import quota

        client = _table_client({"usage_counters": []})
        with patch.object(quota, "get_supabase_client", return_value=client):
            self.assertEqual(quota.get_todays_count("uid-1", "answers"), 0)

    def test_returns_count_value(self) -> None:
        from app.services import quota

        client = _table_client({"usage_counters": [{"count": 7}]})
        with patch.object(quota, "get_supabase_client", return_value=client):
            self.assertEqual(quota.get_todays_count("uid-1", "answers"), 7)

    def test_db_error_returns_zero(self) -> None:
        from app.services import quota

        client = MagicMock()
        client.table.side_effect = RuntimeError("db down")
        with patch.object(quota, "get_supabase_client", return_value=client):
            self.assertEqual(quota.get_todays_count("uid-1", "answers"), 0)


if __name__ == "__main__":
    unittest.main()
