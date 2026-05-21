"""D2 follow-up — `require_authorized_user` invite redeem 게이트 단위 테스트 (E4 fix).

대상: `app.auth.dependencies.require_authorized_user` — 베타 cap 강제 게이트.

검증 (직접 호출 — Supabase mock, 외부 의존성 0):
- auth_enabled=false → 통과 (single-user MVP 보존, DB 호출 0)
- auth_enabled=true + invite_codes 0건 → 403 `초대 코드 redeem 이 필요합니다.`
- auth_enabled=true + invite_codes 1건 → 통과 (current_user 반환)
- auth_enabled=true + SELECT 예외(마이그 017 미적용/장애) → 503

추가 — router-level wiring 검증:
- /documents, /search, /answer, /stats router 의 dependency 가 require_authorized_user 인지
  routes inspection 으로 직접 검증 (실 요청 없이 wiring 만).

실행: `python -m unittest tests.test_auth_dependencies`
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from fastapi import Depends, HTTPException

from app.auth import CurrentUser
from app.auth.dependencies import require_authorized_user
from app.config import Settings

_USER = CurrentUser(user_id="55555555-5555-5555-5555-555555555555")


def _settings(*, auth_enabled: bool) -> Settings:
    """require_authorized_user 가 읽는 auth_enabled 만 의미 있는 최소 Settings."""
    return Settings(
        supabase_url="https://abcd1234.supabase.co",
        supabase_key="",
        supabase_service_role_key="",
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
        auth_enabled=auth_enabled,
    )


def _resp(data):
    r = MagicMock()
    r.data = data
    return r


def _client_with(*, select_rows, select_raises=False):
    """invite_codes SELECT 체인을 반환값으로 구성한 mock client.

    - select_rows: SELECT .execute().data
    - select_raises: SELECT 가 예외 (마이그 017 미적용 시뮬레이션)
    """
    client = MagicMock()
    chain = (
        client.table.return_value.select.return_value.eq.return_value.limit.return_value
    )
    if select_raises:
        chain.execute.side_effect = RuntimeError("relation invite_codes 없음")
    else:
        chain.execute.return_value = _resp(select_rows)
    return client


class RequireAuthorizedUserTest(unittest.TestCase):
    """E4 fix — invite redeem 게이트의 4 분기 동작 검증."""

    def test_require_authorized_user_passes_when_auth_disabled(self) -> None:
        # auth_enabled=false 면 DB 호출 0 — single-user MVP 동작 100% 보존.
        client = MagicMock()
        with patch("app.db.get_supabase_client", return_value=client):
            result = require_authorized_user(
                current_user=_USER,
                settings=_settings(auth_enabled=False),
            )
        self.assertIs(result, _USER)
        client.table.assert_not_called()

    def test_require_authorized_user_blocks_when_no_invite_redeem(self) -> None:
        # SELECT 0건 → 403 (Supabase signup 만 한 random user 차단).
        client = _client_with(select_rows=[])
        with patch("app.db.get_supabase_client", return_value=client):
            with self.assertRaises(HTTPException) as ctx:
                require_authorized_user(
                    current_user=_USER,
                    settings=_settings(auth_enabled=True),
                )
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail, "초대 코드 redeem 이 필요합니다.")
        # SELECT 가 호출자 user_id 로 필터되는지.
        client.table.assert_called_once_with("invite_codes")
        client.table.return_value.select.return_value.eq.assert_called_once_with(
            "used_by", _USER.user_id
        )

    def test_require_authorized_user_passes_when_invite_redeem(self) -> None:
        # SELECT 1건 → 통과 (복귀 유저 — 코드 소진 이력 있음).
        client = _client_with(select_rows=[{"code": "USED-CODE"}])
        with patch("app.db.get_supabase_client", return_value=client):
            result = require_authorized_user(
                current_user=_USER,
                settings=_settings(auth_enabled=True),
            )
        self.assertIs(result, _USER)

    def test_require_authorized_user_503_when_db_error(self) -> None:
        # SELECT 예외(마이그 017 미적용/DB 장애) → 503 graceful.
        client = _client_with(select_rows=None, select_raises=True)
        with patch("app.db.get_supabase_client", return_value=client):
            with self.assertRaises(HTTPException) as ctx:
                require_authorized_user(
                    current_user=_USER,
                    settings=_settings(auth_enabled=True),
                )
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertEqual(
            ctx.exception.detail, "초대 코드 시스템이 준비되지 않았습니다."
        )


class RouterWiringTest(unittest.TestCase):
    """4 보호 라우터의 router-level dependency 가 require_authorized_user 인지 직접 검증.

    routes inspection — 실 요청 없이 wiring 만 확인. 향후 require_auth 로 회귀 시 즉시 fail.
    """

    def _find_router_deps(self, router) -> list:
        """APIRouter 의 router-level dependencies (=`router.dependencies`) 의 call 객체 목록."""
        return [d.dependency for d in router.dependencies]

    def test_documents_router_wires_require_authorized_user(self) -> None:
        from app.routers.documents import router

        deps = self._find_router_deps(router)
        self.assertIn(require_authorized_user, deps)

    def test_search_router_wires_require_authorized_user(self) -> None:
        from app.routers.search import router

        deps = self._find_router_deps(router)
        self.assertIn(require_authorized_user, deps)

    def test_answer_router_wires_require_authorized_user(self) -> None:
        from app.routers.answer import router

        deps = self._find_router_deps(router)
        self.assertIn(require_authorized_user, deps)

    def test_stats_router_wires_require_authorized_user(self) -> None:
        from app.routers.stats import router

        deps = self._find_router_deps(router)
        self.assertIn(require_authorized_user, deps)


if __name__ == "__main__":
    unittest.main()
