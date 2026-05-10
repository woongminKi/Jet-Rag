"""S4-B 엔티티 추출 ingest 통합 단위 테스트.

검증 범위
- chunk.py 의 `_to_chunk_records` 가 chunk.text 에서 entities 추출
- chunks.metadata.entities = {"dates": [...], "amounts": [...], ...}
- 빈 entities (모든 카테고리 비어있음) 시 metadata 키 자체 미주입 (graceful)
- 기존 동작 보존 (entities 키 없어도 다른 metadata 영향 0)

stdlib unittest only — extract → chunk record 회귀 가드.
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")
os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")

from app.adapters.parser import ExtractedSection
from app.ingest.stages.chunk import _to_chunk_records


def _make_section(text: str, page: int = 1, section_title: str = "본문") -> ExtractedSection:
    return ExtractedSection(
        text=text,
        page=page,
        section_title=section_title,
        bbox=None,
        metadata={},
    )


class ChunkEntitiesTest(unittest.TestCase):
    def test_extracts_entities_into_metadata(self) -> None:
        text = "이 내규는 2024년 4월 30일부터 시행하며, 회비 50,000원 (5%)을 ISSN 2288-7083 에 명시한다."
        records = _to_chunk_records(doc_id="d", sections=[_make_section(text)])
        self.assertEqual(len(records), 1)
        meta = records[0].metadata
        self.assertIn("entities", meta)
        ents = meta["entities"]
        self.assertIn("2024년 4월 30일", ents["dates"])
        self.assertIn("50,000원", ents["amounts"])
        self.assertIn("5%", ents["percentages"])
        self.assertIn("2288-7083", ents["identifiers"])

    def test_empty_entities_no_metadata_key(self) -> None:
        # 일반 문장 — entities 매칭 0건 → entities 키 미주입
        text = "그냥 일반 본문 내용입니다."
        records = _to_chunk_records(doc_id="d", sections=[_make_section(text)])
        self.assertNotIn("entities", records[0].metadata)

    def test_entities_coexists_with_other_metadata(self) -> None:
        # 2 sections — 두번째 chunk 는 idx>0 이라 overlap_with_prev_chunk_idx 키 가짐
        text1 = "첫 번째 본문 내용입니다."
        text2 = "2024년 4월 30일 시행. 회비 50,000원."
        records = _to_chunk_records(
            doc_id="d", sections=[_make_section(text1), _make_section(text2, page=2)]
        )
        self.assertEqual(len(records), 2)
        # 1st: entities 없음, overlap 없음
        self.assertNotIn("entities", records[0].metadata)
        # 2nd: entities 있음 + overlap_with_prev_chunk_idx
        self.assertIn("entities", records[1].metadata)
        self.assertIn("overlap_with_prev_chunk_idx", records[1].metadata)
        self.assertEqual(records[1].metadata["overlap_with_prev_chunk_idx"], 0)


if __name__ == "__main__":
    unittest.main()
