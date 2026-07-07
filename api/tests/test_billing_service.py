# api/tests/test_billing_service.py
from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")

from app.adapters.payment import ApproveResult, PaymentError, ReadyResult
from app.services import billing


def _sb_select_returning(rows: list[dict]) -> MagicMock:
    """table().select()...execute().data = rows 형태 mock 빌더."""
    sb = MagicMock()
    chain = sb.table.return_value
    for attr in ("select", "eq", "lte", "in_", "order", "limit", "update", "insert", "upsert"):
        getattr(chain, attr).return_value = chain
    chain.execute.return_value.data = rows
    return sb


class StartSubscriptionTest(unittest.TestCase):
    @patch("app.services.billing.get_payment_provider")
    @patch("app.services.billing.get_supabase_client")
    def test_start_calls_ready_and_stores_pending_tid(self, mock_sb, mock_provider) -> None:
        provider = mock_provider.return_value
        provider.ready.return_value = ReadyResult(tid="T1", redirect_url="https://k/pay")
        sb = _sb_select_returning([])
        mock_sb.return_value = sb
        result = billing.start_subscription("u1")
        self.assertEqual(result.redirect_url, "https://k/pay")
        provider.ready.assert_called_once()
        sb.table.assert_any_call("subscriptions")


class ApproveSubscriptionTest(unittest.TestCase):
    @patch("app.services.billing.encrypt_sid", return_value="ENC(S1)")
    @patch("app.services.billing.get_payment_provider")
    @patch("app.services.billing.get_supabase_client")
    def test_approve_stores_encrypted_sid_and_activates(
        self, mock_sb, mock_provider, _enc
    ) -> None:
        provider = mock_provider.return_value
        provider.approve.return_value = ApproveResult(sid="S1", tid="T1")
        sb = _sb_select_returning([{"pending_tid": "T1"}])
        mock_sb.return_value = sb
        billing.approve_subscription("u1", "pg_token_x")
        provider.approve.assert_called_once()
        update_calls = [c.args[0] for c in sb.table.return_value.update.call_args_list]
        self.assertTrue(any(d.get("billing_key") == "ENC(S1)" for d in update_calls))
        self.assertTrue(any(d.get("status") == "active" for d in update_calls))

    @patch("app.services.billing.get_payment_provider")
    @patch("app.services.billing.get_supabase_client")
    def test_approve_without_pending_tid_raises(self, mock_sb, mock_provider) -> None:
        sb = _sb_select_returning([{"pending_tid": None}])
        mock_sb.return_value = sb
        with self.assertRaises(billing.SubscriptionNotPendingError):
            billing.approve_subscription("u1", "pg")


class ChargeDueTest(unittest.TestCase):
    @patch("app.services.billing.decrypt_sid", return_value="S1")
    @patch("app.services.billing.get_payment_provider")
    @patch("app.services.billing.get_supabase_client")
    def test_charge_success_advances_period(self, mock_sb, mock_provider, _dec) -> None:
        due = [{
            "user_id": "u1", "billing_key": "ENC", "status": "active",
            "current_period_end": "2026-07-01T00:00:00+00:00", "past_due_since": None,
        }]
        sb = _sb_select_returning(due)
        mock_sb.return_value = sb
        report = billing.charge_due_subscriptions(now=datetime(2026, 7, 7, tzinfo=timezone.utc))
        self.assertEqual(report.charged, 1)
        self.assertEqual(report.failed, 0)
        mock_provider.return_value.subscribe.assert_called_once()

    @patch("app.services.billing.decrypt_sid", return_value="S1")
    @patch("app.services.billing.get_payment_provider")
    @patch("app.services.billing.get_supabase_client")
    def test_charge_failure_sets_past_due(self, mock_sb, mock_provider, _dec) -> None:
        mock_provider.return_value.subscribe.side_effect = PaymentError("declined")
        due = [{
            "user_id": "u1", "billing_key": "ENC", "status": "active",
            "current_period_end": "2026-07-01T00:00:00+00:00", "past_due_since": None,
        }]
        sb = _sb_select_returning(due)
        mock_sb.return_value = sb
        report = billing.charge_due_subscriptions(now=datetime(2026, 7, 7, tzinfo=timezone.utc))
        self.assertEqual(report.charged, 0)
        self.assertEqual(report.failed, 1)


class SweepPastDueTest(unittest.TestCase):
    @patch("app.services.billing.decrypt_sid", return_value="S1")
    @patch("app.services.billing.get_payment_provider")
    @patch("app.services.billing.get_supabase_client")
    def test_sweep_cancels_after_grace(self, mock_sb, mock_provider, _dec) -> None:
        overdue = [{"user_id": "u1", "billing_key": "ENC",
                    "past_due_since": "2026-06-25T00:00:00+00:00"}]
        sb = _sb_select_returning(overdue)
        mock_sb.return_value = sb
        report = billing.sweep_past_due(now=datetime(2026, 7, 7, tzinfo=timezone.utc))
        self.assertEqual(report.canceled, 1)
        mock_provider.return_value.inactivate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
