"""S4-A D1 — Gemini Vision 5필드 (type/ocr_text/caption/table_caption/figure_caption/structured) 파싱 검증.

검증 포인트
- JSON 응답에 table_caption / figure_caption 동시 포함 시 두 필드 모두 dataclass 에 전달
- 두 필드 부재 / null / 빈 문자열 / 비-str 타입 → 모두 graceful None
- 기존 4필드 (type/ocr_text/caption/structured) 회귀 영향 0

stdlib unittest only — Gemini SDK 호출 0 (`_parse` 직접 호출).
"""

from __future__ import annotations

import json
import os
import unittest

# 환경 변수 stub — 단위 테스트가 실 API 호출 회피.
os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")
os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")
os.environ["JET_RAG_METRICS_PERSIST_ENABLED"] = "0"
os.environ["JET_RAG_METRICS_PERSIST_ASYNC"] = "0"


class GeminiVisionCaption5FieldsParseTest(unittest.TestCase):
    """_parse 5필드 (table_caption / figure_caption) JSON 처리."""

    def test_parse_table_and_figure_caption(self) -> None:
        """JSON 에 table_caption / figure_caption 둘 다 비-null 일 때 dataclass 보존."""
        from app.adapters.impl.gemini_vision import GeminiVisionCaptioner

        payload = {
            "type": "표",
            "ocr_text": "분기\t매출\nQ1\t100",
            "caption": "분기별 매출 추이 표",
            "table_caption": "2026년 분기별 매출",
            "figure_caption": "성장률 막대 차트",
            "structured": {"headers": ["분기", "매출"], "rows": [["Q1", "100"]]},
        }
        cap = GeminiVisionCaptioner._parse(json.dumps(payload, ensure_ascii=False))

        # 기존 4필드 회귀 검증
        self.assertEqual(cap.type, "표")
        self.assertEqual(cap.ocr_text, "분기\t매출\nQ1\t100")
        self.assertEqual(cap.caption, "분기별 매출 추이 표")
        self.assertEqual(cap.structured, {"headers": ["분기", "매출"], "rows": [["Q1", "100"]]})

        # S4-A D1 — 신규 2필드
        self.assertEqual(cap.table_caption, "2026년 분기별 매출")
        self.assertEqual(cap.figure_caption, "성장률 막대 차트")

    def test_parse_missing_table_figure_keys_graceful(self) -> None:
        """JSON 에 두 키 부재 / null / 빈 문자열 / 비-str → 모두 None 으로 graceful 정규화.

        기존 4필드 응답 (S4-A D1 이전 prompt) 호환 + 모델 일탈 응답 안전 처리.
        """
        from app.adapters.impl.gemini_vision import GeminiVisionCaptioner

        # 1) 두 키 부재 (구 prompt 응답)
        payload_absent = {
            "type": "문서",
            "ocr_text": "본문 텍스트",
            "caption": "회의록 문서",
            "structured": None,
        }
        cap = GeminiVisionCaptioner._parse(json.dumps(payload_absent, ensure_ascii=False))
        self.assertEqual(cap.type, "문서")
        self.assertEqual(cap.caption, "회의록 문서")
        self.assertIsNone(cap.table_caption)
        self.assertIsNone(cap.figure_caption)

        # 2) null
        payload_null = dict(payload_absent, table_caption=None, figure_caption=None)
        cap = GeminiVisionCaptioner._parse(json.dumps(payload_null, ensure_ascii=False))
        self.assertIsNone(cap.table_caption)
        self.assertIsNone(cap.figure_caption)

        # 3) 빈 문자열 / 공백
        payload_empty = dict(payload_absent, table_caption="", figure_caption="   ")
        cap = GeminiVisionCaptioner._parse(json.dumps(payload_empty, ensure_ascii=False))
        self.assertIsNone(cap.table_caption)
        self.assertIsNone(cap.figure_caption)

        # 4) 비-str (모델 일탈 응답)
        payload_bad_type = dict(payload_absent, table_caption=["a", "b"], figure_caption=42)
        cap = GeminiVisionCaptioner._parse(json.dumps(payload_bad_type, ensure_ascii=False))
        self.assertIsNone(cap.table_caption)
        self.assertIsNone(cap.figure_caption)


if __name__ == "__main__":
    unittest.main()
