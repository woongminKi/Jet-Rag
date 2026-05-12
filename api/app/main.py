"""Jet-Rag FastAPI 앱 진입점.

- router include + CORS.
- lifespan 에서 BGE-M3 임베딩 모델 cold-start warmup 을 fire-and-forget 으로 trigger
  (§10.11 SLO — 검색 첫 호출 시 HF cold start 5~20s 가 사용자에게 노출되는 것 회피).
  warmup 실패는 graceful — 앱 부팅을 막지 않는다 (토큰 미설정 환경·HF 장애 시에도 기동).
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.routers import (
    admin_router,
    answer_router,
    documents_router,
    search_router,
    stats_router,
)

logger = logging.getLogger(__name__)


async def _warmup_bgem3() -> None:
    """BGE-M3 dense 임베딩 1회 호출로 HF Inference 모델을 미리 깨운다.

    - lazy import — config / httpx / 어댑터 import 비용을 모듈 로드 시점에서 분리.
    - HF_API_TOKEN 미설정이면 조용히 skip (provider 생성자가 RuntimeError 를 던지므로
      catch 하고 종료 — CI·로컬 토큰 없는 환경에서 부팅 막지 않음).
    - 동기 HTTP 호출이라 `asyncio.to_thread` 로 이벤트 루프를 막지 않음.
    - 어떤 예외도 graceful — warmup 은 best-effort. 실패해도 첫 검색에서 정상 재시도.
    """
    try:
        from app.adapters.impl.bgem3_hf_embedding import get_bgem3_provider
        from app.config import get_settings

        if not get_settings().hf_api_token:
            logger.info("BGE-M3 warmup skip — HF_API_TOKEN 미설정")
            return

        provider = get_bgem3_provider()
        await asyncio.to_thread(provider.embed_query, "warmup")
        logger.info("BGE-M3 warmup 완료 — HF Inference 모델 준비됨")
    except asyncio.CancelledError:
        # shutdown 시 task cancel — 정상 흐름이므로 재전파.
        raise
    except Exception:  # noqa: BLE001 — warmup 은 best-effort, 부팅 차단 금지.
        logger.warning("BGE-M3 warmup 실패 (무시) — 첫 검색에서 재시도됨", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # fire-and-forget — warmup 완료를 기다리지 않고 곧바로 서비스 시작.
    # task 를 app.state 에 강참조로 보관 (보관 안 하면 GC 가 미완료 task 를 수거할 수 있음).
    app.state.bgem3_warmup_task = asyncio.create_task(_warmup_bgem3())
    try:
        yield
    finally:
        task: asyncio.Task[None] | None = getattr(app.state, "bgem3_warmup_task", None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass


app = FastAPI(
    title="Jet-Rag API",
    version="0.1.0",
    description="한국어 멀티포맷 RAG 개인 지식 에이전트 — 백엔드",
    lifespan=lifespan,
)

# 로컬 Next.js 개발 서버에서 직접 호출 허용. 운영 배포 시 도메인 화이트리스트로 교체할 것.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3001"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(documents_router)
app.include_router(search_router)
app.include_router(stats_router)
app.include_router(answer_router)
app.include_router(admin_router)


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
