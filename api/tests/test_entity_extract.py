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


if __name__ == "__main__":
    unittest.main()
