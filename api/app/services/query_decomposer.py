"""S3 D3 — gated paid query decomposition (planner v0.1 §C).

목적
----
intent_router (D1) 가 ``needs_decomposition=True`` 로 판정한 query 를
**유료 LLM (Gemini 2.5 Flash-Lite)** 으로 2~5개 sub-query 로 분해.
``/answer`` 가 원본 query top_k=20 + sub-query 별 top_k=10 → RRF merge 로
recall 향상 — Phase 1 S3 의 핵심 정확도 손잡이.

설계 원칙
---------
- **default OFF** — `JETRAG_PAID_DECOMPOSITION_ENABLED=false` 일 때 LLM 호출 0.
  ENV ON 명시 + intent_router needs_decomposition=True 둘 다 만족해야 호출.
- **budget guard 통합** — `vision_usage_log` 재사용 (마이그 0). 분해 호출도
  `model_used`/`source_type` 컬럼으로 분리 집계되도록 결정. monthly cap 초과
  시 호출 skip + reason 마킹.
- **LRU cache 200건** — 같은 (query, signals) 재호출 시 cost=0.0 / cached=True.
  운영 토글 `JETRAG_DECOMPOSITION_CACHE_DISABLE=1` (디버깅용).
- **graceful** — JSON 파싱 실패 / LLM raise / budget cap 초과 모두 빈 tuple
  반환 + 한국어 reason. 호출자는 빈 tuple 을 "분해 skip → 단일 query 검색"
  으로 처리 (회귀 0).
- **의존성 추가 0** — 기존 google-genai SDK + factory 재사용.
- **마이그 0** — vision_usage_log 재사용 (사용자 결정 Q-S3-D3-1).

ENV 토글 (.env.example 참조)
----------------------------
| ENV | default | 의미 |
|---|---|---|
| `JETRAG_PAID_DECOMPOSITION_ENABLED` | `false` | 분해 호출 활성 (default OFF) |
| `JETRAG_DECOMPOSITION_MONTHLY_CAP_USD` | `0.30` | 월간 분해 비용 cap (USD) |
| `JETRAG_DECOMPOSITION_CACHE_DISABLE` | `0` | LRU cache 비활성 (디버깅) |

호출 흐름
---------
1. `decision.needs_decomposition=False` → 즉시 skip (LLM 호출 0)
2. ENV `JETRAG_PAID_DECOMPOSITION_ENABLED!=true` → skip (LLM 호출 0)
3. LRU cache hit → cost=0.0 / cached=True 반환 (LLM 호출 0)
4. budget cap 초과 → skip + reason (LLM 호출 0)
5. LLM 호출 → JSON parse → 2~5 sub-query tuple 반환

회귀 영향
--------
- 외부 API 0 (default OFF). ENV ON 시점에만 실 호출.
- DB 0 (vision_usage_log 재사용, 신규 컬럼/테이블 0).
- 검색 path 변경 0 (호출자가 빈 tuple 분기 처리).
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional

from app.adapters.factory import get_gemini_pricing, get_llm_provider
from app.adapters.llm import ChatMessage, LLMProvider
from app.services import budget_guard
from app.services.intent_router import IntentRouterDecision

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ENV 키 + default
# ---------------------------------------------------------------------------
_ENV_ENABLED = "JETRAG_PAID_DECOMPOSITION_ENABLED"
_ENV_MONTHLY_CAP = "JETRAG_DECOMPOSITION_MONTHLY_CAP_USD"
_ENV_CACHE_DISABLE = "JETRAG_DECOMPOSITION_CACHE_DISABLE"

_MONTHLY_CAP_DEFAULT_USD = 0.30

# ---------------------------------------------------------------------------
# LLM prompt 상수
# ---------------------------------------------------------------------------
# decomposition purpose → factory 가 gemini-2.5-flash-lite 로 매핑 (factory.py:69).
_LLM_PURPOSE = "decomposition"
_LLM_TEMPERATURE = 0.1
# 짧은 JSON 출력만 — 토큰 폭주 방지. 5 sub-query × 한국어 ~30자 ≈ 150 token 충분.
_LLM_MAX_OUTPUT_TOKENS = 200

# Sub-query 개수 cap — 너무 많으면 검색 latency 폭주, 너무 적으면 분해 의미 없음.
_MIN_SUBQUERIES = 2
_MAX_SUBQUERIES = 5

# vision_usage_log.source_type 식별자 — budget_guard 가 SUM 시 분리 집계.
_USAGE_LOG_SOURCE_TYPE = "query_decomposition"

# ---------------------------------------------------------------------------
# LRU cache — 200건 (planner v0.1 §C). OrderedDict move_to_end + popitem(last=False).
# ---------------------------------------------------------------------------
_CACHE_MAX_SIZE = 200
_cache: "OrderedDict[tuple[str, tuple[str, ...]], tuple[str, ...]]" = OrderedDict()

# ---------------------------------------------------------------------------
# Few-shot prompt — planner default 2건 (cross_doc 1 + 인과 1).
# 사용자 결정 Q-S3-D3-3.
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = (
    "당신은 한국어 RAG 검색을 돕는 query decomposer 입니다. "
    "사용자의 질문을 검색에 적합한 2~5개의 짧은 sub-query 로 분해하세요. "
    "다음 규칙을 반드시 지키세요:\n"
    "1. 출력은 JSON array (문자열 리스트) 만. 다른 텍스트나 설명 금지.\n"
    "2. sub-query 는 각 30자 이내, 검색 키워드 중심.\n"
    "3. 원본 질문의 의도를 보존 — 새 정보를 추가하지 마세요.\n"
    "\n"
    "예시 1 (cross-doc 비교):\n"
    "질문: 작년 보고서랑 올해 자료를 비교해줘\n"
    'JSON: ["작년 보고서 내용", "올해 자료 내용", "보고서 비교 차이점"]\n'
    "\n"
    "예시 2 (인과):\n"
    "질문: 매출이 떨어진 이유가 뭐야\n"
    'JSON: ["매출 감소 원인", "매출 하락 시점", "매출 감소 영향 요인"]'
)


@dataclass(frozen=True)
class QueryDecomposition:
    """분해 결과 — 호출자 (`/answer`) 가 즉시 분기 가능한 형태.

    Attributes
    ----------
    subqueries:
        분해된 sub-query 튜플. 빈 tuple 이면 "분해 skip" — 호출자는 원본 query
        만으로 검색 진행. 길이는 [_MIN_SUBQUERIES, _MAX_SUBQUERIES].
    cost_usd:
        이번 호출의 추정 비용 (USD). cache hit / skip 시 0.0.
    cached:
        LRU cache hit 여부. True 면 LLM 호출 0회.
    skipped_reason:
        skip 사유 (한국어). subqueries 가 비어있을 때만 채워짐. 정상 분해
        성공 시 None.
    """

    subqueries: tuple[str, ...]
    cost_usd: float
    cached: bool
    skipped_reason: Optional[str]


def decompose(
    query: str,
    decision: IntentRouterDecision,
    *,
    llm: LLMProvider | None = None,
) -> QueryDecomposition:
    """query 분해 — gated by ENV + intent_router + budget cap + LRU cache.

    Parameters
    ----------
    query:
        원본 query (NFC normalized). 일반적으로 `decision.query_normalized`
        와 동일하나 호출자가 별도 정규화한 경우를 위해 별도 인자.
    decision:
        intent_router.route() 결과. `needs_decomposition=False` 면 즉시 skip.
    llm:
        테스트 용 주입점. None 이면 factory 가 결정.

    Returns
    -------
    QueryDecomposition — `subqueries` 빈 tuple 이면 호출자는 단일 query 검색.
    """
    # 1) intent_router 가 분해 불필요로 판정 → skip
    if not decision.needs_decomposition:
        return QueryDecomposition(
            subqueries=(),
            cost_usd=0.0,
            cached=False,
            skipped_reason="intent_router 가 분해 불필요로 판정",
        )

    # 2) ENV 토글 OFF → skip (LLM 호출 0)
    if not _is_enabled():
        return QueryDecomposition(
            subqueries=(),
            cost_usd=0.0,
            cached=False,
            skipped_reason="유료 분해 비활성 (ENV)",
        )

    # 3) LRU cache hit → 즉시 반환 (LLM 호출 0)
    cache_key = _build_cache_key(query, decision.triggered_signals)
    if not _is_cache_disabled():
        cached_subs = _cache_get(cache_key)
        if cached_subs is not None:
            return QueryDecomposition(
                subqueries=cached_subs,
                cost_usd=0.0,
                cached=True,
                skipped_reason=None,
            )

    # 4) budget cap 초과 → skip + reason
    monthly_cap = _resolve_monthly_cap_usd()
    budget_status = check_decomposition_budget(monthly_cap_usd=monthly_cap)
    if not budget_status.allowed:
        return QueryDecomposition(
            subqueries=(),
            cost_usd=0.0,
            cached=False,
            skipped_reason=budget_status.reason,
        )

    # 5) LLM 호출 → JSON parse
    if llm is None:
        llm = get_llm_provider(_LLM_PURPOSE)

    messages = _build_messages(query)
    try:
        raw = llm.complete(
            messages,
            temperature=_LLM_TEMPERATURE,
        )
    except Exception as exc:  # noqa: BLE001 — graceful (회귀 0)
        logger.warning("query_decomposer LLM 호출 실패 — skip: %s", exc)
        return QueryDecomposition(
            subqueries=(),
            cost_usd=0.0,
            cached=False,
            skipped_reason=f"LLM 호출 실패: {exc}",
        )

    parsed = _parse_subqueries(raw)
    if not parsed:
        logger.warning(
            "query_decomposer JSON 파싱 실패 — skip. raw=%r", raw[:200]
        )
        return QueryDecomposition(
            subqueries=(),
            cost_usd=0.0,
            cached=False,
            skipped_reason="LLM 응답 JSON 파싱 실패",
        )

    # 비용 추정 — token 정확도 부재 시 prompt 길이 기반 보수적 산정.
    cost = _estimate_cost_usd(prompt_chars=_prompt_char_count(messages), output_chars=len(raw))

    # cache 저장 (성공 시만 — 실패 결과는 재시도 여지 보존)
    if not _is_cache_disabled():
        _cache_put(cache_key, parsed)

    # vision_usage_log 에 비용 기록 — budget_guard 가 SUM 시 활용.
    _record_usage(cost_usd=cost)

    return QueryDecomposition(
        subqueries=parsed,
        cost_usd=cost,
        cached=False,
        skipped_reason=None,
    )


# ---------------------------------------------------------------------------
# Budget guard 통합 — vision_usage_log 의 source_type='query_decomposition' SUM
# ---------------------------------------------------------------------------
def check_decomposition_budget(*, monthly_cap_usd: float) -> budget_guard.BudgetStatus:
    """이번 달 (UTC 1일~now) 의 분해 비용 누적이 monthly_cap_usd 초과했는지.

    `vision_usage_log` 재사용 — `source_type='query_decomposition'` 필터로 분리.
    DB 부재 / 마이그 014 미적용 / SUM 실패 시 graceful (allowed=True).

    `budget_guard.is_disabled()` (`JETRAG_BUDGET_GUARD_DISABLE=1`) 시 통과.
    """
    if budget_guard.is_disabled():
        return budget_guard.BudgetStatus(
            allowed=True,
            used_usd=0.0,
            cap_usd=monthly_cap_usd,
            scope="query_decomposition",
            reason="가드 비활성 (ENV)",
        )

    used = _sum_decomposition_monthly_cost()
    if used is None:
        return budget_guard.BudgetStatus(
            allowed=True,
            used_usd=0.0,
            cap_usd=monthly_cap_usd,
            scope="query_decomposition",
            reason="DB 조회 실패 — 가드 graceful (allowed)",
        )
    if used > monthly_cap_usd:
        return budget_guard.BudgetStatus(
            allowed=False,
            used_usd=used,
            cap_usd=monthly_cap_usd,
            scope="query_decomposition",
            reason=(
                f"월간 분해 비용 한도 초과 "
                f"(${used:.4f} > ${monthly_cap_usd:.4f}) — query 분해 생략"
            ),
        )
    return budget_guard.BudgetStatus(
        allowed=True,
        used_usd=used,
        cap_usd=monthly_cap_usd,
        scope="query_decomposition",
        reason="월간 분해 한도 내",
    )


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------
def _is_enabled() -> bool:
    """`JETRAG_PAID_DECOMPOSITION_ENABLED=true|1|yes|on` → True. 그 외 False (default OFF)."""
    raw = os.environ.get(_ENV_ENABLED, "false").strip().lower()
    return raw in ("true", "1", "yes", "on")


def is_enabled() -> bool:
    """`_is_enabled()` 의 public wrapper — 호출자가 LLM 호출 전 게이트 선판정.

    `/search` (M1 W-1(a)) 가 `not router_decision.needs_decomposition or
    not query_decomposer.is_enabled()` 일 때 분해 스레드 자체를 안 만들기 위해 사용.
    `decompose()` 내부 게이트와 동일 ENV 를 본다.
    """
    return _is_enabled()


def _is_cache_disabled() -> bool:
    """`JETRAG_DECOMPOSITION_CACHE_DISABLE=1` → True. 그 외 False (default 캐시 ON)."""
    return os.environ.get(_ENV_CACHE_DISABLE, "0").strip() == "1"


def _resolve_monthly_cap_usd() -> float:
    """`JETRAG_DECOMPOSITION_MONTHLY_CAP_USD` → float. invalid 시 default 0.30."""
    raw = os.environ.get(_ENV_MONTHLY_CAP)
    if raw is None or raw == "":
        return _MONTHLY_CAP_DEFAULT_USD
    try:
        value = float(raw)
    except ValueError:
        return _MONTHLY_CAP_DEFAULT_USD
    if value < 0:
        return _MONTHLY_CAP_DEFAULT_USD
    return value


def _build_cache_key(query: str, signals: tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
    """LRU cache 키 — (query lowercased, signals tuple).

    signals 가 다르면 같은 query 라도 prompt 가 미묘히 달라질 여지가 있어 분리.
    query 는 lower-case 로 normalize — 대소문자만 다른 query 는 같은 결과로 묶음.
    """
    return (query.lower().strip(), signals)


def _cache_get(
    key: tuple[str, tuple[str, ...]],
) -> tuple[str, ...] | None:
    """LRU 조회 — hit 시 move_to_end, miss 시 None."""
    if key not in _cache:
        return None
    _cache.move_to_end(key)
    return _cache[key]


def _cache_put(
    key: tuple[str, tuple[str, ...]],
    value: tuple[str, ...],
) -> None:
    """LRU 저장 — 200 초과 시 oldest 제거."""
    _cache[key] = value
    _cache.move_to_end(key)
    while len(_cache) > _CACHE_MAX_SIZE:
        _cache.popitem(last=False)


def _reset_cache_for_test() -> None:
    """단위 테스트 용 — cache 전체 초기화."""
    _cache.clear()


def _build_messages(query: str) -> list[ChatMessage]:
    """LLM prompt 구성 — system (instructions + few-shot) + user (질문)."""
    return [
        ChatMessage(role="system", content=_SYSTEM_PROMPT),
        ChatMessage(role="user", content=f"질문: {query}\nJSON:"),
    ]


# JSON array 추출 — LLM 이 markdown fence 나 prefix 를 붙여도 첫 [ ... ] 만 매칭.
_JSON_ARRAY_RE = re.compile(r"\[.*?\]", re.DOTALL)


def _parse_subqueries(raw: str) -> tuple[str, ...]:
    """LLM 응답에서 sub-query 리스트 추출. 실패 시 빈 tuple.

    - markdown fence (```json ... ```) 포함 가능 → 첫 [ ... ] 만 추출.
    - 길이 [_MIN_SUBQUERIES, _MAX_SUBQUERIES] 벗어나면 빈 tuple (skip).
    - 공백 문자열 / 비-string 항목은 제거.
    """
    if not raw or not raw.strip():
        return ()

    match = _JSON_ARRAY_RE.search(raw)
    if not match:
        return ()
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return ()
    if not isinstance(parsed, list):
        return ()

    cleaned: list[str] = []
    for item in parsed:
        if not isinstance(item, str):
            continue
        s = item.strip()
        if s:
            cleaned.append(s)

    if not (_MIN_SUBQUERIES <= len(cleaned) <= _MAX_SUBQUERIES):
        return ()
    return tuple(cleaned)


def _prompt_char_count(messages: list[ChatMessage]) -> int:
    """prompt 토큰 추정용 char 합계. token = char/4 근사 (한국어 안전 측).

    Gemini SDK 가 정확 token count 를 즉시 반환 안 해도 비용 추정 가능.
    """
    return sum(len(m.content) for m in messages)


# Gemini 2.5 Flash-Lite 의 단가 (factory.py 의 _GEMINI_PRICING 에서 lookup).
# input/output 분리 — char/4 ≈ token 근사로 보수적 산정.
_DECOMPOSITION_MODEL = "gemini-2.5-flash-lite"
_CHAR_PER_TOKEN = 4.0


def _estimate_cost_usd(*, prompt_chars: int, output_chars: int) -> float:
    """USD 비용 추정 — chars→token 근사 + factory 단가 lookup.

    LLM SDK 가 usage_metadata 를 노출하지 않는 경로 (현재) 에서도 비용 집계
    가능. 정확도 ±20% 수준이지만 monthly cap (0.30 USD) 같은 보수적 가드에는 충분.
    """
    pricing = get_gemini_pricing(_DECOMPOSITION_MODEL)
    in_tokens = prompt_chars / _CHAR_PER_TOKEN
    out_tokens = output_chars / _CHAR_PER_TOKEN
    # _GEMINI_PRICING 단가는 USD per 1M tokens.
    cost = (in_tokens * pricing["input"] + out_tokens * pricing["output"]) / 1_000_000.0
    return round(cost, 6)


def _record_usage(*, cost_usd: float) -> None:
    """vision_usage_log 에 분해 호출 1건 기록 — budget_guard 가 SUM 시 활용.

    DB 부재 / 마이그 014 미적용 시 graceful skip (warn 1회).
    `source_type='query_decomposition'` + `model_used='gemini-2.5-flash-lite%'`
    로 vision 호출과 분리 집계.
    """
    try:
        from app.db import get_supabase_client

        client = get_supabase_client()
        client.table("vision_usage_log").insert(
            {
                "success": True,
                "quota_exhausted": False,
                "source_type": _USAGE_LOG_SOURCE_TYPE,
                "model_used": _DECOMPOSITION_MODEL,
                "estimated_cost": cost_usd,
            }
        ).execute()
    except Exception as exc:  # noqa: BLE001 — DB 부재 graceful
        logger.debug("query_decomposer usage 기록 실패 (graceful): %s", exc)


def _sum_decomposition_monthly_cost() -> float | None:
    """이번 달 (UTC 1일 ~ now) 의 분해 비용 SUM(estimated_cost). 실패 시 None.

    `source_type='query_decomposition'` AND `success=true` 만 집계. 014 마이그
    미적용 / DB 부재 시 None (graceful — allowed=True).
    """
    try:
        from datetime import datetime, time, timezone

        from app.db import get_supabase_client

        client = get_supabase_client()
        now = datetime.now(timezone.utc)
        month_start = datetime.combine(
            now.date().replace(day=1), time.min, tzinfo=timezone.utc
        )
        resp = (
            client.table("vision_usage_log")
            .select("estimated_cost,success,source_type")
            .eq("source_type", _USAGE_LOG_SOURCE_TYPE)
            .eq("success", True)
            .gte("called_at", month_start.isoformat())
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 — DB 부재 graceful
        logger.debug("query_decomposer monthly SUM 실패 (graceful): %s", exc)
        return None

    total = 0.0
    for row in (resp.data or []):
        cost = row.get("estimated_cost")
        if cost is None:
            continue
        try:
            total += float(cost)
        except (TypeError, ValueError):
            continue
    return total


# Settings 통합 X — 본 모듈은 cap 만 ENV 직접 lookup (planner v0.1 §C).
# settings 추가 시 dataclass 갱신 필요 — 본 ship 에서는 회피 (마이그/스키마 영향 0).


__all__ = [
    "QueryDecomposition",
    "decompose",
    "check_decomposition_budget",
    "is_enabled",
]
