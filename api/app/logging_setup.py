"""`app` 로거 트리에 콘솔 핸들러를 멱등하게 부착하는 부트스트랩.

배경 (왜 필요한가)
- uvicorn 기본 dictConfig 는 `disable_existing_loggers=False` 이지만 핸들러를 `uvicorn`
  / `uvicorn.error` / `uvicorn.access` 로거에만 부착한다. root 로거는 미설정이라
  `app.*` 의 INFO 로그가 어디로도 흘러가지 않고 드롭된다 (WARNING/ERROR 만
  `logging.lastResort` 로 stderr 노출).
- 그 결과 `app/main.py` 의 BGE-M3 warmup 성공 INFO 가 production 콘솔에서 보이지 않음.

설계 (MVP 수준 — over-engineering 금지)
- `app` 로거에만 `StreamHandler` 1개 + INFO + `propagate=False`. root 는 건드리지 않으므로
  httpx / asyncio / supabase 로그를 끌어오지 않는다.
- 멱등: 이미 `app` 로거에 핸들러가 있으면 (외부 `--log-config` 등) 완전 no-op —
  레벨·propagate 도 건드리지 않아 외부 설정을 존중한다.
- JSON 구조화 로깅 / 파일 회전 / 외부 수집(Sentry) / dictConfig 전체 스키마 / 로깅
  미들웨어 는 범위 밖.
"""

from __future__ import annotations

import logging

# uvicorn 의 `%(levelprefix)s %(message)s` 톤을 흉내내되 asctime 은 빼서 시각 충돌을
# 피한다 (uvicorn 도 reload 모드에서 timestamp 를 찍지 않음). 로거 이름은 포함해
# `app.main` / `app.ingest.pipeline` 등 출처를 식별 가능하게 한다.
_LOG_FORMAT = "%(levelname)s:     %(name)s - %(message)s"

_APP_LOGGER_NAME = "app"


def configure_app_logging(level: int = logging.INFO) -> None:
    """`app` 로거에 콘솔 핸들러 1개를 멱등하게 부착. 이미 핸들러 있으면 no-op.

    - `app/main.py` 모듈 상단(routers import 전)에서 1회 호출.
    - 핸들러가 이미 있으면 레벨·propagate 도 손대지 않는다 (외부 `--log-config` 존중).
    """
    app_logger = logging.getLogger(_APP_LOGGER_NAME)
    if app_logger.handlers:
        return

    handler = logging.StreamHandler()  # 기본 stream=stderr — uvicorn default 와 동일.
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    app_logger.addHandler(handler)
    app_logger.setLevel(level)
    # root 에 누가 핸들러를 붙였더라도 (`--log-config` 로 root 설정 등) 중복 출력 방지.
    app_logger.propagate = False
