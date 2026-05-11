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


class MakeImageFetcherTest(unittest.TestCase):
    """`make_image_fetcher` — Supabase storage + PyMuPDF page render 통합.

    SDK 직접 호출은 mock — storage.get / fitz.open / page.get_pixmap 패치.
    """

    def _make_minimal_pdf(self) -> bytes:
        """단위 테스트용 1-page PDF (fitz 로 생성)."""
        import fitz

        d = fitz.open()
        page = d.new_page()
        page.insert_text((72, 72), "judge test page")
        out = d.tobytes()
        d.close()
        return out

    def test_renders_png_from_storage_pdf(self) -> None:
        """storage.get → fitz.open → get_pixmap → tobytes('png') 의 통합 동작."""
        from unittest import mock

        pdf_bytes = self._make_minimal_pdf()

        # supabase client / storage / settings 모두 mock — 외부 의존 0.
        fake_settings = mock.Mock(supabase_storage_bucket="documents")
        fake_storage = mock.Mock()
        fake_storage.get.return_value = pdf_bytes
        fake_client = mock.Mock()
        fake_client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {"storage_path": "abc.pdf"}
        ]

        with mock.patch(
            "app.adapters.impl.supabase_storage.SupabaseBlobStorage",
            return_value=fake_storage,
        ), mock.patch(
            "app.config.get_settings", return_value=fake_settings
        ), mock.patch(
            "app.db.get_supabase_client", return_value=fake_client
        ):
            from _multimodal_judge import make_image_fetcher

            fetch = make_image_fetcher(dpi=72)  # 빠른 render
            png = fetch("doc-1", 1)

        self.assertIsInstance(png, bytes)
        self.assertTrue(png.startswith(b"\x89PNG"))
        fake_storage.get.assert_called_once_with("abc.pdf")

    def test_raises_on_null_storage_path(self) -> None:
        from unittest import mock

        fake_settings = mock.Mock(supabase_storage_bucket="documents")
        fake_storage = mock.Mock()
        fake_client = mock.Mock()
        fake_client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {"storage_path": None}
        ]

        with mock.patch(
            "app.adapters.impl.supabase_storage.SupabaseBlobStorage",
            return_value=fake_storage,
        ), mock.patch(
            "app.config.get_settings", return_value=fake_settings
        ), mock.patch(
            "app.db.get_supabase_client", return_value=fake_client
        ):
            from _multimodal_judge import make_image_fetcher

            fetch = make_image_fetcher()
            with self.assertRaisesRegex(RuntimeError, "storage_path is NULL"):
                fetch("doc-null", 1)

    def test_raises_on_doc_not_found(self) -> None:
        from unittest import mock

        fake_settings = mock.Mock(supabase_storage_bucket="documents")
        fake_storage = mock.Mock()
        fake_client = mock.Mock()
        fake_client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = []

        with mock.patch(
            "app.adapters.impl.supabase_storage.SupabaseBlobStorage",
            return_value=fake_storage,
        ), mock.patch(
            "app.config.get_settings", return_value=fake_settings
        ), mock.patch(
            "app.db.get_supabase_client", return_value=fake_client
        ):
            from _multimodal_judge import make_image_fetcher

            fetch = make_image_fetcher()
            with self.assertRaisesRegex(RuntimeError, "documents row not found"):
                fetch("doc-ghost", 1)

    def test_raises_on_page_out_of_range(self) -> None:
        from unittest import mock

        pdf_bytes = self._make_minimal_pdf()
        fake_settings = mock.Mock(supabase_storage_bucket="documents")
        fake_storage = mock.Mock()
        fake_storage.get.return_value = pdf_bytes
        fake_client = mock.Mock()
        fake_client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {"storage_path": "abc.pdf"}
        ]

        with mock.patch(
            "app.adapters.impl.supabase_storage.SupabaseBlobStorage",
            return_value=fake_storage,
        ), mock.patch(
            "app.config.get_settings", return_value=fake_settings
        ), mock.patch(
            "app.db.get_supabase_client", return_value=fake_client
        ):
            from _multimodal_judge import make_image_fetcher

            fetch = make_image_fetcher(dpi=72)
            with self.assertRaisesRegex(RuntimeError, "page out of range"):
                fetch("doc-1", 999)


class MakeLlmCallerTest(unittest.TestCase):
    """`make_llm_caller` — Gemini multimodal API 호출 통합.

    Gemini client mock — generate_content 응답 text 만 검증. vision_usage_log
    record_call 도 mock 으로 호출 횟수만 확인.
    """

    def test_returns_text_and_records_usage(self) -> None:
        from unittest import mock

        fake_response = mock.Mock()
        fake_response.text = json.dumps(
            {"n_claims": 2, "n_verified": 2, "reasoning": "ok"}
        )
        # usage_metadata mock
        fake_response.usage_metadata = mock.Mock(
            prompt_token_count=1500,
            candidates_token_count=50,
            thoughts_token_count=0,
            prompt_tokens_details=[],
        )

        fake_client = mock.Mock()
        fake_client.models.generate_content.return_value = fake_response

        with mock.patch(
            "app.adapters.impl._gemini_common.get_client", return_value=fake_client
        ), mock.patch(
            "app.services.vision_metrics.record_call"
        ) as record_mock:
            from _multimodal_judge import make_llm_caller

            call = make_llm_caller(model="gemini-2.5-flash")
            out = call(b"\x89PNG_data", "sys prompt", "usr prompt")

        self.assertIn("n_claims", out)
        # generate_content 호출 contents 인자에 system / image / user 3개 part 가 포함되어야 함.
        kwargs = fake_client.models.generate_content.call_args.kwargs
        self.assertEqual(kwargs["model"], "gemini-2.5-flash")
        # record_call 1회 호출 (success=True, source_type="multimodal_judge")
        record_mock.assert_called_once()
        rc_kwargs = record_mock.call_args.kwargs
        self.assertTrue(rc_kwargs.get("success"))
        self.assertEqual(rc_kwargs.get("source_type"), "multimodal_judge")

    def test_raises_on_empty_response_text(self) -> None:
        from unittest import mock

        fake_response = mock.Mock()
        fake_response.text = ""

        fake_client = mock.Mock()
        fake_client.models.generate_content.return_value = fake_response

        with mock.patch(
            "app.adapters.impl._gemini_common.get_client", return_value=fake_client
        ):
            from _multimodal_judge import make_llm_caller

            call = make_llm_caller()
            with self.assertRaises(RuntimeError):
                call(b"\x89PNG", "s", "u")

    def test_record_usage_failure_is_graceful(self) -> None:
        """vision_metrics.record_call 실패해도 judge text 는 정상 반환."""
        from unittest import mock

        fake_response = mock.Mock()
        fake_response.text = json.dumps({"n_claims": 1, "n_verified": 1})
        fake_response.usage_metadata = mock.Mock(
            prompt_token_count=100,
            candidates_token_count=10,
            thoughts_token_count=0,
            prompt_tokens_details=[],
        )
        fake_client = mock.Mock()
        fake_client.models.generate_content.return_value = fake_response

        with mock.patch(
            "app.adapters.impl._gemini_common.get_client", return_value=fake_client
        ), mock.patch(
            "app.services.vision_metrics.record_call",
            side_effect=RuntimeError("DB down"),
        ):
            from _multimodal_judge import make_llm_caller

            call = make_llm_caller()
            out = call(b"\x89PNG", "s", "u")  # 예외 무시되어야 함

        self.assertIn("n_claims", out)


class MultimodalJudgeSourceTypeTest(unittest.TestCase):
    """vision_metrics._VALID_SOURCE_TYPES 에 `multimodal_judge` 가 포함되었는지 확인."""

    def test_multimodal_judge_in_valid_source_types(self) -> None:
        from app.services.vision_metrics import _VALID_SOURCE_TYPES

        self.assertIn("multimodal_judge", _VALID_SOURCE_TYPES)


if __name__ == "__main__":
    unittest.main()
