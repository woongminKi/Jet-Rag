"""수익화 W3 — admin 구독 수동 upsert/조회. require_admin 게이트는 라우터 레벨."""
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")

from fastapi.testclient import TestClient

from app.auth.dependencies import CurrentUser, get_current_user, require_admin
from app.main import app

_ADMIN = CurrentUser(user_id="00000000-0000-0000-0000-0000000000ff", is_authenticated=True)


class AdminSubscriptionsTest(unittest.TestCase):
    def setUp(self) -> None:
        app.dependency_overrides[require_admin] = lambda: _ADMIN
        app.dependency_overrides[get_current_user] = lambda: _ADMIN
        self.client = TestClient(app, raise_server_exceptions=False)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_upsert_subscription(self) -> None:
        mock_client = MagicMock()
        mock_client.table.return_value.upsert.return_value.execute.return_value.data = [
            {"user_id": "u-1", "plan_code": "pro", "status": "active"}
        ]
        with patch(
            "app.routers.admin.get_supabase_client", return_value=mock_client
        ):
            resp = self.client.post(
                "/admin/subscriptions",
                json={"user_id": "u-1", "plan_code": "pro"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["plan_code"], "pro")
        mock_client.table.assert_called_with("subscriptions")

    def test_list_subscriptions(self) -> None:
        mock_client = MagicMock()
        (
            mock_client.table.return_value.select.return_value.order.return_value
            .limit.return_value.execute.return_value
        ).data = [
            {
                "user_id": "u-1",
                "plan_code": "pro",
                "status": "active",
                "current_period_end": None,
                "updated_at": "2026-07-06T00:00:00+00:00",
            }
        ]
        with patch(
            "app.routers.admin.get_supabase_client", return_value=mock_client
        ):
            resp = self.client.get("/admin/subscriptions")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()["items"]), 1)


if __name__ == "__main__":
    unittest.main()
