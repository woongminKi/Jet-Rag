"""app/main.py 의 BGE-M3 cold-start warmup lifespan 단위 테스트.

검증 범위
- `_warmup_bgem3` — 토큰 없으면 provider 생성 없이 skip
- `_warmup_bgem3` — 토큰 있으면 `provider.embed_query("warmup")` 1회 호출
- `_warmup_bgem3` — embed_query 가 던져도 graceful (예외 전파 안 함)
- `_warmup_bgem3` — CancelledError 는 재전파 (shutdown task cancel 정상 흐름)
- lifespan — `TestClient(app)` 진입 시 warmup task 가 app.state 에 강참조로 보관

HF · Supabase 외부 호출 0 — provider 와 settings 를 mock.
실행: `python -m unittest tests.test_coldstart_warmup`
"""

from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import MagicMock, patch


class WarmupBgem3Test(unittest.IsolatedAsyncioTestCase):
    """`_warmup_bgem3` 코루틴 동작 — 토큰 분기 / graceful / cancel."""

    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")

    async def test_skips_when_no_hf_token(self) -> None:
        """HF_API_TOKEN 미설정 → provider 생성·HF 호출 없이 조용히 종료."""
        from app import main as main_module

        fake_settings = MagicMock()
        fake_settings.hf_api_token = ""
        provider_factory = MagicMock()

        with patch("app.config.get_settings", return_value=fake_settings), patch(
            "app.adapters.impl.bgem3_hf_embedding.get_bgem3_provider",
            provider_factory,
        ):
            await main_module._warmup_bgem3()

        provider_factory.assert_not_called()

    async def test_calls_embed_query_when_token_present(self) -> None:
        """토큰 있으면 `provider.embed_query("warmup")` 정확히 1회."""
        from app import main as main_module

        fake_settings = MagicMock()
        fake_settings.hf_api_token = "real-looking-token"
        provider_mock = MagicMock()

        with patch("app.config.get_settings", return_value=fake_settings), patch(
            "app.adapters.impl.bgem3_hf_embedding.get_bgem3_provider",
            return_value=provider_mock,
        ):
            await main_module._warmup_bgem3()

        provider_mock.embed_query.assert_called_once_with("warmup")

    async def test_swallows_embed_query_exception(self) -> None:
        """embed_query 가 던져도 warmup 은 예외 전파 안 함 (best-effort)."""
        from app import main as main_module

        fake_settings = MagicMock()
        fake_settings.hf_api_token = "real-looking-token"
        provider_mock = MagicMock()
        provider_mock.embed_query.side_effect = RuntimeError("HF down")

        with patch("app.config.get_settings", return_value=fake_settings), patch(
            "app.adapters.impl.bgem3_hf_embedding.get_bgem3_provider",
            return_value=provider_mock,
        ):
            # 예외가 새어 나오면 이 await 가 raise → 테스트 fail.
            await main_module._warmup_bgem3()

    async def test_swallows_provider_construction_error(self) -> None:
        """provider 생성자 RuntimeError(토큰 부재 등) 도 graceful."""
        from app import main as main_module

        fake_settings = MagicMock()
        fake_settings.hf_api_token = "real-looking-token"

        with patch("app.config.get_settings", return_value=fake_settings), patch(
            "app.adapters.impl.bgem3_hf_embedding.get_bgem3_provider",
            side_effect=RuntimeError("HF_API_TOKEN 이 설정되지 않았습니다."),
        ):
            await main_module._warmup_bgem3()

    async def test_reraises_cancelled_error(self) -> None:
        """asyncio.CancelledError 는 삼키지 않고 재전파 (shutdown task cancel 정상 경로)."""
        from app import main as main_module

        fake_settings = MagicMock()
        fake_settings.hf_api_token = "real-looking-token"
        provider_mock = MagicMock()
        provider_mock.embed_query.side_effect = asyncio.CancelledError()

        with patch("app.config.get_settings", return_value=fake_settings), patch(
            "app.adapters.impl.bgem3_hf_embedding.get_bgem3_provider",
            return_value=provider_mock,
        ):
            with self.assertRaises(asyncio.CancelledError):
                await main_module._warmup_bgem3()


class LifespanWarmupTaskTest(unittest.TestCase):
    """lifespan — TestClient 진입 시 warmup task 가 fire-and-forget 으로 등록되는지."""

    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")

    def test_warmup_task_attached_to_app_state(self) -> None:
        """`app.state.bgem3_warmup_task` 가 lifespan 동안 강참조로 보관됨.

        warmup 자체는 no-op 으로 patch — HF 호출 0. task 생성·보관만 검증.
        """
        from unittest.mock import AsyncMock

        from fastapi.testclient import TestClient

        from app import main as main_module
        from app.main import app

        with patch.object(
            main_module, "_warmup_bgem3", new=AsyncMock(return_value=None)
        ):
            with TestClient(app) as _client:
                task = getattr(app.state, "bgem3_warmup_task", None)
                self.assertIsNotNone(task, "lifespan 이 warmup task 를 생성하지 않음")
                self.assertIsInstance(task, asyncio.Task)


if __name__ == "__main__":
    unittest.main()
