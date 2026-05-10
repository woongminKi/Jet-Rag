"""evals/_multimodal_judge.py 단위 테스트.

검증 범위
- build_judge_prompt: query + answer 포함
- parse_judgment: 정상 / fence / clamp / score 계산
- evaluate_multimodal: empty answer / image fetch 실패 / LLM 실패 graceful

stdlib unittest only — 외부 LLM/image 호출 0 (DI 패턴 mock).
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_EVALS_DIR = Path(__file__).resolve().parents[2] / "evals"
sys.path.insert(0, str(_EVALS_DIR))


class BuildJudgePromptTest(unittest.TestCase):
    def test_includes_query_and_answer(self) -> None:
        from _multimodal_judge import build_judge_prompt

        prompt = build_judge_prompt(query="2026년 GDP", answer="2.0% 성장")
        self.assertIn("2026년 GDP", prompt)
        self.assertIn("2.0% 성장", prompt)


class ParseJudgmentTest(unittest.TestCase):
    def test_parses_valid_json(self) -> None:
        from _multimodal_judge import parse_judgment

        raw = json.dumps({
            "n_claims": 4,
            "n_verified": 3,
            "reasoning": "도표와 일치하지만 1 claim 은 답변에만 있음."
        })
        result = parse_judgment(raw)
        self.assertEqual(result.n_claims, 4)
        self.assertEqual(result.n_verified, 3)
        self.assertAlmostEqual(result.score, 0.75)
        self.assertIn("일치", result.reasoning)

    def test_strips_markdown_fence(self) -> None:
        from _multimodal_judge import parse_judgment

        raw = "```json\n" + json.dumps({"n_claims": 2, "n_verified": 2}) + "\n```"
        result = parse_judgment(raw)
        self.assertEqual(result.score, 1.0)

    def test_zero_claims_returns_none_score(self) -> None:
        from _multimodal_judge import parse_judgment

        raw = json.dumps({"n_claims": 0, "n_verified": 0})
        result = parse_judgment(raw)
        self.assertIsNone(result.score)

    def test_clamps_verified_to_claims(self) -> None:
        from _multimodal_judge import parse_judgment

        raw = json.dumps({"n_claims": 3, "n_verified": 10})  # impossible
        result = parse_judgment(raw)
        self.assertEqual(result.n_verified, 3)
        self.assertEqual(result.score, 1.0)

    def test_invalid_json_raises(self) -> None:
        from _multimodal_judge import parse_judgment

        with self.assertRaises(RuntimeError):
            parse_judgment("{not json}")


class EvaluateMultimodalTest(unittest.TestCase):
    def test_empty_answer_returns_zero(self) -> None:
        from _multimodal_judge import evaluate_multimodal

        result = evaluate_multimodal(
            query="q", answer="", doc_id="d", page=1,
            image_fetch_fn=lambda d, p: b"\x89PNG\r\n",
            llm_call_fn=lambda img, sys, usr: "",
        )
        self.assertEqual(result.score, 0.0)

    def test_image_fetch_failure_returns_none(self) -> None:
        from _multimodal_judge import evaluate_multimodal

        def failing_fetch(doc_id: str, page: int) -> bytes:
            raise RuntimeError("storage 404")

        result = evaluate_multimodal(
            query="q", answer="a", doc_id="d", page=1,
            image_fetch_fn=failing_fetch,
            llm_call_fn=lambda img, sys, usr: "",
        )
        self.assertIsNone(result.score)
        self.assertIn("image_fetch_failed", result.reasoning)

    def test_empty_image_returns_none(self) -> None:
        from _multimodal_judge import evaluate_multimodal

        result = evaluate_multimodal(
            query="q", answer="a", doc_id="d", page=1,
            image_fetch_fn=lambda d, p: b"",
            llm_call_fn=lambda img, sys, usr: "",
        )
        self.assertIsNone(result.score)
        self.assertEqual(result.reasoning, "empty_image")

    def test_llm_failure_returns_none(self) -> None:
        from _multimodal_judge import evaluate_multimodal

        def failing_llm(img, sys, usr):
            raise RuntimeError("API down")

        result = evaluate_multimodal(
            query="q", answer="a", doc_id="d", page=1,
            image_fetch_fn=lambda d, p: b"\x89PNG",
            llm_call_fn=failing_llm,
        )
        self.assertIsNone(result.score)
        self.assertIn("llm_call_failed", result.reasoning)

    def test_full_pipeline_success(self) -> None:
        from _multimodal_judge import evaluate_multimodal

        def mock_llm(img, sys, usr) -> str:
            return json.dumps({
                "n_claims": 5,
                "n_verified": 4,
                "reasoning": "vision verify ok",
            })

        result = evaluate_multimodal(
            query="2026년 GDP", answer="2.0% 성장률 + 2.5% 물가",
            doc_id="d-1234", page=14,
            image_fetch_fn=lambda d, p: b"\x89PNG_data",
            llm_call_fn=mock_llm,
        )
        self.assertAlmostEqual(result.score, 0.8)
        self.assertEqual(result.n_claims, 5)
        self.assertEqual(result.n_verified, 4)


if __name__ == "__main__":
    unittest.main()
