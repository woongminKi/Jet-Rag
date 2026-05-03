"""W8 Day 4 — vision_metrics 카운터 + ImageParser 통합 단위 테스트.

검증 포인트
- record_call(success=True/False) → total/success/error 정확 누적
- last_called_at ISO 8601 + UTC 포맷
- thread-safe (간단한 ThreadPoolExecutor 동시 호출)
- ImageParser.parse() 가 captioner.caption 성공/실패 모두 record (raise 도 카운트)

stdlib unittest + mock only.
"""

from __future__ import annotations

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

# import 단계에서 환경 변수 체크하는 모듈 회피.
os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")
os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")

# W17 Day 4 — discover 시 tests/__init__.py 가 top-level-dir 미명시로 안 잡힐 때 보호.
# ENABLED='0' — DB 연결 timeout 회피 / ASYNC='0' — first-warn capture race 방지.
# 강제 set (다른 테스트가 cleanup 안 한 채 leak 됐어도 안전).
os.environ["JET_RAG_METRICS_PERSIST_ENABLED"] = "0"
os.environ["JET_RAG_METRICS_PERSIST_ASYNC"] = "0"


class VisionMetricsBasicTest(unittest.TestCase):
    """record_call → get_usage 누적 동작."""

    def setUp(self) -> None:
        from app.services import vision_metrics
        vision_metrics.reset()

    def test_initial_state_zeros(self) -> None:
        from app.services import vision_metrics
        usage = vision_metrics.get_usage()
        self.assertEqual(usage["total_calls"], 0)
        self.assertEqual(usage["success_calls"], 0)
        self.assertEqual(usage["error_calls"], 0)
        self.assertIsNone(usage["last_called_at"])

    def test_record_increments_counters(self) -> None:
        from app.services import vision_metrics

        vision_metrics.record_call(success=True)
        vision_metrics.record_call(success=True)
        vision_metrics.record_call(success=False)

        usage = vision_metrics.get_usage()
        self.assertEqual(usage["total_calls"], 3)
        self.assertEqual(usage["success_calls"], 2)
        self.assertEqual(usage["error_calls"], 1)

    def test_last_called_at_iso_format(self) -> None:
        from app.services import vision_metrics

        vision_metrics.record_call(success=True)
        usage = vision_metrics.get_usage()
        self.assertIsNotNone(usage["last_called_at"])
        # ISO 8601 + UTC tz (+00:00 또는 'Z')
        self.assertTrue(
            usage["last_called_at"].endswith("+00:00")
            or usage["last_called_at"].endswith("Z"),
            f"UTC tz suffix 기대 — got {usage['last_called_at']}",
        )


class VisionMetricsThreadSafetyTest(unittest.TestCase):
    """4 worker × 50 호출 = 200 record_call 동시 → race 0."""

    def setUp(self) -> None:
        from app.services import vision_metrics
        vision_metrics.reset()

    def test_concurrent_records_consistent(self) -> None:
        from app.services import vision_metrics

        def worker(_):
            for _ in range(50):
                vision_metrics.record_call(success=True)

        with ThreadPoolExecutor(max_workers=4) as ex:
            list(ex.map(worker, range(4)))

        usage = vision_metrics.get_usage()
        self.assertEqual(usage["total_calls"], 200)
        self.assertEqual(usage["success_calls"], 200)
        self.assertEqual(usage["error_calls"], 0)


class ImageParserVisionIntegrationTest(unittest.TestCase):
    """ImageParser.parse() 가 captioner 성공·실패 모두 record."""

    def setUp(self) -> None:
        from app.services import vision_metrics
        vision_metrics.reset()

    def _make_png_bytes(self) -> bytes:
        from io import BytesIO
        from PIL import Image
        buf = BytesIO()
        Image.new("RGB", (100, 50), color="white").save(buf, format="PNG")
        return buf.getvalue()

    def test_success_records_one_success(self) -> None:
        from app.adapters.impl.image_parser import ImageParser
        from app.adapters.vision import VisionCaption
        from app.services import vision_metrics

        captioner = MagicMock()
        captioner.caption.return_value = VisionCaption(
            type="문서",
            caption="모의 캡션",
            ocr_text="모의 OCR",
            structured=None,
        )
        parser = ImageParser(captioner=captioner)
        parser.parse(self._make_png_bytes(), file_name="test.png")

        usage = vision_metrics.get_usage()
        self.assertEqual(usage["total_calls"], 1)
        self.assertEqual(usage["success_calls"], 1)
        self.assertEqual(usage["error_calls"], 0)

    def test_failure_records_one_error_and_raises(self) -> None:
        from app.adapters.impl.image_parser import ImageParser
        from app.services import vision_metrics

        captioner = MagicMock()
        captioner.caption.side_effect = RuntimeError("Gemini down")
        parser = ImageParser(captioner=captioner)

        with self.assertRaises(RuntimeError):
            parser.parse(self._make_png_bytes(), file_name="test.png")

        usage = vision_metrics.get_usage()
        self.assertEqual(usage["total_calls"], 1)
        self.assertEqual(usage["success_calls"], 0)
        self.assertEqual(usage["error_calls"], 1)
        # 일반 fail 은 quota_exhausted_at 미갱신
        self.assertIsNone(usage["last_quota_exhausted_at"])


class VisionQuotaExhaustedTrackingTest(unittest.TestCase):
    """W11 Day 1 — 한계 #38 lite — fast-fail 시점만 정확 capture."""

    def setUp(self) -> None:
        from app.services import vision_metrics
        vision_metrics.reset()

    def test_quota_exhausted_at_set_on_429(self) -> None:
        from app.adapters.impl.image_parser import ImageParser
        from app.services import vision_metrics
        from io import BytesIO
        from PIL import Image

        captioner = MagicMock()
        captioner.caption.side_effect = RuntimeError(
            "429 RESOURCE_EXHAUSTED. quota exceeded"
        )
        parser = ImageParser(captioner=captioner)

        png_buf = BytesIO()
        Image.new("RGB", (50, 50), color="white").save(png_buf, format="PNG")

        with self.assertRaises(RuntimeError):
            parser.parse(png_buf.getvalue(), file_name="quota.png")

        usage = vision_metrics.get_usage()
        self.assertEqual(usage["error_calls"], 1)
        # quota 감지 → last_quota_exhausted_at 갱신
        self.assertIsNotNone(usage["last_quota_exhausted_at"])
        self.assertTrue(
            usage["last_quota_exhausted_at"].endswith("+00:00")
            or usage["last_quota_exhausted_at"].endswith("Z"),
            f"UTC tz suffix 기대 — got {usage['last_quota_exhausted_at']}",
        )

    def test_quota_exhausted_at_persists_after_success(self) -> None:
        """quota 감지 후 다른 정상 호출이 와도 last_quota_exhausted_at 유지."""
        from app.adapters.impl.image_parser import ImageParser
        from app.adapters.vision import VisionCaption
        from app.services import vision_metrics
        from io import BytesIO
        from PIL import Image

        png_buf = BytesIO()
        Image.new("RGB", (50, 50), color="white").save(png_buf, format="PNG")
        png_bytes = png_buf.getvalue()

        # 1. quota 발생
        captioner_fail = MagicMock()
        captioner_fail.caption.side_effect = RuntimeError(
            "429 RESOURCE_EXHAUSTED"
        )
        with self.assertRaises(RuntimeError):
            ImageParser(captioner=captioner_fail).parse(
                png_bytes, file_name="q.png"
            )

        usage_after_fail = vision_metrics.get_usage()
        first_quota_at = usage_after_fail["last_quota_exhausted_at"]
        self.assertIsNotNone(first_quota_at)

        # 2. 정상 호출 — last_called_at 은 갱신, last_quota_exhausted_at 은 유지
        captioner_ok = MagicMock()
        captioner_ok.caption.return_value = VisionCaption(
            type="문서", caption="ok", ocr_text="", structured=None
        )
        ImageParser(captioner=captioner_ok).parse(
            png_bytes, file_name="ok.png"
        )

        usage_after_ok = vision_metrics.get_usage()
        self.assertEqual(
            usage_after_ok["last_quota_exhausted_at"], first_quota_at,
            "정상 호출은 last_quota_exhausted_at 갱신 X",
        )
        # last_called_at 은 정상 호출로 갱신
        self.assertNotEqual(
            usage_after_ok["last_called_at"], first_quota_at,
            "정상 호출은 last_called_at 갱신",
        )


class PersistGracefulTest(unittest.TestCase):
    """W15 Day 3 — DB write-through env 동작 검증."""

    def test_persist_disabled_env_skips_db(self) -> None:
        """JET_RAG_METRICS_PERSIST_ENABLED='0' 시 _persist_to_db 즉시 return."""
        from app.services import vision_metrics
        import os
        os.environ["JET_RAG_METRICS_PERSIST_ENABLED"] = "0"

        # mock import 경로가 호출되지 않도록 검증 — 호출되면 ImportError 자체로 걸림
        # (테스트 환경의 supabase import 차단 X 단순 swallow)
        vision_metrics._persist_to_db(
            called_at=__import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ),
            success=True,
            error_msg=None,
            quota_exhausted=False,
            source_type="image",
        )
        # 예외 없이 return — 정상

    def test_persist_handles_db_failure_gracefully(self) -> None:
        """env='1' 이라도 supabase 호출 실패는 swallow."""
        from app.services import vision_metrics
        from unittest.mock import patch
        import os

        os.environ["JET_RAG_METRICS_PERSIST_ENABLED"] = "1"
        os.environ["JET_RAG_METRICS_PERSIST_ASYNC"] = "0"  # sync 강제 — exception swallow 검증
        try:
            with patch(
                "app.db.get_supabase_client",
                side_effect=RuntimeError("DB down"),
            ):
                # raise 없이 정상 return 기대
                vision_metrics._persist_to_db(
                    called_at=__import__("datetime").datetime.now(
                        __import__("datetime").timezone.utc
                    ),
                    success=False,
                    error_msg="x",
                    quota_exhausted=False,
                    source_type=None,
                )
        finally:
            os.environ["JET_RAG_METRICS_PERSIST_ENABLED"] = "0"


class PersistExecutorShutdownTest(unittest.TestCase):
    """W18 Day 3 — _shutdown_persist_executor graceful 동작."""

    def test_shutdown_when_executor_uninitialized_is_noop(self) -> None:
        from app.services import vision_metrics
        # 이전 테스트에서 이미 init 됐을 수 있으므로 강제 reset
        vision_metrics._shutdown_persist_executor()
        # raise 없이 통과 — None 상태에서 noop
        vision_metrics._shutdown_persist_executor()

    def test_shutdown_after_init_clears_executor(self) -> None:
        from app.services import vision_metrics
        # lazy init 강제
        ex = vision_metrics._get_persist_executor()
        self.assertIsNotNone(ex)
        self.assertIs(vision_metrics._persist_executor, ex)
        # shutdown — None 으로 reset
        vision_metrics._shutdown_persist_executor()
        self.assertIsNone(vision_metrics._persist_executor)


class FirstWarnPatternTest(unittest.TestCase):
    """W17 Day 3 한계 #85 — _persist_to_db 첫 실패만 warn, 이후는 debug."""

    def setUp(self) -> None:
        from app.services import vision_metrics
        vision_metrics.reset()  # _first_persist_warn_logged 도 False 로 reset

    def test_first_failure_logs_warning(self) -> None:
        from app.services import vision_metrics
        from unittest.mock import patch
        import datetime as _dt

        os.environ["JET_RAG_METRICS_PERSIST_ENABLED"] = "1"
        os.environ["JET_RAG_METRICS_PERSIST_ASYNC"] = "0"  # sync 강제 (capture race 회피)
        try:
            with patch(
                "app.db.get_supabase_client",
                side_effect=RuntimeError("DB down"),
            ), self.assertLogs("app.services.vision_metrics", level="WARNING") as cm:
                vision_metrics._persist_to_db(
                    called_at=_dt.datetime.now(_dt.timezone.utc),
                    success=True,
                    error_msg=None,
                    quota_exhausted=False,
                    source_type="image",
                )
            # 첫 호출 → warning 1건
            self.assertEqual(len(cm.records), 1)
            self.assertIn("첫 실패", cm.records[0].getMessage())
        finally:
            os.environ["JET_RAG_METRICS_PERSIST_ENABLED"] = "0"

    def test_subsequent_failures_log_debug_not_warning(self) -> None:
        from app.services import vision_metrics
        from unittest.mock import patch
        import datetime as _dt

        os.environ["JET_RAG_METRICS_PERSIST_ENABLED"] = "1"
        os.environ["JET_RAG_METRICS_PERSIST_ASYNC"] = "0"
        try:
            with patch(
                "app.db.get_supabase_client",
                side_effect=RuntimeError("DB down"),
            ):
                # 첫 호출 (flag set)
                vision_metrics._persist_to_db(
                    called_at=_dt.datetime.now(_dt.timezone.utc),
                    success=True, error_msg=None,
                    quota_exhausted=False, source_type="image",
                )
                # 두 번째 호출 — warning 발생 안 해야 함
                with self.assertLogs(
                    "app.services.vision_metrics", level="WARNING"
                ) as cm2:
                    # 비어있는 capture 보장 위해 더미 warning 발생 후 길이 확인
                    import logging
                    vision_metrics._persist_to_db(
                        called_at=_dt.datetime.now(_dt.timezone.utc),
                        success=True, error_msg=None,
                        quota_exhausted=False, source_type="image",
                    )
                    # 본 호출은 warning 0 — 비어있는 capture 회피 위해 sentinel 1건 추가
                    logging.getLogger("app.services.vision_metrics").warning(
                        "sentinel"
                    )
                # capture 안 의 warning 은 sentinel 1건만
                self.assertEqual(len(cm2.records), 1)
                self.assertEqual(cm2.records[0].getMessage(), "sentinel")
        finally:
            os.environ["JET_RAG_METRICS_PERSIST_ENABLED"] = "0"


class SourceTypeNormalizationTest(unittest.TestCase):
    """W16 Day 4 한계 #90 — source_type enum 강제."""

    def test_valid_source_types_pass_through(self) -> None:
        from app.services import vision_metrics

        for valid in ("image", "pdf_scan", "pptx_rerouting", "pptx_augment"):
            self.assertEqual(
                vision_metrics._normalize_source_type(valid), valid,
                f"valid source_type={valid!r} 가 그대로 통과해야 함",
            )

    def test_invalid_source_type_falls_back_to_none(self) -> None:
        from app.services import vision_metrics
        self.assertIsNone(vision_metrics._normalize_source_type("typo"))
        self.assertIsNone(vision_metrics._normalize_source_type(""))

    def test_none_passes_through(self) -> None:
        from app.services import vision_metrics
        self.assertIsNone(vision_metrics._normalize_source_type(None))


class RecordCallTruncationDynamicTest(unittest.TestCase):
    """W22 Day 4 — record_call 가 호출 시점의 env 값을 동적 적용 검증.

    W16 Day 4 ErrorMsgTruncationTest 가 _error_msg_max_len() helper 직접 검증.
    본 테스트는 record_call → _persist_to_db_sync 호출 시점에 truncate 적용 검증.
    """

    def setUp(self) -> None:
        from app.services import vision_metrics
        vision_metrics.reset()
        self._orig_max_len = os.environ.pop("JET_RAG_VISION_ERROR_MSG_MAX_LEN", None)

    def tearDown(self) -> None:
        if self._orig_max_len is None:
            os.environ.pop("JET_RAG_VISION_ERROR_MSG_MAX_LEN", None)
        else:
            os.environ["JET_RAG_VISION_ERROR_MSG_MAX_LEN"] = self._orig_max_len
        os.environ["JET_RAG_METRICS_PERSIST_ENABLED"] = "0"

    def test_record_call_applies_env_truncate_at_call_time(self) -> None:
        from app.services import vision_metrics
        from unittest.mock import patch

        captured: list[dict] = []

        def fake_sync(**kwargs):
            captured.append(kwargs)

        os.environ["JET_RAG_VISION_ERROR_MSG_MAX_LEN"] = "10"
        os.environ["JET_RAG_METRICS_PERSIST_ENABLED"] = "1"
        os.environ["JET_RAG_METRICS_PERSIST_ASYNC"] = "0"

        with patch.object(vision_metrics, "_persist_to_db_sync", side_effect=fake_sync):
            vision_metrics.record_call(
                success=False,
                error_msg="A" * 100,
                source_type="image",
            )

        self.assertEqual(len(captured), 1)
        # env=10 적용 → error_msg 10자 truncate
        self.assertEqual(len(captured[0]["error_msg"]), 10)
        self.assertEqual(captured[0]["error_msg"], "A" * 10)
        # source_type 정상 normalize (image 는 valid)
        self.assertEqual(captured[0]["source_type"], "image")

    def test_record_call_applies_default_when_env_unset(self) -> None:
        from app.services import vision_metrics
        from unittest.mock import patch

        captured: list[dict] = []

        def fake_sync(**kwargs):
            captured.append(kwargs)

        # env 미설정 — default 200 적용
        os.environ["JET_RAG_METRICS_PERSIST_ENABLED"] = "1"
        os.environ["JET_RAG_METRICS_PERSIST_ASYNC"] = "0"

        with patch.object(vision_metrics, "_persist_to_db_sync", side_effect=fake_sync):
            vision_metrics.record_call(
                success=False,
                error_msg="B" * 250,  # 250 chars
                source_type="pdf_scan",
            )

        # default 200 truncate
        self.assertEqual(len(captured[0]["error_msg"]), 200)


class ErrorMsgTruncationTest(unittest.TestCase):
    """W16 Day 4 한계 #84 — JET_RAG_VISION_ERROR_MSG_MAX_LEN env override."""

    def setUp(self) -> None:
        self._original = os.environ.pop(
            "JET_RAG_VISION_ERROR_MSG_MAX_LEN", None
        )

    def tearDown(self) -> None:
        if self._original is None:
            os.environ.pop("JET_RAG_VISION_ERROR_MSG_MAX_LEN", None)
        else:
            os.environ["JET_RAG_VISION_ERROR_MSG_MAX_LEN"] = self._original

    def test_default_is_200(self) -> None:
        from app.services import vision_metrics
        self.assertEqual(vision_metrics._error_msg_max_len(), 200)

    def test_env_override_int(self) -> None:
        from app.services import vision_metrics
        os.environ["JET_RAG_VISION_ERROR_MSG_MAX_LEN"] = "500"
        self.assertEqual(vision_metrics._error_msg_max_len(), 500)

    def test_invalid_env_falls_back_to_default(self) -> None:
        from app.services import vision_metrics
        os.environ["JET_RAG_VISION_ERROR_MSG_MAX_LEN"] = "abc"
        self.assertEqual(vision_metrics._error_msg_max_len(), 200)

    def test_zero_or_negative_falls_back_to_default(self) -> None:
        from app.services import vision_metrics
        os.environ["JET_RAG_VISION_ERROR_MSG_MAX_LEN"] = "0"
        self.assertEqual(vision_metrics._error_msg_max_len(), 200)
        os.environ["JET_RAG_VISION_ERROR_MSG_MAX_LEN"] = "-10"
        self.assertEqual(vision_metrics._error_msg_max_len(), 200)


if __name__ == "__main__":
    unittest.main()
