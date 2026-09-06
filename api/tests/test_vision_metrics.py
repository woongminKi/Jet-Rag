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
from datetime import datetime, time, timedelta, timezone
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

        for valid in ("image", "pdf_scan", "pptx_rerouting", "pptx_augment", "pdf_vision_enrich"):
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


# 이 파일은 상단 env 설정 뒤에 import 하는 게 규칙이라, 아래 클래스 전용으로 여기서 한 번만
# 가져온다 (매 메서드에서 반복 import 하지 않도록).
from app.services import vision_metrics  # noqa: E402


class VisionUsageSourceTest(unittest.TestCase):
    """2026-09-06 Edge 이관 — 사용량 출처(DB / in-memory) 전환.

    `/stats` 가 Edge 로 넘어가면 isolate 가 휘발성이라 in-memory 카운터가 영구히 0 이 된다.
    게다가 vision 호출은 인제스트(아직 Railway) 경로에서 나므로 다른 프로세스의 카운터를
    읽을 방법도 없다. 그래서 `vision_usage_log` 의 **오늘(KST)** 호출을 기본 출처로 삼았다.
    네트워크 없이 재려고 가짜 클라이언트를 끼워 넣는다.
    """

    def setUp(self) -> None:
        vision_metrics.reset()
        self._saved = {
            k: os.environ.get(k)
            for k in ("JETRAG_VISION_USAGE_SOURCE", "JET_RAG_METRICS_PERSIST_ENABLED")
        }

    def tearDown(self) -> None:
        vision_metrics.reset()
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # --- 출처 결정 규칙 -----------------------------------------------------

    def test_source_defaults_to_db_when_write_through_on(self) -> None:
        os.environ.pop("JETRAG_VISION_USAGE_SOURCE", None)
        os.environ["JET_RAG_METRICS_PERSIST_ENABLED"] = "1"
        self.assertEqual(vision_metrics._usage_source(), "db")

    def test_source_defaults_to_memory_when_write_through_off(self) -> None:
        # write-through 가 꺼져 있으면 DB 에 쌓일 리가 없다 → in-memory 가 유일한 소스.
        os.environ.pop("JETRAG_VISION_USAGE_SOURCE", None)
        os.environ["JET_RAG_METRICS_PERSIST_ENABLED"] = "0"
        self.assertEqual(vision_metrics._usage_source(), "memory")

    def test_explicit_env_wins(self) -> None:
        os.environ["JET_RAG_METRICS_PERSIST_ENABLED"] = "0"
        os.environ["JETRAG_VISION_USAGE_SOURCE"] = "db"
        self.assertEqual(vision_metrics._usage_source(), "db")
        os.environ["JETRAG_VISION_USAGE_SOURCE"] = "bogus"  # 모르는 값은 무시
        self.assertEqual(vision_metrics._usage_source(), "memory")

    # --- DB 경로 -----------------------------------------------------------

    def _install_fake_db(self, rows, raise_on_call=False):
        """`app.db.get_supabase_client` 를 가짜로 바꿔 끼운다.

        모듈이 **행을 세지 않고 `count` 질의를 쓴다**는 것까지 여기서 고정한다 —
        행을 받아 `len()` 하면 PostgREST 의 1,000 행 상한에서 조용히 과소 집계된다
        (2026-09-06 에 실제로 밟았다: 2,090 행짜리 창을 1,000 으로 셌다).
        """
        import app.db as app_db

        seen = {"count_queries": 0, "row_limits": [], "gte": []}

        class _Q:
            def __init__(self):
                self._eq = {}
                self._count = False
                self._limit = None
                self._gte = None

            def select(self, _cols, count=None):
                if count == "exact":
                    self._count = True
                    seen["count_queries"] += 1
                return self

            def gte(self, _col, value):
                # **실제로 적용한다.** no-op 으로 두면 "오늘로 제한한다" 는 계약이
                # 어디서도 검증되지 않는다 — 음성 대조에서 실제로 그 구멍을 발견했다.
                self._gte = value
                seen["gte"].append(value)
                return self

            def eq(self, k, v):
                self._eq[k] = v
                return self

            def order(self, *_a, **_k):
                return self

            def limit(self, n):
                self._limit = n
                if not self._count:
                    seen["row_limits"].append(n)
                return self

            def _matching(self):
                out = [
                    r for r in rows
                    if all(r.get(k) == v for k, v in self._eq.items())
                ]
                if self._gte is not None:
                    # **시각으로 비교한다.** 문자열로 비교하면 하한은 KST(+09:00),
                    # 행은 UTC(+00:00) 라 어긋난다 — Postgres 는 오프셋을 해석해
                    # 시각으로 비교하므로 가짜도 그래야 한다.
                    bound = datetime.fromisoformat(self._gte)
                    out = [
                        r for r in out
                        if datetime.fromisoformat(r["called_at"]) >= bound
                    ]
                return out

            def execute(self):
                m = self._matching()
                if self._count:
                    return type("R", (), {"data": m[:1], "count": len(m)})()
                return type("R", (), {"data": m[: (self._limit or len(m))]})()

        class _C:
            def table(self, _name):
                return _Q()

        def _factory():
            if raise_on_call:
                raise RuntimeError("DB 없음")
            return _C()

        original = app_db.get_supabase_client
        app_db.get_supabase_client = _factory
        self.addCleanup(lambda: setattr(app_db, "get_supabase_client", original))
        return seen

    # **날짜를 박지 않는다.** 처음엔 `2026-09-06T05:00:00+00:00` 처럼 고정 값을 썼는데,
    # 집계 창이 "오늘(KST)" 이라 날짜가 바뀌는 순간 전부 창 밖으로 나가 0 건이 됐다
    # (2026-09-07 에 실제로 깨졌다). 시각은 **KST 오늘 자정 기준 상대**로 만든다.
    @staticmethod
    def _sample() -> list[dict]:
        kst = timezone(timedelta(hours=9))
        midnight = datetime.combine(datetime.now(kst).date(), time(0, 0), tzinfo=kst)
        at = lambda h: (midnight + timedelta(hours=h)).astimezone(timezone.utc).isoformat()
        return [
            {"called_at": at(5), "success": True, "quota_exhausted": False},
            {"called_at": at(4), "success": False, "quota_exhausted": True},
            {"called_at": at(3), "success": True, "quota_exhausted": False},
        ]

    @property
    def SAMPLE(self) -> list[dict]:
        return self._sample()

    def test_db_path_aggregates(self) -> None:
        sample = self._sample()
        seen = self._install_fake_db(sample)
        os.environ["JETRAG_VISION_USAGE_SOURCE"] = "db"
        u = vision_metrics.get_usage()
        self.assertEqual(u["source"], "db")
        self.assertEqual(u["total_calls"], 3)
        self.assertEqual(u["success_calls"], 2)
        self.assertEqual(u["error_calls"], 1)
        self.assertEqual(u["last_called_at"], sample[0]["called_at"])
        self.assertEqual(u["last_quota_exhausted_at"], sample[1]["called_at"])

    def test_counts_come_from_count_queries_not_row_length(self) -> None:
        """행 길이로 세면 1,000 행 상한에서 조용히 틀린다 — count 질의를 쓰는지 고정."""
        seen = self._install_fake_db(self.SAMPLE)
        os.environ["JETRAG_VISION_USAGE_SOURCE"] = "db"
        vision_metrics.get_usage()
        self.assertEqual(seen["count_queries"], 2, "총계·성공 둘 다 count 질의여야 한다")
        # 최근 시각 조회는 1 행만 받아야 한다.
        self.assertTrue(seen["row_limits"], "최근 시각 조회가 없다")
        self.assertTrue(
            all(n == 1 for n in seen["row_limits"]),
            f"행 조회가 1 행 초과: {seen['row_limits']}",
        )

    @staticmethod
    def _kst_midnight_today():
        """**모듈을 안 쓰고** KST 자정을 직접 구한다.

        모듈의 `_today_start_kst()` 로 픽스처를 만들면 그 함수를 UTC 로 바꿔도 기대값이
        같이 움직여 검사가 통과한다 — 음성 대조에서 실제로 그 구멍을 발견했다.
        """
        kst = timezone(timedelta(hours=9))
        return datetime.now(kst).replace(hour=0, minute=0, second=0, microsecond=0)

    def test_window_is_today_kst_only(self) -> None:
        """어제 호출은 안 세야 한다 — 프론트 카드가 RPD(일일 한도) 대비로 그리기 때문."""
        today = self._kst_midnight_today()
        inside = today.replace(hour=0, minute=1).astimezone(timezone.utc).isoformat()
        outside = (today - timedelta(minutes=1)).astimezone(timezone.utc).isoformat()
        self._install_fake_db([
            {"called_at": inside, "success": True, "quota_exhausted": False},
            {"called_at": outside, "success": True, "quota_exhausted": True},
            {"called_at": outside, "success": False, "quota_exhausted": False},
        ])
        os.environ["JETRAG_VISION_USAGE_SOURCE"] = "db"
        u = vision_metrics.get_usage()
        self.assertEqual(u["total_calls"], 1, "어제 호출이 섞였다")
        self.assertEqual(u["error_calls"], 0)
        self.assertEqual(u["last_called_at"], inside)
        # 어제의 quota 소진은 오늘 창에 들어오면 안 된다.
        self.assertIsNone(u["last_quota_exhausted_at"])

    def test_every_query_is_bounded_by_the_window(self) -> None:
        """네 질의 전부에 하한이 붙어야 한다 — 하나라도 빠지면 전체 기간을 센다."""
        seen = self._install_fake_db(self.SAMPLE)
        os.environ["JETRAG_VISION_USAGE_SOURCE"] = "db"
        vision_metrics.get_usage()
        self.assertEqual(len(seen["gte"]), 4, f"하한 없는 질의가 있다: {seen['gte']}")
        bound = self._kst_midnight_today()
        for v in seen["gte"]:
            self.assertEqual(
                datetime.fromisoformat(v), bound,
                f"하한이 KST 자정이 아니다: {v}",
            )

    def test_quota_never_exhausted_is_none(self) -> None:
        self._install_fake_db([
            {"called_at": "2026-09-06T05:00:00+00:00", "success": True,
             "quota_exhausted": False},
        ])
        os.environ["JETRAG_VISION_USAGE_SOURCE"] = "db"
        u = vision_metrics.get_usage()
        self.assertIsNone(u["last_quota_exhausted_at"])

    def test_empty_window_is_not_a_fallback(self) -> None:
        """호출이 0 건인 것과 조회 실패는 다르다 — 전자는 그대로 `db` 다."""
        self._install_fake_db([])
        os.environ["JETRAG_VISION_USAGE_SOURCE"] = "db"
        u = vision_metrics.get_usage()
        self.assertEqual(u["source"], "db")
        self.assertEqual(u["total_calls"], 0)
        self.assertIsNone(u["last_called_at"])

    def test_db_failure_falls_back_to_memory_and_says_so(self) -> None:
        self._install_fake_db([], raise_on_call=True)
        vision_metrics.record_call(success=True)
        os.environ["JETRAG_VISION_USAGE_SOURCE"] = "db"
        u = vision_metrics.get_usage()
        self.assertEqual(u["source"], "memory_fallback")
        self.assertEqual(u["total_calls"], 1)
