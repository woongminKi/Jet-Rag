"""evals/_visual_grounding.py 단위 테스트.

검증 범위
- extract_vision_captions: `[문서] X` / `[표] Y` prefix 추출 + dedup + 비-vision skip
- cosine: 정상 / dim mismatch / zero vector graceful
- compute_visual_grounding: caption 0건 → None / answer 빈 → 0.0 / max sim 정확

stdlib unittest only — embed_fn 은 mock.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_EVALS_DIR = Path(__file__).resolve().parents[2] / "evals"
sys.path.insert(0, str(_EVALS_DIR))


class ExtractVisionCaptionsTest(unittest.TestCase):
    def test_extracts_munseo_prefix(self) -> None:
        from _visual_grounding import extract_vision_captions

        ctx = "[문서] 경제전망 보고서의 목차를 보여주는 문서\n\n차 례\n경제전망 요약"
        captions = extract_vision_captions([ctx])
        self.assertEqual(len(captions), 1)
        self.assertEqual(captions[0], "[문서] 경제전망 보고서의 목차를 보여주는 문서")

    def test_extracts_pyo_prefix(self) -> None:
        from _visual_grounding import extract_vision_captions

        ctx = "[표] 2026년 2월 기준 경제 전망 요약표\n\n경제전망 요약표 (2026.2월)"
        captions = extract_vision_captions([ctx])
        self.assertEqual(len(captions), 1)
        self.assertEqual(captions[0], "[표] 2026년 2월 기준 경제 전망 요약표")

    def test_dedup_same_caption(self) -> None:
        from _visual_grounding import extract_vision_captions

        ctx1 = "[문서] 경제전망 보고서의 목차를 보여주는 문서\n\n본문 1"
        ctx2 = "[문서] 경제전망 보고서의 목차를 보여주는 문서\n\n본문 2"
        captions = extract_vision_captions([ctx1, ctx2])
        self.assertEqual(len(captions), 1)

    def test_skips_non_vision_context(self) -> None:
        from _visual_grounding import extract_vision_captions

        ctxs = [
            "본문은 일반 텍스트입니다.",
            "[문서] vision OCR caption\n\nbody",
            "또 다른 일반 텍스트.",
        ]
        captions = extract_vision_captions(ctxs)
        self.assertEqual(captions, ["[문서] vision OCR caption"])

    def test_empty_contexts_returns_empty(self) -> None:
        from _visual_grounding import extract_vision_captions

        self.assertEqual(extract_vision_captions([]), [])


class CosineTest(unittest.TestCase):
    def test_identical_vectors(self) -> None:
        from _visual_grounding import cosine

        v = [1.0, 2.0, 3.0]
        self.assertAlmostEqual(cosine(v, v), 1.0, places=4)

    def test_orthogonal_vectors(self) -> None:
        from _visual_grounding import cosine

        a = [1.0, 0.0]
        b = [0.0, 1.0]
        self.assertAlmostEqual(cosine(a, b), 0.0, places=4)

    def test_dim_mismatch_returns_zero(self) -> None:
        from _visual_grounding import cosine

        self.assertEqual(cosine([1.0, 2.0], [1.0]), 0.0)

    def test_empty_vector_returns_zero(self) -> None:
        from _visual_grounding import cosine

        self.assertEqual(cosine([], [1.0]), 0.0)


class ComputeVisualGroundingTest(unittest.TestCase):
    def test_no_vision_caption_returns_none(self) -> None:
        from _visual_grounding import compute_visual_grounding

        result = compute_visual_grounding(
            answer="텍스트 답변",
            contexts=["일반 본문 text 1", "일반 본문 text 2"],
            embed_fn=lambda t: [1.0, 0.0, 0.0],
        )
        self.assertIsNone(result.score)
        self.assertEqual(result.n_captions, 0)

    def test_empty_answer_returns_zero(self) -> None:
        from _visual_grounding import compute_visual_grounding

        result = compute_visual_grounding(
            answer="",
            contexts=["[문서] caption\n\nbody"],
            embed_fn=lambda t: [1.0, 0.0],
        )
        self.assertEqual(result.score, 0.0)
        self.assertEqual(result.n_captions, 1)

    def test_returns_max_cosine(self) -> None:
        from _visual_grounding import compute_visual_grounding

        # Mock: 답변과 caption1 cosine = 1.0 (same vec), caption2 cosine = 0.0
        embed_map = {
            "answer text": [1.0, 0.0],
            "[문서] cap1": [1.0, 0.0],  # 답변 과 동일
            "[표] cap2": [0.0, 1.0],    # 직교
        }

        def embed_fn(text: str) -> list[float]:
            return embed_map.get(text, [0.0, 0.0])

        contexts = [
            "[문서] cap1\n\nbody1",
            "[표] cap2\n\nbody2",
        ]
        result = compute_visual_grounding(
            answer="answer text", contexts=contexts, embed_fn=embed_fn
        )
        self.assertAlmostEqual(result.score, 1.0, places=4)
        self.assertEqual(result.matched_caption, "[문서] cap1")
        self.assertEqual(result.n_captions, 2)
        self.assertEqual(len(result.sims), 2)

    def test_embed_fn_failure_returns_none_score(self) -> None:
        from _visual_grounding import compute_visual_grounding

        def failing_embed(text: str):
            raise RuntimeError("embedding API down")

        result = compute_visual_grounding(
            answer="answer",
            contexts=["[문서] cap\n\nbody"],
            embed_fn=failing_embed,
        )
        self.assertIsNone(result.score)


if __name__ == "__main__":
    unittest.main()
