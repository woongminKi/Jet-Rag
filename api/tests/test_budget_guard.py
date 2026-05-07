"""S0 D4 + D5 (2026-05-07) — `app.services.budget_guard` 단위 테스트.

검증 포인트
- doc/daily 통과/도달 케이스 (D4)
- doc + daily 동시 도달 (priority = doc) (D4)
- DB graceful (마이그 014 미적용 / SUM 실패 → allowed=True) (D4)
- ENV 비활성 토글 (`JETRAG_BUDGET_GUARD_DISABLE=1`) (D4)
- 인제스트 통합 — extract.py 의 사전 cap 검사가 vision 호출 0회 + flags 마킹 (D4)
- D5 24h sliding window — 통과/도달/DB graceful 케이스
- D5 check_combined 우선순위 — doc → daily → 24h_sliding (가장 먼저 fail 한 scope)
- D5 인제스트 통합 — sliding 도달 시 vision 호출 0 + flags.scope='24h_sliding'

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


class BudgetGuard24hSlidingTest(unittest.TestCase):
    """D5 — 24h sliding window cap 검사."""

    def setUp(self) -> None:
        from app.services import budget_guard
        budget_guard._reset_first_warn_for_test()
        os.environ.pop("JETRAG_BUDGET_GUARD_DISABLE", None)

    def _make_client(self, *, costs: list[float | None]) -> MagicMock:
        """gte('called_at', cutoff).eq('success', True) 흐름 mock."""
        client = MagicMock()
        chain = (
            client.table.return_value
            .select.return_value
            .gte.return_value
            .eq.return_value
        )
        chain.execute.return_value.data = [
            {"estimated_cost": c, "success": True} for c in costs
        ]
        return client

    def test_sliding_under_cap_returns_allowed_true(self) -> None:
        from app.services import budget_guard

        client = self._make_client(costs=[0.10, 0.10])  # SUM=0.20 < 0.50
        with patch("app.db.get_supabase_client", return_value=client):
            status = budget_guard.check_24h_sliding_budget(cap_usd=0.50)
        self.assertTrue(status.allowed)
        self.assertAlmostEqual(status.used_usd, 0.20)
        self.assertEqual(status.scope, "24h_sliding")
        self.assertIn("24시간 한도 내", status.reason)

    def test_sliding_over_cap_returns_allowed_false(self) -> None:
        from app.services import budget_guard

        client = self._make_client(costs=[0.30, 0.30])  # SUM=0.60 > 0.50
        with patch("app.db.get_supabase_client", return_value=client):
            status = budget_guard.check_24h_sliding_budget(cap_usd=0.50)
        self.assertFalse(status.allowed)
        self.assertAlmostEqual(status.used_usd, 0.60)
        self.assertEqual(status.scope, "24h_sliding")
        self.assertIn("최근 24시간 비용 한도 초과", status.reason)

    def test_sliding_db_failure_graceful_allowed_true(self) -> None:
        """마이그 014 미적용 → DB 실패 → allowed=True (graceful)."""
        from app.services import budget_guard

        client = MagicMock()
        client.table.side_effect = RuntimeError(
            "column \"estimated_cost\" does not exist"
        )
        with patch("app.db.get_supabase_client", return_value=client):
            status = budget_guard.check_24h_sliding_budget(cap_usd=0.50)
        self.assertTrue(status.allowed)
        self.assertEqual(status.used_usd, 0.0)
        self.assertEqual(status.scope, "24h_sliding")
        self.assertIn("DB 조회 실패", status.reason)

    def test_sliding_disabled_env_returns_allowed_true(self) -> None:
        """ENV 토글 시 DB 미접근 + allowed=True."""
        from app.services import budget_guard

        os.environ["JETRAG_BUDGET_GUARD_DISABLE"] = "1"
        try:
            client = MagicMock()
            with patch("app.db.get_supabase_client", return_value=client):
                status = budget_guard.check_24h_sliding_budget(cap_usd=0.001)
            self.assertTrue(status.allowed)
            self.assertEqual(status.scope, "24h_sliding")
            client.table.assert_not_called()
        finally:
            os.environ.pop("JETRAG_BUDGET_GUARD_DISABLE", None)

    def test_sliding_cutoff_is_24h_before_now(self) -> None:
        """now 인자 주입 시 정확히 -24h 시점으로 gte 호출 검증."""
        from datetime import datetime, timezone

        from app.services import budget_guard

        fixed_now = datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)
        # 결정성 테스트 — _sliding_cutoff_iso 단독 검증.
        cutoff_iso = budget_guard._sliding_cutoff_iso(now=fixed_now)
        self.assertEqual(cutoff_iso, "2026-05-06T12:00:00+00:00")


class BudgetGuardCombinedSlidingTest(unittest.TestCase):
    """D5 — check_combined 에 sliding_24h_cap_usd 추가 시 우선순위 검증."""

    def setUp(self) -> None:
        from app.services import budget_guard
        budget_guard._reset_first_warn_for_test()
        os.environ.pop("JETRAG_BUDGET_GUARD_DISABLE", None)

    def test_doc_pass_daily_pass_sliding_exceed(self) -> None:
        """doc + daily 통과 + sliding 초과 → sliding reason."""
        from app.services import budget_guard

        client = MagicMock()
        # doc: eq.eq.execute → small SUM
        chain_doc = (
            client.table.return_value
            .select.return_value
            .eq.return_value
            .eq.return_value
        )
        chain_doc.execute.return_value.data = [
            {"estimated_cost": 0.01, "success": True}
        ]
        # daily + sliding 모두 gte.eq.execute 사용 — 같은 chain 으로 수렴.
        # daily 는 small, sliding 은 large 가 필요한데 chain mock 분리는 어려움.
        # → 같은 chain 이 SUM=0.55 반환. daily_cap=1.00 (통과), sliding_cap=0.50 (도달).
        chain_gte = (
            client.table.return_value
            .select.return_value
            .gte.return_value
            .eq.return_value
        )
        chain_gte.execute.return_value.data = [
            {"estimated_cost": 0.55, "success": True}
        ]
        with patch("app.db.get_supabase_client", return_value=client):
            status = budget_guard.check_combined(
                doc_id="d1",
                doc_cap_usd=0.10,
                daily_cap_usd=1.00,
                sliding_24h_cap_usd=0.50,
            )
        self.assertFalse(status.allowed)
        self.assertEqual(status.scope, "24h_sliding")
        self.assertIn("최근 24시간", status.reason)

    def test_doc_pass_daily_exceed_short_circuits_sliding(self) -> None:
        """doc 통과 + daily 도달 → sliding 검사 skip + scope='daily'."""
        from app.services import budget_guard

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
        chain_gte = (
            client.table.return_value
            .select.return_value
            .gte.return_value
            .eq.return_value
        )
        # daily 도달 (cap_daily=0.50 인데 0.60).
        chain_gte.execute.return_value.data = [
            {"estimated_cost": 0.60, "success": True}
        ]
        with patch("app.db.get_supabase_client", return_value=client):
            status = budget_guard.check_combined(
                doc_id="d1",
                doc_cap_usd=0.10,
                daily_cap_usd=0.50,
                sliding_24h_cap_usd=0.30,  # sliding 도 도달했지만 daily 가 먼저 fail
            )
        self.assertFalse(status.allowed)
        # priority: doc → daily → sliding. daily 가 먼저 fail.
        self.assertEqual(status.scope, "daily")

    def test_all_three_pass_returns_sliding_status(self) -> None:
        """셋 다 통과 → 가장 마지막 검사 (sliding) status 반환."""
        from app.services import budget_guard

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
        chain_gte = (
            client.table.return_value
            .select.return_value
            .gte.return_value
            .eq.return_value
        )
        chain_gte.execute.return_value.data = [
            {"estimated_cost": 0.10, "success": True}
        ]
        with patch("app.db.get_supabase_client", return_value=client):
            status = budget_guard.check_combined(
                doc_id="d1",
                doc_cap_usd=0.50,
                daily_cap_usd=0.50,
                sliding_24h_cap_usd=0.50,
            )
        self.assertTrue(status.allowed)
        self.assertEqual(status.scope, "24h_sliding")

    def test_sliding_arg_none_falls_back_to_daily_only(self) -> None:
        """D4 호환 — sliding 인자 None 이면 doc + daily 만 검사."""
        from app.services import budget_guard

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
        chain_gte = (
            client.table.return_value
            .select.return_value
            .gte.return_value
            .eq.return_value
        )
        chain_gte.execute.return_value.data = [
            {"estimated_cost": 0.10, "success": True}
        ]
        with patch("app.db.get_supabase_client", return_value=client):
            status = budget_guard.check_combined(
                doc_id="d1",
                doc_cap_usd=0.50,
                daily_cap_usd=0.50,
                # sliding 미지정 → 기존 D4 동작
            )
        self.assertTrue(status.allowed)
        self.assertEqual(status.scope, "daily")  # sliding 검사 skip


class BudgetGuardIngestSlidingIntegrationTest(unittest.TestCase):
    """D5 — extract.py 의 사전 가드가 sliding 도달 시 vision 호출 0 + flags 마킹."""

    def setUp(self) -> None:
        from app.services import budget_guard
        budget_guard._reset_first_warn_for_test()
        os.environ.pop("JETRAG_BUDGET_GUARD_DISABLE", None)

    def test_pre_check_blocks_vision_enrich_with_sliding_scope(self) -> None:
        """sliding 도달 시 _enrich_pdf_with_vision 호출 0 + scope='24h_sliding'."""
        from app.ingest.stages import extract
        from app.services import budget_guard

        doc_row = {
            "doc_type": "pdf",
            "storage_path": "test/foo.pdf",
            "flags": {},
            "sha256": "b" * 64,
        }
        sliding_status = budget_guard.BudgetStatus(
            allowed=False,
            used_usd=0.55,
            cap_usd=0.50,
            scope="24h_sliding",
            reason="최근 24시간 비용 한도 초과 ($0.5500 > $0.5000)",
        )

        update_calls: list[dict] = []
        client = MagicMock()
        (
            client.table.return_value
            .select.return_value
            .eq.return_value
            .limit.return_value
            .execute.return_value.data
        ) = [doc_row]
        update_chain = MagicMock()
        client.table.return_value.update.return_value.eq.return_value = update_chain
        update_chain.execute.return_value = MagicMock()

        def capture_update(payload):
            update_calls.append(payload)
            return client.table.return_value.update.return_value

        client.table.return_value.update.side_effect = capture_update

        fake_storage = MagicMock()
        fake_storage.get.return_value = b"%PDF-1.7\nfake"

        from app.adapters.parser import ExtractionResult
        fake_pdf_parser = MagicMock()
        fake_pdf_parser.parse.return_value = ExtractionResult(
            source_type="pdf",
            sections=[],
            raw_text="hello world " * 20,
            warnings=[],
        )

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
                            budget_guard, "check_combined", return_value=sliding_status
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

        # vision_enrich 호출 0
        self.assertEqual(len(enrich_called), 0)
        # flags.vision_budget.scope='24h_sliding'
        flags_updates = [
            c for c in update_calls
            if isinstance(c, dict) and "flags" in c
            and c["flags"].get("vision_budget_exceeded") is True
        ]
        self.assertGreaterEqual(len(flags_updates), 1)
        flags = flags_updates[0]["flags"]
        self.assertEqual(flags["vision_budget"]["scope"], "24h_sliding")
        self.assertAlmostEqual(flags["vision_budget"]["used_usd"], 0.55)
        self.assertAlmostEqual(flags["vision_budget"]["cap_usd"], 0.50)
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


class BudgetGuardPageCapTest(unittest.TestCase):
    """S2 D2 (2026-05-08) — `check_doc_page_cap` 페이지 cap 검사.

    in-memory 카운터라 DB 미접근 — mock 불필요. ENV 비활성 토글 + 무한 모드 (cap=0) +
    cap 미만/도달 4 case 전수 커버.
    """

    def setUp(self) -> None:
        from app.services import budget_guard
        budget_guard._reset_first_warn_for_test()
        os.environ.pop("JETRAG_BUDGET_GUARD_DISABLE", None)

    def test_page_cap_within_limit_allowed(self) -> None:
        from app.services import budget_guard

        status = budget_guard.check_doc_page_cap(called_pages=10, page_cap=50)
        self.assertTrue(status.allowed)
        self.assertEqual(status.scope, "page_cap")
        self.assertEqual(status.used_usd, 10.0)
        self.assertEqual(status.cap_usd, 50.0)
        self.assertIn("페이지 한도 내", status.reason)

    def test_page_cap_at_limit_blocked(self) -> None:
        """called_pages == page_cap 시 차단 (다음 호출부터 stop)."""
        from app.services import budget_guard

        status = budget_guard.check_doc_page_cap(called_pages=50, page_cap=50)
        self.assertFalse(status.allowed)
        self.assertEqual(status.scope, "page_cap")
        self.assertEqual(status.used_usd, 50.0)
        self.assertEqual(status.cap_usd, 50.0)
        self.assertIn("페이지 한도 도달", status.reason)
        self.assertIn("50/50", status.reason)

    def test_page_cap_over_limit_blocked(self) -> None:
        """called_pages > page_cap (방어적 case) 도 차단."""
        from app.services import budget_guard

        status = budget_guard.check_doc_page_cap(called_pages=51, page_cap=50)
        self.assertFalse(status.allowed)
        self.assertEqual(status.used_usd, 51.0)
        self.assertIn("51/50", status.reason)

    def test_page_cap_disabled_when_zero_returns_allowed(self) -> None:
        """ENV 0 (무한 모드) — called_pages 무관 항상 allowed=True (회복 토글)."""
        from app.services import budget_guard

        # 매우 큰 called_pages 도 cap=0 시 통과.
        status = budget_guard.check_doc_page_cap(called_pages=10000, page_cap=0)
        self.assertTrue(status.allowed)
        self.assertEqual(status.scope, "page_cap")
        self.assertIn("무한", status.reason)

    def test_page_cap_negative_treated_as_unlimited(self) -> None:
        """page_cap 음수도 무한 모드로 처리 (방어적 — _parse_int 가 음수 허용)."""
        from app.services import budget_guard

        status = budget_guard.check_doc_page_cap(called_pages=999, page_cap=-1)
        self.assertTrue(status.allowed)

    def test_page_cap_disabled_env_returns_allowed(self) -> None:
        """ENV `JETRAG_BUDGET_GUARD_DISABLE=1` 시 cap 도달해도 allowed=True."""
        from app.services import budget_guard

        os.environ["JETRAG_BUDGET_GUARD_DISABLE"] = "1"
        try:
            status = budget_guard.check_doc_page_cap(called_pages=100, page_cap=50)
            self.assertTrue(status.allowed)
            self.assertEqual(status.scope, "page_cap")
            self.assertIn("가드 비활성", status.reason)
        finally:
            os.environ.pop("JETRAG_BUDGET_GUARD_DISABLE", None)


class BudgetGuardScopeLiteralTest(unittest.TestCase):
    """S2 D2 — BudgetScope literal 에 'page_cap' 추가 회귀 보호.

    JSON flags 에 그대로 저장되는 string 이라 spelling 회귀 시 UI 처리 깨짐.
    """

    def test_page_cap_scope_value(self) -> None:
        from app.services import budget_guard

        status = budget_guard.check_doc_page_cap(called_pages=10, page_cap=5)
        # flags 저장 시 그대로 str 화되는 값이라 string 비교 검증.
        self.assertEqual(status.scope, "page_cap")
        # 다른 scope 들과 충돌 X
        self.assertNotIn(status.scope, ("doc", "daily", "24h_sliding"))


if __name__ == "__main__":
    unittest.main()
