"""W25 D14 Sprint C — postgrest httpx HTTP/1.1 강제 회귀 보호.

postgrest-py 가 http2=True 하드코딩이라 Supabase 게이트웨이 GOAWAY
error_code:9 (COMPRESSION_ERROR / HPACK 손상) 시 /stats, /documents 등에서
500 으로 노출되는 문제를 _force_postgrest_http1() 으로 우회.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch


class TestPostgrestSessionHttp1(unittest.TestCase):
    def setUp(self) -> None:
        from app.config import get_settings
        from app.db.client import get_supabase_client

        get_settings.cache_clear()
        get_supabase_client.cache_clear()

    @patch.dict(
        os.environ,
        {
            "SUPABASE_URL": "https://test.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoidGVzdCJ9.x",
        },
        clear=False,
    )
    def test_postgrest_session_uses_http1_only(self) -> None:
        from app.db.client import get_supabase_client

        client = get_supabase_client()
        session = client.postgrest.session

        # 패치 마커 — 라이브러리 업그레이드 후에도 1차 회귀 신호
        self.assertTrue(
            getattr(session, "_jetrag_http1_only", False),
            "_force_postgrest_http1() 가 새 httpx.Client 로 교체하지 않았음",
        )

        # httpx 0.28.x private path — 향후 깨지면 단위 테스트가 먼저 알림
        pool = session._transport._pool
        self.assertFalse(pool._http2, "postgrest 세션 http2 비활성 회귀")
        self.assertTrue(pool._http1, "postgrest 세션 http1 활성 회귀")

    @patch.dict(
        os.environ,
        {
            "SUPABASE_URL": "https://test.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoidGVzdCJ9.x",
        },
        clear=False,
    )
    def test_postgrest_session_preserves_base_url_and_headers(self) -> None:
        from app.db.client import get_supabase_client

        client = get_supabase_client()
        session = client.postgrest.session

        self.assertIn("test.supabase.co", str(session.base_url))
        # supabase-py 는 X-Client-Info 헤더를 postgrest 세션에 주입한다
        self.assertIn("x-client-info", {k.lower() for k in session.headers.keys()})


if __name__ == "__main__":
    unittest.main()
