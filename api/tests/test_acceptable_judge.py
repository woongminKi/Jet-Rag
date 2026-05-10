"""evals/_acceptable_judge.py 단위 테스트.

검증 범위
- build_judge_prompt: query + candidates JSON 포함 + 평가 가이드
- parse_judgment: 정상 / markdown fence 제거 / 누락 chunk graceful / score clip [0,1]
- select_acceptable: threshold + max_count + 정렬

stdlib unittest only — LLM 호출 0.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_EVALS_DIR = Path(__file__).resolve().parents[2] / "evals"
sys.path.insert(0, str(_EVALS_DIR))


class BuildJudgePromptTest(unittest.TestCase):
    def test_includes_query_and_candidates(self) -> None:
        from _acceptable_judge import build_judge_prompt

        candidates = [(10, "한마음 운영 내규 시행일은 2022년 7월 1일."), (50, "회원카드 발급 규정.")]
        prompt = build_judge_prompt(query="한마음 시행일", candidates=candidates)
        self.assertIn("한마음 시행일", prompt)
        self.assertIn("chunk_idx", prompt)
        self.assertIn("0.0~1.0", prompt)
        self.assertIn("acceptable", prompt)

    def test_truncates_long_text(self) -> None:
        from _acceptable_judge import build_judge_prompt

        long_text = "x" * 1000
        candidates = [(1, long_text)]
        prompt = build_judge_prompt(query="q", candidates=candidates)
        # 300 chars 까지만 prompt 에 포함
        self.assertNotIn(long_text, prompt)


class ParseJudgmentTest(unittest.TestCase):
    def test_parses_valid_json(self) -> None:
        from _acceptable_judge import parse_judgment

        raw = json.dumps([
            {"chunk_idx": 10, "score": 0.9, "reason": "직접 답"},
            {"chunk_idx": 50, "score": 0.3, "reason": "무관"},
        ])
        result = parse_judgment(raw, expected_indices=[10, 50])
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].chunk_idx, 10)
        self.assertEqual(result[0].score, 0.9)
        self.assertEqual(result[1].score, 0.3)

    def test_strips_markdown_fence(self) -> None:
        from _acceptable_judge import parse_judgment

        raw = "```json\n" + json.dumps([{"chunk_idx": 1, "score": 0.5}]) + "\n```"
        result = parse_judgment(raw, expected_indices=[1])
        self.assertEqual(result[0].score, 0.5)

    def test_missing_chunks_default_zero(self) -> None:
        from _acceptable_judge import parse_judgment

        raw = json.dumps([{"chunk_idx": 1, "score": 0.8}])
        result = parse_judgment(raw, expected_indices=[1, 2, 3])
        self.assertEqual(result[0].score, 0.8)
        self.assertEqual(result[1].score, 0.0)
        self.assertIn("미응답", result[1].reason)

    def test_score_clipped_to_unit_range(self) -> None:
        from _acceptable_judge import parse_judgment

        raw = json.dumps([
            {"chunk_idx": 1, "score": 1.5},   # > 1 → 1
            {"chunk_idx": 2, "score": -0.2},  # < 0 → 0
        ])
        result = parse_judgment(raw, expected_indices=[1, 2])
        self.assertEqual(result[0].score, 1.0)
        self.assertEqual(result[1].score, 0.0)

    def test_invalid_json_raises(self) -> None:
        from _acceptable_judge import parse_judgment

        with self.assertRaises(RuntimeError):
            parse_judgment("{not json}", expected_indices=[1])

    def test_non_array_raises(self) -> None:
        from _acceptable_judge import parse_judgment

        with self.assertRaises(RuntimeError):
            parse_judgment('{"score":1}', expected_indices=[1])


class SelectAcceptableTest(unittest.TestCase):
    def test_filters_by_threshold(self) -> None:
        from _acceptable_judge import JudgedChunk, select_acceptable

        judgments = [
            JudgedChunk(chunk_idx=1, score=0.9, reason=""),
            JudgedChunk(chunk_idx=2, score=0.3, reason=""),  # below
            JudgedChunk(chunk_idx=3, score=0.6, reason=""),
        ]
        result = select_acceptable(judgments, threshold=0.5)
        self.assertEqual(result, [1, 3])

    def test_max_count_caps(self) -> None:
        from _acceptable_judge import JudgedChunk, select_acceptable

        judgments = [
            JudgedChunk(chunk_idx=i, score=0.9 - i * 0.1, reason="")
            for i in range(5)
        ]
        result = select_acceptable(judgments, threshold=0.5, max_count=2)
        self.assertEqual(len(result), 2)
        # top-2 by score = chunk_idx 0 (0.9), 1 (0.8) — sorted ascending = [0, 1]
        self.assertEqual(sorted(result), [0, 1])

    def test_empty_when_all_below_threshold(self) -> None:
        from _acceptable_judge import JudgedChunk, select_acceptable

        judgments = [JudgedChunk(chunk_idx=1, score=0.3, reason="")]
        self.assertEqual(select_acceptable(judgments, threshold=0.5), [])


if __name__ == "__main__":
    unittest.main()
