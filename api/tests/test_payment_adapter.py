# api/tests/test_payment_adapter.py
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")

from app.adapters.payment import ApproveResult, PaymentError, ReadyResult
from app.adapters.impl.kakaopay import KakaoPayImpl
from app.adapters.impl.kakaopay import KakaoPayImpl as _KPImpl
from app.adapters.payment_factory import get_payment_provider
from app.config import get_settings


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


def _resp(status_code: int, body: dict) -> MagicMock:
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = body
    m.text = str(body)
    return m


class KakaoPayImplTest(unittest.TestCase):
    def _impl(self) -> KakaoPayImpl:
        return KakaoPayImpl(secret_key="sk_test", cid="TCSUBSCRIP")

    def test_ready_returns_tid_and_redirect(self) -> None:
        with patch("app.adapters.impl.kakaopay.httpx.Client") as MockClient:
            client = MockClient.return_value.__enter__.return_value
            client.post.return_value = _resp(200, {
                "tid": "T123",
                "next_redirect_pc_url": "https://kakao/pc",
                "next_redirect_mobile_url": "https://kakao/m",
            })
            result = self._impl().ready(
                partner_order_id="u1", partner_user_id="u1",
                approval_url="https://a", cancel_url="https://c", fail_url="https://f",
            )
        self.assertEqual(result.tid, "T123")
        self.assertEqual(result.redirect_url, "https://kakao/pc")
        _, kwargs = client.post.call_args
        self.assertEqual(kwargs["headers"]["Authorization"], "SECRET_KEY sk_test")
        self.assertEqual(kwargs["json"]["cid"], "TCSUBSCRIP")
        self.assertEqual(kwargs["json"]["total_amount"], 6900)

    def test_approve_returns_sid(self) -> None:
        with patch("app.adapters.impl.kakaopay.httpx.Client") as MockClient:
            client = MockClient.return_value.__enter__.return_value
            client.post.return_value = _resp(200, {"sid": "S999", "tid": "T123"})
            result = self._impl().approve(
                tid="T123", partner_order_id="u1", partner_user_id="u1", pg_token="pg",
            )
        self.assertEqual(result.sid, "S999")

    def test_approve_missing_sid_raises(self) -> None:
        with patch("app.adapters.impl.kakaopay.httpx.Client") as MockClient:
            client = MockClient.return_value.__enter__.return_value
            client.post.return_value = _resp(200, {"tid": "T123"})
            with self.assertRaises(PaymentError):
                self._impl().approve(
                    tid="T123", partner_order_id="u1", partner_user_id="u1", pg_token="pg",
                )

    def test_non_2xx_raises_payment_error(self) -> None:
        with patch("app.adapters.impl.kakaopay.httpx.Client") as MockClient:
            client = MockClient.return_value.__enter__.return_value
            client.post.return_value = _resp(400, {"error_code": -780})
            with self.assertRaises(PaymentError):
                self._impl().subscribe(sid="S1", partner_order_id="u1", partner_user_id="u1")

    def test_empty_secret_key_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            KakaoPayImpl(secret_key="", cid="TCSUBSCRIP")

    def test_network_error_raises_payment_error(self) -> None:
        import httpx
        with patch("app.adapters.impl.kakaopay.httpx.Client") as MockClient:
            client = MockClient.return_value.__enter__.return_value
            client.post.side_effect = httpx.ConnectTimeout("boom")
            with self.assertRaises(PaymentError):
                self._impl().inactivate(sid="S1")

    def test_ready_incomplete_response_raises(self) -> None:
        with patch("app.adapters.impl.kakaopay.httpx.Client") as MockClient:
            client = MockClient.return_value.__enter__.return_value
            client.post.return_value = _resp(200, {"tid": "T1"})  # redirect 없음
            with self.assertRaises(PaymentError):
                self._impl().ready(
                    partner_order_id="u1", partner_user_id="u1",
                    approval_url="https://a", cancel_url="https://c", fail_url="https://f",
                )

    def test_non_dict_json_raises(self) -> None:
        with patch("app.adapters.impl.kakaopay.httpx.Client") as MockClient:
            client = MockClient.return_value.__enter__.return_value
            client.post.return_value = _resp(200, ["unexpected"])
            with self.assertRaises(PaymentError):
                self._impl().inactivate(sid="S1")

    def test_subscribe_sends_correct_body(self) -> None:
        with patch("app.adapters.impl.kakaopay.httpx.Client") as MockClient:
            client = MockClient.return_value.__enter__.return_value
            client.post.return_value = _resp(200, {"aid": "A1"})
            self._impl().subscribe(sid="S9", partner_order_id="u1-20260707", partner_user_id="u1")
            _, kwargs = client.post.call_args
            self.assertEqual(kwargs["json"]["sid"], "S9")
            self.assertEqual(kwargs["json"]["cid"], "TCSUBSCRIP")
            self.assertEqual(kwargs["json"]["total_amount"], 6900)
            self.assertEqual(kwargs["json"]["partner_order_id"], "u1-20260707")


class PaymentFactoryTest(unittest.TestCase):
    def tearDown(self) -> None:
        get_settings.cache_clear()

    def test_kakaopay_default(self) -> None:
        with patch.dict(os.environ, {
            "JETRAG_PAYMENT_PROVIDER": "kakaopay",
            "JETRAG_KAKAOPAY_SECRET_KEY": "sk_test",
        }):
            get_settings.cache_clear()
            provider = get_payment_provider()
        self.assertIsInstance(provider, _KPImpl)

    def test_unknown_provider_raises(self) -> None:
        with patch.dict(os.environ, {"JETRAG_PAYMENT_PROVIDER": "bogus"}):
            get_settings.cache_clear()
            with self.assertRaises(ValueError):
                get_payment_provider()


if __name__ == "__main__":
    unittest.main()
