"""S4-A D2 — VisionCaption 두 필드(table_caption / figure_caption) 의 chunk
metadata 전파 + chunk.text 합성 회귀 차단.

명세 (senior-planner v0.1):
- vision-derived chunk (vision_incremental flag 또는 `(vision)` prefix) 한정으로
  `table_caption` / `figure_caption` 을 chunk.metadata 주입 + text 끝에 부착
- 둘 다 None → 합성 skip + metadata 미주입 (v1 cache row 호환)
- 한쪽만 set → 해당 한 줄만 부착, 반대쪽 metadata 키는 미주입
- 양쪽 set → `[표: ...]\\n[그림: ...]` 두 줄 모두 부착, metadata 두 키 모두 set

본 테스트는 외부 API 호출 0 — ExtractedSection 을 직접 만들어 _to_chunk_records
회귀 가드. ImageParser → extract.py → chunk.py 까지 path 일관성은 기존
test_extract_pdf_vision_enrich.py 가 보장.
"""
from __future__ import annotations

import unittest

from app.adapters.parser import ExtractedSection
from app.ingest.stages.chunk import _to_chunk_records


class TestVisionCaptionPropagation(unittest.TestCase):
    """명세 §E 의 4 시나리오 — 합성 분기 + metadata 주입 + v1 호환."""

    def test_both_captions_set_compose_two_lines_and_metadata(self):
        """시나리오 1: 두 필드 모두 set → text 끝에 두 줄 부착 + metadata 두 키 set."""
        section = ExtractedSection(
            text="원본 vision 캡션 본문",
            page=1,
            section_title="(vision) p.1 표/그림",
            bbox=None,
            metadata={
                "table_caption": "분기별 매출 추이",
                "figure_caption": "조직도 다이어그램",
            },
        )
        records = _to_chunk_records(doc_id="doc-x", sections=[section])

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(
            record.text,
            "원본 vision 캡션 본문\n\n[표: 분기별 매출 추이]\n[그림: 조직도 다이어그램]",
        )
        self.assertEqual(record.metadata.get("table_caption"), "분기별 매출 추이")
        self.assertEqual(record.metadata.get("figure_caption"), "조직도 다이어그램")

    def test_table_caption_only_compose_single_line(self):
        """시나리오 2: table_caption only → `[표: ...]` 한 줄만, figure 메타 미주입."""
        section = ExtractedSection(
            text="원본 vision 캡션 본문",
            page=2,
            section_title="(vision) p.2 표만",
            bbox=None,
            metadata={"table_caption": "월별 사용량 표"},
        )
        records = _to_chunk_records(doc_id="doc-x", sections=[section])

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(
            record.text, "원본 vision 캡션 본문\n\n[표: 월별 사용량 표]"
        )
        self.assertEqual(record.metadata.get("table_caption"), "월별 사용량 표")
        # figure_caption 키는 부재 — None 아님, 키 자체 미주입.
        self.assertNotIn("figure_caption", record.metadata)
        # 부정 회귀 — `[그림:` 토큰 합성 안 됨.
        self.assertNotIn("[그림:", record.text)

    def test_both_none_skip_compose_keep_text(self):
        """시나리오 3: 두 필드 모두 None → 합성 skip + metadata 두 키 미주입."""
        section = ExtractedSection(
            text="원본 vision 캡션 본문",
            page=3,
            section_title="(vision) p.3 캡션 부재",
            bbox=None,
            metadata={"table_caption": None, "figure_caption": None},
        )
        records = _to_chunk_records(doc_id="doc-x", sections=[section])

        self.assertEqual(len(records), 1)
        record = records[0]
        # text 무변경 — 합성 skip.
        self.assertEqual(record.text, "원본 vision 캡션 본문")
        # 두 키 모두 chunk.metadata 에 부재 (None 으로도 들어가지 않음).
        self.assertNotIn("table_caption", record.metadata)
        self.assertNotIn("figure_caption", record.metadata)

    def test_v1_cache_row_compatibility_no_caption_keys(self):
        """시나리오 4: v1 cache row 시뮬 — caption 키 자체 부재 → 합성 skip.

        v1 cache row 는 `table_caption` / `figure_caption` 컬럼이 없어 deserialize
        시 VisionCaption 의 두 필드가 default None 으로 채워지고 ImageParser 가
        section.metadata 에 키 자체를 주입하지 않는다 (D1 ship 동작). chunk
        단계에서 metadata.get("...") → None → 합성 skip 으로 v1 row 의 기존
        chunk.text 와 100% 동일해야 한다.
        """
        section = ExtractedSection(
            text="원본 vision 캡션 본문 (v1 cache hit)",
            page=4,
            section_title="(vision) p.4 v1 row",
            bbox=None,
            metadata={},  # v1 row → ImageParser 가 caption_metadata 에 키 미주입
        )
        records = _to_chunk_records(doc_id="doc-x", sections=[section])

        self.assertEqual(len(records), 1)
        record = records[0]
        # 기존 chunk.text 일치 — v1 row 영향 0 보장.
        self.assertEqual(record.text, "원본 vision 캡션 본문 (v1 cache hit)")
        self.assertNotIn("table_caption", record.metadata)
        self.assertNotIn("figure_caption", record.metadata)
        # v1 row 도 (vision) prefix 라 분기에는 진입 — 그러나 키 부재로 skip.


if __name__ == "__main__":
    unittest.main()
