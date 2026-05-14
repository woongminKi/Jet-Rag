"""Query 9 라벨 룰 분류 — production 측 표준 위치.

원래 `evals/auto_goldenset.py` 에 있던 룰 기반 분류 함수를 production 모듈로 이전.
역방향 의존 (`api/app/routers/admin.py` → `evals/auto_goldenset.py` lazy import) 해소.

라벨 9종 (PRD §1.5 + master plan §8.2):
    exact_fact / fuzzy_memory / synonym_mismatch / numeric_lookup /
    table_lookup / vision_diagram / summary / cross_doc / out_of_scope

`/admin/queries/stats` 가 query_type 분포 집계 시 호출. evals 측은 본 모듈을 import
하여 backward-compat 유지 (`from auto_goldenset import classify_query_type` 호출도
`auto_goldenset.py` 가 본 모듈에서 re-export 하므로 그대로 작동).

CLAUDE.md 정합 — DB 호출 0, 외부 의존 0 (stdlib re + typing).
"""

from __future__ import annotations

import re
from typing import Literal

QueryType = Literal[
    "exact_fact",
    "fuzzy_memory",
    "synonym_mismatch",
    "numeric_lookup",
    "table_lookup",
    "vision_diagram",
    "summary",
    "cross_doc",
    "out_of_scope",
]

QUERY_TYPE_LABELS: tuple[QueryType, ...] = (
    "exact_fact",
    "fuzzy_memory",
    "synonym_mismatch",
    "numeric_lookup",
    "table_lookup",
    "vision_diagram",
    "summary",
    "cross_doc",
    "out_of_scope",
)

# 룰 키워드 (substring 매칭 — 한국어 토큰화 의존 0)
_VISION_KEYWORDS: tuple[str, ...] = (
    "다이어그램", "그림", "도식", "구조도", "이미지", "사진", "도표",
)
_TABLE_KEYWORDS: tuple[str, ...] = (
    "표", "리스트", "목록", "별표", "카테고리", "항목 목록",
)
_SUMMARY_KEYWORDS: tuple[str, ...] = (
    "요약", "핵심", "정리", "개요", "짧게", "한줄", "한 줄",
)
_CROSS_DOC_KEYWORDS: tuple[str, ...] = (
    "비교", "차이", "대비", "달라", "차이점",
)
_FUZZY_KEYWORDS: tuple[str, ...] = (
    "그때", "어디 있더라", "어디 있었", "뭐였지", "있었나", "있었지",
    "었더라", "았더라", "기억나",
)
_NUMERIC_PATTERNS: tuple[re.Pattern[str], ...] = (
    # 단위 alternation 은 **긴 것 먼저** — "개월" 이 "개" 보다 먼저 매칭되도록.
    re.compile(r"\d+(?:\.\d+)?\s*(?:개월|시간|kg|km|cm|%|원|년|월|일|회|건|개|점|명|분|초|m)"),
    re.compile(r"몇\s*[가-힣]"),
    re.compile(r"얼마"),
)
_NUMERIC_KEYWORDS: tuple[str, ...] = (
    "얼마", "금액", "가격", "비용", "수치", "수량", "개수", "지원금",
)

# 동의어 쌍 — query 가 한쪽 표현, source 가 반대편 표현이면 synonym_mismatch.
# (a, b): query 안에 a 가 있고 source 안에 b 가 있거나 반대일 때.
_SYNONYM_PAIRS: tuple[tuple[str, str], ...] = (
    ("개인정보", "비식별화"),
    ("환자 정보", "비식별화"),
    ("색상", "컬러"),
    ("시트", "가죽"),
    ("규정", "내규"),
    ("직원", "임직원"),
    ("회의", "협의"),
)


def classify_query_type(
    query: str,
    *,
    source_chunk_text: str = "",
    expected_doc_titles: list[str] | None = None,
    is_negative: bool = False,
) -> QueryType:
    """query → 9 라벨 중 1개 (룰 기반).

    우선순위:
    1. is_negative=True → out_of_scope
    2. vision_diagram (그림/다이어그램 키워드)
    3. table_lookup (표/목록 키워드)
    4. cross_doc (비교 키워드 또는 doc_title 2개 이상)
    5. numeric_lookup (숫자 패턴 또는 금액 키워드)
    6. summary (요약 키워드)
    7. synonym_mismatch (동의어 쌍 cross 매칭)
    8. fuzzy_memory (흐릿한 톤 키워드)
    9. exact_fact (default)
    """
    if is_negative:
        return "out_of_scope"

    q = query.strip()

    if any(kw in q for kw in _VISION_KEYWORDS):
        return "vision_diagram"

    if any(kw in q for kw in _TABLE_KEYWORDS):
        return "table_lookup"

    if expected_doc_titles and len(expected_doc_titles) >= 2:
        return "cross_doc"
    if any(kw in q for kw in _CROSS_DOC_KEYWORDS):
        return "cross_doc"

    if any(p.search(q) for p in _NUMERIC_PATTERNS):
        return "numeric_lookup"
    if any(kw in q for kw in _NUMERIC_KEYWORDS):
        return "numeric_lookup"

    if any(kw in q for kw in _SUMMARY_KEYWORDS):
        return "summary"

    if source_chunk_text:
        for term_a, term_b in _SYNONYM_PAIRS:
            in_query_a = term_a in q
            in_query_b = term_b in q
            in_source_a = term_a in source_chunk_text
            in_source_b = term_b in source_chunk_text
            # query 가 한쪽, source 가 반대편이면 synonym_mismatch
            if (in_query_a and in_source_b and not in_source_a) or (
                in_query_b and in_source_a and not in_source_b
            ):
                return "synonym_mismatch"

    if any(kw in q for kw in _FUZZY_KEYWORDS):
        return "fuzzy_memory"

    return "exact_fact"
