"""D1 — 초대 코드 redeem 단위 테스트 (plan §5·§7).

대상: `app.routers.auth.redeem_invite` 검증 + 소진 (조건부 UPDATE race 방어).

검증 (redeem_invite 직접 호출 — Supabase mock, 외부 의존성 0):
- 유효 미사용 코드 → redeemed=True
- 미존재 코드 → 404
- 이미 사용된 코드(used_by 있음) → 409
- 만료된 코드 → 410
- 동시 소진(SELECT 통과 후 UPDATE 0건) → 409
- 빈 코드 → 400
- invite_codes 미존재(SELECT 예외) → 503

`get_supabase_client` 를 MagicMock 으로 교체. SELECT/UPDATE 체인을 케이스별 구성.
실행: `python -m unittest tests.test_invite_codes`
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from app.auth import CurrentUser
from app.config import Settings
from app.routers.auth import RedeemInviteRequest, auth_me, redeem_invite

_USER = CurrentUser(user_id="44444444-4444-4444-4444-444444444444")


def _settings(*, auth_enabled: bool) -> Settings:
    """auth_me 가 읽는 auth_enabled 만 의미 있는 최소 Settings."""
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


def _client_for(*, select_rows, update_rows=None, select_raises=False):
    """invite_codes SELECT / UPDATE 체인을 반환값으로 구성한 mock client.

    - select_rows: 검증 SELECT 의 .execute().data
    - update_rows: 소진 UPDATE 의 .execute().data (None = 호출 안 됨 케이스)
    - select_raises: SELECT 가 예외 (마이그 017 미적용 시뮬레이션)
    """
    client = MagicMock()
    table = client.table.return_value

    # SELECT 체인: .select(...).eq(...).limit(...).execute()
    select_chain = (
        table.select.return_value.eq.return_value.limit.return_value
    )
    if select_raises:
        select_chain.execute.side_effect = RuntimeError("relation invite_codes 없음")
    else:
        select_chain.execute.return_value = _resp(select_rows)

    # UPDATE 체인: .update(...).eq(...).is_(...).execute()
    update_chain = (
        table.update.return_value.eq.return_value.is_.return_value
    )
    update_chain.execute.return_value = _resp(update_rows or [])

    return client


class RedeemInviteTest(unittest.TestCase):
    def _call(self, *, code="GOOD-CODE", **kwargs):
        client = _client_for(**kwargs)
        with patch("app.routers.auth.get_supabase_client", return_value=client):
            return redeem_invite(
                RedeemInviteRequest(code=code), current_user=_USER
            ), client

    def test_valid_unused_code_redeems(self) -> None:
        resp, client = self._call(
            select_rows=[{"code": "GOOD-CODE", "used_by": None, "expires_at": None}],
            update_rows=[{"code": "GOOD-CODE", "used_by": _USER.user_id}],
        )
        self.assertTrue(resp.redeemed)
        self.assertEqual(resp.code, "GOOD-CODE")
        # 소진 UPDATE 가 호출자 user_id 로 기록되는지.
        client.table.return_value.update.assert_called_once()
        update_payload = client.table.return_value.update.call_args[0][0]
        self.assertEqual(update_payload["used_by"], _USER.user_id)

    def test_nonexistent_code_404(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            self._call(select_rows=[])
        self.assertEqual(ctx.exception.status_code, 404)

    def test_already_used_code_409(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            self._call(
                select_rows=[
                    {"code": "GOOD-CODE", "used_by": "someone", "expires_at": None}
                ]
            )
        self.assertEqual(ctx.exception.status_code, 409)

    def test_expired_code_410(self) -> None:
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        with self.assertRaises(HTTPException) as ctx:
            self._call(
                select_rows=[
                    {"code": "GOOD-CODE", "used_by": None, "expires_at": past}
                ]
            )
        self.assertEqual(ctx.exception.status_code, 410)

    def test_future_expiry_still_valid(self) -> None:
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        resp, _ = self._call(
            select_rows=[
                {"code": "GOOD-CODE", "used_by": None, "expires_at": future}
            ],
            update_rows=[{"code": "GOOD-CODE"}],
        )
        self.assertTrue(resp.redeemed)

    def test_concurrent_redeem_loses_race_409(self) -> None:
        # SELECT 는 미사용으로 보였지만 UPDATE 가 0건 = 그 사이 다른 가입자가 소진.
        with self.assertRaises(HTTPException) as ctx:
            self._call(
                select_rows=[
                    {"code": "GOOD-CODE", "used_by": None, "expires_at": None}
                ],
                update_rows=[],
            )
        self.assertEqual(ctx.exception.status_code, 409)

    def test_empty_code_400(self) -> None:
        client = MagicMock()
        with patch("app.routers.auth.get_supabase_client", return_value=client):
            with self.assertRaises(HTTPException) as ctx:
                redeem_invite(RedeemInviteRequest(code="   "), current_user=_USER)
        self.assertEqual(ctx.exception.status_code, 400)
        # 빈 코드는 DB 조회 전 차단 — table 미호출.
        client.table.assert_not_called()

    def test_migration_pending_503(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            self._call(select_rows=None, select_raises=True)
        self.assertEqual(ctx.exception.status_code, 503)


class AuthMeTest(unittest.TestCase):
    """GET /auth/me — OAuth 복귀 유저 게이트 (plan §1.1)."""

    def test_auth_disabled_always_authorized(self) -> None:
        # auth_enabled=false 면 DB 조회 없이 authorized=true.
        client = MagicMock()
        with patch("app.routers.auth.get_supabase_client", return_value=client):
            resp = auth_me(current_user=_USER, settings=_settings(auth_enabled=False))
        self.assertTrue(resp.authorized)
        self.assertEqual(resp.user_id, _USER.user_id)
        client.table.assert_not_called()

    def test_redeemed_user_is_authorized(self) -> None:
        # invite_codes 에 used_by=호출자 행 존재 → authorized=true (복귀 유저).
        client = MagicMock()
        chain = client.table.return_value.select.return_value.eq.return_value.limit.return_value
        chain.execute.return_value = _resp([{"code": "USED-CODE"}])
        with patch("app.routers.auth.get_supabase_client", return_value=client):
            resp = auth_me(current_user=_USER, settings=_settings(auth_enabled=True))
        self.assertTrue(resp.authorized)

    def test_no_invite_user_not_authorized(self) -> None:
        # 코드 소진 이력 없음 → authorized=false (코드 미보유 신규).
        client = MagicMock()
        chain = client.table.return_value.select.return_value.eq.return_value.limit.return_value
        chain.execute.return_value = _resp([])
        with patch("app.routers.auth.get_supabase_client", return_value=client):
            resp = auth_me(current_user=_USER, settings=_settings(auth_enabled=True))
        self.assertFalse(resp.authorized)

    def test_db_error_not_authorized(self) -> None:
        # 조회 실패(마이그 017 미적용/DB 장애)는 차단 우선 → authorized=false.
        client = MagicMock()
        chain = client.table.return_value.select.return_value.eq.return_value.limit.return_value
        chain.execute.side_effect = RuntimeError("relation invite_codes 없음")
        with patch("app.routers.auth.get_supabase_client", return_value=client):
            resp = auth_me(current_user=_USER, settings=_settings(auth_enabled=True))
        self.assertFalse(resp.authorized)


if __name__ == "__main__":
    unittest.main()
