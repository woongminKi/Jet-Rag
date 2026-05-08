"""S3 D1 — intent_router 룰 기반 query 의도 분류 (planner v0.1 Part B).

목적
----
사용자 query 를 분석해 **decomposition (sub-query 분해) 필요 여부** 와
**신호별 trigger** 를 산출. D2 (`/search` `/answer` 통합) 와 D3 (외부 API
보조 라우터) 의 입력 계약 — 본 모듈은 **순수 룰 기반** 이며 외부 API 호출 0.

설계 원칙
---------
- 외부 API 호출 0 — 전부 정규식 + 키워드 매칭으로 결정 (latency 0ms 목표).
- 의존성 추가 0 — 표준 라이브러리만 사용 (`re` / `unicodedata` / `dataclasses`).
- 검색 path 변경 0 — 본 모듈은 `/search` `/answer` 에서 import 되지 않음 (D2 책임).
- 신호 명세는 planner v0.1 §3 표 그대로 — T1~T7 7 trigger.

7 Trigger
---------
| # | Trigger | 룰 |
|---|---|---|
| T1 | cross-doc | regex `(자료|문서|보고서).{0,15}(랑|와|과|및).{0,15}(자료|문서)` |
| T2 | 비교 | 키워드 OR — 차이 / 비교 / vs / 달라 / 대비 |
| T3 | 인과 | 키워드 OR — 왜 / 이유 / 때문 / 원인 / 어째서 (말미 ? 가산점) |
| T4 | 변경점 | 키워드 OR — 달라진 / 바뀐 / 변경 / 수정된 / 업데이트 |
| T5 | 긴 query | char ≥ 40 또는 token ≥ 12 |
| T6 | low confidence | 모호 표현 — 그거 / 그때 / 그 / 어디였더라 / 뭐였지 / 어떻게 됐더라 |
| T7 | 복수 대상 | T1 미발화 + count("랑") + count("과") ≥ 2 |

Decomposition 판정
-----------------
``needs_decomposition = (T1 or T2 or T3 or T7) or (T5 and T6)``

T4 / T5 / T6 단독은 분해 불필요 (T5+T6 만 결합 시 분해) — 의도 자체가
다중 sub-query 를 요구하지 않으므로.

Confidence score
----------------
``confidence_score = max(0.0, min(1.0, 1.0 - 0.15 * len(signals)))``
T6 발화 시 추가로 ``-0.3`` cap (모호 표현은 신뢰도 본질적으로 낮음).

회귀 영향
--------
- 외부 API 0, DB 0, 마이그 0.
- 의존성 추가 0, 검색 경로 import 0.
- D2 가 `/search` `/answer` 에서 import 시작하기 전까지 dead code (의도된 설계).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# T1 — cross-doc regex
# ---------------------------------------------------------------------------
_T1_CROSS_DOC = re.compile(
    r"(자료|문서|보고서).{0,15}(랑|와|과|및).{0,15}(자료|문서)"
)

# ---------------------------------------------------------------------------
# T2~T6 — 키워드 사전 (OR 매칭)
# ---------------------------------------------------------------------------
_T2_COMPARE_KEYWORDS: tuple[str, ...] = ("차이", "비교", "vs", "달라", "대비")
_T3_CAUSAL_KEYWORDS: tuple[str, ...] = ("왜", "이유", "때문", "원인", "어째서")
_T4_CHANGE_KEYWORDS: tuple[str, ...] = ("달라진", "바뀐", "변경", "수정된", "업데이트")
_T6_AMBIGUOUS_KEYWORDS: tuple[str, ...] = (
    "그거",
    "그때",
    "그 ",  # trailing space — "그 자료" 등 demonstrative 노이즈와 구분
    "어디였더라",
    "뭐였지",
    "어떻게 됐더라",
)

# ---------------------------------------------------------------------------
# T5 — 긴 query 임계
# ---------------------------------------------------------------------------
_T5_CHAR_THRESHOLD = 40
_T5_TOKEN_THRESHOLD = 12

# ---------------------------------------------------------------------------
# T7 — 복수 대상 (조사 발화 횟수)
# ---------------------------------------------------------------------------
_T7_PARTICLE_THRESHOLD = 2

# ---------------------------------------------------------------------------
# Confidence score 가중치
# ---------------------------------------------------------------------------
_CONFIDENCE_BASE = 1.0
_CONFIDENCE_PER_SIGNAL = 0.15
_CONFIDENCE_T6_PENALTY = 0.3

# Signal label — `triggered_signals` 튜플에 들어가는 표준 식별자.
_SIGNAL_T1 = "T1_cross_doc"
_SIGNAL_T2 = "T2_compare"
_SIGNAL_T3 = "T3_causal"
_SIGNAL_T4 = "T4_change"
_SIGNAL_T5 = "T5_long_query"
_SIGNAL_T6 = "T6_low_confidence"
_SIGNAL_T7 = "T7_multi_target"


@dataclass(frozen=True)
class IntentRouterDecision:
    """룰 기반 의도 분석 결과.

    Attributes
    ----------
    needs_decomposition:
        sub-query 분해 필요 여부. ``(T1 or T2 or T3 or T7) or (T5 and T6)``.
    triggered_signals:
        발화한 신호 식별자 튜플. 순서는 T1~T7. 빈 튜플이면 "단순 query".
    confidence_score:
        0.0~1.0. 높을수록 룰 분류 신뢰도 높음. T6 발화 시 `-0.3` cap.
    query_normalized:
        NFC 정규화 + 양 끝 공백 제거된 query.
    matched_keywords:
        T2/T3/T4/T6 에서 실제 매칭된 키워드 튜플 (디버그·로그용).
    """

    needs_decomposition: bool
    triggered_signals: tuple[str, ...]
    confidence_score: float
    query_normalized: str
    matched_keywords: tuple[str, ...]


def route(query: str) -> IntentRouterDecision:
    """query 를 7 trigger 룰로 분석해 `IntentRouterDecision` 반환.

    Parameters
    ----------
    query:
        사용자 입력 질의. NFC 정규화 + 공백 정규화 후 매칭.

    Raises
    ------
    ValueError:
        empty / whitespace only 입력 시.
    """
    if query is None or not query.strip():
        raise ValueError("query 는 비어있을 수 없습니다")

    normalized = _normalize(query)

    signals: list[str] = []
    matched: list[str] = []

    # T1 — cross-doc regex (T7 판정에서도 참조)
    t1_hit = _T1_CROSS_DOC.search(normalized) is not None
    if t1_hit:
        signals.append(_SIGNAL_T1)

    # T2 — 비교 키워드 OR
    t2_matches = _match_keywords(normalized, _T2_COMPARE_KEYWORDS)
    if t2_matches:
        signals.append(_SIGNAL_T2)
        matched.extend(t2_matches)

    # T3 — 인과 키워드 OR (말미 ? 는 신호 발화 자체에는 영향 없음, 가산점만 향후 활용)
    t3_matches = _match_keywords(normalized, _T3_CAUSAL_KEYWORDS)
    if t3_matches:
        signals.append(_SIGNAL_T3)
        matched.extend(t3_matches)

    # T4 — 변경점 키워드 OR
    t4_matches = _match_keywords(normalized, _T4_CHANGE_KEYWORDS)
    if t4_matches:
        signals.append(_SIGNAL_T4)
        matched.extend(t4_matches)

    # T5 — 긴 query
    if _is_long_query(normalized):
        signals.append(_SIGNAL_T5)

    # T6 — low confidence (모호 표현)
    t6_matches = _match_keywords(normalized, _T6_AMBIGUOUS_KEYWORDS)
    if t6_matches:
        signals.append(_SIGNAL_T6)
        matched.extend(t6_matches)

    # T7 — 복수 대상 (T1 미발화 + 조사 횟수 ≥ 2)
    if not t1_hit and _count_target_particles(normalized) >= _T7_PARTICLE_THRESHOLD:
        signals.append(_SIGNAL_T7)

    needs_decomp = _decide_decomposition(signals)
    confidence = _compute_confidence(signals)

    return IntentRouterDecision(
        needs_decomposition=needs_decomp,
        triggered_signals=tuple(signals),
        confidence_score=confidence,
        query_normalized=normalized,
        matched_keywords=tuple(matched),
    )


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------
def _normalize(query: str) -> str:
    """NFC 정규화 + 양 끝 공백 제거 + 내부 다중 공백 단일화.

    한글 호환 자모 (예: ``ᄀ``) → 완성형 (``가``) 일관 처리. 키워드 사전이
    완성형 기반이므로 NFC 가 안전.
    """
    nfc = unicodedata.normalize("NFC", query.strip())
    # 내부 연속 공백·탭·개행 → 단일 space (T6 의 "그 " 매칭 안정화)
    return re.sub(r"\s+", " ", nfc)


def _match_keywords(text: str, keywords: tuple[str, ...]) -> list[str]:
    """키워드 사전에서 hit 한 항목만 순서대로 반환 (중복 제거 X — 다신호 추적용)."""
    return [kw for kw in keywords if kw in text]


def _is_long_query(text: str) -> bool:
    """T5 — char ≥ 40 또는 whitespace token ≥ 12."""
    if len(text) >= _T5_CHAR_THRESHOLD:
        return True
    if len(text.split()) >= _T5_TOKEN_THRESHOLD:
        return True
    return False


def _count_target_particles(text: str) -> int:
    """T7 — '랑' '과' 발화 횟수 합계."""
    return text.count("랑") + text.count("과")


def _decide_decomposition(signals: list[str]) -> bool:
    """``(T1 or T2 or T3 or T7) or (T5 and T6)`` — 명세 §3."""
    fired = set(signals)
    primary = bool(
        fired & {_SIGNAL_T1, _SIGNAL_T2, _SIGNAL_T3, _SIGNAL_T7}
    )
    combined = _SIGNAL_T5 in fired and _SIGNAL_T6 in fired
    return primary or combined


def _compute_confidence(signals: list[str]) -> float:
    """``1.0 - 0.15 * len(signals)`` , T6 발화 시 추가 -0.3, [0.0, 1.0] cap."""
    score = _CONFIDENCE_BASE - _CONFIDENCE_PER_SIGNAL * len(signals)
    if _SIGNAL_T6 in signals:
        score -= _CONFIDENCE_T6_PENALTY
    return max(0.0, min(1.0, score))


__all__ = [
    "IntentRouterDecision",
    "route",
]
