"""Jet-Rag FastAPI 앱 진입점.

- 모듈 로드 시 `app` 로거 콘솔 핸들러 부트스트랩 (routers import 전 — import 중 발생하는
  로그도 잡히게). uvicorn 기본 dictConfig 가 `app.*` INFO 를 드롭하는 문제 회피.
- router include + CORS.
- lifespan 에서 BGE-M3 임베딩 모델 cold-start warmup 을 fire-and-forget 으로 trigger
  (§10.11 SLO — 검색 첫 호출 시 HF cold start 5~20s 가 사용자에게 노출되는 것 회피).
  warmup 실패는 graceful — 앱 부팅을 막지 않는다 (토큰 미설정 환경·HF 장애 시에도 기동).
- lifespan 에서 `ingest_jobs` 고아 running job sweep 도 fire-and-forget 1회 (M0-a W-14).
  프로세스 비정상 종료로 남은 stale `running` job 의 status 만 failed 마킹 (chunks 미정리).
  Supabase 미설정·DB 장애 시 graceful — 부팅 차단 금지.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.logging_setup import configure_app_logging

configure_app_logging()

from app.routers import (  # noqa: E402 — logger 부트스트랩 후 import (import 중 로그 캡처).
    admin_router,
    answer_router,
    auth_router,
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

        logger.info("BGE-M3 warmup 시작 — HF cold-start 대기 (수 초~수십 초 소요 가능)")
        provider = get_bgem3_provider()
        await asyncio.to_thread(provider.embed_query, "warmup")
        logger.info("BGE-M3 warmup 완료 — HF Inference 모델 준비됨")
    except asyncio.CancelledError:
        # shutdown 시 task cancel — 정상 흐름이므로 재전파.
        raise
    except Exception:  # noqa: BLE001 — warmup 은 best-effort, 부팅 차단 금지.
        logger.warning("BGE-M3 warmup 실패 (무시) — 첫 검색에서 재시도됨", exc_info=True)


async def _sweep_stale_ingest_jobs() -> None:
    """기동 시 1회 — 프로세스 비정상 종료로 남은 `ingest_jobs` 고아 running job 정리 (M0-a W-14).

    - lazy import — config / supabase / 서비스 import 비용을 모듈 로드 시점에서 분리.
    - Supabase 미설정(`supabase_url` 빈값)이면 조용히 skip — CI·로컬 자격 없는 환경 부팅 보장.
    - 동기 HTTP 호출이라 `asyncio.to_thread` 로 이벤트 루프를 막지 않음.
    - 어떤 예외도 graceful — sweep 은 best-effort. 실패해도 다음 기동 / 수동 CLI 로 재시도.
    - chunks 는 절대 안 건드림 (status 만 마킹) — sample-report 2026-05-12 사고 교훈.
    """
    try:
        from app.config import get_settings
        from app.services.ingest_job_watchdog import sweep_stale_ingest_jobs

        settings = get_settings()
        if not settings.supabase_url:
            logger.info("ingest_jobs watchdog skip — SUPABASE_URL 미설정")
            return

        result = await asyncio.to_thread(
            sweep_stale_ingest_jobs,
            threshold_hours=settings.stale_ingest_job_hours,
            apply=True,
        )
        if result.scanned:
            logger.warning(
                "ingest_jobs watchdog — 고아 running %d건 발견, %d건 failed 마킹: %s",
                result.scanned,
                result.marked_failed,
                result.stale_job_ids,
            )
        else:
            logger.info("ingest_jobs watchdog — 고아 running 0건")
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — sweep 은 best-effort, 부팅 차단 금지.
        logger.warning("ingest_jobs watchdog 실패 (무시) — 다음 기동/수동 CLI 로 재시도", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # fire-and-forget — warmup / sweep 완료를 기다리지 않고 곧바로 서비스 시작.
    # task 를 app.state 에 강참조로 보관 (보관 안 하면 GC 가 미완료 task 를 수거할 수 있음).
    app.state.bgem3_warmup_task = asyncio.create_task(_warmup_bgem3())
    app.state.stale_job_sweep_task = asyncio.create_task(_sweep_stale_ingest_jobs())
    try:
        yield
    finally:
        for attr in ("bgem3_warmup_task", "stale_job_sweep_task"):
            task: asyncio.Task[None] | None = getattr(app.state, attr, None)
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

# CORS — env `JETRAG_CORS_ORIGINS` 콤마 분리. 미설정 시 localhost 개발 서버만.
# Vercel preview 도메인(`*.vercel.app`)은 regex 로 허용 — origin 마다 새로 발급되는
# preview URL 을 매번 env 에 추가하지 않아도 된다. 운영 도메인 확정 시 env 로 정식
# whitelist 교체.
# D1 Phase B (plan §1.1) — 아키텍처 B(httpOnly 쿠키) 채택으로 `allow_credentials=True`.
# 이때 와일드카드 `*` origin 은 CORS 스펙상 금지(credentials 비호환)이므로 명시 origin 만 사용.
# `allow_origin_regex` 는 명시 패턴(`*.vercel.app`)이라 credentials 와 호환됨.
_DEFAULT_CORS_ORIGINS = "http://localhost:3001,http://localhost:3000"
_cors_origins = [
    origin.strip()
    for origin in os.environ.get("JETRAG_CORS_ORIGINS", _DEFAULT_CORS_ORIGINS).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(documents_router)
app.include_router(search_router)
app.include_router(stats_router)
app.include_router(answer_router)
app.include_router(admin_router)
# D1 — 가입 게이트 (POST /auth/redeem-invite). get_current_user dependency 내장이라
# router-level require_auth 불필요 (auth_enabled=true 시 토큰 없으면 401).
app.include_router(auth_router)


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
