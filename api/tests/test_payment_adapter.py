# api/tests/test_payment_adapter.py
from __future__ import annotations

import os
import unittest

os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")

from app.adapters.payment import ApproveResult, PaymentError, ReadyResult


class PaymentTypesTest(unittest.TestCase):
    def test_ready_result_fields(self) -> None:
        r = ReadyResult(tid="T1", redirect_url="https://k/pay")
        self.assertEqual(r.tid, "T1")
        self.assertEqual(r.redirect_url, "https://k/pay")

    def test_approve_result_fields(self) -> None:
        a = ApproveResult(sid="S1", tid="T1")
        self.assertEqual(a.sid, "S1")

    def test_payment_error_is_exception(self) -> None:
        self.assertTrue(issubclass(PaymentError, Exception))


if __name__ == "__main__":
    unittest.main()
