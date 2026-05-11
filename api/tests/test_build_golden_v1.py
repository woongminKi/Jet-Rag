"""Phase 2 — `evals/build_golden_v1.py` 단위 테스트.

검증 범위
- ``--validate-doc-ids`` CLI 플래그 OR ``JETRAG_GOLDEN_VALIDATE_DOC_IDS`` env var
  활성 시 merged rows 의 doc_id 가 Supabase documents 테이블에 존재하는지 검증.
- 모두 valid → exit 0 (정상 종료).
- 1건이라도 invalid → exit 1.

의존성 주입 — ``fetch_valid_ids_fn`` mock 으로 Supabase 호출 0.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# evals/ 의 build_golden_v1 import — api/tests/ 에서 evals 모듈 경로 보정
_EVALS_DIR = Path(__file__).resolve().parents[2] / "evals"
sys.path.insert(0, str(_EVALS_DIR))


class ValidateDocIdsTest(unittest.TestCase):
    """``validate_doc_ids`` 단위 — Supabase mock 주입."""

    def test_all_valid_returns_empty_missing(self) -> None:
        """모든 doc_id 가 Supabase 에 존재 → missing 빈 리스트."""
        from build_golden_v1 import validate_doc_ids

        merged = [
            {"id": "G-A-001", "doc_id": "doc-A", "query": "q1"},
            {"id": "G-A-002", "doc_id": "doc-B", "query": "q2"},
            {"id": "G-A-003", "doc_id": "doc-A", "query": "q3"},  # 중복 doc_id
            {"id": "G-U-100", "doc_id": "", "query": "q4"},  # cross_doc — skip
        ]
        valid_ids = {"doc-A", "doc-B", "doc-C"}
        checked, missing = validate_doc_ids(
            merged, fetch_valid_ids_fn=lambda: valid_ids
        )
        self.assertEqual(set(checked), {"doc-A", "doc-B"})
        self.assertEqual(missing, [])

    def test_one_stale_doc_id_returns_missing(self) -> None:
        """1건이라도 stale id 면 missing 에 포함."""
        from build_golden_v1 import validate_doc_ids

        merged = [
            {"id": "G-A-001", "doc_id": "doc-A", "query": "q1"},
            {"id": "G-A-104", "doc_id": "stale-id-2024", "query": "q2"},
        ]
        valid_ids = {"doc-A", "doc-B"}
        checked, missing = validate_doc_ids(
            merged, fetch_valid_ids_fn=lambda: valid_ids
        )
        self.assertEqual(set(checked), {"doc-A", "stale-id-2024"})
        self.assertEqual(missing, ["stale-id-2024"])


class CliValidationExitCodeTest(unittest.TestCase):
    """CLI main() 의 exit code 검증 — `--validate-doc-ids` 플래그.

    실제 CSV 파일 + Supabase fetch 는 mock 으로 우회. main() 의 분기만 검증.
    """

    def test_main_returns_1_when_missing_doc_id(self) -> None:
        """validate ON + missing 발견 → main() exit 1."""
        from unittest import mock

        import build_golden_v1

        # 가짜 merged rows — 1건 stale
        fake_merged = [
            {
                "id": "G-A-001", "doc_id": "doc-A",
                "query": "q1", "query_type": "exact_fact",
                "expected_doc_title": "", "expected_answer_summary": "",
                "must_include": "", "source_hint": "", "negative": "false",
                "relevant_chunks": "", "acceptable_chunks": "",
                "source_chunk_text": "",
            },
            {
                "id": "G-A-104", "doc_id": "stale-id",
                "query": "q2", "query_type": "exact_fact",
                "expected_doc_title": "", "expected_answer_summary": "",
                "must_include": "", "source_hint": "", "negative": "false",
                "relevant_chunks": "", "acceptable_chunks": "",
                "source_chunk_text": "",
            },
        ]
        fake_stats = {
            "auto_total": 2, "user_total": 0,
            "duplicates_removed_from_auto": 0, "merged_total": 2,
        }

        with (
            mock.patch.object(
                build_golden_v1, "_load_csv_rows", return_value=fake_merged,
            ),
            mock.patch.object(
                build_golden_v1, "merge_golden",
                return_value=(fake_merged, fake_stats),
            ),
            mock.patch.object(
                build_golden_v1, "_fetch_valid_doc_ids_from_supabase",
                return_value={"doc-A"},  # stale-id 누락
            ),
            mock.patch("builtins.open", mock.mock_open()),
        ):
            rc = build_golden_v1.main(
                ["--validate-doc-ids", "--output", "/tmp/golden_v1_test.csv"]
            )
        self.assertEqual(rc, 1, "stale id 1건 → exit 1")

    def test_main_returns_0_when_all_valid(self) -> None:
        """validate ON + 모두 valid → main() exit 0."""
        from unittest import mock

        import build_golden_v1

        fake_merged = [
            {
                "id": "G-A-001", "doc_id": "doc-A",
                "query": "q1", "query_type": "exact_fact",
                "expected_doc_title": "", "expected_answer_summary": "",
                "must_include": "", "source_hint": "", "negative": "false",
                "relevant_chunks": "", "acceptable_chunks": "",
                "source_chunk_text": "",
            },
        ]
        fake_stats = {
            "auto_total": 1, "user_total": 0,
            "duplicates_removed_from_auto": 0, "merged_total": 1,
        }

        with (
            mock.patch.object(
                build_golden_v1, "_load_csv_rows", return_value=fake_merged,
            ),
            mock.patch.object(
                build_golden_v1, "merge_golden",
                return_value=(fake_merged, fake_stats),
            ),
            mock.patch.object(
                build_golden_v1, "_fetch_valid_doc_ids_from_supabase",
                return_value={"doc-A", "doc-B"},
            ),
            mock.patch("builtins.open", mock.mock_open()),
        ):
            rc = build_golden_v1.main(
                ["--validate-doc-ids", "--output", "/tmp/golden_v1_test.csv"]
            )
        self.assertEqual(rc, 0, "모두 valid → exit 0")


if __name__ == "__main__":
    unittest.main()
