from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.routers import documents_router, search_router, stats_router

app = FastAPI(
    title="Jet-Rag API",
    version="0.1.0",
    description="한국어 멀티포맷 RAG 개인 지식 에이전트 — 백엔드",
)

# 로컬 Next.js 개발 서버에서 직접 호출 허용. 운영 배포 시 도메인 화이트리스트로 교체할 것.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(documents_router)
app.include_router(search_router)
app.include_router(stats_router)


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
