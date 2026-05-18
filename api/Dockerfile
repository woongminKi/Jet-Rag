# Jet-Rag API — Railway 배포용 Dockerfile (single-stage, uv sync)
#
# 설계 결정:
# - Python 3.12-slim base (pyproject.toml requires-python = ">=3.12").
# - uv 로 의존성 설치 + frozen lock 으로 재현성 보장.
# - system 패키지 추가 0 (pure-Python only — pyhwp / pymupdf / pillow 모두 wheel 제공).
# - pyproject.toml + uv.lock 만 먼저 COPY → uv sync 후 source COPY 로 layer 캐시 최적화
#   (소스만 변경된 일반 commit 의 build 는 의존성 layer 재사용, 30s → ~5s 수준).
# - PORT 는 Railway 가 주입 (default 8000 fallback — 로컬 docker run 호환).
# - PYTHONUNBUFFERED=1 — Railway log tail 에 즉시 노출.
# - .env 미포함 — Railway dashboard Variables 에서 OS 환경변수로 주입 (config.py 가 load_dotenv
#   를 거치지만 .env 미존재 시 graceful, os.environ 만 읽음).

FROM python:3.12-slim

# uv 만 시스템 단에 설치 (system pip 대신 uv 가 .venv 관리). 캐시 비활성으로 image 슬림.
RUN pip install --no-cache-dir uv==0.5.20

WORKDIR /app

# 의존성 layer 캐시 분리 — pyproject/lock 미변경 시 uv sync layer 재사용.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# source 복사 — .dockerignore 가 .venv / __pycache__ / tests / .env* 등 제외.
COPY . .

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Railway 가 $PORT 주입 — sh 로 변수 치환 후 uvicorn 기동. uv run 으로 .venv 활성.
CMD ["sh", "-c", "uv run uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
