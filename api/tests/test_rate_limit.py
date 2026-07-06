"""수익화 W2 — app.services.rate_limit 단위 테스트.

stdlib unittest + MagicMock (Supabase RPC mock). 외부 I/O 0.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")

from fastapi import HTTPException

from app.auth.dependencies import CurrentUser
from app.config import Settings


def _make_settings(**over) -> Settings:
    base = dict(
        supabase_url="https://x.supabase.co",
        supabase_key="",
        supabase_service_role_key="svc",
        supabase_storage_bucket="documents",
        gemini_api_key="",
        hf_api_token="",
        default_user_id="00000000-0000-0000-0000-000000000001",
        doc_budget_usd=0.1,
        daily_budget_usd=0.5,
        sliding_24h_budget_usd=0.5,
        budget_krw_per_usd=1380.0,
        vision_need_score_enabled=True,
        vision_page_cap_per_doc=50,
        auth_enabled=True,
        owner_user_id="00000000-0000-0000-0000-0000000000ff",
        rate_limit_answers_per_day=5,
        rate_limit_docs_per_day=3,
    )
    base.update(over)
    return Settings(**base)


class _FakeRequest:
    def __init__(self, headers=None, client_host="9.9.9.9"):
        self.headers = headers or {}
        self.client = type("C", (), {"host": client_host})()


class ClientIpTest(unittest.TestCase):
    def test_xff_first_wins(self) -> None:
        from app.services.rate_limit import _client_ip

        req = _FakeRequest(headers={"X-Forwarded-For": "1.1.1.1, 2.2.2.2"})
        self.assertEqual(_client_ip(req), "1.1.1.1")

    def test_fallback_to_client_host(self) -> None:
        from app.services.rate_limit import _client_ip

        self.assertEqual(_client_ip(_FakeRequest(client_host="3.3.3.3")), "3.3.3.3")


class BuildUserKeyTest(unittest.TestCase):
    def test_authenticated_uses_user_id(self) -> None:
        from app.services.rate_limit import build_user_key

        user = CurrentUser(user_id="uid-42", is_authenticated=True)
        self.assertEqual(build_user_key(user, _FakeRequest()), "uid-42")

    def test_anonymous_uses_ip_prefix(self) -> None:
        from app.services.rate_limit import build_user_key

        user = CurrentUser(user_id="owner", is_authenticated=False)
        req = _FakeRequest(headers={"X-Forwarded-For": "8.8.8.8"})
        self.assertEqual(build_user_key(user, req), "ip:8.8.8.8")


class EnforceRateLimitTest(unittest.TestCase):
    def _mock_client(self, returned_count: int) -> MagicMock:
        client = MagicMock()
        client.rpc.return_value.execute.return_value.data = returned_count
        return client

    def test_skips_when_auth_disabled(self) -> None:
        from app.services import rate_limit

        settings = _make_settings(auth_enabled=False)
        user = CurrentUser(user_id="u", is_authenticated=True)
        with patch.object(rate_limit, "get_supabase_client") as gc:
            rate_limit.enforce_rate_limit("answers", _FakeRequest(), user, settings)
            gc.assert_not_called()  # RPC 호출 자체가 없어야 함

    def test_unlimited_when_cap_zero(self) -> None:
        from app.services import rate_limit

        settings = _make_settings(rate_limit_answers_per_day=0)
        user = CurrentUser(user_id="u", is_authenticated=True)
        with patch.object(rate_limit, "get_supabase_client") as gc:
            rate_limit.enforce_rate_limit("answers", _FakeRequest(), user, settings)
            gc.assert_not_called()

    def test_under_cap_passes(self) -> None:
        from app.services import rate_limit

        settings = _make_settings(rate_limit_answers_per_day=5)
        user = CurrentUser(user_id="u", is_authenticated=True)
        with patch.object(rate_limit, "get_supabase_client", return_value=self._mock_client(5)):
            # count == cap → 통과 (cap 은 허용 최대치)
            rate_limit.enforce_rate_limit("answers", _FakeRequest(), user, settings)

    def test_over_cap_raises_429(self) -> None:
        from app.services import rate_limit

        settings = _make_settings(rate_limit_answers_per_day=5)
        user = CurrentUser(user_id="u", is_authenticated=True)
        with patch.object(rate_limit, "get_supabase_client", return_value=self._mock_client(6)):
            with self.assertRaises(HTTPException) as ctx:
                rate_limit.enforce_rate_limit("answers", _FakeRequest(), user, settings)
            self.assertEqual(ctx.exception.status_code, 429)

    def test_rpc_failure_fails_open(self) -> None:
        from app.services import rate_limit

        settings = _make_settings(rate_limit_answers_per_day=5)
        user = CurrentUser(user_id="u", is_authenticated=True)
        client = MagicMock()
        client.rpc.side_effect = RuntimeError("db down")
        with patch.object(rate_limit, "get_supabase_client", return_value=client):
            # 예외 전파 없이 통과해야 함 (fail-open)
            rate_limit.enforce_rate_limit("answers", _FakeRequest(), user, settings)

    def test_docs_metric_uses_docs_cap(self) -> None:
        from app.services import rate_limit

        settings = _make_settings(rate_limit_docs_per_day=3)
        user = CurrentUser(user_id="u", is_authenticated=True)
        with patch.object(rate_limit, "get_supabase_client", return_value=self._mock_client(4)):
            with self.assertRaises(HTTPException) as ctx:
                rate_limit.enforce_rate_limit("docs", _FakeRequest(), user, settings)
            self.assertEqual(ctx.exception.status_code, 429)


if __name__ == "__main__":
    unittest.main()
