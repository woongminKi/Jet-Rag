# api/tests/test_billing_crypto.py
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")

from cryptography.fernet import Fernet

from app.config import get_settings
from app.services import billing_crypto


class BillingCryptoTest(unittest.TestCase):
    def setUp(self) -> None:
        self.key = Fernet.generate_key().decode("utf-8")

    def test_roundtrip(self) -> None:
        with patch.dict(os.environ, {"JETRAG_BILLING_KEY_ENCRYPTION_KEY": self.key}):
            get_settings.cache_clear()
            token = billing_crypto.encrypt_sid("S1234567890abcdef")
            self.assertNotEqual(token, "S1234567890abcdef")
            self.assertEqual(billing_crypto.decrypt_sid(token), "S1234567890abcdef")
        get_settings.cache_clear()

    def test_missing_key_raises(self) -> None:
        with patch.dict(os.environ, {"JETRAG_BILLING_KEY_ENCRYPTION_KEY": ""}):
            get_settings.cache_clear()
            with self.assertRaises(RuntimeError):
                billing_crypto.encrypt_sid("S123")
        get_settings.cache_clear()


if __name__ == "__main__":
    unittest.main()
