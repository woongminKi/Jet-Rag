"""Phase 1 S0 D2 — `vision_page_cache` lookup / upsert + ImageParser 통합.

검증 포인트
- cache hit → ImageParser 가 captioner.caption 호출 0 (Vision API 절감)
- cache miss → captioner.caption 1회 호출 + vision_cache.upsert 호출
- prompt_version 변경 시 invalidate (다른 prompt_version 으로 lookup → None)
- DB 부재 (마이그 015 미적용) graceful — lookup None / upsert no-raise

stdlib unittest + mock only — Supabase 의존성 0.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

# 환경 변수 stub — 단위 테스트가 실 DB 접근 회피.
os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")
os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")
os.environ["JET_RAG_METRICS_PERSIST_ENABLED"] = "0"
os.environ["JET_RAG_METRICS_PERSIST_ASYNC"] = "0"


class VisionCacheLookupTest(unittest.TestCase):
    """sha256+page+prompt_version 키로 row 조회 → VisionCaption 복원."""

    def setUp(self) -> None:
        from app.services import vision_cache
        vision_cache._reset_first_warn_for_test()
        os.environ.pop("JETRAG_VISION_CACHE_ENABLED", None)
        os.environ.pop("JETRAG_VISION_PROMPT_VERSION", None)

    def test_lookup_hit_returns_vision_caption(self) -> None:
        from app.services import vision_cache

        client = MagicMock()
        # supabase-py: table().select().eq().eq().eq().limit().execute()
        chain = (
            client.table.return_value
            .select.return_value
            .eq.return_value
            .eq.return_value
            .eq.return_value
            .limit.return_value
        )
        chain.execute.return_value.data = [
            {
                "result": {
                    "type": "표",
                    "ocr_text": "셀1\t셀2",
                    "caption": "재무제표 표",
                    "structured": {"rows": 3},
                }
            }
        ]
        with patch("app.db.get_supabase_client", return_value=client):
            cap = vision_cache.lookup("abc" + "0" * 61, page=4)

        self.assertIsNotNone(cap)
        self.assertEqual(cap.type, "표")
        self.assertEqual(cap.ocr_text, "셀1\t셀2")
        self.assertEqual(cap.caption, "재무제표 표")
        self.assertEqual(cap.structured, {"rows": 3})

    def test_lookup_miss_returns_none(self) -> None:
        from app.services import vision_cache

        client = MagicMock()
        chain = (
            client.table.return_value
            .select.return_value
            .eq.return_value
            .eq.return_value
            .eq.return_value
            .limit.return_value
        )
        chain.execute.return_value.data = []
        with patch("app.db.get_supabase_client", return_value=client):
            cap = vision_cache.lookup("abc" + "0" * 61, page=4)
        self.assertIsNone(cap)

    def test_lookup_db_failure_returns_none_graceful(self) -> None:
        """마이그 015 미적용 시 DB 가 PGRST 에러 → None + warning 1회."""
        from app.services import vision_cache

        client = MagicMock()
        client.table.side_effect = RuntimeError("relation \"vision_page_cache\" does not exist")
        with patch("app.db.get_supabase_client", return_value=client):
            cap1 = vision_cache.lookup("a" * 64, page=1)
            cap2 = vision_cache.lookup("a" * 64, page=2)
        self.assertIsNone(cap1)
        self.assertIsNone(cap2)

    def test_lookup_disabled_via_env(self) -> None:
        """ENV='0' 시 DB 접근 0 — fast disable."""
        from app.services import vision_cache

        os.environ["JETRAG_VISION_CACHE_ENABLED"] = "0"
        try:
            client = MagicMock()
            with patch("app.db.get_supabase_client", return_value=client):
                cap = vision_cache.lookup("a" * 64, page=1)
            self.assertIsNone(cap)
            client.table.assert_not_called()
        finally:
            os.environ.pop("JETRAG_VISION_CACHE_ENABLED", None)

    def test_lookup_uses_current_prompt_version(self) -> None:
        """prompt_version eq 필터에 정확히 현재 상수가 들어가는지."""
        from app.services import vision_cache

        client = MagicMock()
        captured: list = []

        def eq_side_effect(col: str, val):
            captured.append((col, val))
            return chain  # noqa: F821

        chain = MagicMock()
        chain.eq.side_effect = eq_side_effect
        chain.limit.return_value.execute.return_value.data = []
        client.table.return_value.select.return_value = chain
        with patch("app.db.get_supabase_client", return_value=client):
            vision_cache.lookup("a" * 64, page=2)

        # 마지막 eq 호출이 prompt_version
        prompt_calls = [v for (c, v) in captured if c == "prompt_version"]
        self.assertEqual(prompt_calls, [vision_cache.get_prompt_version()])


class VisionCachePromptVersionInvalidateTest(unittest.TestCase):
    """prompt_version 변경 시 같은 (sha256, page) 라도 cache miss."""

    def setUp(self) -> None:
        from app.services import vision_cache
        vision_cache._reset_first_warn_for_test()

    def test_prompt_version_change_causes_miss(self) -> None:
        from app.services import vision_cache

        client = MagicMock()
        # DB 에는 v1 row 가 있음. v2 lookup 은 eq("prompt_version","v2") 매칭 → 빈 결과.
        # MagicMock 의 chain.execute().data = [] 로 빈 결과 강제.
        chain = (
            client.table.return_value
            .select.return_value
            .eq.return_value
            .eq.return_value
            .eq.return_value
            .limit.return_value
        )
        chain.execute.return_value.data = []  # v2 매칭 row 없음

        os.environ["JETRAG_VISION_PROMPT_VERSION"] = "v2"
        try:
            # 모듈 reload 없이도 함수가 ENV 직접 안 읽고 모듈 상수 사용 →
            # _VISION_PROMPT_VERSION 을 monkey-patch 해서 v2 강제.
            with patch.object(vision_cache, "_VISION_PROMPT_VERSION", "v2"):
                with patch("app.db.get_supabase_client", return_value=client):
                    cap = vision_cache.lookup("a" * 64, page=1)
            self.assertIsNone(cap)
            # eq 호출 중 prompt_version="v2" 가 정확히 들어갔는지 검증
            eq_calls = client.table.return_value.select.return_value.eq.call_args_list
            # 첫 인자에 'prompt_version' 들어간 호출이 있어야 함
            prompt_versions_used = [
                c.args[1] for c in (
                    client.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.call_args_list
                )
            ]
            self.assertIn("v2", prompt_versions_used)
        finally:
            os.environ.pop("JETRAG_VISION_PROMPT_VERSION", None)


class VisionCacheUpsertTest(unittest.TestCase):
    """upsert — ON CONFLICT DO NOTHING 동등 동작 + estimated_cost 보존."""

    def setUp(self) -> None:
        from app.services import vision_cache
        vision_cache._reset_first_warn_for_test()

    def test_upsert_calls_supabase_upsert_with_correct_row(self) -> None:
        from app.adapters.vision import VisionCaption
        from app.services import vision_cache

        cap = VisionCaption(
            type="표",
            ocr_text="셀A\t셀B",
            caption="비용 분석 표",
            structured={"cols": 2},
            usage={"estimated_cost": 0.00075},
        )
        client = MagicMock()
        with patch("app.db.get_supabase_client", return_value=client):
            vision_cache.upsert(
                "a" * 64,
                page=3,
                caption=cap,
                estimated_cost=0.00075,
            )

        client.table.assert_called_with("vision_page_cache")
        upsert_args = client.table.return_value.upsert.call_args
        row = upsert_args.args[0]
        self.assertEqual(row["sha256"], "a" * 64)
        self.assertEqual(row["page"], 3)
        self.assertEqual(row["prompt_version"], vision_cache.get_prompt_version())
        self.assertAlmostEqual(row["estimated_cost"], 0.00075)
        self.assertEqual(row["result"]["type"], "표")
        self.assertEqual(row["result"]["ocr_text"], "셀A\t셀B")
        self.assertEqual(row["result"]["caption"], "비용 분석 표")
        self.assertEqual(row["result"]["structured"], {"cols": 2})
        # ON CONFLICT DO NOTHING 동등 — ignore_duplicates=True
        kwargs = upsert_args.kwargs
        self.assertEqual(kwargs.get("on_conflict"), "sha256,page,prompt_version")
        self.assertTrue(kwargs.get("ignore_duplicates"))

    def test_upsert_db_failure_graceful(self) -> None:
        """DB 실패 시 raise 안 함 (호출자 영향 0)."""
        from app.adapters.vision import VisionCaption
        from app.services import vision_cache

        cap = VisionCaption(type="표", ocr_text="", caption="x", structured=None)
        client = MagicMock()
        client.table.side_effect = RuntimeError("relation does not exist")
        with patch("app.db.get_supabase_client", return_value=client):
            # raise 안 함
            vision_cache.upsert("a" * 64, page=1, caption=cap)


class ImageParserCacheIntegrationTest(unittest.TestCase):
    """ImageParser.parse — sha256/page 전달 시 cache lookup → hit 면 captioner skip."""

    def setUp(self) -> None:
        from app.services import vision_cache, vision_metrics
        vision_metrics.reset()
        vision_cache._reset_first_warn_for_test()

    def test_cache_hit_skips_captioner_call(self) -> None:
        """cache hit 시 captioner.caption 호출 0, vision_metrics 도 record 0."""
        from app.adapters.impl.image_parser import ImageParser
        from app.adapters.vision import VisionCaption
        from app.services import vision_cache, vision_metrics

        captioner = MagicMock()
        # 호출되면 안 됨 — 호출 시 강제 fail
        captioner.caption.side_effect = AssertionError("captioner.caption 호출되면 안 됨")
        parser = ImageParser(captioner=captioner)

        cached = VisionCaption(
            type="표",
            ocr_text="cached ocr",
            caption="cached caption",
            structured={"a": 1},
        )

        # 1x1 PNG bytes (가장 간단한 valid PNG)
        png_bytes = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xfa\xcf"
            b"\x00\x00\x00\x03\x00\x01\x9eY\xe2\xfa\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        with patch.object(vision_cache, "lookup", return_value=cached) as lookup_mock:
            with patch.object(vision_cache, "upsert") as upsert_mock:
                result = parser.parse(
                    png_bytes,
                    file_name="x.png",
                    source_type="pdf_vision_enrich",
                    sha256="a" * 64,
                    page=4,
                )

        # cache lookup 1회, captioner.caption 0회, upsert 0회
        lookup_mock.assert_called_once_with("a" * 64, 4)
        captioner.caption.assert_not_called()
        upsert_mock.assert_not_called()

        # vision_metrics 도 0 (parse 자체가 caption 호출 안 했으므로)
        self.assertEqual(vision_metrics.get_usage()["total_calls"], 0)

        # ExtractionResult 합성 검증 (cache hit 도 동일 sections 구조)
        self.assertEqual(result.metadata.get("vision_type"), "표")
        # caption + ocr_text 모두 sections 에 포함
        section_texts = [s.text for s in result.sections]
        self.assertTrue(any("cached caption" in t for t in section_texts))
        self.assertTrue(any("cached ocr" in t for t in section_texts))

    def test_cache_miss_calls_captioner_and_upserts(self) -> None:
        """cache miss → captioner.caption 1회 + vision_cache.upsert 1회."""
        from app.adapters.impl.image_parser import ImageParser
        from app.adapters.vision import VisionCaption
        from app.services import vision_cache

        captioner = MagicMock()
        captioner.caption.return_value = VisionCaption(
            type="문서",
            ocr_text="신규 ocr",
            caption="신규 캡션",
            structured=None,
            usage={"estimated_cost": 0.00075, "model_used": "gemini-2.0-flash"},
        )
        parser = ImageParser(captioner=captioner)

        png_bytes = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xfa\xcf"
            b"\x00\x00\x00\x03\x00\x01\x9eY\xe2\xfa\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        with patch.object(vision_cache, "lookup", return_value=None) as lookup_mock:
            with patch.object(vision_cache, "upsert") as upsert_mock:
                result = parser.parse(
                    png_bytes,
                    file_name="x.png",
                    source_type="pdf_vision_enrich",
                    sha256="b" * 64,
                    page=5,
                )

        # lookup 1회, captioner 1회, upsert 1회
        lookup_mock.assert_called_once_with("b" * 64, 5)
        captioner.caption.assert_called_once()
        upsert_mock.assert_called_once()
        # upsert kwargs 검증
        kwargs = upsert_mock.call_args.kwargs
        self.assertEqual(kwargs["caption"].type, "문서")
        self.assertEqual(kwargs["caption"].caption, "신규 캡션")
        self.assertAlmostEqual(kwargs["estimated_cost"], 0.00075)
        # 결과 검증
        self.assertEqual(result.metadata.get("vision_type"), "문서")

    def test_no_sha256_skips_cache_entirely(self) -> None:
        """sha256 None 시 cache lookup/upsert 0 — 단독 이미지 호출 영향 0 보존."""
        from app.adapters.impl.image_parser import ImageParser
        from app.adapters.vision import VisionCaption
        from app.services import vision_cache

        captioner = MagicMock()
        captioner.caption.return_value = VisionCaption(
            type="문서", ocr_text="", caption="x", structured=None,
        )
        parser = ImageParser(captioner=captioner)

        png_bytes = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xfa\xcf"
            b"\x00\x00\x00\x03\x00\x01\x9eY\xe2\xfa\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        with patch.object(vision_cache, "lookup") as lookup_mock:
            with patch.object(vision_cache, "upsert") as upsert_mock:
                parser.parse(png_bytes, file_name="x.png")  # sha256/page 미전달

        lookup_mock.assert_not_called()
        upsert_mock.assert_not_called()
        captioner.caption.assert_called_once()


if __name__ == "__main__":
    unittest.main()
