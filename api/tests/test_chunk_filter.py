"""W3 v0.5 §3.G(3) — chunk_filter 자동 마킹 룰 단위 테스트.

검증 범위
- table_noise: 강화된 임계값 (≥ 0.9 / ≥ 0.7) 에서 명확한 표만 마킹, 일반 산문 false positive 0
- header_footer: 같은 doc 안 동일 짧은 텍스트 ≥ 3회 → 마킹
- 기존 flags 값 보존 — filtered_reason 만 추가
- ChunkRecord.flags 필드 추가 후 dataclasses.replace 가 정상 작동

stdlib unittest 만 사용 — 외부 의존성 0 (CLAUDE.md 준수).
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

# import 단계에서 HF 토큰 필요할 수 있는 모듈 import → dummy 주입.
os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")


class ClassifyChunkTableNoiseTest(unittest.TestCase):
    """table_noise 마킹 — 임계값 강화 (≥ 0.9 / ≥ 0.7) 정합."""

    def test_clear_table_is_marked(self) -> None:
        """짧은 라인 100% + 숫자/특수문자 ≥ 70% → table_noise."""
        from app.adapters.vectorstore import ChunkRecord
        from app.ingest.stages.chunk_filter import _classify_chunk

        # 의도적으로 _SHORT_LINE_LEN(30) 미만 라인만 + 숫자 비중 충분히 높음.
        # _line_metrics 의 digit_punct_ratio 분모는 non-whitespace 문자 수.
        text = "\n".join([
            "1 | 100 | 200",
            "2 | 150 | 300",
            "3 | 175 | 400",
            "4 | 200 | 500",
            "5 | 225 | 600",
            "6 | 250 | 700",
        ])
        chunk = ChunkRecord(doc_id="d1", chunk_idx=0, text=text)
        reason = _classify_chunk(chunk, header_footer_texts=set())
        self.assertEqual(
            reason, "table_noise",
            f"명확한 표가 마킹되지 않음: text 미마킹",
        )

    def test_prose_is_not_marked(self) -> None:
        """일반 한국어 산문 — false positive 0."""
        from app.adapters.vectorstore import ChunkRecord
        from app.ingest.stages.chunk_filter import _classify_chunk

        prose = (
            "이 계약은 갑과 을 사이에 체결된 공사 도급 계약으로, "
            "공사 대금의 지급 및 합의 해지에 관한 사항을 명확히 규정한다. "
            "본 계약의 효력은 양 당사자가 서명한 날로부터 발생하며, "
            "별도의 합의가 없는 한 공사 완료일까지 유효하다."
        )
        chunk = ChunkRecord(doc_id="d1", chunk_idx=0, text=prose)
        reason = _classify_chunk(chunk, header_footer_texts=set())
        self.assertIsNone(
            reason, f"산문이 잘못 마킹됨: reason={reason}"
        )

    def test_borderline_short_lines_but_low_digit_ratio_is_not_marked(self) -> None:
        """짧은 라인 ≥ 0.9 인데 digit_punct_ratio < 0.7 → 미마킹 (G(1) 보다 강화).

        G(1) 진단 도구는 0.50 이라 마킹할 수도 있으나, 본 자동 마킹은 0.70 임계.
        """
        from app.adapters.vectorstore import ChunkRecord
        from app.ingest.stages.chunk_filter import _classify_chunk

        # 짧은 라인 + 한국어 위주 (digit_punct 낮음) — 헤더 같은 짧은 텍스트 모음.
        text = "\n".join([
            "갑의 의무 및 책임",
            "을의 의무 및 책임",
            "공사 진행 일정",
            "공사 대금 지급",
            "분쟁 해결 절차",
        ])
        chunk = ChunkRecord(doc_id="d1", chunk_idx=0, text=text)
        reason = _classify_chunk(chunk, header_footer_texts=set())
        self.assertIsNone(
            reason,
            f"짧은 라인이지만 한국어 위주 텍스트가 마킹됨 (G(1) 보다 강화 검증 실패): {reason}",
        )

    def test_too_short_chunk_is_not_marked(self) -> None:
        """길이 50 미만 — 분류 의미 없어 미마킹."""
        from app.adapters.vectorstore import ChunkRecord
        from app.ingest.stages.chunk_filter import _classify_chunk

        chunk = ChunkRecord(doc_id="d1", chunk_idx=0, text="짧은 텍스트")
        self.assertIsNone(_classify_chunk(chunk, set()))


class ClassifyChunkEmptyTest(unittest.TestCase):
    """W4-Q-15 (c) — 빈 청크 마킹."""

    def test_strip_empty_text_marked_as_empty(self) -> None:
        """text.strip() == '' 인 청크 → empty 마킹."""
        from app.adapters.vectorstore import ChunkRecord
        from app.ingest.stages.chunk_filter import _classify_chunk

        chunk = ChunkRecord(doc_id="d1", chunk_idx=0, text="   \n  \t  ")
        self.assertEqual(_classify_chunk(chunk, set()), "empty")

    def test_truly_empty_text_marked_as_empty(self) -> None:
        """text == '' → empty 마킹."""
        from app.adapters.vectorstore import ChunkRecord
        from app.ingest.stages.chunk_filter import _classify_chunk

        chunk = ChunkRecord(doc_id="d1", chunk_idx=0, text="")
        self.assertEqual(_classify_chunk(chunk, set()), "empty")

    def test_non_empty_text_not_marked_as_empty(self) -> None:
        """비어있지 않은 텍스트는 empty 마킹 안 함 (다른 룰로 위임)."""
        from app.adapters.vectorstore import ChunkRecord
        from app.ingest.stages.chunk_filter import _classify_chunk

        chunk = ChunkRecord(doc_id="d1", chunk_idx=0, text="짧은 텍스트")
        self.assertIsNone(_classify_chunk(chunk, set()))

    def test_empty_takes_priority_over_header_footer(self) -> None:
        """빈 텍스트는 header_footer 마킹보다 empty 우선."""
        from app.adapters.vectorstore import ChunkRecord
        from app.ingest.stages.chunk_filter import _classify_chunk

        chunk = ChunkRecord(doc_id="d1", chunk_idx=0, text="")
        # 가설: header_footer 후보가 있어도 empty 가 먼저 분류
        self.assertEqual(_classify_chunk(chunk, {"any text"}), "empty")


class HeaderFooterClassifyTest(unittest.TestCase):
    """W4-Q-15 (b) — header_footer 마킹의 _classify_chunk 단계 검증."""

    def test_classify_marks_header_footer_when_text_in_set(self) -> None:
        """후보 set 에 포함된 짧은 텍스트는 header_footer 마킹."""
        from app.adapters.vectorstore import ChunkRecord
        from app.ingest.stages.chunk_filter import _classify_chunk

        chunk = ChunkRecord(doc_id="d1", chunk_idx=0, text="Page 1 of 10")
        result = _classify_chunk(
            chunk, header_footer_texts={"Page 1 of 10"}
        )
        self.assertEqual(result, "header_footer")

    def test_classify_strips_text_before_lookup(self) -> None:
        """공백 padding 된 텍스트도 strip 후 lookup."""
        from app.adapters.vectorstore import ChunkRecord
        from app.ingest.stages.chunk_filter import _classify_chunk

        chunk = ChunkRecord(doc_id="d1", chunk_idx=0, text="  Page 1 of 10  \n")
        result = _classify_chunk(
            chunk, header_footer_texts={"Page 1 of 10"}
        )
        self.assertEqual(result, "header_footer")


class HeaderFooterDetectionTest(unittest.TestCase):
    """header_footer 마킹 — 같은 doc 안 동일 짧은 텍스트 ≥ 3회."""

    def test_repeating_short_text_is_detected(self) -> None:
        from app.adapters.vectorstore import ChunkRecord
        from app.ingest.stages.chunk_filter import _detect_header_footer_texts

        chunks = [
            ChunkRecord(doc_id="d1", chunk_idx=0, text="Page 1 of 10"),
            ChunkRecord(doc_id="d1", chunk_idx=1, text="본문 내용 1"),
            ChunkRecord(doc_id="d1", chunk_idx=2, text="Page 1 of 10"),
            ChunkRecord(doc_id="d1", chunk_idx=3, text="본문 내용 2"),
            ChunkRecord(doc_id="d1", chunk_idx=4, text="Page 1 of 10"),
        ]
        repeated = _detect_header_footer_texts(chunks)
        self.assertEqual(repeated, {"Page 1 of 10"})

    def test_below_threshold_is_not_detected(self) -> None:
        """동일 텍스트 2회 — 임계값 (3) 미달."""
        from app.adapters.vectorstore import ChunkRecord
        from app.ingest.stages.chunk_filter import _detect_header_footer_texts

        chunks = [
            ChunkRecord(doc_id="d1", chunk_idx=0, text="Header A"),
            ChunkRecord(doc_id="d1", chunk_idx=1, text="본문"),
            ChunkRecord(doc_id="d1", chunk_idx=2, text="Header A"),
        ]
        self.assertEqual(_detect_header_footer_texts(chunks), set())

    def test_long_repeating_text_is_not_detected(self) -> None:
        """반복되더라도 len ≥ _HEADER_FOOTER_MAX_LEN(100) 이면 헤더/푸터 아님 (본문 가능)."""
        from app.adapters.vectorstore import ChunkRecord
        from app.ingest.stages.chunk_filter import _detect_header_footer_texts

        long_text = "이것은 매우 긴 본문 텍스트입니다. " * 10  # > 100 chars
        chunks = [
            ChunkRecord(doc_id="d1", chunk_idx=i, text=long_text) for i in range(5)
        ]
        self.assertEqual(_detect_header_footer_texts(chunks), set())


class RunChunkFilterStageTest(unittest.TestCase):
    """`run_chunk_filter_stage` — flags 마킹 + 기존 flags 보존."""

    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")

    def test_marks_table_noise_and_preserves_existing_flags(self) -> None:
        """table_noise 마킹 + 기존 flags 보존 (다른 마커 보존)."""
        from app.adapters.vectorstore import ChunkRecord
        from app.ingest.stages import chunk_filter

        # stage() context manager 가 jobs DB 호출 → mock.
        with patch.object(chunk_filter, "stage") as mock_stage:
            mock_stage.return_value.__enter__ = MagicMock(return_value=None)
            mock_stage.return_value.__exit__ = MagicMock(return_value=False)

            table_text = "\n".join([
                "1 | 10 | 100",
                "2 | 20 | 200",
                "3 | 30 | 300",
                "4 | 40 | 400",
                "5 | 50 | 500",
                "6 | 60 | 600",
            ])
            prose = (
                "이 계약은 갑과 을 사이에 체결된 공사 도급 계약입니다. "
                "본 계약의 효력은 양 당사자가 서명한 날로부터 발생합니다. "
                "분쟁이 발생할 경우 협의를 통해 해결합니다."
            )
            chunks = [
                ChunkRecord(
                    doc_id="d1", chunk_idx=0, text=table_text,
                    flags={"existing_marker": "keep_me"},  # 기존 마커
                ),
                ChunkRecord(doc_id="d1", chunk_idx=1, text=prose),
            ]
            out = chunk_filter.run_chunk_filter_stage(
                "job-x", doc_id="d1", chunks=chunks
            )

            # 표는 마킹 + 기존 flags 보존
            self.assertEqual(out[0].flags.get("filtered_reason"), "table_noise")
            self.assertEqual(out[0].flags.get("existing_marker"), "keep_me")
            # 산문은 미마킹
            self.assertNotIn("filtered_reason", out[1].flags)

    def test_empty_chunks_return_empty(self) -> None:
        from app.ingest.stages import chunk_filter

        with patch.object(chunk_filter, "stage") as mock_stage:
            mock_stage.return_value.__enter__ = MagicMock(return_value=None)
            mock_stage.return_value.__exit__ = MagicMock(return_value=False)
            out = chunk_filter.run_chunk_filter_stage(
                "job-x", doc_id="d1", chunks=[]
            )
            self.assertEqual(out, [])


class ChunkRecordFlagsFieldTest(unittest.TestCase):
    """ChunkRecord.flags 필드 — 새 필드라 기존 사용처 영향 0 검증."""

    def test_default_is_empty_dict(self) -> None:
        from app.adapters.vectorstore import ChunkRecord

        chunk = ChunkRecord(doc_id="d1", chunk_idx=0, text="test")
        self.assertEqual(chunk.flags, {})

    def test_dataclasses_replace_works(self) -> None:
        """chunk_filter 가 사용하는 dataclasses.replace 가 frozen + 새 필드와 호환."""
        import dataclasses

        from app.adapters.vectorstore import ChunkRecord

        original = ChunkRecord(doc_id="d1", chunk_idx=0, text="test")
        updated = dataclasses.replace(
            original, flags={"filtered_reason": "table_noise"}
        )
        self.assertEqual(original.flags, {})  # frozen — 원본 보존
        self.assertEqual(updated.flags, {"filtered_reason": "table_noise"})

    def test_serialize_chunk_includes_flags(self) -> None:
        """SupabasePgVectorStore._serialize_chunk 가 flags 컬럼 포함."""
        from app.adapters.impl.supabase_vectorstore import SupabasePgVectorStore
        from app.adapters.vectorstore import ChunkRecord

        chunk = ChunkRecord(
            doc_id="d1", chunk_idx=0, text="test",
            flags={"filtered_reason": "table_noise"},
        )
        row = SupabasePgVectorStore._serialize_chunk(chunk)
        self.assertEqual(row.get("flags"), {"filtered_reason": "table_noise"})

    def test_serialize_chunk_with_empty_flags(self) -> None:
        """빈 flags 도 명시 직렬화 — DB 컬럼이 NOT NULL DEFAULT '{}'."""
        from app.adapters.impl.supabase_vectorstore import SupabasePgVectorStore
        from app.adapters.vectorstore import ChunkRecord

        chunk = ChunkRecord(doc_id="d1", chunk_idx=0, text="test")
        row = SupabasePgVectorStore._serialize_chunk(chunk)
        self.assertIn("flags", row)
        self.assertEqual(row["flags"], {})


if __name__ == "__main__":
    unittest.main()
