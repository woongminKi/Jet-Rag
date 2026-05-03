"""W9 — Gemini API quota 초과 감지 유틸리티.

배경
- W9 Day 4 PptxParser 가 Vision RESOURCE_EXHAUSTED 시 fast-fail 도입.
- W9 Day 6 tag_summarize 도 동일 패턴 적용 — 두 stage 가 같은 휴리스틱 공유.
- pptx_parser 에 두지 않고 별도 모듈로 분리 — 의존성 방향 정석화 (stage → util).
- W9 Day 7 (한계 #50) — class name + status code 직접 검사로 정확도↑.

설계 — stdlib only. google.api_core / google.genai SDK 직접 import 회피
(의존성 방향 + 패키지 변경 시 회귀 risk 회피) → type name 검사로 우회.
"""

from __future__ import annotations

# 알려진 quota 초과 exception class name 화이트리스트.
# - ResourceExhausted: google.api_core.exceptions (gRPC 표준)
# - ClientError: google.genai.errors (HTTP wrapper) — code 검사 필요
_QUOTA_EXCEPTION_NAMES: frozenset[str] = frozenset({
    "ResourceExhausted",
    "TooManyRequests",
})


def is_quota_exhausted(error_or_msg) -> bool:
    """quota 초과 케이스 감지 — Exception object 또는 str message 모두 수용.

    감지 우선순위 (W9 Day 7 — 한계 #50 회수)
    1. exception class name 화이트리스트 — google SDK 의 표준 type
    2. exception 의 status_code/code attribute == 429
    3. 메시지 휴리스틱 (RESOURCE_EXHAUSTED / 429 / QUOTA) — fallback

    1·2 는 SDK 응답 형식과 무관하게 정확. 3 은 문자열 변경 시 회귀 가능하나,
    1·2 가 false negative 일 때 안전망 역할.
    """
    if isinstance(error_or_msg, BaseException):
        # 1) class name 직접 검사 — import 없이도 google SDK 표준 type 인식
        if type(error_or_msg).__name__ in _QUOTA_EXCEPTION_NAMES:
            return True
        # 2) HTTP-style status code attribute (google.genai.errors.ClientError 등)
        for attr in ("status_code", "code"):
            value = getattr(error_or_msg, attr, None)
            if value == 429:
                return True
        # 3) fallback — 메시지 검사
        msg = str(error_or_msg)
    else:
        msg = error_or_msg

    if not msg:
        return False
    upper = msg.upper()
    return (
        "RESOURCE_EXHAUSTED" in upper
        or "429" in msg
        or "QUOTA" in upper
    )
