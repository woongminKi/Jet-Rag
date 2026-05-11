"""evals/_acceptable_judge.py 단위 테스트.

검증 범위
- build_judge_prompt: query + candidates JSON 포함 + 평가 가이드
- parse_judgment: 정상 / markdown fence 제거 / 누락 chunk graceful / score clip [0,1]
- select_acceptable: threshold + max_count + 정렬
- evaluate_acceptable: 정상 / LLM 실패 / parse 실패 / candidates empty / exclude / max_count
- make_acceptable_judge_caller: Gemini client mock / config / vision_metrics 기록 / 빈 응답

stdlib unittest only — LLM/DB 호출 모두 mock.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("GEMINI_API_KEY", "dummy-test-key")
os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")

_EVALS_DIR = Path(__file__).resolve().parents[2] / "evals"
if str(_EVALS_DIR) not in sys.path:
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


class EvaluateAcceptableTest(unittest.TestCase):
    """evaluate_acceptable DI entry — LLM/parse mock."""

    def _candidates(self):
        return [(10, "직접 답 chunk"), (20, "부분 정보 chunk"), (30, "무관 chunk")]

    def test_normal_filters_threshold_and_exclude(self) -> None:
        from _acceptable_judge import evaluate_acceptable

        raw = json.dumps([
            {"chunk_idx": 10, "score": 0.9, "reason": "직접"},
            {"chunk_idx": 20, "score": 0.6, "reason": "부분"},
            {"chunk_idx": 30, "score": 0.2, "reason": "무관"},
        ])
        result = evaluate_acceptable(
            query="질의",
            candidates=self._candidates(),
            judge_call_fn=lambda s, u: raw,
            threshold=0.5,
            max_count=8,
            exclude={10},  # relevant 와 겹침 → 제외
        )
        self.assertEqual(result, [20])  # 30 은 threshold 미달, 10 은 exclude

    def test_llm_call_failure_returns_empty(self) -> None:
        from _acceptable_judge import evaluate_acceptable

        def boom(s, u):
            raise RuntimeError("quota exhausted")

        result = evaluate_acceptable(
            query="q",
            candidates=self._candidates(),
            judge_call_fn=boom,
        )
        self.assertEqual(result, [])

    def test_parse_failure_returns_empty(self) -> None:
        from _acceptable_judge import evaluate_acceptable

        result = evaluate_acceptable(
            query="q",
            candidates=self._candidates(),
            judge_call_fn=lambda s, u: "not json at all",
        )
        self.assertEqual(result, [])

    def test_empty_candidates_skips_llm(self) -> None:
        from _acceptable_judge import evaluate_acceptable

        called = []
        result = evaluate_acceptable(
            query="q",
            candidates=[],
            judge_call_fn=lambda s, u: called.append(1) or "[]",
        )
        self.assertEqual(result, [])
        self.assertEqual(called, [])  # LLM 호출 0

    def test_exclude_removes_relevant_even_if_high_score(self) -> None:
        from _acceptable_judge import evaluate_acceptable

        raw = json.dumps([
            {"chunk_idx": 10, "score": 1.0},
            {"chunk_idx": 20, "score": 0.9},
        ])
        result = evaluate_acceptable(
            query="q",
            candidates=[(10, "a"), (20, "b")],
            judge_call_fn=lambda s, u: raw,
            exclude=[10],
        )
        self.assertEqual(result, [20])

    def test_max_count_cap(self) -> None:
        from _acceptable_judge import evaluate_acceptable

        raw = json.dumps([
            {"chunk_idx": i, "score": 0.9 - i * 0.05} for i in range(5)
        ])
        result = evaluate_acceptable(
            query="q",
            candidates=[(i, f"c{i}") for i in range(5)],
            judge_call_fn=lambda s, u: raw,
            threshold=0.5,
            max_count=2,
        )
        self.assertEqual(len(result), 2)
        # top-2 by score = idx 0, 1 → sorted ascending
        self.assertEqual(result, [0, 1])


class MakeAcceptableJudgeCallerTest(unittest.TestCase):
    """make_acceptable_judge_caller — Gemini client + vision_metrics mock."""

    def _fake_response(self, text: str):
        from types import SimpleNamespace

        return SimpleNamespace(text=text, usage_metadata=None)

    def test_returns_text_with_correct_config_and_records_usage(self) -> None:
        from _acceptable_judge import make_acceptable_judge_caller

        fake_client = mock.MagicMock()
        fake_client.models.generate_content.return_value = self._fake_response('[{"chunk_idx":1,"score":0.8}]')

        with mock.patch("app.adapters.impl._gemini_common.get_client", return_value=fake_client), \
             mock.patch("app.services.vision_metrics.record_call") as rec:
            caller = make_acceptable_judge_caller(model="gemini-2.5-flash")
            out = caller("system prompt", "user prompt")

        self.assertIn("chunk_idx", out)
        # config 검증
        _, kwargs = fake_client.models.generate_content.call_args
        config = kwargs["config"]
        self.assertEqual(config.temperature, 0.0)
        self.assertEqual(config.response_mime_type, "application/json")
        # contents 에 image part 없음 — text part 2개
        contents = kwargs["contents"]
        parts = contents[0].parts
        self.assertEqual(len(parts), 2)
        # vision_metrics.record_call 1회 + source_type
        self.assertEqual(rec.call_count, 1)
        self.assertEqual(rec.call_args.kwargs["source_type"], "acceptable_judge")

    def test_graceful_when_record_call_raises(self) -> None:
        from _acceptable_judge import make_acceptable_judge_caller

        fake_client = mock.MagicMock()
        fake_client.models.generate_content.return_value = self._fake_response('[]')

        with mock.patch("app.adapters.impl._gemini_common.get_client", return_value=fake_client), \
             mock.patch("app.services.vision_metrics.record_call", side_effect=RuntimeError("db down")):
            caller = make_acceptable_judge_caller()
            out = caller("s", "u")
        self.assertEqual(out, "[]")  # record_call 실패해도 정상 반환

    def test_empty_response_raises(self) -> None:
        from _acceptable_judge import make_acceptable_judge_caller

        fake_client = mock.MagicMock()
        fake_client.models.generate_content.return_value = self._fake_response("   ")

        with mock.patch("app.adapters.impl._gemini_common.get_client", return_value=fake_client):
            caller = make_acceptable_judge_caller()
            with self.assertRaises(RuntimeError):
                caller("s", "u")


if __name__ == "__main__":
    unittest.main()
