"""S4-B 핵심 엔티티 추출 (룰 기반 1차) — master plan §6 P1 (S4-B).

목적
----
chunks 의 metadata 에 핵심 엔티티 (날짜, 금액, 식별 번호, 비율) 자동 추출 →
search 시 단편 정확도 향상. 룰 기반 (정규식) 우선 — Flash-Lite LLM 보강은 별도 sprint.

설계 원칙
- **외부 의존성 0** — 정규식만 사용
- **graceful** — 매칭 실패 시 빈 list (raise X)
- **Korean-first** — 한국어 날짜/금액 패턴 우선 매칭
- **dedup** — 같은 string 중복 제거

추출 범위 (룰 기반)
- **dates**: 2024년 4월 30일 / 2024.04.30 / 2024-04-30 / 2024.4. / 24/4/30 등
- **amounts**: 1,000원 / 100만원 / $100 / 1억원 / 50% / 25.5% 등
- **identifiers**: 법령번호 / 보고서번호 / 표준번호 (ISSN, ISBN 등)
- **percentages**: 1.5%, 25%, 100%

LLM 보강 영역 (별도 sprint)
- 제품명 (예: "쏘나타 디 엣지", "Indigo Book")
- 기관명 (예: "한국은행", "한마음생활체육관")
- 인명 (예: "김뮤지", "아리아나")

사용
----
    from app.services.entity_extract import extract_entities

    text = "2024년 4월 30일부터 시행. 회비 50,000원 (50%) — 한국은행 ISSN 2288-7083"
    entities = extract_entities(text)
    # {"dates": ["2024년 4월 30일"], "amounts": ["50,000원"],
    #  "identifiers": ["2288-7083"], "percentages": ["50%"]}
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 날짜 — 한국어 + ISO 형식 다양.
# `\b` 가 한국어 인접 시 매칭 불안정 (Korean char 가 \w 라 boundary X).
# → 한국어 패턴은 \b 제거 + greedy match 의존, ISO 패턴은 lookbehind/lookahead 사용.
_DATE_PATTERNS = [
    # 2024년 4월 30일 / 2024년 4월 (optional "일" group)
    re.compile(r"(\d{4})\s*년\s*(\d{1,2})\s*월(?:\s*(\d{1,2})\s*일)?"),
    # 2024.04.30 / 2024-04-30 / 2024/4/30 (ISO, ASCII boundary)
    re.compile(r"(?<!\d)(\d{4})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})(?!\d)"),
    # 2024.4 / 2024-04 (월까지만)
    re.compile(r"(?<!\d)(\d{4})[.\-]\s*(\d{1,2})(?!\d)"),
]

# 금액 — 1,000원 / 100만원 / 1억원 / $100 / ₩1,000
_AMOUNT_PATTERNS = [
    # 50,000원 / 1,234.56원 / 1,000,000원
    re.compile(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\s*원\b"),
    # 100만원 / 1억원 / 50조원
    re.compile(r"\b\d{1,4}(?:\.\d+)?\s*(?:만|억|조|천)\s*원\b"),
    # $100 / $1,000.50
    re.compile(r"\$\d{1,3}(?:,\d{3})*(?:\.\d+)?\b"),
    # ₩1,000
    re.compile(r"₩\d{1,3}(?:,\d{3})*(?:\.\d+)?\b"),
]

# 백분율 — 25%, 1.5%, 100%
_PERCENT_PATTERN = re.compile(r"\b\d{1,3}(?:\.\d+)?\s*%")

# 식별자 — ISSN/ISBN/법령번호 등
_IDENTIFIER_PATTERNS = [
    # ISSN 2288-7083
    re.compile(r"\bISSN\s+(\d{4}-\d{3}[\dXx])\b"),
    # ISBN 978-0-...
    re.compile(r"\bISBN\s+([\d\-Xx]{10,17})\b"),
    # 내규 제709호 / 법률 제12345호
    re.compile(r"제\s*\d+\s*호"),
]


@dataclass
class ExtractedEntities:
    dates: list[str]
    amounts: list[str]
    percentages: list[str]
    identifiers: list[str]
    # 2026-05-10 — Flash-Lite LLM 보강 영역 (extract_entities_with_llm 시 채워짐)
    persons: list[str] | None = None
    orgs: list[str] | None = None
    products: list[str] | None = None

    def to_dict(self) -> dict[str, list[str]]:
        d: dict[str, list[str]] = {
            "dates": self.dates,
            "amounts": self.amounts,
            "percentages": self.percentages,
            "identifiers": self.identifiers,
        }
        # LLM 보강 영역 — None 이 아닌 경우만 포함 (룰 기반 only 응답 호환)
        if self.persons is not None:
            d["persons"] = self.persons
        if self.orgs is not None:
            d["orgs"] = self.orgs
        if self.products is not None:
            d["products"] = self.products
        return d

    def is_empty(self) -> bool:
        return not (
            self.dates or self.amounts or self.percentages or self.identifiers
            or self.persons or self.orgs or self.products
        )


def _dedup_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        s = it.strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def extract_dates(text: str) -> list[str]:
    out: list[str] = []
    if not text:
        return out
    for pattern in _DATE_PATTERNS:
        for m in pattern.finditer(text):
            out.append(m.group(0).strip())
    return _dedup_preserve_order(out)


def extract_amounts(text: str) -> list[str]:
    out: list[str] = []
    if not text:
        return out
    for pattern in _AMOUNT_PATTERNS:
        for m in pattern.finditer(text):
            out.append(m.group(0).strip())
    return _dedup_preserve_order(out)


def extract_percentages(text: str) -> list[str]:
    if not text:
        return []
    return _dedup_preserve_order(
        m.group(0).strip() for m in _PERCENT_PATTERN.finditer(text)
    )


def extract_identifiers(text: str) -> list[str]:
    out: list[str] = []
    if not text:
        return out
    for pattern in _IDENTIFIER_PATTERNS:
        for m in pattern.finditer(text):
            # group(1) 있으면 captured part, 아니면 전체 match
            value = m.group(1) if m.lastindex else m.group(0)
            out.append(value.strip())
    return _dedup_preserve_order(out)


def extract_entities(text: str) -> ExtractedEntities:
    """텍스트에서 룰 기반 엔티티 일괄 추출."""
    return ExtractedEntities(
        dates=extract_dates(text),
        amounts=extract_amounts(text),
        percentages=extract_percentages(text),
        identifiers=extract_identifiers(text),
    )


# ---------------------------------------------------------------------------
# Flash-Lite LLM 보강 — 비정형 entities (persons / orgs / products)
# 2026-05-10 — master plan §6 P1 (S4-B) — Flash-Lite 보강.
# 룰 기반은 정형 패턴 (date/amount/%) 정확. 비정형 (인명/기관명/제품명) 은 LLM 필요.
# ---------------------------------------------------------------------------

_LLM_SYSTEM_PROMPT = """당신은 한국어 엔티티 추출 전문가입니다.
주어진 텍스트에서 다음 비정형 엔티티를 JSON 으로 추출하세요:
- persons: 인명 (예: "김뮤지", "이한주")
- orgs: 기관명 (예: "한국은행", "한마음생활체육관")
- products: 제품/시스템/모델명 (예: "쏘나타 디 엣지", "Indigo Book", "BGE-M3")

JSON object 만 반환 (markdown fence 금지):
{"persons": [...], "orgs": [...], "products": [...]}

빈 list 사용 (null 금지). 동일 string 중복 제거. 일반 명사 (예: "사용자", "회사") 제외.
"""


def parse_llm_entities(raw: str) -> dict[str, list[str]]:
    """LLM JSON → {persons, orgs, products}. 누락 카테고리 빈 list."""
    import json

    cleaned = raw.strip()
    # markdown fence 제거 시도
    if cleaned.startswith("```"):
        # ```json\n...\n``` 또는 ```\n...\n```
        lines = cleaned.split("\n")
        if len(lines) >= 2:
            cleaned = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
    try:
        d = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"LLM JSON parse 실패: {exc}\nraw: {raw[:300]}") from exc
    if not isinstance(d, dict):
        raise RuntimeError(f"LLM 응답 dict 아님: {type(d)}")
    out: dict[str, list[str]] = {}
    for key in ("persons", "orgs", "products"):
        vals = d.get(key) or []
        if not isinstance(vals, list):
            vals = []
        # str only + dedup + strip
        seen: set[str] = set()
        clean_vals: list[str] = []
        for v in vals:
            if not isinstance(v, str):
                continue
            s = v.strip()
            if s and s not in seen:
                seen.add(s)
                clean_vals.append(s)
        out[key] = clean_vals
    return out


def extract_entities_with_llm(
    text: str,
    *,
    llm_call,  # callable(system, user) -> str (raw JSON)
    rule_based: ExtractedEntities | None = None,
) -> ExtractedEntities:
    """룰 기반 + LLM 보강.

    `llm_call(system, user) -> raw_json_str` 콜백으로 의존성 주입 (테스트 용이).
    LLM 실패 시 룰 기반 결과만 반환 (graceful).
    """
    base = rule_based if rule_based is not None else extract_entities(text)
    if not text or not text.strip():
        return base
    try:
        raw = llm_call(_LLM_SYSTEM_PROMPT, text[:2000])  # text 길이 cap
        llm_ents = parse_llm_entities(raw)
    except Exception:  # noqa: BLE001
        return base  # 룰 기반만 반환 (LLM 실패 graceful)
    return ExtractedEntities(
        dates=base.dates,
        amounts=base.amounts,
        percentages=base.percentages,
        identifiers=base.identifiers,
        persons=llm_ents.get("persons") or [],
        orgs=llm_ents.get("orgs") or [],
        products=llm_ents.get("products") or [],
    )
