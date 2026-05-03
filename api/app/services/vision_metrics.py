"""W8 Day 4 — Vision API 호출 카운터 (한계 #29 회수).

배경
- W8 Day 2 PPTX Vision OCR rerouting 추가 후 Gemini Flash RPD 20 무료 티어 cap 모니터링 필요.
- W8 Day 2 실 reingest 시 tag_summarize 에서 429 RESOURCE_EXHAUSTED 발생 → quota 추적 가시성 부재.

설계 원칙
- search_metrics 패턴 재사용 — in-memory + threading.Lock + stdlib only
- 모든 Vision 경로 통일 — ImageParser.parse() 진입점 (image 인제스트, 스캔 PDF rerouting, PPTX rerouting)
- 외부 메트릭 시스템 도입 전 임시 대체 (Prometheus/OpenTelemetry 미도입)

알려진 한계
- 프로세스 재시작 시 휘발 (search_metrics 와 동일)
- multi-worker uvicorn 시 worker 별 카운트 (단일 사용자 MVP 단일 worker 전제)
- Gemini API 의 실 RPD 와는 별개 — 본 카운터는 *클라이언트 측* 호출 횟수
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

_lock = threading.Lock()
_total_calls: int = 0
_success_calls: int = 0
_error_calls: int = 0
_last_called_at: datetime | None = None


def record_call(*, success: bool) -> None:
    """Vision API 1회 호출 결과 기록 — ImageParser.parse() 가 호출."""
    global _total_calls, _success_calls, _error_calls, _last_called_at
    with _lock:
        _total_calls += 1
        if success:
            _success_calls += 1
        else:
            _error_calls += 1
        _last_called_at = datetime.now(timezone.utc)


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
        }


def reset() -> None:
    """테스트용 — 카운터 초기화."""
    global _total_calls, _success_calls, _error_calls, _last_called_at
    with _lock:
        _total_calls = 0
        _success_calls = 0
        _error_calls = 0
        _last_called_at = None
