"""W3 Day 2 Phase 3 D-1 보강 — 503 응답에 `Retry-After` 헤더가 라이브 부착되는지 검증.

배경 (qa 재검증 §D-1):
    `test_bgem3_singleton.py::SearchRouterFallbackBranchingTest::test_permanent_4xx_raises_503`
    가 이미 `HTTPException.status_code == 503` 과 `detail` 만 assert.
    그러나 라우터가 `headers={"Retry-After": "60"}` 를 실제로 전달하는지,
    그리고 FastAPI 가 그 headers 를 실 응답에 부착하는지는 검증 안 됨 — 회귀 시
    "사용자가 60초 후 자동 재시도" 가이드가 silently 사라짐.

본 파일은 두 레이어 모두 커버:
    1) 직접 호출 — `HTTPException.headers["Retry-After"] == "60"`
    2) TestClient 라이브 — 실 응답 `response.headers["retry-after"] == "60"`
    3) transient 5xx 케이스 — 응답 200, `Retry-After` 헤더 없음 (대조군)

HF API · Supabase 모두 monkeypatch — 외부 의존성 0.
실행: `python -m unittest tests.test_search_503_retry_after`
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

import httpx


def _make_status_error(status_code: int) -> httpx.HTTPStatusError:
    """주어진 status code 의 HTTPStatusError 픽스처."""
    request = httpx.Request("POST", "https://example.invalid/x")
    response = httpx.Response(status_code, request=request, content=b"{}")
    return httpx.HTTPStatusError(
        f"{status_code} test", request=request, response=response
    )


def _empty_supabase_client() -> MagicMock:
    """sparse-only fallback path 의 supabase chain 을 빈 결과로 mock.

    `client.table("chunks").select(...).filter(...).eq(...).is_(...).limit(...).execute()`
    체인이 `data=[]` 를 반환하도록 구성.
    """
    client = MagicMock()
    empty_resp = MagicMock()
    empty_resp.data = []
    chunks_chain = (
        client.table.return_value.select.return_value.filter.return_value
        .eq.return_value.is_.return_value.limit.return_value
    )
    chunks_chain.execute.return_value = empty_resp
    return client


class HttpExceptionHeadersUnitTest(unittest.TestCase):
    """레이어 1 — 라우터 함수 직접 호출 시 `HTTPException.headers` 검증."""

    @classmethod
    def setUpClass(cls) -> None:
        # BGEM3HFEmbeddingProvider.__init__ 가 HF_API_TOKEN 부재 시 RuntimeError.
        # provider 자체는 mock 으로 교체되지만 import 시점 안전을 위해 dummy 주입.
        os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")

    def setUp(self) -> None:
        from app.adapters.impl.bgem3_hf_embedding import get_bgem3_provider

        get_bgem3_provider.cache_clear()

    def test_permanent_4xx_raises_503_with_retry_after_header(self) -> None:
        """search.py L177 — `headers={"Retry-After": _RETRY_AFTER_SECONDS}` 가 실제 attach.

        회귀 차단: 누군가 headers 인자를 누락하거나 키 이름을 오타 내면 본 케이스 fail.
        """
        from fastapi import HTTPException

        from app.routers import search as search_module

        provider_mock = MagicMock()
        provider_mock.embed_query.side_effect = _make_status_error(401)
        client_mock = MagicMock()

        with patch.object(
            search_module, "get_bgem3_provider", return_value=provider_mock
        ), patch.object(
            search_module, "get_supabase_client", return_value=client_mock
        ):
            with self.assertRaises(HTTPException) as ctx:
                search_module.search(
                    q="테스트", limit=10, offset=0,
                    tags=None, doc_type=None, from_date=None, to_date=None,
                )

        # status / detail 은 기존 테스트와 중복이지만 self-contained 보장.
        self.assertEqual(ctx.exception.status_code, 503)
        # 본 파일의 핵심 assert — headers dict 자체와 Retry-After 키.
        self.assertIsNotNone(
            ctx.exception.headers,
            "503 응답에 headers 가 None — Retry-After 누락 회귀.",
        )
        self.assertIn(
            "Retry-After",
            ctx.exception.headers or {},
            "Retry-After 헤더 키 누락 — search.py L177 의 headers 인자 점검.",
        )
        self.assertEqual(
            (ctx.exception.headers or {}).get("Retry-After"),
            "60",
            "Retry-After 값이 60 이 아님 — _RETRY_AFTER_SECONDS 상수 변경 의심.",
        )

    def test_transient_5xx_response_has_no_retry_after(self) -> None:
        """대조군 — sparse-only fallback (200) 케이스는 Retry-After 부착되면 안 됨.

        transient 5xx 는 client 측 즉시 재시도 가이드가 부적절 (이미 sparse 결과 반환).
        Retry-After 가 노출되면 사용자가 200 응답을 보고도 60초 대기하는 UX 회귀.
        """
        from app.routers import search as search_module

        provider_mock = MagicMock()
        provider_mock.embed_query.side_effect = _make_status_error(503)
        client_mock = _empty_supabase_client()

        with patch.object(
            search_module, "get_bgem3_provider", return_value=provider_mock
        ), patch.object(
            search_module, "get_supabase_client", return_value=client_mock
        ):
            resp = search_module.search(
                q="테스트", limit=10, offset=0,
                tags=None, doc_type=None, from_date=None, to_date=None,
            )

        # 정상 200 응답 — SearchResponse 모델. fallback_reason 만 marker.
        self.assertEqual(resp.total, 0)
        self.assertFalse(resp.query_parsed.has_dense)
        self.assertEqual(resp.query_parsed.fallback_reason, "transient_5xx")
        # 본 모델은 headers 필드가 없으므로 헤더 검증은 라이브 테스트(L2) 로 분리.


class HttpExceptionHeadersLiveTest(unittest.TestCase):
    """레이어 2 — FastAPI TestClient 로 실제 HTTP 응답 헤더 부착 검증.

    `HTTPException(headers=...)` 이 LegacyException → ResponseHeaders 변환을 거치는데,
    이 변환이 회귀하면 단위 테스트(L1)는 통과하나 실 운영 응답에는 헤더 누락.
    Starlette 의 `_starlette_exception_handler` 동작에 의존 — 라이브 검증 필요.
    """

    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")

    def setUp(self) -> None:
        from app.adapters.impl.bgem3_hf_embedding import get_bgem3_provider

        get_bgem3_provider.cache_clear()

    def test_503_response_has_retry_after_header_via_testclient(self) -> None:
        """`/search?q=...` 호출 → 실제 response.headers 에 Retry-After: 60.

        HTTP 헤더는 case-insensitive — TestClient 도 lowercase 정규화 후 dict.
        `response.headers["retry-after"]` 또는 `["Retry-After"]` 모두 동일.
        """
        from fastapi.testclient import TestClient

        from app.main import app
        from app.routers import search as search_module

        provider_mock = MagicMock()
        provider_mock.embed_query.side_effect = _make_status_error(401)
        client_mock = MagicMock()

        with patch.object(
            search_module, "get_bgem3_provider", return_value=provider_mock
        ), patch.object(
            search_module, "get_supabase_client", return_value=client_mock
        ):
            with TestClient(app) as test_client:
                response = test_client.get("/search", params={"q": "테스트"})

        self.assertEqual(response.status_code, 503)
        # case-insensitive lookup — httpx Response.headers 는 대소문자 구분 안 함.
        self.assertEqual(
            response.headers.get("retry-after"),
            "60",
            "TestClient 응답에 Retry-After 헤더 누락 — Starlette 의 "
            "HTTPException → Response 변환에서 headers 유실 회귀.",
        )
        # detail JSON body 도 함께 확인 — 사용자가 받는 메시지가 한국어인지.
        body = response.json()
        self.assertIn("검색 일시 오류", body.get("detail", ""))

    def test_200_sparse_only_response_has_no_retry_after_via_testclient(
        self,
    ) -> None:
        """대조군 라이브 — transient 5xx fallback 200 응답에는 Retry-After 부재."""
        from fastapi.testclient import TestClient

        from app.main import app
        from app.routers import search as search_module

        provider_mock = MagicMock()
        provider_mock.embed_query.side_effect = _make_status_error(503)
        client_mock = _empty_supabase_client()

        with patch.object(
            search_module, "get_bgem3_provider", return_value=provider_mock
        ), patch.object(
            search_module, "get_supabase_client", return_value=client_mock
        ):
            with TestClient(app) as test_client:
                response = test_client.get("/search", params={"q": "테스트"})

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(
            "retry-after",
            {k.lower() for k in response.headers.keys()},
            "200 sparse-only fallback 응답에 Retry-After 부착됨 — "
            "503 분기에만 부착되어야 함 (UX 가이드 일관성).",
        )
        body = response.json()
        self.assertEqual(body["total"], 0)
        self.assertFalse(body["query_parsed"]["has_dense"])
        self.assertEqual(body["query_parsed"]["fallback_reason"], "transient_5xx")


if __name__ == "__main__":
    unittest.main()
