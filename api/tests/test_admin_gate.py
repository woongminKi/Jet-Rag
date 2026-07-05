"""D1 — admin OWNER 게이트 단위 테스트 (plan §3·§7, D1-Q7).

대상: `app.auth.dependencies.require_admin` + /admin/* router-level 게이트.

검증 (require_admin 직접 호출 — 외부 의존성 0):
- auth_enabled=false → 통과 (기존 single-user MVP 동작 보존)
- auth_enabled=true + 호출자 == OWNER → 통과
- auth_enabled=true + 호출자 != OWNER → 403
- auth_enabled=true + OWNER 미설정 → 403 (안전 — 운영자 미지정 시 전면 차단)

+ TestClient 레이어: auth_enabled=true 에서 토큰 없이 /admin/* → 401.
실행: `python -m unittest tests.test_admin_gate`
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")

from fastapi import HTTPException

from app.auth import CurrentUser
from app.auth.dependencies import get_current_user, require_admin
from app.config import Settings, get_settings

_OWNER_ID = "99999999-9999-9999-9999-999999999999"
_OTHER_ID = "33333333-3333-3333-3333-333333333333"
_DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001"


def _make_settings(*, auth_enabled: bool, owner: str | None) -> Settings:
    return Settings(
        supabase_url="",
        supabase_key="",
        supabase_service_role_key="",
        supabase_storage_bucket="documents",
        gemini_api_key="",
        hf_api_token="dummy-test-token",
        default_user_id=_DEFAULT_USER_ID,
        doc_budget_usd=0.1,
        daily_budget_usd=0.5,
        sliding_24h_budget_usd=0.5,
        budget_krw_per_usd=1380.0,
        vision_need_score_enabled=True,
        vision_page_cap_per_doc=50,
        auth_enabled=auth_enabled,
        supabase_jwt_secret="test-secret",
        supabase_jwt_algorithm="HS256",
        owner_user_id=owner,
    )


class RequireAdminTest(unittest.TestCase):
    def test_auth_disabled_passes(self) -> None:
        settings = _make_settings(auth_enabled=False, owner=None)
        user = CurrentUser(user_id=_DEFAULT_USER_ID)
        # owner 미설정이라도 auth_enabled=false 면 통과.
        result = require_admin(current_user=user, settings=settings)
        self.assertEqual(result.user_id, _DEFAULT_USER_ID)

    def test_owner_passes(self) -> None:
        settings = _make_settings(auth_enabled=True, owner=_OWNER_ID)
        user = CurrentUser(user_id=_OWNER_ID)
        result = require_admin(current_user=user, settings=settings)
        self.assertEqual(result.user_id, _OWNER_ID)

    def test_non_owner_forbidden(self) -> None:
        settings = _make_settings(auth_enabled=True, owner=_OWNER_ID)
        user = CurrentUser(user_id=_OTHER_ID)
        with self.assertRaises(HTTPException) as ctx:
            require_admin(current_user=user, settings=settings)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail, "운영자 권한이 필요합니다.")

    def test_owner_unset_forbidden_when_enabled(self) -> None:
        # auth_enabled=true + OWNER 미설정 → 전면 차단 (안전).
        settings = _make_settings(auth_enabled=True, owner=None)
        user = CurrentUser(user_id=_OTHER_ID)
        with self.assertRaises(HTTPException) as ctx:
            require_admin(current_user=user, settings=settings)
        self.assertEqual(ctx.exception.status_code, 403)


class AdminRouteGateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._patchers = [
            patch("app.main._warmup_bgem3", new=AsyncMock(return_value=None)),
            patch("app.main._sweep_stale_ingest_jobs", new=AsyncMock(return_value=None)),
        ]
        for p in cls._patchers:
            p.start()
        from fastapi.testclient import TestClient

        from app.main import app

        cls.app = app
        cls.TestClient = TestClient

    @classmethod
    def tearDownClass(cls) -> None:
        for p in cls._patchers:
            p.stop()

    def tearDown(self) -> None:
        self.app.dependency_overrides.clear()

    def test_admin_without_token_returns_403(self) -> None:
        # 토큰 없음 → 익명(is_authenticated=False) → require_admin 이 403 반환.
        # require_admin 은 is_authenticated=False 도 차단하므로 401 이 아닌 403.
        self.app.dependency_overrides[get_settings] = lambda: _make_settings(
            auth_enabled=True, owner=_OWNER_ID
        )
        with self.TestClient(self.app) as client:
            resp = client.get("/admin/queries/stats")
            self.assertEqual(resp.status_code, 403)

    def test_admin_non_owner_returns_403(self) -> None:
        self.app.dependency_overrides[get_settings] = lambda: _make_settings(
            auth_enabled=True, owner=_OWNER_ID
        )
        self.app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            user_id=_OTHER_ID
        )
        with self.TestClient(self.app) as client:
            resp = client.get("/admin/queries/stats")
            self.assertEqual(resp.status_code, 403)
            self.assertEqual(resp.json().get("detail"), "운영자 권한이 필요합니다.")


if __name__ == "__main__":
    unittest.main()
