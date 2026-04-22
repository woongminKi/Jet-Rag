from fastapi import FastAPI

app = FastAPI(
    title="Jet-Lag API",
    version="0.1.0",
    description="한국어 멀티포맷 RAG 개인 지식 에이전트 — 백엔드",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
