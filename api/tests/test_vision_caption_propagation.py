"""S4-A D2 — VisionCaption 두 필드(table_caption / figure_caption) 의 chunk
metadata 전파 + chunk.text 합성 회귀 차단.

명세 (senior-planner v0.1):
- vision-derived chunk (vision_incremental flag 또는 `(vision)` prefix) 한정으로
  `table_caption` / `figure_caption` 을 chunk.metadata 주입 + text 끝에 부착
- 둘 다 None → 합성 skip + metadata 미주입 (v1 cache row 호환)
- 한쪽만 set → 해당 한 줄만 부착, 반대쪽 metadata 키는 미주입
- 양쪽 set → `[표: ...]\\n[그림: ...]` 두 줄 모두 부착, metadata 두 키 모두 set

2026-05-09 — D2 보강: ImageParser._compose_result 의 OCR section + action_items
section 에도 caption_metadata broadcast → 같은 vision page 의 모든 sections 가
caption metadata 공유. caption section 1 chunk 한정에서 OCR/action chunks 까지
효과 확장.

본 테스트는 외부 API 호출 0 — ExtractedSection 을 직접 만들어 _to_chunk_records
회귀 가드. ImageParser → extract.py → chunk.py 까지 path 일관성은 기존
test_extract_pdf_vision_enrich.py 가 보장.
"""
from __future__ import annotations

import os
import unittest
from io import BytesIO
from unittest.mock import MagicMock

from PIL import Image

os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")
os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")

from app.adapters.parser import ExtractedSection
from app.ingest.stages.chunk import (
    _merge_short_sections,
    _split_long_sections,
    _to_chunk_records,
)


def _png_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (50, 50), color="white").save(buf, format="PNG")
    return buf.getvalue()


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


class TestImageParserCaptionBroadcast(unittest.TestCase):
    """2026-05-09 — ImageParser._compose_result 의 OCR / action_items section 에도
    caption_metadata broadcast 검증. 기존엔 caption section 1개만 부착되어 vision
    page 의 OCR chunks (대부분 chunks) 가 caption 효과 미수혜였음.
    """

    def setUp(self) -> None:
        from app.services import vision_metrics
        vision_metrics.reset()

    def test_ocr_section_inherits_caption_metadata(self) -> None:
        """캡션 두 필드 모두 set + OCR text 있음 → OCR section 에도 동일 metadata."""
        from app.adapters.impl.image_parser import ImageParser
        from app.adapters.vision import VisionCaption

        captioner = MagicMock()
        captioner.caption.return_value = VisionCaption(
            type="표",
            ocr_text="2024년 매출 500억, 2025년 매출 700억",
            caption="분기별 매출 추이 표",
            structured=None,
            table_caption="분기별 매출 추이",
            figure_caption=None,
        )

        result = ImageParser(captioner=captioner).parse(
            _png_bytes(), file_name="report.png"
        )

        # caption section + OCR section 2개
        ocr_section = next(
            s for s in result.sections if s.section_title == "OCR 텍스트"
        )
        self.assertEqual(ocr_section.metadata.get("table_caption"), "분기별 매출 추이")
        self.assertNotIn("figure_caption", ocr_section.metadata)

        caption_section = result.sections[0]
        self.assertEqual(
            caption_section.metadata.get("table_caption"), "분기별 매출 추이"
        )

    def test_action_items_section_inherits_caption_metadata(self) -> None:
        """화이트보드 + caption 둘 다 set → action_items section 에도 metadata."""
        from app.adapters.impl.image_parser import ImageParser
        from app.adapters.vision import VisionCaption

        captioner = MagicMock()
        captioner.caption.return_value = VisionCaption(
            type="화이트보드",
            ocr_text="OKR 회의",
            caption="OKR 회의 화이트보드",
            structured={"action_items": ["보고", "검토"]},
            table_caption="OKR 진척도 표",
            figure_caption="조직도",
        )

        result = ImageParser(captioner=captioner).parse(
            _png_bytes(), file_name="board.png"
        )

        action_section = next(
            s for s in result.sections if s.section_title == "액션 아이템"
        )
        self.assertEqual(action_section.metadata.get("table_caption"), "OKR 진척도 표")
        self.assertEqual(action_section.metadata.get("figure_caption"), "조직도")

    def test_v1_compatible_no_caption_no_keys(self) -> None:
        """v1 cache row 시뮬 — caption 두 필드 None → OCR/caption section 모두 키 부재."""
        from app.adapters.impl.image_parser import ImageParser
        from app.adapters.vision import VisionCaption

        captioner = MagicMock()
        captioner.caption.return_value = VisionCaption(
            type="문서",
            ocr_text="일반 문서 텍스트",
            caption="문서 사진",
            structured=None,
            table_caption=None,
            figure_caption=None,
        )

        result = ImageParser(captioner=captioner).parse(
            _png_bytes(), file_name="doc.png"
        )

        for sec in result.sections:
            self.assertNotIn("table_caption", sec.metadata)
            self.assertNotIn("figure_caption", sec.metadata)

    def test_caption_metadata_independent_per_section(self) -> None:
        """frozen dataclass 의 metadata 가 sections 사이 mutate 격리."""
        from app.adapters.impl.image_parser import ImageParser
        from app.adapters.vision import VisionCaption

        captioner = MagicMock()
        captioner.caption.return_value = VisionCaption(
            type="표",
            ocr_text="OCR 본문",
            caption="표 사진",
            structured=None,
            table_caption="A 표",
            figure_caption=None,
        )

        result = ImageParser(captioner=captioner).parse(
            _png_bytes(), file_name="x.png"
        )

        # 한 section 의 metadata mutate 가 다른 section 에 누설되면 안 됨.
        result.sections[0].metadata["table_caption"] = "MUTATED"
        ocr_section = next(
            s for s in result.sections if s.section_title == "OCR 텍스트"
        )
        self.assertEqual(ocr_section.metadata.get("table_caption"), "A 표")


class TestSplitMergeMetadataPreservation(unittest.TestCase):
    """2026-05-09 — chunk.py 의 _split_long_sections / _merge_short_sections 가
    section.metadata 를 보존하도록 회귀 차단. 기존엔 둘 다 metadata 인자 누락으로
    default 빈 dict 가 채워져 vision-derived OCR section 의 caption metadata 가
    split/merge 후 모든 chunks 에서 손실됨. 본 테스트로 회귀 가드.
    """

    def test_split_long_section_preserves_caption_metadata(self) -> None:
        """긴 OCR section 이 split 시 모든 piece 가 caption metadata 보존."""
        long_text = (
            "OCR 텍스트 본문. " * 200
        )  # _MAX_SIZE 1000 초과 — split 발생 보장
        section = ExtractedSection(
            text=long_text,
            page=5,
            section_title="(vision) p.5 OCR 텍스트",
            bbox=None,
            metadata={
                "table_caption": "분기별 매출",
                "figure_caption": "조직도",
            },
        )
        pieces = _split_long_sections([section])
        self.assertGreater(len(pieces), 1, "split이 일어나지 않으면 회귀 가드 무효")
        for piece in pieces:
            self.assertEqual(
                piece.metadata.get("table_caption"), "분기별 매출",
                f"split piece 에서 table_caption 손실: {piece.metadata}",
            )
            self.assertEqual(piece.metadata.get("figure_caption"), "조직도")

    def test_split_metadata_independent_per_piece(self) -> None:
        """split 후 piece 의 metadata mutate 가 다른 piece 로 누설 안 됨."""
        long_text = "본문. " * 300
        section = ExtractedSection(
            text=long_text,
            page=1,
            section_title="(vision) p.1",
            bbox=None,
            metadata={"table_caption": "원본"},
        )
        pieces = _split_long_sections([section])
        self.assertGreater(len(pieces), 1)
        pieces[0].metadata["table_caption"] = "MUTATED"
        for p in pieces[1:]:
            self.assertEqual(p.metadata.get("table_caption"), "원본")

    def test_split_short_section_keeps_metadata(self) -> None:
        """_MAX_SIZE 이하 section 은 split 안 일어나고 원본 그대로 통과 (metadata 포함)."""
        short = ExtractedSection(
            text="짧은 본문",
            page=2,
            section_title="(vision) p.2",
            bbox=None,
            metadata={"table_caption": "보존되어야 함"},
        )
        out = _split_long_sections([short])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].metadata.get("table_caption"), "보존되어야 함")

    def test_merge_short_sections_dict_merges_metadata(self) -> None:
        """짧은 두 sections merge 시 양쪽 metadata 가 dict-merge (section 우선) 로 보존."""
        a = ExtractedSection(
            text="앞 짧은 텍스트",  # _MIN_MERGE_SIZE 200 미만
            page=3,
            section_title="(vision) p.3 caption",
            bbox=None,
            metadata={"table_caption": "buf 의 caption"},
        )
        b = ExtractedSection(
            text="뒤 짧은 텍스트",
            page=3,
            section_title="(vision) p.3 OCR",
            bbox=None,
            metadata={"figure_caption": "section 의 caption"},
        )
        out = _merge_short_sections([a, b])
        self.assertEqual(len(out), 1)
        merged = out[0]
        self.assertEqual(merged.metadata.get("table_caption"), "buf 의 caption")
        self.assertEqual(merged.metadata.get("figure_caption"), "section 의 caption")

    def test_merge_section_overrides_buf_on_key_conflict(self) -> None:
        """key 충돌 시 section 의 값이 buf 를 override (dict-merge 의미 유지)."""
        a = ExtractedSection(
            text="짧은 A",
            page=4,
            section_title="(vision) p.4",
            bbox=None,
            metadata={"table_caption": "A 값"},
        )
        b = ExtractedSection(
            text="짧은 B",
            page=4,
            section_title="(vision) p.4",
            bbox=None,
            metadata={"table_caption": "B 값"},
        )
        out = _merge_short_sections([a, b])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].metadata.get("table_caption"), "B 값")


if __name__ == "__main__":
    unittest.main()
