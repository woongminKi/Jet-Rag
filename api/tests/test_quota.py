"""W9 Day 7 — `app.services.quota.is_quota_exhausted` 감지 정확도 단위 테스트.

배경
- W9 Day 4·6 의 fast-fail 은 메시지 휴리스틱 ("RESOURCE_EXHAUSTED" / "429" / "QUOTA").
- 한계 #50: SDK 메시지 형식 변경 시 false negative 가능 → class-based catch 보강.
- 본 테스트는 class name 화이트리스트 + status code attribute + 메시지 fallback 3 단계 검증.

stdlib unittest only.
"""

from __future__ import annotations

import unittest


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


if __name__ == "__main__":
    unittest.main()
