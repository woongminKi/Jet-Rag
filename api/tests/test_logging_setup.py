"""`app/logging_setup.py` 의 `configure_app_logging` 단위 테스트.

검증 범위
- 1회 호출 → `app` 로거에 `StreamHandler` 1개 + 레벨 INFO + `propagate is False`
- 2회 호출 → 핸들러 길이 1 유지 (멱등)
- 사전에 외부 핸들러가 붙어 있으면 → 완전 no-op (핸들러·레벨·propagate 불변)
- `app.*` 자식 로거의 INFO 가 `app` 핸들러 stream 으로 출력됨 (propagate=False 여도 자식 → app 전파)

테스트 격리 — 글로벌 logging 상태(`app` 로거 handlers/level/propagate)를 setUp 에서 백업,
addCleanup 으로 복원. 안 하면 후속 테스트 오염 (특히 다른 테스트가 `app.*` 로그를 캡처할 때).

실행: `python -m unittest tests.test_logging_setup`
"""

from __future__ import annotations

import io
import logging
import unittest

from app.logging_setup import configure_app_logging

_APP_LOGGER = "app"


class ConfigureAppLoggingTest(unittest.TestCase):
    """`configure_app_logging` — 멱등 부착 / 외부 설정 존중 / 자식 로그 캡처."""

    def setUp(self) -> None:
        app_logger = logging.getLogger(_APP_LOGGER)
        # 글로벌 상태 백업 — list 는 copy 로 (clear/append 가 원본을 망가뜨리지 않게).
        saved_handlers = list(app_logger.handlers)
        saved_level = app_logger.level
        saved_propagate = app_logger.propagate

        def _restore() -> None:
            app_logger.handlers[:] = saved_handlers
            app_logger.setLevel(saved_level)
            app_logger.propagate = saved_propagate

        self.addCleanup(_restore)

        # 깨끗한 출발점 — 다른 테스트가 이미 부착했을 수 있으니 초기화.
        app_logger.handlers[:] = []
        app_logger.setLevel(logging.NOTSET)
        app_logger.propagate = True

    def test_attaches_single_stream_handler(self) -> None:
        configure_app_logging()

        app_logger = logging.getLogger(_APP_LOGGER)
        self.assertEqual(len(app_logger.handlers), 1)
        self.assertIsInstance(app_logger.handlers[0], logging.StreamHandler)
        self.assertEqual(app_logger.level, logging.INFO)
        self.assertFalse(app_logger.propagate)

    def test_idempotent_on_repeat_calls(self) -> None:
        configure_app_logging()
        configure_app_logging()

        self.assertEqual(len(logging.getLogger(_APP_LOGGER).handlers), 1)

    def test_respects_existing_external_handler(self) -> None:
        """이미 핸들러가 있으면 no-op — 레벨·propagate 도 안 건드린다 (`--log-config` 존중)."""
        app_logger = logging.getLogger(_APP_LOGGER)
        external = logging.NullHandler()
        app_logger.addHandler(external)
        app_logger.setLevel(logging.WARNING)
        app_logger.propagate = True

        configure_app_logging()

        self.assertEqual(app_logger.handlers, [external])
        self.assertEqual(app_logger.level, logging.WARNING)
        self.assertTrue(app_logger.propagate)

    def test_child_logger_message_reaches_app_handler(self) -> None:
        """`app.main` INFO 가 `app` 로거 핸들러 stream 으로 출력된다."""
        configure_app_logging()
        app_logger = logging.getLogger(_APP_LOGGER)

        buffer = io.StringIO()
        app_logger.handlers[0].setStream(buffer)  # type: ignore[union-attr]
        logging.getLogger("app.main").info("hello-from-child")

        self.assertIn("hello-from-child", buffer.getvalue())
        self.assertIn("INFO", buffer.getvalue())

    def test_assert_logs_still_works_with_propagate_false(self) -> None:
        """propagate=False + 영구 핸들러가 있어도 `assertLogs` 는 정상 동작."""
        configure_app_logging()

        with self.assertLogs("app.main", level="INFO") as captured:
            logging.getLogger("app.main").info("captured-line")

        self.assertTrue(any("captured-line" in m for m in captured.output))


if __name__ == "__main__":
    unittest.main()
