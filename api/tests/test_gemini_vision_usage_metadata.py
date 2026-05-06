"""Phase 1 S0 D1 보강 (P1-3) — `gemini_vision._parse_usage_metadata` modality 분리 검증.

Gemini SDK `usage_metadata.prompt_tokens_details` 가 IMAGE/TEXT modality 분리
제공 → image_tokens 컬럼이 NULL 이 아닌 실제 값을 받는지 검증.

stdlib unittest only — 실 SDK 호출 없음.
"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace

# import 단계 ENV 회피용 더미.
os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")
os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")


def _make_metadata(
    *,
    prompt_token_count: int = 0,
    candidates_token_count: int = 0,
    thoughts_token_count: int = 0,
    prompt_tokens_details: list | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        prompt_token_count=prompt_token_count,
        candidates_token_count=candidates_token_count,
        thoughts_token_count=thoughts_token_count,
        prompt_tokens_details=prompt_tokens_details or [],
    )


def _modality_token_count(modality: str, token_count: int) -> SimpleNamespace:
    """Gemini SDK ModalityTokenCount 모방. modality 는 SDK enum 또는 str 둘 다 지원."""
    return SimpleNamespace(modality=modality, token_count=token_count)


class ParseUsageMetadataModalityTest(unittest.TestCase):
    """P1-3: prompt_tokens_details → image_tokens 분리 추출."""

    def test_image_tokens_from_modality_split(self) -> None:
        """IMAGE 100 + TEXT 50 → image_tokens=100, prompt_tokens=150."""
        from app.adapters.impl.gemini_vision import _parse_usage_metadata

        metadata = _make_metadata(
            prompt_token_count=150,
            candidates_token_count=80,
            thoughts_token_count=0,
            prompt_tokens_details=[
                _modality_token_count("IMAGE", 100),
                _modality_token_count("TEXT", 50),
            ],
        )
        response = SimpleNamespace(usage_metadata=metadata)

        result = _parse_usage_metadata(response, model="gemini-2.5-flash")
        assert result is not None  # for type narrowing
        self.assertEqual(result["prompt_tokens"], 150)
        self.assertEqual(result["image_tokens"], 100)
        self.assertEqual(result["output_tokens"], 80)
        self.assertEqual(result["thinking_tokens"], 0)
        self.assertEqual(result["model_used"], "gemini-2.5-flash")
        # estimated_cost 는 prompt_tokens 단일 단가 적용 (image_tokens 별도 단가 X)
        self.assertGreater(result["estimated_cost"], 0)

    def test_image_tokens_none_when_no_image_modality(self) -> None:
        """TEXT 만 있으면 image_tokens=None (NULL 컬럼)."""
        from app.adapters.impl.gemini_vision import _parse_usage_metadata

        metadata = _make_metadata(
            prompt_token_count=200,
            candidates_token_count=50,
            prompt_tokens_details=[_modality_token_count("TEXT", 200)],
        )
        response = SimpleNamespace(usage_metadata=metadata)

        result = _parse_usage_metadata(response, model="gemini-2.5-flash")
        assert result is not None
        self.assertIsNone(result["image_tokens"])
        self.assertEqual(result["prompt_tokens"], 200)

    def test_image_tokens_none_when_details_empty(self) -> None:
        """prompt_tokens_details 자체가 빈 list 면 image_tokens=None."""
        from app.adapters.impl.gemini_vision import _parse_usage_metadata

        metadata = _make_metadata(
            prompt_token_count=300,
            candidates_token_count=100,
            prompt_tokens_details=[],
        )
        response = SimpleNamespace(usage_metadata=metadata)

        result = _parse_usage_metadata(response, model="gemini-2.5-flash")
        assert result is not None
        self.assertIsNone(result["image_tokens"])
        self.assertEqual(result["prompt_tokens"], 300)

    def test_modality_enum_object_recognized(self) -> None:
        """SDK 가 enum object 를 넘겨도 IMAGE 인식. (실 SDK = MediaModality enum)"""
        from app.adapters.impl.gemini_vision import _parse_usage_metadata

        # MediaModality.IMAGE 모방 — name='IMAGE', value='IMAGE'
        modality_enum = SimpleNamespace(name="IMAGE", value="IMAGE")
        metadata = _make_metadata(
            prompt_token_count=120,
            candidates_token_count=40,
            prompt_tokens_details=[
                _modality_token_count(modality_enum, 90),
                _modality_token_count(SimpleNamespace(name="TEXT", value="TEXT"), 30),
            ],
        )
        response = SimpleNamespace(usage_metadata=metadata)
        result = _parse_usage_metadata(response, model="gemini-2.5-flash")
        assert result is not None
        self.assertEqual(result["image_tokens"], 90)

    def test_no_metadata_returns_none(self) -> None:
        """response.usage_metadata 가 없으면 dict 자체가 None (모든 컬럼 NULL)."""
        from app.adapters.impl.gemini_vision import _parse_usage_metadata

        response = SimpleNamespace()  # usage_metadata 속성 없음
        result = _parse_usage_metadata(response, model="gemini-2.5-flash")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
