"""S0 D4 (2026-05-07) — `app.services.budget_guard` 단위 테스트.

검증 포인트
- doc/daily 통과/도달 케이스
- doc + daily 동시 도달 (priority = doc)
- DB graceful (마이그 014 미적용 / SUM 실패 → allowed=True)
- ENV 비활성 토글 (`JETRAG_BUDGET_GUARD_DISABLE=1`)
- 인제스트 통합 — extract.py 의 사전 cap 검사가 vision 호출 0회 + flags 마킹

stdlib unittest + mock only — Supabase 의존성 0.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

# 환경 변수 stub — 단위 테스트가 실 DB 접근 회피.
os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")
os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")
os.environ["JET_RAG_METRICS_PERSIST_ENABLED"] = "0"
os.environ["JET_RAG_METRICS_PERSIST_ASYNC"] = "0"


class BudgetGuardDocBudgetTest(unittest.TestCase):
    """doc 단위 cap 검사."""

    def setUp(self) -> None:
        from app.services import budget_guard
        budget_guard._reset_first_warn_for_test()
        os.environ.pop("JETRAG_BUDGET_GUARD_DISABLE", None)

    def _make_client(self, *, success_costs: list[float | None]) -> MagicMock:
        """vision_usage_log 의 success=true 행을 흉내내는 client."""
        client = MagicMock()
        chain = (
            client.table.return_value
            .select.return_value
            .eq.return_value
            .eq.return_value
        )
        chain.execute.return_value.data = [
            {"estimated_cost": c, "success": True} for c in success_costs
        ]
        return client

    def test_doc_under_cap_returns_allowed_true(self) -> None:
        from app.services import budget_guard

        client = self._make_client(success_costs=[0.01, 0.02, 0.03])  # SUM=0.06
        with patch("app.db.get_supabase_client", return_value=client):
            status = budget_guard.check_doc_budget(
                doc_id="d1", cap_usd=0.10
            )
        self.assertTrue(status.allowed)
        self.assertAlmostEqual(status.used_usd, 0.06)
        self.assertEqual(status.cap_usd, 0.10)
        self.assertEqual(status.scope, "doc")

    def test_doc_over_cap_returns_allowed_false(self) -> None:
        from app.services import budget_guard

        client = self._make_client(success_costs=[0.05, 0.05, 0.05])  # SUM=0.15 > 0.10
        with patch("app.db.get_supabase_client", return_value=client):
            status = budget_guard.check_doc_budget(
                doc_id="d2", cap_usd=0.10
            )
        self.assertFalse(status.allowed)
        self.assertAlmostEqual(status.used_usd, 0.15)
        self.assertIn("문서당 비용 한도 초과", status.reason)

    def test_doc_db_failure_graceful_allowed_true(self) -> None:
        """마이그 014 미적용 → DB 실패 → allowed=True (graceful)."""
        from app.services import budget_guard

        client = MagicMock()
        client.table.side_effect = RuntimeError(
            "column \"estimated_cost\" does not exist"
        )
        with patch("app.db.get_supabase_client", return_value=client):
            status = budget_guard.check_doc_budget(doc_id="d3", cap_usd=0.10)
        self.assertTrue(status.allowed)
        self.assertEqual(status.used_usd, 0.0)
        self.assertIn("DB 조회 실패", status.reason)

    def test_doc_id_empty_returns_allowed_true(self) -> None:
        """doc_id 미지정 (단독 이미지 호출) → 가드 통과."""
        from app.services import budget_guard

        status = budget_guard.check_doc_budget(doc_id="", cap_usd=0.10)
        self.assertTrue(status.allowed)
        self.assertIn("doc_id 미지정", status.reason)


class BudgetGuardDailyBudgetTest(unittest.TestCase):
    """일일 cap 검사 (UTC midnight 기준)."""

    def setUp(self) -> None:
        from app.services import budget_guard
        budget_guard._reset_first_warn_for_test()
        os.environ.pop("JETRAG_BUDGET_GUARD_DISABLE", None)

    def test_daily_under_cap_returns_allowed_true(self) -> None:
        from app.services import budget_guard

        client = MagicMock()
        chain = (
            client.table.return_value
            .select.return_value
            .gte.return_value
            .eq.return_value
        )
        chain.execute.return_value.data = [
            {"estimated_cost": 0.10, "success": True},
            {"estimated_cost": 0.20, "success": True},
        ]
        with patch("app.db.get_supabase_client", return_value=client):
            status = budget_guard.check_daily_budget(cap_usd=0.50)
        self.assertTrue(status.allowed)
        self.assertAlmostEqual(status.used_usd, 0.30)
        self.assertEqual(status.scope, "daily")

    def test_daily_over_cap_returns_allowed_false(self) -> None:
        from app.services import budget_guard

        client = MagicMock()
        chain = (
            client.table.return_value
            .select.return_value
            .gte.return_value
            .eq.return_value
        )
        chain.execute.return_value.data = [
            {"estimated_cost": 0.30, "success": True},
            {"estimated_cost": 0.30, "success": True},
        ]
        with patch("app.db.get_supabase_client", return_value=client):
            status = budget_guard.check_daily_budget(cap_usd=0.50)
        self.assertFalse(status.allowed)
        self.assertAlmostEqual(status.used_usd, 0.60)
        self.assertIn("일일 비용 한도 초과", status.reason)


class BudgetGuardCombinedTest(unittest.TestCase):
    """doc + daily 동시 검사."""

    def setUp(self) -> None:
        from app.services import budget_guard
        budget_guard._reset_first_warn_for_test()
        os.environ.pop("JETRAG_BUDGET_GUARD_DISABLE", None)

    def test_doc_exceeded_short_circuits_daily_check(self) -> None:
        """doc 초과 → daily 검사 안 함 (priority = doc)."""
        from app.services import budget_guard

        client = MagicMock()
        # doc SUM call 만 응답하면 됨 (daily 는 호출 안 됨).
        chain_doc = (
            client.table.return_value
            .select.return_value
            .eq.return_value
            .eq.return_value
        )
        chain_doc.execute.return_value.data = [
            {"estimated_cost": 0.20, "success": True}
        ]
        with patch("app.db.get_supabase_client", return_value=client):
            status = budget_guard.check_combined(
                doc_id="d1", doc_cap_usd=0.10, daily_cap_usd=0.50
            )
        self.assertFalse(status.allowed)
        self.assertEqual(status.scope, "doc")

    def test_doc_pass_and_daily_exceed(self) -> None:
        """doc 통과 + daily 초과 → daily reason 반환."""
        from app.services import budget_guard

        # 첫 호출 (doc) data 작은 값 → allowed
        # 둘째 호출 (daily) data 큰 값 → not allowed
        # supabase-py 의 chain 두 케이스가 다른 method (eq vs gte) 라 분리 가능.
        client = MagicMock()
        chain_doc = (
            client.table.return_value
            .select.return_value
            .eq.return_value
            .eq.return_value
        )
        chain_doc.execute.return_value.data = [
            {"estimated_cost": 0.01, "success": True}
        ]
        chain_daily = (
            client.table.return_value
            .select.return_value
            .gte.return_value
            .eq.return_value
        )
        chain_daily.execute.return_value.data = [
            {"estimated_cost": 0.55, "success": True}
        ]
        with patch("app.db.get_supabase_client", return_value=client):
            status = budget_guard.check_combined(
                doc_id="d1", doc_cap_usd=0.10, daily_cap_usd=0.50
            )
        self.assertFalse(status.allowed)
        self.assertEqual(status.scope, "daily")
        self.assertIn("일일", status.reason)


class BudgetGuardDisabledEnvTest(unittest.TestCase):
    """ENV 비활성 토글 — 모든 호출 즉시 allowed=True."""

    def setUp(self) -> None:
        from app.services import budget_guard
        budget_guard._reset_first_warn_for_test()

    def tearDown(self) -> None:
        os.environ.pop("JETRAG_BUDGET_GUARD_DISABLE", None)

    def test_disabled_skips_db_query(self) -> None:
        from app.services import budget_guard

        os.environ["JETRAG_BUDGET_GUARD_DISABLE"] = "1"
        client = MagicMock()
        with patch("app.db.get_supabase_client", return_value=client):
            status_doc = budget_guard.check_doc_budget(
                doc_id="d1", cap_usd=0.001
            )
            status_daily = budget_guard.check_daily_budget(cap_usd=0.001)
            status_comb = budget_guard.check_combined(
                doc_id="d1", doc_cap_usd=0.001, daily_cap_usd=0.001
            )
        self.assertTrue(status_doc.allowed)
        self.assertTrue(status_daily.allowed)
        self.assertTrue(status_comb.allowed)
        # DB 미접근 검증
        client.table.assert_not_called()


class BudgetGuardIngestIntegrationTest(unittest.TestCase):
    """인제스트 통합 — extract.py 의 사전 cap 검사 후 vision 호출 분기.

    `_enrich_pdf_with_vision` 자체 진입을 막는 사전 분기 검증.
    """

    def setUp(self) -> None:
        from app.services import budget_guard
        budget_guard._reset_first_warn_for_test()
        os.environ.pop("JETRAG_BUDGET_GUARD_DISABLE", None)

    def test_pre_check_blocks_vision_enrich_and_marks_flags(self) -> None:
        """cap 도달 시 _enrich_pdf_with_vision 호출 0 + flags.vision_budget_exceeded=true."""
        from app.ingest.stages import extract
        from app.services import budget_guard

        # documents row 흉내 — vision_enrich 활성 + scan flag 없음.
        doc_row = {
            "doc_type": "pdf",
            "storage_path": "test/foo.pdf",
            "flags": {},
            "sha256": "a" * 64,
        }

        # extract 모듈의 vision_enrich 활성 ENV 강제 + 가드는 not allowed 반환 강제.
        not_allowed_status = budget_guard.BudgetStatus(
            allowed=False,
            used_usd=0.20,
            cap_usd=0.10,
            scope="doc",
            reason="문서당 비용 한도 초과 ($0.2000 > $0.1000)",
        )

        # supabase update 호출 capture
        update_calls: list[dict] = []

        client = MagicMock()
        # _fetch_document
        (
            client.table.return_value
            .select.return_value
            .eq.return_value
            .limit.return_value
            .execute.return_value.data
        ) = [doc_row]
        # _mark_budget_exceeded_flag — table('documents').update({...}).eq('id', doc_id).execute()
        update_chain = MagicMock()
        client.table.return_value.update.return_value.eq.return_value = update_chain
        update_chain.execute.return_value = MagicMock()

        def capture_update(payload):
            update_calls.append(payload)
            return client.table.return_value.update.return_value

        client.table.return_value.update.side_effect = capture_update

        # storage.get
        fake_storage = MagicMock()
        fake_storage.get.return_value = b"%PDF-1.7\nfake"

        # parser.parse — vision_enrich 가 안 호출되어야 함을 검증하기 위해
        # PyMuPDF parser 의 parse 결과는 정상 ExtractionResult 반환.
        from app.adapters.parser import ExtractionResult
        fake_pdf_parser = MagicMock()
        fake_pdf_parser.parse.return_value = ExtractionResult(
            source_type="pdf",
            sections=[],
            raw_text="hello world " * 20,  # 텍스트 충분 → 스캔 분기 회피
            warnings=[],
        )

        # _enrich_pdf_with_vision 호출 0 검증
        enrich_called: list = []

        def fake_enrich(*args, **kwargs):
            enrich_called.append(kwargs)
            return fake_pdf_parser.parse.return_value

        with patch.object(extract, "_PDF_VISION_ENRICH_ENABLED", True):
            with patch.object(extract, "_get_image_parser", return_value=MagicMock()):
                with patch.object(
                    extract, "_get_parsers_by_doc_type",
                    return_value={"pdf": fake_pdf_parser},
                ):
                    with patch.object(extract, "_enrich_pdf_with_vision", side_effect=fake_enrich):
                        with patch.object(
                            budget_guard, "check_combined", return_value=not_allowed_status
                        ):
                            with patch(
                                "app.ingest.stages.extract.get_supabase_client",
                                return_value=client,
                            ):
                                with patch.object(
                                    extract, "SupabaseBlobStorage",
                                    return_value=fake_storage,
                                ):
                                    with patch("app.ingest.stages.extract.stage"):
                                        result = extract.run_extract_stage(
                                            "job-1", "doc-1"
                                        )

        # vision_enrich 호출 0 검증
        self.assertEqual(len(enrich_called), 0)
        # flags.vision_budget_exceeded=true update payload 검증
        flags_updates = [
            c for c in update_calls
            if isinstance(c, dict) and "flags" in c
            and (c["flags"].get("vision_budget_exceeded") is True)
        ]
        self.assertGreaterEqual(len(flags_updates), 1)
        flags = flags_updates[0]["flags"]
        self.assertEqual(flags["vision_budget"]["scope"], "doc")
        self.assertAlmostEqual(flags["vision_budget"]["used_usd"], 0.20)
        self.assertAlmostEqual(flags["vision_budget"]["cap_usd"], 0.10)
        # ExtractionResult 자체는 base parser 결과 (vision 없음)
        self.assertIsNotNone(result)


class BudgetGuardCostSumHelperTest(unittest.TestCase):
    """`_sum_cost_rows` — None / 잘못된 값 처리."""

    def test_sum_skips_none_and_invalid(self) -> None:
        from app.services import budget_guard

        rows = [
            {"estimated_cost": 0.01, "success": True},
            {"estimated_cost": None, "success": True},  # NULL
            {"estimated_cost": "abc", "success": True},  # 잘못된 값
            {"estimated_cost": 0.02, "success": True},
        ]
        total = budget_guard._sum_cost_rows(rows)
        self.assertAlmostEqual(total, 0.03)


if __name__ == "__main__":
    unittest.main()
