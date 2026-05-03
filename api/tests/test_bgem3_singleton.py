"""BGEM3HFEmbeddingProvider 싱글톤 + transient 분류 헬퍼 단위 테스트.

8-7 fix 검증: `get_bgem3_provider()` 가 동일 인스턴스를 반환해
`httpx.Client` 누수가 발생하지 않는지 확인.

5-1 fix 검증: `is_transient_hf_error()` 가 4xx 영구 실패는 False,
5xx/네트워크 transient 는 True 로 분류하는지 확인.

stdlib `unittest` 만 사용 — 외부 의존성 0 (CLAUDE.md "의존성 추가 금지" 준수).
실행: `python -m unittest tests.test_bgem3_singleton`
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

import httpx


class GetBgem3ProviderSingletonTest(unittest.TestCase):
    """`get_bgem3_provider()` 가 lru_cache 로 동일 인스턴스를 반환해야 한다."""

    @classmethod
    def setUpClass(cls) -> None:
        # `BGEM3HFEmbeddingProvider.__init__` 가 HF_API_TOKEN 부재 시 RuntimeError.
        # 테스트 환경에 토큰이 없을 수 있으므로 dummy 주입.
        os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")

    def setUp(self) -> None:
        # lru_cache 격리 — 다른 테스트가 호출했을 수 있으니 초기화.
        from app.adapters.impl.bgem3_hf_embedding import get_bgem3_provider

        get_bgem3_provider.cache_clear()

    def test_returns_same_instance_on_repeated_calls(self) -> None:
        from app.adapters.impl.bgem3_hf_embedding import (
            BGEM3HFEmbeddingProvider,
            get_bgem3_provider,
        )

        first = get_bgem3_provider()
        second = get_bgem3_provider()

        self.assertIsInstance(first, BGEM3HFEmbeddingProvider)
        self.assertIs(first, second, "lru_cache 가 동일 인스턴스를 반환해야 한다")

    def test_cache_clear_yields_new_instance(self) -> None:
        """디버깅 목적 — 캐시 무효화 후엔 신규 인스턴스 (lru_cache 정상 동작 확인)."""
        from app.adapters.impl.bgem3_hf_embedding import get_bgem3_provider

        first = get_bgem3_provider()
        get_bgem3_provider.cache_clear()
        second = get_bgem3_provider()

        self.assertIsNot(first, second)


class IsTransientHfErrorTest(unittest.TestCase):
    """5-1 fix — 4xx 영구 실패는 fallback 진입을 차단해야 한다."""

    def _make_status_error(self, status_code: int) -> httpx.HTTPStatusError:
        """주어진 status code 의 HTTPStatusError 픽스처."""
        request = httpx.Request("POST", "https://example.invalid/x")
        response = httpx.Response(status_code, request=request, content=b"{}")
        return httpx.HTTPStatusError(
            f"{status_code} test", request=request, response=response
        )

    def test_4xx_auth_failure_is_not_transient(self) -> None:
        """401: HF 토큰 만료 → silent fallback 금지, 503 raise 되어야 한다."""
        from app.adapters.impl.bgem3_hf_embedding import is_transient_hf_error

        self.assertFalse(is_transient_hf_error(self._make_status_error(401)))

    def test_4xx_endpoint_not_found_is_not_transient(self) -> None:
        """404: HF endpoint 변경 → 즉시 노출되어야 한다."""
        from app.adapters.impl.bgem3_hf_embedding import is_transient_hf_error

        self.assertFalse(is_transient_hf_error(self._make_status_error(404)))

    def test_4xx_bad_request_is_not_transient(self) -> None:
        """400: 잘못된 request body → 코드 버그 가능성, 즉시 노출."""
        from app.adapters.impl.bgem3_hf_embedding import is_transient_hf_error

        self.assertFalse(is_transient_hf_error(self._make_status_error(400)))

    def test_429_rate_limit_is_transient(self) -> None:
        """429: rate limit → backoff 후 재시도 가치 있음."""
        from app.adapters.impl.bgem3_hf_embedding import is_transient_hf_error

        self.assertTrue(is_transient_hf_error(self._make_status_error(429)))

    def test_5xx_server_error_is_transient(self) -> None:
        """500/502/503/504: 서버 일시 문제 → fallback 허용."""
        from app.adapters.impl.bgem3_hf_embedding import is_transient_hf_error

        for code in (500, 502, 503, 504):
            with self.subTest(code=code):
                self.assertTrue(is_transient_hf_error(self._make_status_error(code)))

    def test_connect_error_is_transient(self) -> None:
        """네트워크 연결 실패 → transient (Day 3 smoke 의 ConnectionTerminated 유형)."""
        from app.adapters.impl.bgem3_hf_embedding import is_transient_hf_error

        self.assertTrue(is_transient_hf_error(httpx.ConnectError("dns fail")))
        self.assertTrue(is_transient_hf_error(httpx.ReadTimeout("slow")))
        self.assertTrue(is_transient_hf_error(httpx.RemoteProtocolError("term")))

    def test_runtime_error_is_not_transient(self) -> None:
        """응답 파싱 실패 (RuntimeError) 등 비-HTTP 오류는 transient 아님."""
        from app.adapters.impl.bgem3_hf_embedding import is_transient_hf_error

        self.assertFalse(is_transient_hf_error(RuntimeError("parse fail")))
        self.assertFalse(is_transient_hf_error(ValueError("bad value")))


class SearchRouterFallbackBranchingTest(unittest.TestCase):
    """search.py 의 fallback 분기 — transient 만 fallback, 4xx 는 503 raise."""

    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")

    def setUp(self) -> None:
        from app.adapters.impl.bgem3_hf_embedding import get_bgem3_provider

        get_bgem3_provider.cache_clear()

    def _make_status_error(self, status_code: int) -> httpx.HTTPStatusError:
        request = httpx.Request("POST", "https://example.invalid/x")
        response = httpx.Response(status_code, request=request, content=b"{}")
        return httpx.HTTPStatusError(
            f"{status_code}", request=request, response=response
        )

    def _call_search(self, search_fn, q: str = "테스트"):
        """`search()` 의 FastAPI `Query()` default 우회 — 모든 인자 명시 전달.

        라우터 함수를 직접 호출하면 `Query(...)` default 가 그대로 들어와
        `doc_type` 검증 등에서 오작동. 테스트는 의존성 주입 없이 호출하므로
        모든 optional 인자에 None / 기본값을 명시한다.
        """
        return search_fn(
            q=q,
            limit=10,
            offset=0,
            tags=None,
            doc_type=None,
            from_date=None,
            to_date=None,
            doc_id=None,
            mode="hybrid",
        )

    def test_permanent_4xx_raises_503(self) -> None:
        """HF 401 같은 영구 실패는 sparse-only fallback 대신 503 raise."""
        from fastapi import HTTPException

        from app.routers import search as search_module

        provider_mock = MagicMock()
        provider_mock.embed_query.side_effect = self._make_status_error(401)

        # supabase client 호출이 발생하면 안 됨 (raise 가 먼저).
        client_mock = MagicMock()

        with patch.object(
            search_module, "get_bgem3_provider", return_value=provider_mock
        ), patch.object(
            search_module, "get_supabase_client", return_value=client_mock
        ):
            with self.assertRaises(HTTPException) as ctx:
                self._call_search(search_module.search)

        self.assertEqual(ctx.exception.status_code, 503)
        self.assertIn("검색 일시 오류", ctx.exception.detail)
        # supabase RPC 호출이 일어나지 않았는지 — 영구 실패 분기는 즉시 raise.
        client_mock.rpc.assert_not_called()

    def test_transient_5xx_falls_back_to_sparse_only(self) -> None:
        """HF 503 cold start 같은 transient 는 sparse-only fallback 진입.

        마이그레이션 004 (PGroonga) 적용 후 fallback 은 RPC `search_sparse_only_pgroonga`
        를 호출 — 옛 PostgREST `client.table("chunks")` 체인은 사용하지 않는다.
        """
        from app.routers import search as search_module

        provider_mock = MagicMock()
        provider_mock.embed_query.side_effect = self._make_status_error(503)

        # sparse-only fallback 은 client.rpc("search_sparse_only_pgroonga", ...) 호출.
        # 빈 결과로 단순화 — 분기만 검증.
        client_mock = MagicMock()
        empty_resp = MagicMock()
        empty_resp.data = []
        client_mock.rpc.return_value.execute.return_value = empty_resp

        with patch.object(
            search_module, "get_bgem3_provider", return_value=provider_mock
        ), patch.object(
            search_module, "get_supabase_client", return_value=client_mock
        ):
            resp = self._call_search(search_module.search)

        # fallback 진입 → sparse 결과 0건이라 total=0 응답.
        self.assertEqual(resp.total, 0)
        self.assertFalse(resp.query_parsed.has_dense)
        # 새 RPC 가 호출됐는지 — sparse fallback 의 PGroonga RPC 경로 검증.
        called_rpc_names = [
            call.args[0] for call in client_mock.rpc.call_args_list
        ]
        self.assertIn(
            "search_sparse_only_pgroonga",
            called_rpc_names,
            f"PGroonga sparse RPC 가 호출되지 않음: {called_rpc_names}",
        )


if __name__ == "__main__":
    unittest.main()
