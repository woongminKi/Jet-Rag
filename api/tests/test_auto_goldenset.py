"""W26 S1 D1 — auto_goldenset.py v2 룰 기반 함수 단위 테스트.

검증 범위
- query_type 9 라벨 룰 분류 (vision/table/numeric/cross_doc/summary/synonym/fuzzy/exact/negative)
- must_include 추출 (숫자 + 한글 명사, stopword 필터)
- source_hint 포맷 (page 있음/없음/잘못된 값)
- expected_answer_summary 룰 요약 (60자 cap, 공백 정리)
- build_negative_rows — 5건 사전 정의 schema 정합
- v0.7 schema 12 컬럼 정합 (v0.6 user 와 호환)

stdlib unittest 만 사용 — 외부 의존성 0, DB 연결 0 (CLAUDE.md 준수).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# evals/ 의 auto_goldenset import — 본 테스트는 api/tests/ 안이라 path 보정 필요.
_EVALS_DIR = Path(__file__).resolve().parents[2] / "evals"
sys.path.insert(0, str(_EVALS_DIR))


class ClassifyQueryTypeReExportTest(unittest.TestCase):
    """`from auto_goldenset import classify_query_type` re-export 호환 검증.

    분류 룰 본체 검증은 `tests/test_query_classifier.py` 로 이전. 본 클래스는
    evals 측 alias 가 production 모듈 (`app.services.query_classifier`) 과 동일
    객체를 가리키는지만 sanity check (backward compat 보호).
    """

    def test_alias_is_production_function(self) -> None:
        """auto_goldenset.classify_query_type == query_classifier.classify_query_type."""
        from auto_goldenset import classify_query_type as alias

        from app.services.query_classifier import classify_query_type as prod

        self.assertIs(alias, prod)

    def test_alias_smoke_call(self) -> None:
        """alias 호출이 9 라벨 중 1개 반환 (smoke)."""
        from auto_goldenset import classify_query_type

        from app.services.query_classifier import QUERY_TYPE_LABELS

        result = classify_query_type("이용료 얼마야")
        self.assertIn(result, QUERY_TYPE_LABELS)


class ExtractMustIncludeTest(unittest.TestCase):
    """must_include 추출 룰 검증."""

    def test_numeric_with_unit(self) -> None:
        """숫자+단위 토큰 추출 — 단위 포함 정확."""
        from auto_goldenset import extract_must_include
        text = "지원금은 최대 5000원이며, 12개월 동안 100% 지원됩니다"
        result = extract_must_include(text)
        # 숫자 토큰들 (단위 정규화 후, 공백 제거됨) 이 결과에 포함
        result_str = ";".join(result)
        self.assertIn("5000원", result_str)
        self.assertIn("12개월", result_str)
        self.assertIn("100%", result_str)

    def test_korean_noun_extraction(self) -> None:
        """3~8 글자 한글 토큰 추출 — stopword 제외."""
        from auto_goldenset import extract_must_include
        text = "보건의료 빅데이터 플랫폼은 비식별화 방안을 통해 운영합니다"
        result = extract_must_include(text)
        # 한글 토큰 (stopword 아닌 것) 포함
        result_str = ";".join(result)
        # "보건의료", "빅데이터", "비식별화" 같은 의미 토큰 중 1개 이상 포함
        meaningful = ("보건의료", "빅데이터", "비식별화", "플랫폼")
        self.assertTrue(
            any(t in result_str for t in meaningful),
            msg=f"의미 있는 한글 토큰이 하나도 추출되지 않음: {result}",
        )

    def test_stopword_filtered(self) -> None:
        """stopword (있습니다·따라서 등) 는 제외."""
        from auto_goldenset import extract_must_include
        text = "있습니다 합니다 따라서 그리고 운영 관리 적용 결과 사용 활용"
        result = extract_must_include(text)
        # 모두 stopword — 결과는 빈 리스트 또는 매우 적음
        for stopword in ("있습니다", "합니다", "따라서", "그리고", "운영", "관리", "적용"):
            self.assertNotIn(stopword, result)

    def test_max_total_cap(self) -> None:
        """총 5개 cap."""
        from auto_goldenset import extract_must_include
        text = (
            "5000원 12개월 100% 30년 50일 "
            "보건의료 빅데이터 플랫폼 비식별화 시범사업"
        )
        result = extract_must_include(text)
        self.assertLessEqual(len(result), 5, msg=f"5 cap 초과: {result}")

    def test_empty_input(self) -> None:
        """빈 입력 → 빈 리스트."""
        from auto_goldenset import extract_must_include
        self.assertEqual(extract_must_include(""), [])
        self.assertEqual(extract_must_include("   "), [])


class ExtractSourceHintTest(unittest.TestCase):
    """source_hint 포맷 검증."""

    def test_page_present(self) -> None:
        """page 정수 → 'p.{N}'."""
        from auto_goldenset import extract_source_hint
        self.assertEqual(extract_source_hint({"page": 6}), "p.6")
        self.assertEqual(extract_source_hint({"page": 100}), "p.100")

    def test_page_missing(self) -> None:
        """page 키 없음 또는 None → 빈 문자열."""
        from auto_goldenset import extract_source_hint
        self.assertEqual(extract_source_hint({}), "")
        self.assertEqual(extract_source_hint({"page": None}), "")

    def test_page_invalid(self) -> None:
        """page 가 0/음수/문자열 → 빈 문자열 또는 정수 변환 후 양수만."""
        from auto_goldenset import extract_source_hint
        self.assertEqual(extract_source_hint({"page": 0}), "")
        self.assertEqual(extract_source_hint({"page": -1}), "")
        self.assertEqual(extract_source_hint({"page": "abc"}), "")
        # 문자열 정수는 변환 가능
        self.assertEqual(extract_source_hint({"page": "5"}), "p.5")


class SummarizeExpectedAnswerTest(unittest.TestCase):
    """expected_answer_summary 룰 요약 검증."""

    def test_60_char_cap(self) -> None:
        """60자 cap."""
        from auto_goldenset import summarize_for_expected_answer
        long_text = "가" * 200
        result = summarize_for_expected_answer(long_text)
        self.assertEqual(len(result), 60)

    def test_whitespace_cleaned(self) -> None:
        """개행·중복 공백 → 단일 공백."""
        from auto_goldenset import summarize_for_expected_answer
        text = "보건의료\n\n빅데이터    플랫폼은\n시범사업"
        result = summarize_for_expected_answer(text)
        self.assertNotIn("\n", result)
        self.assertNotIn("  ", result)

    def test_empty_input(self) -> None:
        """빈 입력 → 빈 문자열."""
        from auto_goldenset import summarize_for_expected_answer
        self.assertEqual(summarize_for_expected_answer(""), "")
        self.assertEqual(summarize_for_expected_answer("   "), "")


class BuildNegativeRowsTest(unittest.TestCase):
    """negative/out_of_scope 5건 사전 정의 schema 검증."""

    def test_count_is_5(self) -> None:
        from auto_goldenset import build_negative_rows
        rows = build_negative_rows()
        self.assertEqual(len(rows), 5)

    def test_all_have_v07_fields(self) -> None:
        """모든 row 가 12 컬럼 schema 정합."""
        from auto_goldenset import _V07_FIELDNAMES, build_negative_rows
        rows = build_negative_rows()
        for r in rows:
            self.assertEqual(
                set(r.keys()), set(_V07_FIELDNAMES),
                msg=f"row schema mismatch: {r.keys()}",
            )

    def test_negative_flag_and_query_type(self) -> None:
        """negative='true' 이고 query_type='out_of_scope'."""
        from auto_goldenset import build_negative_rows
        rows = build_negative_rows()
        for r in rows:
            self.assertEqual(r["negative"], "true")
            self.assertEqual(r["query_type"], "out_of_scope")
            self.assertEqual(r["doc_id"], "")
            self.assertEqual(r["relevant_chunks"], "")

    def test_id_format(self) -> None:
        """id = G-N-{i:03d} 형식."""
        from auto_goldenset import build_negative_rows
        rows = build_negative_rows()
        ids = [r["id"] for r in rows]
        self.assertEqual(ids, ["G-N-001", "G-N-002", "G-N-003", "G-N-004", "G-N-005"])

    def test_start_qid_param(self) -> None:
        """start_qid 인자로 시작 번호 조정 가능."""
        from auto_goldenset import build_negative_rows
        rows = build_negative_rows(start_qid=10)
        self.assertEqual(rows[0]["id"], "G-N-010")
        self.assertEqual(rows[-1]["id"], "G-N-014")


class V07SchemaIntegrityTest(unittest.TestCase):
    """v0.7 통합 schema 정합 — v0.6 user CSV 와 호환."""

    def test_fieldnames_count(self) -> None:
        """12 컬럼."""
        from auto_goldenset import _V07_FIELDNAMES
        self.assertEqual(len(_V07_FIELDNAMES), 12)

    def test_fieldnames_include_v06_compat(self) -> None:
        """v0.6 user CSV 의 컬럼 (query_type, expected_doc_title, must_include 등) 모두 포함."""
        from auto_goldenset import _V07_FIELDNAMES
        v06_required = {
            "query", "query_type", "expected_doc_title",
            "expected_answer_summary", "must_include", "source_hint", "negative",
        }
        self.assertTrue(
            v06_required.issubset(set(_V07_FIELDNAMES)),
            msg=f"v0.6 호환 컬럼 누락: {v06_required - set(_V07_FIELDNAMES)}",
        )

    def test_fieldnames_include_v05_compat(self) -> None:
        """v0.5 auto CSV 의 컬럼 (id, doc_id, relevant_chunks 등) 모두 포함."""
        from auto_goldenset import _V07_FIELDNAMES
        v05_required = {
            "id", "doc_id", "relevant_chunks", "acceptable_chunks", "source_chunk_text",
        }
        self.assertTrue(
            v05_required.issubset(set(_V07_FIELDNAMES)),
            msg=f"v0.5 호환 컬럼 누락: {v05_required - set(_V07_FIELDNAMES)}",
        )

    def test_query_type_labels_count(self) -> None:
        """9 라벨 정확히."""
        from auto_goldenset import _QUERY_TYPE_LABELS
        self.assertEqual(len(_QUERY_TYPE_LABELS), 9)
        self.assertIn("out_of_scope", _QUERY_TYPE_LABELS)


if __name__ == "__main__":
    unittest.main()
