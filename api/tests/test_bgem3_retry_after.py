"""`_parse_retry_after` + `_with_retry` 의 Retry-After 존중 단위 테스트.

대상: `app.adapters.impl.bgem3_hf_embedding` 와 `app.adapters.impl.bge_reranker_hf`
(두 파일이 동일 `_parse_retry_after` 를 복사 보유 — 정책 일관성 확인 포함).

검증 범위
- delta-seconds 정수 헤더 → 그 값(초) 반환
- 헤더 없음 / HTTPStatusError 아님 → None (caller 가 지수 백오프)
- 음수·0·비숫자 garbage → None
- 상한 클램프 (_MAX_RETRY_AFTER_SECONDS = 60)
- HTTP-date 형식 → 현재시각과의 양수 차 (과거 날짜는 None)
- `_with_retry` — Retry-After 있는 503 두 번 후 성공: time.sleep 이 헤더값(+jitter)으로 호출됨
- reranker `_parse_retry_after` 도 동일 동작 (복붙 일관성)

HF 실 호출 0 — httpx.Response 픽스처 + time.sleep mock.
실행: `python -m unittest tests.test_bgem3_retry_after`
"""

from __future__ import annotations

import os
import unittest
from email.utils import format_datetime
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx

from app.adapters.impl import bgem3_hf_embedding as bgem3
from app.adapters.impl import bge_reranker_hf as reranker


def _status_error(status_code: int, *, headers: dict[str, str] | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.invalid/x")
    response = httpx.Response(
        status_code, request=request, content=b"{}", headers=headers or {}
    )
    return httpx.HTTPStatusError(
        f"{status_code} test", request=request, response=response
    )


class ParseRetryAfterTest(unittest.TestCase):
    """`bgem3._parse_retry_after` 의 입력별 동작."""

    def test_integer_delta_seconds(self) -> None:
        exc = _status_error(503, headers={"Retry-After": "12"})
        self.assertEqual(bgem3._parse_retry_after(exc), 12.0)

    def test_none_when_header_absent(self) -> None:
        exc = _status_error(503)
        self.assertIsNone(bgem3._parse_retry_after(exc))

    def test_none_when_not_http_status_error(self) -> None:
        self.assertIsNone(bgem3._parse_retry_after(httpx.ConnectError("dns")))
        self.assertIsNone(bgem3._parse_retry_after(RuntimeError("x")))

    def test_none_for_zero_negative_or_garbage(self) -> None:
        for raw in ("0", "-5", "soon", ""):
            with self.subTest(raw=raw):
                exc = _status_error(429, headers={"Retry-After": raw})
                self.assertIsNone(bgem3._parse_retry_after(exc))

    def test_clamped_to_max(self) -> None:
        exc = _status_error(503, headers={"Retry-After": "99999"})
        self.assertEqual(
            bgem3._parse_retry_after(exc), bgem3._MAX_RETRY_AFTER_SECONDS
        )

    def test_http_date_future_returns_positive_delta(self) -> None:
        future = datetime.now(timezone.utc) + timedelta(seconds=30)
        exc = _status_error(503, headers={"Retry-After": format_datetime(future)})
        delta = bgem3._parse_retry_after(exc)
        self.assertIsNotNone(delta)
        # 실행 지연 고려해 느슨하게 — (0, max] 범위 + 대략 30s 근방.
        assert delta is not None
        self.assertGreater(delta, 0.0)
        self.assertLessEqual(delta, bgem3._MAX_RETRY_AFTER_SECONDS)
        self.assertLess(abs(delta - 30.0), 5.0)

    def test_http_date_past_returns_none(self) -> None:
        past = datetime.now(timezone.utc) - timedelta(seconds=60)
        exc = _status_error(503, headers={"Retry-After": format_datetime(past)})
        self.assertIsNone(bgem3._parse_retry_after(exc))


class WithRetryHonorsRetryAfterTest(unittest.TestCase):
    """`bgem3._with_retry` — Retry-After 헤더가 sleep 시간으로 반영되는지."""

    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")

    def test_sleep_uses_retry_after_value(self) -> None:
        """503 + Retry-After: 7 두 번 → 세 번째 성공. sleep 호출 인자 ≈ 7 (+jitter < 1)."""
        attempts = {"n": 0}

        def flaky() -> str:
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise _status_error(503, headers={"Retry-After": "7"})
            return "ok"

        with patch.object(bgem3.time, "sleep") as sleep_mock:
            result = bgem3._with_retry(flaky, label="test")

        self.assertEqual(result, "ok")
        self.assertEqual(attempts["n"], 3)
        self.assertEqual(sleep_mock.call_count, 2)
        for call in sleep_mock.call_args_list:
            delay = call.args[0]
            self.assertGreaterEqual(delay, 7.0)
            self.assertLess(delay, 8.0)  # jitter [0,1)

    def test_falls_back_to_exponential_without_header(self) -> None:
        """Retry-After 없으면 기존 지수 백오프 — 첫 sleep ≈ BASE (+jitter)."""
        attempts = {"n": 0}

        def flaky() -> str:
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise _status_error(503)  # 헤더 없음
            return "ok"

        with patch.object(bgem3.time, "sleep") as sleep_mock:
            result = bgem3._with_retry(flaky, label="test")

        self.assertEqual(result, "ok")
        self.assertEqual(sleep_mock.call_count, 1)
        first_delay = sleep_mock.call_args_list[0].args[0]
        self.assertGreaterEqual(first_delay, bgem3._BASE_BACKOFF_SECONDS)
        self.assertLess(first_delay, bgem3._BASE_BACKOFF_SECONDS + 1.0)


class RerankerParseRetryAfterParityTest(unittest.TestCase):
    """reranker 의 복사본 `_parse_retry_after` 가 bgem3 와 동일 결과를 내는지 (복붙 일관성)."""

    def test_integer_and_clamp_and_garbage_parity(self) -> None:
        cases = [
            _status_error(503, headers={"Retry-After": "9"}),
            _status_error(503, headers={"Retry-After": "99999"}),
            _status_error(429, headers={"Retry-After": "garbage"}),
            _status_error(503),
        ]
        for exc in cases:
            with self.subTest(headers=dict(exc.response.headers)):
                self.assertEqual(
                    reranker._parse_retry_after(exc),
                    bgem3._parse_retry_after(exc),
                )

    def test_reranker_with_retry_uses_header(self) -> None:
        attempts = {"n": 0}

        def flaky() -> str:
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise _status_error(429, headers={"Retry-After": "3"})
            return "ok"

        with patch.object(reranker.time, "sleep") as sleep_mock:
            result = reranker._with_retry(flaky, label="test")

        self.assertEqual(result, "ok")
        self.assertEqual(sleep_mock.call_count, 1)
        delay = sleep_mock.call_args_list[0].args[0]
        self.assertGreaterEqual(delay, 3.0)
        self.assertLess(delay, 4.0)


if __name__ == "__main__":
    unittest.main()
