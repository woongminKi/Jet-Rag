"""S4-B entity_extract.py 단위 테스트.

검증 범위
- extract_dates: 한국어 / ISO / 다양 변형 매칭
- extract_amounts: 원/만원/억원/$/₩ 다양 매칭
- extract_percentages: 25% / 1.5%
- extract_identifiers: ISSN / ISBN / 내규 제N호
- extract_entities: 통합 + dedup + is_empty

stdlib unittest only — 외부 의존성 0.
"""

from __future__ import annotations

import unittest


class ExtractDatesTest(unittest.TestCase):
    def test_korean_full_date(self) -> None:
        from app.services.entity_extract import extract_dates

        text = "이 내규는 2024년 4월 30일부터 시행한다."
        self.assertIn("2024년 4월 30일", extract_dates(text))

    def test_korean_year_month_only(self) -> None:
        from app.services.entity_extract import extract_dates

        text = "2026년 2월 기준"
        self.assertIn("2026년 2월", extract_dates(text))

    def test_iso_format(self) -> None:
        from app.services.entity_extract import extract_dates

        text = "공시일: 2024-04-30, 변경: 2024.04.30, 보관: 2024/4/30"
        results = extract_dates(text)
        self.assertIn("2024-04-30", results)
        self.assertIn("2024.04.30", results)
        self.assertIn("2024/4/30", results)

    def test_dedup(self) -> None:
        from app.services.entity_extract import extract_dates

        text = "2024년 4월 30일 / 2024년 4월 30일 (중복)"
        self.assertEqual(extract_dates(text).count("2024년 4월 30일"), 1)

    def test_empty_text(self) -> None:
        from app.services.entity_extract import extract_dates

        self.assertEqual(extract_dates(""), [])
        self.assertEqual(extract_dates("\n\t  "), [])  # actually \n is matched here, ok skip


class ExtractAmountsTest(unittest.TestCase):
    def test_won_with_commas(self) -> None:
        from app.services.entity_extract import extract_amounts

        text = "회비 50,000원, 보증금 1,000,000원"
        results = extract_amounts(text)
        self.assertIn("50,000원", results)
        self.assertIn("1,000,000원", results)

    def test_korean_unit_amounts(self) -> None:
        from app.services.entity_extract import extract_amounts

        text = "수입 100만원, 자산 1억원, 예산 50조원"
        results = extract_amounts(text)
        self.assertIn("100만원", results)
        self.assertIn("1억원", results)
        self.assertIn("50조원", results)

    def test_dollar_amount(self) -> None:
        from app.services.entity_extract import extract_amounts

        text = "비용 $100, 추가 $1,234.56"
        results = extract_amounts(text)
        self.assertIn("$100", results)
        self.assertIn("$1,234.56", results)

    def test_no_match(self) -> None:
        from app.services.entity_extract import extract_amounts

        # "100" 단독은 amount 아님
        self.assertEqual(extract_amounts("그냥 100 같은 수"), [])


class ExtractPercentagesTest(unittest.TestCase):
    def test_integer_percent(self) -> None:
        from app.services.entity_extract import extract_percentages

        results = extract_percentages("성장률 5%, 변동 25%, 100%")
        self.assertIn("5%", results)
        self.assertIn("25%", results)
        self.assertIn("100%", results)

    def test_decimal_percent(self) -> None:
        from app.services.entity_extract import extract_percentages

        results = extract_percentages("물가 1.5%, 변동 0.3%")
        self.assertIn("1.5%", results)
        self.assertIn("0.3%", results)


class ExtractIdentifiersTest(unittest.TestCase):
    def test_issn(self) -> None:
        from app.services.entity_extract import extract_identifiers

        text = "ISSN 2288-7083"
        self.assertIn("2288-7083", extract_identifiers(text))

    def test_korean_law_number(self) -> None:
        from app.services.entity_extract import extract_identifiers

        text = "내규 제709호 부칙"
        results = extract_identifiers(text)
        # 제 N 호 — 전체 string 매칭 (group(1) 없음)
        self.assertTrue(any("709" in r for r in results))


class ExtractEntitiesTest(unittest.TestCase):
    def test_integrated(self) -> None:
        from app.services.entity_extract import extract_entities

        text = (
            "2024년 4월 30일부터 시행. 회비 50,000원 (50%) — "
            "한국은행 ISSN 2288-7083"
        )
        result = extract_entities(text)
        self.assertIn("2024년 4월 30일", result.dates)
        self.assertIn("50,000원", result.amounts)
        self.assertIn("50%", result.percentages)
        self.assertIn("2288-7083", result.identifiers)
        self.assertFalse(result.is_empty())

    def test_to_dict(self) -> None:
        from app.services.entity_extract import extract_entities

        result = extract_entities("2024년 4월")
        d = result.to_dict()
        self.assertEqual(set(d.keys()), {"dates", "amounts", "percentages", "identifiers"})
        self.assertIn("2024년 4월", d["dates"])

    def test_empty_text_is_empty_result(self) -> None:
        from app.services.entity_extract import extract_entities

        result = extract_entities("그냥 일반 텍스트만 있어요")
        self.assertTrue(result.is_empty())


class ParseLLMEntitiesTest(unittest.TestCase):
    def test_parses_valid_json(self) -> None:
        from app.services.entity_extract import parse_llm_entities
        import json

        raw = json.dumps({
            "persons": ["김뮤지", "이한주"],
            "orgs": ["한국은행"],
            "products": ["쏘나타 디 엣지", "Indigo Book"],
        })
        result = parse_llm_entities(raw)
        self.assertEqual(result["persons"], ["김뮤지", "이한주"])
        self.assertEqual(result["orgs"], ["한국은행"])
        self.assertEqual(result["products"], ["쏘나타 디 엣지", "Indigo Book"])

    def test_strips_markdown_fence(self) -> None:
        from app.services.entity_extract import parse_llm_entities
        import json

        raw = "```json\n" + json.dumps({"persons": ["X"], "orgs": [], "products": []}) + "\n```"
        result = parse_llm_entities(raw)
        self.assertEqual(result["persons"], ["X"])

    def test_missing_keys_default_empty(self) -> None:
        from app.services.entity_extract import parse_llm_entities

        result = parse_llm_entities('{"persons": ["A"]}')
        self.assertEqual(result["persons"], ["A"])
        self.assertEqual(result["orgs"], [])
        self.assertEqual(result["products"], [])

    def test_dedup_and_strip(self) -> None:
        from app.services.entity_extract import parse_llm_entities

        result = parse_llm_entities('{"persons": ["A", "A", "  B  "], "orgs": null, "products": []}')
        self.assertEqual(result["persons"], ["A", "B"])

    def test_invalid_json_raises(self) -> None:
        from app.services.entity_extract import parse_llm_entities

        with self.assertRaises(RuntimeError):
            parse_llm_entities("{not json}")

    def test_non_dict_raises(self) -> None:
        from app.services.entity_extract import parse_llm_entities

        with self.assertRaises(RuntimeError):
            parse_llm_entities('["a", "b"]')


class ExtractEntitiesWithLLMTest(unittest.TestCase):
    def test_integrates_rule_and_llm(self) -> None:
        from app.services.entity_extract import extract_entities_with_llm
        import json

        text = "2024년 4월 30일 한국은행 ISSN 2288-7083 발행"

        def mock_llm(system: str, user: str) -> str:
            return json.dumps({
                "persons": [],
                "orgs": ["한국은행"],
                "products": [],
            })

        result = extract_entities_with_llm(text, llm_call=mock_llm)
        # 룰 기반: dates + identifiers
        self.assertIn("2024년 4월 30일", result.dates)
        self.assertIn("2288-7083", result.identifiers)
        # LLM: orgs
        self.assertEqual(result.orgs, ["한국은행"])
        self.assertEqual(result.persons, [])
        self.assertEqual(result.products, [])

    def test_llm_failure_returns_rule_based_only(self) -> None:
        from app.services.entity_extract import extract_entities_with_llm

        text = "2024년 4월 30일 시행"

        def failing_llm(system: str, user: str) -> str:
            raise RuntimeError("LLM API down")

        result = extract_entities_with_llm(text, llm_call=failing_llm)
        # 룰 기반 결과는 있음
        self.assertIn("2024년 4월 30일", result.dates)
        # LLM 실패 → persons/orgs/products = None
        self.assertIsNone(result.persons)
        self.assertIsNone(result.orgs)

    def test_to_dict_includes_llm_fields_when_set(self) -> None:
        from app.services.entity_extract import extract_entities_with_llm
        import json

        def mock_llm(system: str, user: str) -> str:
            return json.dumps({"persons": ["X"], "orgs": ["Y"], "products": ["Z"]})

        result = extract_entities_with_llm("text", llm_call=mock_llm)
        d = result.to_dict()
        self.assertIn("persons", d)
        self.assertIn("orgs", d)
        self.assertIn("products", d)

    def test_to_dict_omits_llm_fields_when_rule_only(self) -> None:
        from app.services.entity_extract import extract_entities

        result = extract_entities("2024년 4월")
        d = result.to_dict()
        # 룰 기반만 → persons/orgs/products 키 없음 (None)
        self.assertNotIn("persons", d)
        self.assertNotIn("orgs", d)
        self.assertNotIn("products", d)


if __name__ == "__main__":
    unittest.main()
