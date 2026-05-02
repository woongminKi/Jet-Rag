"""W4 Day 3 — W4-Q-14 청킹 정책 본격 변경 단위 테스트.

검증 범위 (청킹 정책 검토 §6 의 1·2·3·5):
- 4.1 lookbehind char class 일반화 — `(?<=[가-힣)\\]][.!?])\\s+` 단일 패턴
  - 한국어 종결어미 (다·요·까·죠·습·니·네·군·지) 모두 split, 마침표 보존
- 4.2 false split 보호 — 숫자/영문 직후 `.` 차단 + 법령 인용 마스킹
  - `Section 1. Intro`, `vs.`, `2025. 7. 9. 선고` 등 보호
- 4.4 100자 prefix overlap — 인접 split 조각 사이만, _MAX_SIZE 보장
- 4.5 section_title 우선순위 swap — 병합 시 section.section_title 우선

stdlib unittest 만 사용 — 외부 의존성 0 (CLAUDE.md 준수).
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")


# =====================================================================
# 4.1 — lookbehind char class 일반화 + 한국어 종결어미 split
# =====================================================================


class SentenceEndKoreanTest(unittest.TestCase):
    """한국어 종결어미 split + 마침표 보존 검증."""

    def test_da_ending_splits_and_preserves_period(self) -> None:
        """`다.` 종결어미 뒤 공백에서 split, 마침표는 좌측 청크에 보존."""
        from app.ingest.stages.chunk import _SENTENCE_END

        text = "이것은 좋다. 다음 문장이다."
        parts = _SENTENCE_END.split(text)
        self.assertEqual(parts, ["이것은 좋다.", "다음 문장이다."])

    def test_yo_ending_splits(self) -> None:
        """`요.` 종결어미 split."""
        from app.ingest.stages.chunk import _SENTENCE_END

        text = "안녕하세요. 반갑습니다."
        parts = _SENTENCE_END.split(text)
        self.assertEqual(parts, ["안녕하세요.", "반갑습니다."])

    def test_kka_ending_splits(self) -> None:
        """`까?` 종결어미 split (의문문)."""
        from app.ingest.stages.chunk import _SENTENCE_END

        text = "그럴까요? 잘 모르겠습니다."
        parts = _SENTENCE_END.split(text)
        self.assertEqual(parts, ["그럴까요?", "잘 모르겠습니다."])

    def test_jyo_ending_splits(self) -> None:
        """`죠.` 종결어미 split."""
        from app.ingest.stages.chunk import _SENTENCE_END

        text = "맞죠. 그렇게 합시다."
        parts = _SENTENCE_END.split(text)
        self.assertEqual(parts, ["맞죠.", "그렇게 합시다."])

    def test_seup_ni_da_chained_endings(self) -> None:
        """`습니다.` `입니다.` `합니다.` — 마지막 `다` 가 매칭되어 정상 split."""
        from app.ingest.stages.chunk import _SENTENCE_END

        text = "감사합니다. 입니다. 했습니다."
        parts = _SENTENCE_END.split(text)
        self.assertEqual(parts, ["감사합니다.", "입니다.", "했습니다."])

    def test_paragraph_break_splits(self) -> None:
        """`\\n\\n` 문단 break 도 split delimiter."""
        from app.ingest.stages.chunk import _SENTENCE_END

        text = "첫 단락이다.\n\n두번째 단락이다."
        parts = _SENTENCE_END.split(text)
        # 첫 매칭은 `다.\n` 위치 (한국어 종결어미), 두번째는 `\n\n`
        # 실제로는 leftmost 매칭이라 첫 alternation 이 `\n` 을 매칭
        self.assertGreaterEqual(len(parts), 2)
        self.assertIn("첫 단락이다.", parts)


# =====================================================================
# 4.2 — false split 보호
# =====================================================================


class FalseSplitProtectionTest(unittest.TestCase):
    """숫자/영문 직후 마침표 + 법령 인용 보호 검증."""

    def test_section_number_not_split(self) -> None:
        """`Section 1. Intro` — 숫자 직후 `.` split 안 함."""
        from app.ingest.stages.chunk import _SENTENCE_END

        text = "Section 1. Introduction here"
        parts = _SENTENCE_END.split(text)
        self.assertEqual(parts, [text])  # split 0건

    def test_legal_citation_masked_and_preserved(self) -> None:
        """`2025. 7. 9. 선고` 법령 인용 — placeholder 마스킹으로 보호."""
        from app.ingest.stages.chunk import _split_by_sentence

        text = "대법원 2025. 7. 9. 선고 2024다74413 판결이다. 다음 본문이다."
        pieces = _split_by_sentence(text)
        # 청크가 _TARGET_SIZE 미만이라 1개로 합쳐지지만, 법령 인용은 원본 그대로 복원
        self.assertEqual(len(pieces), 1)
        self.assertIn("2025. 7. 9.", pieces[0])
        self.assertIn("판결이다.", pieces[0])

    def test_english_period_alone_not_split(self) -> None:
        """`Hello world.` — 영문 직후 마침표는 split 안 함 (한국어/괄호 lookbehind 만 통과)."""
        from app.ingest.stages.chunk import _SENTENCE_END

        text = "Hello world. Next sentence."
        parts = _SENTENCE_END.split(text)
        # 영문 sentence 분할은 의도적으로 지원 안 함 (한국어 dominant 환경 trade-off)
        self.assertEqual(parts, [text])

    def test_decimal_number_not_split(self) -> None:
        """`2.2%` 같은 소수점은 split 안 함."""
        from app.ingest.stages.chunk import _SENTENCE_END

        text = "GDP 성장률 2.2% 달성했다. 다음 분기는 더 좋다."
        parts = _SENTENCE_END.split(text)
        self.assertEqual(parts, ["GDP 성장률 2.2% 달성했다.", "다음 분기는 더 좋다."])

    def test_legal_date_mask_restore_idempotent(self) -> None:
        """법령 인용 마스킹 → 복원 round-trip 동등."""
        from app.ingest.stages.chunk import _mask_legal_dates, _restore_legal_dates

        text = "기준일 2024. 12. 31. 까지 유효하다."
        masked, matches = _mask_legal_dates(text)
        self.assertNotIn("2024. 12. 31.", masked)
        restored = _restore_legal_dates(masked, matches)
        self.assertEqual(restored, text)

    def test_no_legal_date_mask_returns_empty_matches(self) -> None:
        """법령 인용 패턴 없으면 매칭 0건."""
        from app.ingest.stages.chunk import _mask_legal_dates

        text = "법령 인용 없는 일반 문장이다."
        masked, matches = _mask_legal_dates(text)
        self.assertEqual(masked, text)
        self.assertEqual(matches, [])


# =====================================================================
# 4.4 — 100자 prefix overlap
# =====================================================================


class OverlapTest(unittest.TestCase):
    """인접 청크 prefix overlap 검증."""

    def test_overlap_applied_to_adjacent_pieces(self) -> None:
        """split 결과 2개 이상이면 두번째부터 prefix overlap 적용."""
        from app.ingest.stages.chunk import _OVERLAP_SIZE, _split_by_sentence

        # _TARGET_SIZE=800 초과로 split 유도
        sentences = ["이것은 한국어 문장입니다."] * 60  # 13 chars * 60 = 780 + spaces ≈ 840
        text = " ".join(sentences)
        pieces = _split_by_sentence(text)
        self.assertGreaterEqual(len(pieces), 2)
        # 두번째 청크의 시작 부분이 첫번째 청크의 마지막 부분과 일부 겹쳐야 함
        # _OVERLAP_SIZE=100 자 prefix
        first_tail = pieces[0][-_OVERLAP_SIZE:]
        # second 의 처음 일부가 first_tail 의 일부 substring 이어야 함
        # (정확한 prefix 동등은 strip 효과로 약간 어긋날 수 있음 → substring 확인)
        overlap_start = pieces[1][:_OVERLAP_SIZE]
        # 두번째 청크 시작에 first_tail 의 어떤 substring 이 등장해야 함
        # 단순화 — first_tail 의 마지막 30 자가 second 의 head 어딘가에 있는지
        self.assertIn(first_tail[-30:], overlap_start)

    def test_single_piece_no_overlap(self) -> None:
        """청크 1개면 overlap 적용 안 함."""
        from app.ingest.stages.chunk import _apply_overlap

        out = _apply_overlap(["하나의 청크만 있다."])
        self.assertEqual(out, ["하나의 청크만 있다."])

    def test_empty_pieces_safe(self) -> None:
        """빈 입력 안전 처리."""
        from app.ingest.stages.chunk import _apply_overlap

        self.assertEqual(_apply_overlap([]), [])

    def test_short_prev_piece_uses_full_text(self) -> None:
        """이전 청크가 _OVERLAP_SIZE 미만이면 전체를 prefix 로 사용."""
        from app.ingest.stages.chunk import _apply_overlap

        out = _apply_overlap(["짧다.", "다음 청크 본문이다."])
        # 두번째 청크의 시작이 "짧다." 를 포함해야 함
        self.assertTrue(out[1].startswith("짧다."))

    def test_overlap_respects_max_size(self) -> None:
        """overlap 적용 후 _MAX_SIZE 초과하지 않음 (budget 계산)."""
        from app.ingest.stages.chunk import _MAX_SIZE, _apply_overlap

        # 두번째 청크가 이미 _MAX_SIZE 에 가까우면 overlap 줄어듬
        prev = "가" * 200  # 100자 prefix 추출됨
        cur = "나" * (_MAX_SIZE - 50)  # budget = 50자
        out = _apply_overlap([prev, cur])
        self.assertLessEqual(len(out[1]), _MAX_SIZE)

    def test_overlap_at_max_size_skipped(self) -> None:
        """현재 청크가 이미 _MAX_SIZE 면 overlap 생략."""
        from app.ingest.stages.chunk import _MAX_SIZE, _apply_overlap

        prev = "가" * 200
        cur = "나" * _MAX_SIZE  # budget = 0 → overlap 생략
        out = _apply_overlap([prev, cur])
        self.assertEqual(out[1], cur)


# =====================================================================
# 4.5 — section_title 우선순위 swap
# =====================================================================


class SectionTitleSwapTest(unittest.TestCase):
    """병합 시 section.section_title 우선 검증."""

    def test_merge_prefers_section_title_over_buf(self) -> None:
        """buf·section 둘 다 title 있으면 section 우선 (W4 swap)."""
        from app.adapters.parser import ExtractedSection
        from app.ingest.stages.chunk import _merge_short_sections

        sections = [
            ExtractedSection(text="짧은 청크.", page=1, section_title="구 title"),
            ExtractedSection(text="다음 짧은 청크.", page=1, section_title="신 title"),
        ]
        merged = _merge_short_sections(sections)
        self.assertEqual(len(merged), 1)  # 둘 다 _MIN_MERGE_SIZE 미만이라 병합
        self.assertEqual(merged[0].section_title, "신 title")

    def test_merge_buf_title_when_section_none(self) -> None:
        """section.section_title None 이면 buf.section_title fallback."""
        from app.adapters.parser import ExtractedSection
        from app.ingest.stages.chunk import _merge_short_sections

        sections = [
            ExtractedSection(text="짧은 청크.", page=1, section_title="buf title"),
            ExtractedSection(text="다음 짧은 청크.", page=1, section_title=None),
        ]
        merged = _merge_short_sections(sections)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].section_title, "buf title")

    def test_merge_section_title_when_buf_none(self) -> None:
        """buf.section_title None 인데 section 만 있으면 section 채택."""
        from app.adapters.parser import ExtractedSection
        from app.ingest.stages.chunk import _merge_short_sections

        sections = [
            ExtractedSection(text="짧은 청크.", page=1, section_title=None),
            ExtractedSection(text="다음 짧은 청크.", page=1, section_title="section title"),
        ]
        merged = _merge_short_sections(sections)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].section_title, "section title")

    def test_no_merge_different_pages_preserves_titles(self) -> None:
        """다른 페이지면 병합 안 함 — 각자 title 보존."""
        from app.adapters.parser import ExtractedSection
        from app.ingest.stages.chunk import _merge_short_sections

        sections = [
            ExtractedSection(text="짧은 청크.", page=1, section_title="page1"),
            ExtractedSection(text="다음 짧은 청크.", page=2, section_title="page2"),
        ]
        merged = _merge_short_sections(sections)
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0].section_title, "page1")
        self.assertEqual(merged[1].section_title, "page2")


# =====================================================================
# 통합 — _to_chunk_records 의 overlap 메타 + 전체 파이프라인
# =====================================================================


class IntegrationTest(unittest.TestCase):
    """내부 파이프라인 함수 직접 호출 — `run_chunk_stage` 의 DB 의존 회피."""

    def test_chunk_record_records_overlap_meta(self) -> None:
        """idx > 0 청크는 metadata 에 overlap_with_prev_chunk_idx 기록."""
        from app.adapters.parser import ExtractedSection
        from app.ingest.stages.chunk import (
            _merge_short_sections,
            _split_long_sections,
            _to_chunk_records,
        )

        # _MAX_SIZE 초과 단일 섹션 → split 유도 → 인접 청크 다수 생성
        long_text = ". ".join([f"이것은 한국어 문장 번호 {i}이다" for i in range(80)]) + "."
        sections = [ExtractedSection(text=long_text, page=1, section_title="긴 섹션")]
        split = _split_long_sections(sections)
        merged = _merge_short_sections(split)
        records = _to_chunk_records(doc_id="doc-test", sections=merged)
        self.assertGreaterEqual(len(records), 2)
        # 첫 청크는 overlap 메타 없음
        self.assertNotIn("overlap_with_prev_chunk_idx", records[0].metadata)
        # 두번째부터는 idx-1 기록
        self.assertEqual(
            records[1].metadata.get("overlap_with_prev_chunk_idx"), 0
        )

    def test_max_size_invariant_post_overlap(self) -> None:
        """split + overlap 적용 후에도 어떤 청크도 _MAX_SIZE 초과 안 함."""
        from app.adapters.parser import ExtractedSection
        from app.ingest.stages.chunk import (
            _MAX_SIZE,
            _merge_short_sections,
            _split_long_sections,
            _to_chunk_records,
        )

        long_text = "한국어 본문이다. " * 200  # ≈ 1800 chars
        sections = [ExtractedSection(text=long_text, page=1, section_title="t")]
        split = _split_long_sections(sections)
        merged = _merge_short_sections(split)
        records = _to_chunk_records(doc_id="doc-test", sections=merged)
        for r in records:
            self.assertLessEqual(
                len(r.text),
                _MAX_SIZE,
                f"청크 {r.chunk_idx} 길이 {len(r.text)} > _MAX_SIZE",
            )


if __name__ == "__main__":
    unittest.main()
