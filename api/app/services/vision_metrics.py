"""W8 Day 4 — Vision API 호출 카운터 (한계 #29 회수).
W11 Day 1 — quota 시점 추적 추가 (한계 #38 lite).
W15 Day 3 — DB write-through (한계 #34 회수).

배경
- W8 Day 2 PPTX Vision OCR rerouting 후 Gemini Flash RPD 20 무료 티어 cap 모니터링.
- W11 Day 1 fast-fail 시점 capture (한계 #38 lite).
- W15 Day 3 — `vision_usage_log` 테이블 (마이그레이션 005) 에 row 1건씩 영구 저장
  → 프로세스 재시작 시 휘발성 회수.

설계 원칙
- search_metrics 패턴 재사용 — in-memory + threading.Lock + stdlib only
- 모든 Vision 경로 통일 — ImageParser.parse() 진입점
- last_quota_exhausted_at — quota 초과 시점만 따로 기록 (한계 #38 lite)
- DB write-through — Lock 해제 후 fire-and-forget. DB 실패는 log warning, 호출자 영향 0.
- DB 부재 (마이그레이션 005 미적용) 시 graceful — in-memory only 동작.
"""

from __future__ import annotations

import atexit
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# W15 Day 3 — DB write-through 활성 여부.
# default "1" — 운영은 활성. 단위 테스트에서 "0" 설정 시 skip → connection timeout 회피.
_PERSIST_ENV_KEY = "JET_RAG_METRICS_PERSIST_ENABLED"

# W17 Day 4 한계 #88 — DB write-through 비동기 실행 (검색 latency 보호).
# default "1" — 운영은 비동기 (호출자 영향 0). 단위 테스트 / first-warn capture 시 "0" 으로
# sync 강제 → background thread race 회피.
_PERSIST_ASYNC_ENV_KEY = "JET_RAG_METRICS_PERSIST_ASYNC"

# 모듈 레벨 ThreadPoolExecutor — max_workers=2 (운영 단일 사용자 부하).
# fire-and-forget — submit 후 future 무시 (graceful, 실패 시 _persist_to_db_sync 가 swallow).
_persist_executor: ThreadPoolExecutor | None = None
_persist_executor_lock = threading.Lock()


def _get_persist_executor() -> ThreadPoolExecutor:
    """lazy init — 첫 비동기 persist 시점에만 worker 생성.

    W18 Day 3 — atexit hook 등록: 운영 종료 시 cancel_futures + 실행 중 task wait.
    DB hang 회피 위해 cancel_futures=True (Python 3.9+).
    """
    global _persist_executor
    if _persist_executor is None:
        with _persist_executor_lock:
            if _persist_executor is None:
                _persist_executor = ThreadPoolExecutor(
                    max_workers=2, thread_name_prefix="vision-persist"
                )
                atexit.register(_shutdown_persist_executor)
    return _persist_executor


def _shutdown_persist_executor() -> None:
    """atexit hook — vision-persist 풀 graceful shutdown.

    cancel_futures=True: 미실행 task 취소 (DB hang 회피),
                        실행 중인 task 만 wait.
    호출 안전성: atexit 가 swallow 하므로 raise 무관, 단 logger 호출 가능.
    """
    global _persist_executor
    if _persist_executor is None:
        return
    try:
        _persist_executor.shutdown(wait=True, cancel_futures=True)
    except Exception as exc:  # noqa: BLE001 — atexit swallow
        logger.debug("vision-persist shutdown 중 예외: %s", exc)
    finally:
        _persist_executor = None

_lock = threading.Lock()
_total_calls: int = 0
_success_calls: int = 0
_error_calls: int = 0
_last_called_at: datetime | None = None
_last_quota_exhausted_at: datetime | None = None  # W11 Day 1 — 한계 #38 lite

# W16 Day 4 한계 #84 — DB row 크기 보호. env 로 override 가능.
_ERROR_MSG_MAX_LEN_ENV_KEY = "JET_RAG_VISION_ERROR_MSG_MAX_LEN"
_ERROR_MSG_MAX_LEN_DEFAULT = 200

# W17 Day 3 한계 #85 — DB write-through 첫 1회만 warn (이후는 debug 로그).
# 마이그레이션 005 미적용 운영 환경에서 한 번만 명시 알림 → 사용자 인지 + 로그 노이즈 방지.
# 모듈 레벨 단순 bool flag — race 발생해도 warn 이 두 번 찍히는 정도 (정합성 영향 0).
_first_persist_warn_logged: bool = False

# W16 Day 4 한계 #90 — source_type enum 강제. 잘못된 값은 None 으로 fallback.
# 005 schema 의 source_type 컬럼은 자유 TEXT 이나, DB row 의미 일관성 유지 위해 모듈 레벨 검증.
_VALID_SOURCE_TYPES: frozenset[str] = frozenset(
    {"image", "pdf_scan", "pptx_rerouting", "pptx_augment", "pdf_vision_enrich"}
)


def _error_msg_max_len() -> int:
    """env var 우선 — 잘못된 값 (음수 / 비숫자) 시 default 반환."""
    raw = os.environ.get(_ERROR_MSG_MAX_LEN_ENV_KEY)
    if raw is None:
        return _ERROR_MSG_MAX_LEN_DEFAULT
    try:
        n = int(raw)
        return n if n > 0 else _ERROR_MSG_MAX_LEN_DEFAULT
    except ValueError:
        return _ERROR_MSG_MAX_LEN_DEFAULT


def _normalize_source_type(value: str | None) -> str | None:
    """잘못된 source_type → None fallback + warn log (한계 #90 회수)."""
    if value is None:
        return None
    if value in _VALID_SOURCE_TYPES:
        return value
    logger.warning(
        "vision_metrics.record_call source_type=%r 무효 — None 으로 fallback. "
        "허용값: %s",
        value, sorted(_VALID_SOURCE_TYPES),
    )
    return None


def record_call(
    *,
    success: bool,
    quota_exhausted: bool = False,
    error_msg: str | None = None,
    source_type: str | None = None,
    # Phase 1 S0 D1 — 마이그 014 신규 컬럼. 모두 default None → 회귀 0.
    usage: dict | None = None,
    doc_id: str | None = None,
    page: int | None = None,
    retry_attempt: int | None = None,
) -> None:
    """Vision API 1회 호출 결과 기록 — ImageParser.parse() 가 호출.

    `quota_exhausted` (W11 Day 1 한계 #38 lite):
        True 시 last_quota_exhausted_at 갱신.

    `error_msg` / `source_type` (W15 Day 3 — DB write-through):
        - error_msg: success=False 시 Exception str.
          기본 200자 truncate, env JET_RAG_VISION_ERROR_MSG_MAX_LEN 으로 override (W16 Day 4 #84).
        - source_type (W16 Day 4 #90 — enum 강제):
          'image' / 'pdf_scan' / 'pptx_rerouting' / 'pptx_augment' / 'pdf_vision_enrich'.
          잘못된 값은 None 으로 fallback + warn log.
        - 둘 다 in-memory 카운터에는 영향 X, DB row 에만 보존.

    `usage` / `doc_id` / `page` / `retry_attempt` (Phase 1 S0 D1 — 마이그 014):
        - usage 는 dict — `prompt_tokens` / `image_tokens` / `output_tokens` /
          `thinking_tokens` / `estimated_cost` / `model_used` 키 추출.
        - dict 외 값 (None / 잘못된 타입) → 모든 토큰 컬럼 NULL 로 저장.
        - doc_id/page/retry_attempt 도 모두 optional — 기존 호출처 영향 0.
    """
    global _total_calls, _success_calls, _error_calls
    global _last_called_at, _last_quota_exhausted_at
    with _lock:
        _total_calls += 1
        now = datetime.now(timezone.utc)
        if success:
            _success_calls += 1
        else:
            _error_calls += 1
        _last_called_at = now
        if quota_exhausted:
            _last_quota_exhausted_at = now

    # W15 Day 3 — DB write-through (Lock 해제 후, graceful)
    truncate_len = _error_msg_max_len()
    _persist_to_db(
        called_at=now,
        success=success,
        error_msg=(error_msg or "")[:truncate_len] or None,
        quota_exhausted=quota_exhausted,
        source_type=_normalize_source_type(source_type),
        usage=usage if isinstance(usage, dict) else None,
        doc_id=doc_id,
        page=page,
        retry_attempt=retry_attempt,
    )


def _persist_to_db(
    *,
    called_at: datetime,
    success: bool,
    error_msg: str | None,
    quota_exhausted: bool,
    source_type: str | None,
    usage: dict | None = None,
    doc_id: str | None = None,
    page: int | None = None,
    retry_attempt: int | None = None,
) -> None:
    """vision_usage_log 테이블 insert (비동기 dispatch).

    JET_RAG_METRICS_PERSIST_ENABLED='0' 시 skip.
    JET_RAG_METRICS_PERSIST_ASYNC='1' (default) 시 ThreadPoolExecutor 로 fire-and-forget
    → 호출자 latency 영향 0 (W17 Day 4 한계 #88 회수).
    JET_RAG_METRICS_PERSIST_ASYNC='0' 시 sync 호출 (단위 테스트 first-warn capture).
    """
    if os.environ.get(_PERSIST_ENV_KEY, "1") == "0":
        return
    if os.environ.get(_PERSIST_ASYNC_ENV_KEY, "1") == "0":
        _persist_to_db_sync(
            called_at=called_at,
            success=success,
            error_msg=error_msg,
            quota_exhausted=quota_exhausted,
            source_type=source_type,
            usage=usage,
            doc_id=doc_id,
            page=page,
            retry_attempt=retry_attempt,
        )
        return
    _get_persist_executor().submit(
        _persist_to_db_sync,
        called_at=called_at,
        success=success,
        error_msg=error_msg,
        quota_exhausted=quota_exhausted,
        source_type=source_type,
        usage=usage,
        doc_id=doc_id,
        page=page,
        retry_attempt=retry_attempt,
    )


# Phase 1 S0 D1 — usage dict 에서 추출할 키 화이트리스트.
# 마이그 014 컬럼명과 1:1 매핑. 그 외 키는 무시 (forward-compat).
_USAGE_KEYS: tuple[str, ...] = (
    "prompt_tokens",
    "image_tokens",
    "output_tokens",
    "thinking_tokens",
    "estimated_cost",
    "model_used",
)


def _extract_usage_columns(usage: dict | None) -> dict[str, object]:
    """usage dict → 마이그 014 컬럼 매핑. 누락 키는 None.

    usage 자체가 None / dict 아니면 모든 컬럼을 None 으로 반환.
    """
    if not isinstance(usage, dict):
        return {key: None for key in _USAGE_KEYS}
    return {key: usage.get(key) for key in _USAGE_KEYS}


def _persist_to_db_sync(
    *,
    called_at: datetime,
    success: bool,
    error_msg: str | None,
    quota_exhausted: bool,
    source_type: str | None,
    usage: dict | None = None,
    doc_id: str | None = None,
    page: int | None = None,
    retry_attempt: int | None = None,
) -> None:
    """vision_usage_log 테이블 insert (sync). 실패는 log warning + swallow.

    마이그레이션 005 미적용 시 (테이블 부재) Supabase 가 PGRST 에러 → 본 함수 가 catch.
    그 후로도 호출은 이어서 시도 — 사용자가 005 적용하면 자연 회복.

    마이그레이션 014 미적용 시: usage/doc_id/page/retry_attempt 컬럼 부재로 insert 실패 →
    동일하게 graceful skip. 014 적용 후 자연 회복.

    background thread (`_persist_executor`) 또는 sync path (env=0) 에서 호출됨.
    """
    try:
        # lazy import — 단위 테스트가 supabase 의존성 없이도 동작 가능하도록
        from app.db import get_supabase_client

        client = get_supabase_client()
        row: dict[str, object] = {
            "called_at": called_at.isoformat(),
            "success": success,
            "error_msg": error_msg,
            "quota_exhausted": quota_exhausted,
            "source_type": source_type,
            "doc_id": doc_id,
            "page": page,
            "retry_attempt": retry_attempt,
        }
        row.update(_extract_usage_columns(usage))
        client.table("vision_usage_log").insert(row).execute()
    except Exception as exc:  # noqa: BLE001 — DB 부재 / 마이그레이션 미적용 graceful
        # W17 Day 3 #85 — 첫 1회만 warn, 이후는 debug (로그 노이즈 방지)
        global _first_persist_warn_logged
        if not _first_persist_warn_logged:
            _first_persist_warn_logged = True
            logger.warning(
                "vision_usage_log insert 첫 실패 — graceful skip 진입. "
                "마이그레이션 005 적용 후 재시작 시 자동 회복. (cause: %s)",
                exc,
            )
        else:
            logger.debug("vision_usage_log insert skip (graceful): %s", exc)


def get_usage() -> dict:
    """현재 누적 카운트 스냅샷. /stats 응답에서 사용."""
    with _lock:
        return {
            "total_calls": _total_calls,
            "success_calls": _success_calls,
            "error_calls": _error_calls,
            "last_called_at": (
                _last_called_at.isoformat() if _last_called_at else None
            ),
            "last_quota_exhausted_at": (
                _last_quota_exhausted_at.isoformat()
                if _last_quota_exhausted_at
                else None
            ),
        }


def reset() -> None:
    """테스트용 — 카운터 + first-warn flag 초기화."""
    global _total_calls, _success_calls, _error_calls
    global _last_called_at, _last_quota_exhausted_at
    global _first_persist_warn_logged
    with _lock:
        _total_calls = 0
        _success_calls = 0
        _error_calls = 0
        _last_called_at = None
        _last_quota_exhausted_at = None
        _first_persist_warn_logged = False
