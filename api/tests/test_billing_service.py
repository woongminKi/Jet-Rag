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
    sb = MagicMock()
    chain = sb.table.return_value
    for attr in ("select", "eq", "lte", "in_", "order", "limit", "update", "insert", "upsert"):
        getattr(chain, attr).return_value = chain
    chain.execute.return_value.data = rows
    return sb


def _update_dicts(sb: MagicMock) -> list[dict]:
    return [c.args[0] for c in sb.table.return_value.update.call_args_list]


def _insert_dicts(sb: MagicMock) -> list[dict]:
    return [c.args[0] for c in sb.table.return_value.insert.call_args_list]


AT = datetime(2026, 7, 7, tzinfo=timezone.utc)


class StartSubscriptionTest(unittest.TestCase):
    @patch("app.services.billing.get_payment_provider")
    @patch("app.services.billing.get_supabase_client")
    def test_start_new_user_inserts_placeholder(self, mock_sb, mock_provider) -> None:
        mock_provider.return_value.ready.return_value = ReadyResult(tid="T1", redirect_url="https://k/pay")
        sb = _sb_select_returning([])  # 기존 행 없음
        mock_sb.return_value = sb
        result = billing.start_subscription("u1")
        self.assertEqual(result.redirect_url, "https://k/pay")
        inserts = _insert_dicts(sb)
        self.assertTrue(any(d.get("pending_tid") == "T1" and d.get("status") == "canceled" for d in inserts))

    @patch("app.services.billing.get_payment_provider")
    @patch("app.services.billing.get_supabase_client")
    def test_start_existing_active_preserves_status(self, mock_sb, mock_provider) -> None:
        mock_provider.return_value.ready.return_value = ReadyResult(tid="T2", redirect_url="https://k/pay")
        sb = _sb_select_returning([{"status": "active"}])  # 기존 active 구독자
        mock_sb.return_value = sb
        billing.start_subscription("u1")
        updates = _update_dicts(sb)
        self.assertTrue(any(d.get("pending_tid") == "T2" for d in updates))
        self.assertFalse(any("status" in d for d in updates))  # status 안 건드림
        self.assertEqual(len(_insert_dicts(sb)), 0)  # insert 안 함


class ApproveSubscriptionTest(unittest.TestCase):
    @patch("app.services.billing.encrypt_sid", return_value="ENC(S1)")
    @patch("app.services.billing.get_payment_provider")
    @patch("app.services.billing.get_supabase_client")
    def test_approve_stores_encrypted_sid_and_activates(self, mock_sb, mock_provider, _enc) -> None:
        mock_provider.return_value.approve.return_value = ApproveResult(sid="S1", tid="T1")
        sb = _sb_select_returning([{"pending_tid": "T1"}])
        mock_sb.return_value = sb
        billing.approve_subscription("u1", "pg_token_x")
        mock_provider.return_value.approve.assert_called_once()
        updates = _update_dicts(sb)
        self.assertTrue(any(d.get("billing_key") == "ENC(S1)" for d in updates))
        self.assertTrue(any(d.get("status") == "active" for d in updates))

    @patch("app.services.billing.get_payment_provider")
    @patch("app.services.billing.get_supabase_client")
    def test_approve_without_pending_tid_raises(self, mock_sb, mock_provider) -> None:
        sb = _sb_select_returning([{"pending_tid": None}])
        mock_sb.return_value = sb
        with self.assertRaises(billing.SubscriptionNotPendingError):
            billing.approve_subscription("u1", "pg")


class ChargeDueTest(unittest.TestCase):
    @patch("app.services.billing._already_charged", return_value=False)
    @patch("app.services.billing.decrypt_sid", return_value="S1")
    @patch("app.services.billing.get_payment_provider")
    @patch("app.services.billing.get_supabase_client")
    def test_charge_success_advances_period(self, mock_sb, mock_provider, _dec, _idem) -> None:
        due = [{"user_id": "u1", "billing_key": "ENC", "status": "active",
                "current_period_end": "2026-07-01T00:00:00+00:00", "past_due_since": None}]
        sb = _sb_select_returning(due)
        mock_sb.return_value = sb
        report = billing.charge_due_subscriptions(now=AT)
        self.assertEqual(report.charged, 1)
        self.assertEqual(report.failed, 0)
        mock_provider.return_value.subscribe.assert_called_once()
        updates = _update_dicts(sb)
        self.assertTrue(any(
            d.get("current_period_end") == "2026-08-01T00:00:00+00:00" and d.get("status") == "active"
            for d in updates
        ))

    @patch("app.services.billing._already_charged", return_value=True)
    @patch("app.services.billing.decrypt_sid", return_value="S1")
    @patch("app.services.billing.get_payment_provider")
    @patch("app.services.billing.get_supabase_client")
    def test_charge_idempotent_skips_recharge(self, mock_sb, mock_provider, _dec, _idem) -> None:
        due = [{"user_id": "u1", "billing_key": "ENC", "status": "active",
                "current_period_end": "2026-07-01T00:00:00+00:00", "past_due_since": None}]
        sb = _sb_select_returning(due)
        mock_sb.return_value = sb
        report = billing.charge_due_subscriptions(now=AT)
        mock_provider.return_value.subscribe.assert_not_called()  # 재청구 안 함
        self.assertEqual(report.charged, 1)  # 기간 갱신은 진행
        updates = _update_dicts(sb)
        self.assertTrue(any(d.get("current_period_end") == "2026-08-01T00:00:00+00:00" for d in updates))

    @patch("app.services.billing._already_charged", return_value=False)
    @patch("app.services.billing.decrypt_sid", return_value="S1")
    @patch("app.services.billing.get_payment_provider")
    @patch("app.services.billing.get_supabase_client")
    def test_charge_failure_sets_past_due(self, mock_sb, mock_provider, _dec, _idem) -> None:
        mock_provider.return_value.subscribe.side_effect = PaymentError("declined")
        due = [{"user_id": "u1", "billing_key": "ENC", "status": "active",
                "current_period_end": "2026-07-01T00:00:00+00:00", "past_due_since": None}]
        sb = _sb_select_returning(due)
        mock_sb.return_value = sb
        report = billing.charge_due_subscriptions(now=AT)
        self.assertEqual(report.charged, 0)
        self.assertEqual(report.failed, 1)
        pd = [d for d in _update_dicts(sb) if d.get("status") == "past_due"]
        self.assertEqual(len(pd), 1)
        self.assertIn("past_due_since", pd[0])  # 최초 실패 → 기록

    @patch("app.services.billing._already_charged", return_value=False)
    @patch("app.services.billing.decrypt_sid", return_value="S1")
    @patch("app.services.billing.get_payment_provider")
    @patch("app.services.billing.get_supabase_client")
    def test_charge_failure_preserves_grace_clock(self, mock_sb, mock_provider, _dec, _idem) -> None:
        mock_provider.return_value.subscribe.side_effect = PaymentError("declined")
        due = [{"user_id": "u1", "billing_key": "ENC", "status": "past_due",
                "current_period_end": "2026-07-01T00:00:00+00:00",
                "past_due_since": "2026-06-20T00:00:00+00:00"}]  # 이미 설정됨
        sb = _sb_select_returning(due)
        mock_sb.return_value = sb
        billing.charge_due_subscriptions(now=AT)
        pd = [d for d in _update_dicts(sb) if d.get("status") == "past_due"]
        self.assertEqual(len(pd), 1)
        self.assertNotIn("past_due_since", pd[0])  # grace clock 리셋 안 함

    @patch("app.services.billing.get_payment_provider")
    @patch("app.services.billing.get_supabase_client")
    def test_missing_billing_key_marks_past_due(self, mock_sb, mock_provider) -> None:
        due = [{"user_id": "u1", "billing_key": None, "status": "active",
                "current_period_end": "2026-07-01T00:00:00+00:00", "past_due_since": None}]
        sb = _sb_select_returning(due)
        mock_sb.return_value = sb
        report = billing.charge_due_subscriptions(now=AT)
        self.assertEqual(report.failed, 1)
        mock_provider.return_value.subscribe.assert_not_called()
        self.assertTrue(any(d.get("status") == "past_due" for d in _update_dicts(sb)))


class SweepPastDueTest(unittest.TestCase):
    @patch("app.services.billing.decrypt_sid", return_value="S1")
    @patch("app.services.billing.get_payment_provider")
    @patch("app.services.billing.get_supabase_client")
    def test_sweep_cancels_after_grace(self, mock_sb, mock_provider, _dec) -> None:
        overdue = [{"user_id": "u1", "billing_key": "ENC", "past_due_since": "2026-06-25T00:00:00+00:00"}]
        sb = _sb_select_returning(overdue)
        mock_sb.return_value = sb
        report = billing.sweep_past_due(now=AT)
        self.assertEqual(report.canceled, 1)
        mock_provider.return_value.inactivate.assert_called_once()


class CancelSubscriptionTest(unittest.TestCase):
    @patch("app.services.billing.decrypt_sid", return_value="S1")
    @patch("app.services.billing.get_payment_provider")
    @patch("app.services.billing.get_supabase_client")
    def test_cancel_inactivates_and_sets_canceled(self, mock_sb, mock_provider, _dec) -> None:
        sb = _sb_select_returning([{"billing_key": "ENC"}])
        mock_sb.return_value = sb
        billing.cancel_subscription("u1")
        mock_provider.return_value.inactivate.assert_called_once()
        self.assertTrue(any(d.get("status") == "canceled" for d in _update_dicts(sb)))


if __name__ == "__main__":
    unittest.main()
