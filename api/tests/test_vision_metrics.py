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


if __name__ == "__main__":
    unittest.main()
