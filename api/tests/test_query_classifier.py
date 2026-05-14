"""app.services.query_classifier — 9 라벨 룰 분류 단위 테스트.

원래 `tests/test_auto_goldenset.py` 의 `ClassifyQueryTypeTest` 에 있던 검증을
production 모듈 (`app.services.query_classifier`) 직접 import 로 이전.
`auto_goldenset.py` 측 re-export sanity 1건만 별도 유지.

stdlib unittest 만 사용 — 외부 의존성 0, DB 연결 0 (CLAUDE.md 준수).
"""

from __future__ import annotations

import unittest

from app.services.query_classifier import (
    QUERY_TYPE_LABELS,
    QueryType,
    classify_query_type,
)


class ClassifyQueryTypeTest(unittest.TestCase):
    """query_type 9 라벨 룰 분류 검증 — master plan §8.2 정합."""

    def test_negative_overrides_all(self) -> None:
        """is_negative=True → 다른 키워드 무관하게 out_of_scope."""
        result = classify_query_type(
            "이 자료들에 환경 다이어그램 그림 12% 나와있어?",
            is_negative=True,
        )
        self.assertEqual(result, "out_of_scope")

    def test_vision_diagram_keyword(self) -> None:
        """다이어그램·그림·도식 → vision_diagram."""
        for q in ("그 다이어그램 어떻게 생겼어", "쏘나타 인테리어 사진 보여줘", "구조도 어디 있지"):
            self.assertEqual(
                classify_query_type(q), "vision_diagram",
                msg=f"query={q!r} 가 vision_diagram 으로 분류되지 않음",
            )

    def test_table_lookup_keyword(self) -> None:
        """표·목록·리스트 → table_lookup."""
        for q in ("휠 사이즈 표 어디", "지원금 목록 알려줘", "별표 1 내용 뭐야"):
            self.assertEqual(classify_query_type(q), "table_lookup", msg=f"{q!r}")

    def test_numeric_lookup_pattern(self) -> None:
        """숫자+단위 또는 '얼마/몇' 패턴 → numeric_lookup."""
        for q in ("이용료 얼마야", "지원금 12% 어디", "체육관 5000원 인가", "몇 명 까지"):
            self.assertEqual(classify_query_type(q), "numeric_lookup", msg=f"{q!r}")

    def test_cross_doc_two_titles_or_keyword(self) -> None:
        """expected_doc_titles 2개 또는 '비교/차이' 키워드 → cross_doc."""
        result = classify_query_type(
            "두 자료 안전 관련 내용",
            expected_doc_titles=["docA", "docB"],
        )
        self.assertEqual(result, "cross_doc")
        result = classify_query_type("운영내규랑 직제규정 위원회 차이")
        self.assertEqual(result, "cross_doc")

    def test_summary_keyword(self) -> None:
        """요약·핵심·정리 → summary."""
        for q in ("보건의료 빅데이터 요약해줘", "핵심만 짧게", "전체 정리"):
            self.assertEqual(classify_query_type(q), "summary", msg=f"{q!r}")

    def test_synonym_mismatch_cross_term(self) -> None:
        """query 가 한쪽 표현, source 가 반대편 → synonym_mismatch."""
        result = classify_query_type(
            "환자 정보 보호 어떻게 해",
            source_chunk_text="개인정보 비식별화 방안을 통해 데이터를 처리합니다",
        )
        self.assertEqual(result, "synonym_mismatch")

    def test_synonym_mismatch_no_cross(self) -> None:
        """동의어 둘 다 source 에 있으면 synonym_mismatch 아님."""
        result = classify_query_type(
            "환자 정보 보호 방안",
            source_chunk_text="환자 정보 및 개인정보 비식별화 방안을 적용합니다",
        )
        self.assertNotEqual(result, "synonym_mismatch")

    def test_fuzzy_memory_keyword(self) -> None:
        """그때·뭐였지·있었나 등 흐릿한 톤 → fuzzy_memory."""
        for q in ("그때 시트 뭐였지", "쏘나타 휠 어디 있더라", "법률 자료 있었나"):
            self.assertEqual(classify_query_type(q), "fuzzy_memory", msg=f"{q!r}")

    def test_exact_fact_default(self) -> None:
        """위 분류 안 되는 단편 사실 query → exact_fact (default)."""
        for q in ("결재 라인 단계", "직제 규정 부서 구조", "프로젝트 경력"):
            self.assertEqual(classify_query_type(q), "exact_fact", msg=f"{q!r}")


class QueryTypeLabelsTest(unittest.TestCase):
    """라벨 9개 상수 정합 검증."""

    def test_9_labels_present(self) -> None:
        """`QUERY_TYPE_LABELS` 는 9개 element + 중복 0."""
        self.assertEqual(len(QUERY_TYPE_LABELS), 9)
        self.assertEqual(len(set(QUERY_TYPE_LABELS)), 9)

    def test_labels_match_classifier_output_domain(self) -> None:
        """classifier 가 반환하는 모든 가능한 값이 9 labels set 안에 있어야."""
        # 9 라벨 출력 가능한 대표 query 모음 (test cases 위와 중복 무방)
        cases: tuple[tuple[str, QueryType], ...] = (
            ("이용료 얼마야", "numeric_lookup"),
            ("그 다이어그램 어떻게", "vision_diagram"),
            ("휠 사이즈 표", "table_lookup"),
            ("자료 비교", "cross_doc"),
            ("요약 해줘", "summary"),
            ("뭐였지", "fuzzy_memory"),
            ("결재 라인", "exact_fact"),
        )
        for q, expected in cases:
            label = classify_query_type(q)
            self.assertIn(label, QUERY_TYPE_LABELS, msg=f"{q!r} → {label}")
            self.assertEqual(label, expected, msg=f"{q!r}")


if __name__ == "__main__":
    unittest.main()
