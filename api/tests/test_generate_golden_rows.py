"""B 단계 — `evals/generate_golden_rows.py` 단위 테스트.

검증 범위
- `load_examples_by_qtype` — golden v2 CSV → qtype 별 GoldenExample
- `build_prompt` — qtype × N candidate prompt 조립 (가이드 + few-shot 포함)
- `parse_candidates` — LLM JSON → CandidateRow (id 자동 부여 / 누락 필드 graceful)
- `write_candidates_csv` — 14 컬럼 CSV (golden v2 동일 schema) 라운드트립
- `CandidateRow.to_csv_row` — relevant_chunks/acceptable_chunks 빈값 + negative=false default

외부 의존성 0 — Gemini API 호출 0. JSON 입력 직접 주입으로 parse 검증.
"""

from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

_EVALS_DIR = Path(__file__).resolve().parents[2] / "evals"
sys.path.insert(0, str(_EVALS_DIR))


def _write_minimal_csv(rows: list[dict[str, str]]) -> Path:
    """golden_v2 schema 14 컬럼 minimal CSV — qtype/doc 분포 검증용."""
    fd = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, encoding="utf-8-sig", newline=""
    )
    columns = [
        "id",
        "query",
        "query_type",
        "doc_id",
        "expected_doc_title",
        "relevant_chunks",
        "acceptable_chunks",
        "source_chunk_text",
        "expected_answer_summary",
        "must_include",
        "source_hint",
        "negative",
        "doc_type",
        "caption_dependent",
    ]
    writer = csv.DictWriter(fd, fieldnames=columns)
    writer.writeheader()
    for r in rows:
        writer.writerow({c: r.get(c, "") for c in columns})
    fd.close()
    return Path(fd.name)


class LoadExamplesTest(unittest.TestCase):
    def test_groups_by_qtype(self) -> None:
        from generate_golden_rows import load_examples_by_qtype

        path = _write_minimal_csv(
            [
                {
                    "id": "G-A-001",
                    "query": "쏘나타 가격",
                    "query_type": "exact_fact",
                    "doc_id": "doc-A",
                    "expected_doc_title": "sonata",
                    "doc_type": "pdf",
                    "caption_dependent": "false",
                },
                {
                    "id": "G-U-001",
                    "query": "그때 시트 뭐였더라",
                    "query_type": "fuzzy_memory",
                    "doc_id": "doc-A",
                    "expected_doc_title": "sonata",
                    "doc_type": "pdf",
                    "caption_dependent": "false",
                },
                {
                    "id": "G-A-002",
                    "query": "쏘나타 트림",
                    "query_type": "exact_fact",
                    "doc_id": "doc-A",
                    "expected_doc_title": "sonata",
                    "doc_type": "pdf",
                    "caption_dependent": "false",
                },
            ]
        )
        out = load_examples_by_qtype(path)
        self.assertEqual(set(out.keys()), {"exact_fact", "fuzzy_memory"})
        self.assertEqual(len(out["exact_fact"]), 2)
        self.assertEqual(len(out["fuzzy_memory"]), 1)
        self.assertEqual(out["fuzzy_memory"][0].id, "G-U-001")

    def test_skips_empty_id_or_qtype(self) -> None:
        from generate_golden_rows import load_examples_by_qtype

        path = _write_minimal_csv(
            [
                {"id": "", "query": "no id", "query_type": "exact_fact"},
                {"id": "G-A-100", "query": "no qtype", "query_type": ""},
                {"id": "G-A-101", "query": "ok", "query_type": "exact_fact"},
            ]
        )
        out = load_examples_by_qtype(path)
        self.assertEqual(list(out.keys()), ["exact_fact"])
        self.assertEqual(len(out["exact_fact"]), 1)
        self.assertEqual(out["exact_fact"][0].id, "G-A-101")


class BuildPromptTest(unittest.TestCase):
    def test_includes_guidance_and_examples(self) -> None:
        from generate_golden_rows import GoldenExample, build_prompt

        examples = [
            GoldenExample(
                id="G-U-005",
                query="쏘나타 그 인테리어 사진 어떻게 생겼더라",
                query_type="vision_diagram",
                doc_id="doc-sonata",
                expected_doc_title="sonata",
                expected_answer_summary="인테리어 페이지 vision",
                must_include="인테리어;사진",
                doc_type="pdf",
                caption_dependent="true",
            )
        ]
        prompt = build_prompt(qtype="vision_diagram", examples=examples, count=3)
        self.assertIn("vision_diagram", prompt)
        self.assertIn("그림/도표/사진", prompt)
        self.assertIn("G-U-005", prompt)
        self.assertIn("3 건", prompt)

    def test_unknown_qtype_uses_empty_guidance(self) -> None:
        from generate_golden_rows import build_prompt

        prompt = build_prompt(qtype="brand_new_qtype", examples=[], count=1)
        self.assertIn("brand_new_qtype", prompt)
        # 알 수 없는 qtype 도 prompt 자체는 정상 생성 (guidance만 비어있음)
        self.assertIn("1 건", prompt)


class ParseCandidatesTest(unittest.TestCase):
    def test_parses_valid_json_and_assigns_ids(self) -> None:
        from generate_golden_rows import parse_candidates

        raw = json.dumps(
            [
                {
                    "query": "쏘나타 색상 사진 어디 있어",
                    "doc_id": "doc-sonata",
                    "expected_doc_title": "sonata",
                    "expected_answer_summary": "색상 vision 페이지",
                    "must_include": "색상;사진",
                    "doc_type": "pdf",
                    "caption_dependent": "true",
                },
                {
                    "query": "외관 디자인 도면",
                    "doc_id": "doc-sonata",
                    "expected_doc_title": "sonata",
                    "expected_answer_summary": "외관 도면",
                    "must_include": "외관;도면",
                    "doc_type": "pdf",
                    "caption_dependent": "true",
                },
            ]
        )
        candidates = parse_candidates(
            raw_json=raw, qtype="vision_diagram", id_prefix="G-Z-", start_idx=1
        )
        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0].id, "G-Z-001")
        self.assertEqual(candidates[1].id, "G-Z-002")
        self.assertEqual(candidates[0].query_type, "vision_diagram")
        self.assertEqual(candidates[0].caption_dependent, "true")

    def test_invalid_json_raises(self) -> None:
        from generate_golden_rows import parse_candidates

        with self.assertRaises(RuntimeError):
            parse_candidates(
                raw_json="{not json}", qtype="x", id_prefix="G-Z-"
            )

    def test_non_array_raises(self) -> None:
        from generate_golden_rows import parse_candidates

        with self.assertRaises(RuntimeError):
            parse_candidates(
                raw_json='{"query":"x"}', qtype="x", id_prefix="G-Z-"
            )

    def test_missing_fields_default_to_empty(self) -> None:
        from generate_golden_rows import parse_candidates

        raw = json.dumps([{"query": "minimal"}])
        candidates = parse_candidates(raw_json=raw, qtype="x", id_prefix="P-")
        self.assertEqual(candidates[0].query, "minimal")
        self.assertEqual(candidates[0].doc_id, "")
        self.assertEqual(candidates[0].caption_dependent, "false")
        self.assertEqual(candidates[0].id, "P-001")

    def test_start_idx_continues_numbering(self) -> None:
        from generate_golden_rows import parse_candidates

        raw = json.dumps([{"query": "q1"}, {"query": "q2"}])
        candidates = parse_candidates(
            raw_json=raw, qtype="x", id_prefix="G-Z-", start_idx=10
        )
        self.assertEqual(candidates[0].id, "G-Z-010")
        self.assertEqual(candidates[1].id, "G-Z-011")


class CsvWriterTest(unittest.TestCase):
    def test_round_trip_preserves_schema(self) -> None:
        from generate_golden_rows import (
            CandidateRow,
            _CSV_COLUMNS,
            write_candidates_csv,
        )

        cands = [
            CandidateRow(
                id="G-Z-001",
                query="신규 query",
                query_type="vision_diagram",
                doc_id="doc-A",
                expected_doc_title="title-A",
                expected_answer_summary="summary",
                must_include="kw1;kw2",
                doc_type="pdf",
                caption_dependent="true",
            )
        ]
        with tempfile.NamedTemporaryFile(
            suffix=".csv", delete=False, mode="w", encoding="utf-8-sig"
        ) as f:
            out_path = Path(f.name)
        write_candidates_csv(path=out_path, candidates=cands)

        with out_path.open(encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        self.assertEqual(len(rows), 1)
        self.assertEqual(set(rows[0].keys()), set(_CSV_COLUMNS))
        self.assertEqual(rows[0]["id"], "G-Z-001")
        self.assertEqual(rows[0]["negative"], "false")
        self.assertEqual(rows[0]["relevant_chunks"], "")  # 사용자 수동 채움
        self.assertEqual(rows[0]["acceptable_chunks"], "")


if __name__ == "__main__":
    unittest.main()
