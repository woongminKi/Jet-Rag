"""acceptable_chunks LLM-judge 자동 보완 — query 의도와 chunks 의미 매칭 평가.

motivation
----------
1순위 sprint 의 ILIKE 자동 라벨링 한계 (R@10 0.37 검증) — keyword 매칭 ≠
의미 매칭. LLM judge 가 query + chunk text 를 보고 "이 chunk 가 정답에 도움
되는지" 판정 → 더 정확한 acceptable_chunks 라벨링 가능.

설계 원칙
- query 별 N 개 candidate chunks (top-K from search) → LLM 이 각각 0.0~1.0 판정
- threshold 이상 → acceptable_chunks 채택 (default 0.5)
- 외부 의존성: Gemini text API (cost 발생 — cap 적용)
- ILIKE 기반 보완 (대체 X) — 두 방법 union 가능

cost
----
- per row: 1 LLM call (5~10 candidates 일괄 평가) ~$0.002~0.02
- 실 적용: `evals/run_acceptable_chunks_judge.py` (2026-05-11) — empty
  acceptable_chunks row 한정 (~23 row), cost cap $0.30.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Callable, Iterable

logger = logging.getLogger(__name__)

_DEFAULT_JUDGE_MODEL = "gemini-2.5-flash"
_USAGE_SOURCE_TYPE = "acceptable_judge"


@dataclass
class JudgedChunk:
    """LLM 이 판정한 1 chunk."""

    chunk_idx: int
    score: float  # 0.0~1.0
    reason: str


def build_judge_prompt(*, query: str, candidates: list[tuple[int, str]]) -> str:
    """LLM judge 용 prompt — query + (chunk_idx, text snippet) list.

    candidates: [(chunk_idx, text_snippet), ...]
    """
    items_json = json.dumps(
        [
            {"chunk_idx": idx, "text": text[:300]}
            for idx, text in candidates
        ],
        ensure_ascii=False,
        indent=2,
    )
    return f"""사용자 query: {query!r}

다음은 검색 결과의 candidate chunks 입니다:
{items_json}

각 chunk 가 query 의 정답 (answer) 에 **얼마나 도움 되는지** 0.0~1.0 으로 평가하세요.
- 1.0: 직접 답을 포함하거나 핵심 정보 제공
- 0.5: 부분 정보 / 맥락 / 참고 가치 (acceptable threshold)
- 0.0: 무관 또는 noise

JSON array 로 응답: [{{"chunk_idx": <idx>, "score": <0.0~1.0>, "reason": "<짧은 근거>"}}]
markdown fence 금지, array 만 반환."""


_SYSTEM_PROMPT = """당신은 한국어 RAG 검색 결과 라벨링 전문가입니다.
사용자 query 와 candidate chunks 를 보고 각 chunk 가 정답에 도움 되는지
0.0~1.0 으로 평가하세요. JSON array 만 반환.
"""


def parse_judgment(raw: str, *, expected_indices: list[int]) -> list[JudgedChunk]:
    """LLM JSON → JudgedChunk list.

    expected_indices: 입력으로 보낸 chunk_idx list (미응답 chunk 는 score=0 채움).
    """
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        # markdown fence 제거 시도
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
        try:
            items = json.loads(cleaned)
        except json.JSONDecodeError:
            raise RuntimeError(f"LLM JSON parse 실패: {exc}\nraw: {raw[:300]}") from exc
    if not isinstance(items, list):
        raise RuntimeError(f"LLM 응답 array 아님: {type(items)} / {raw[:200]}")
    by_idx: dict[int, JudgedChunk] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            cidx = int(item.get("chunk_idx"))
        except (TypeError, ValueError):
            continue
        try:
            score = float(item.get("score", 0.0))
        except (TypeError, ValueError):
            score = 0.0
        score = max(0.0, min(1.0, score))
        reason = str(item.get("reason", "")).strip()
        by_idx[cidx] = JudgedChunk(chunk_idx=cidx, score=score, reason=reason)
    out = []
    for idx in expected_indices:
        if idx in by_idx:
            out.append(by_idx[idx])
        else:
            out.append(JudgedChunk(chunk_idx=idx, score=0.0, reason="LLM 미응답"))
    return out


def select_acceptable(
    judgments: list[JudgedChunk],
    *,
    threshold: float = 0.5,
    max_count: int | None = None,
) -> list[int]:
    """threshold 이상 chunk_idx list (score 내림차순). max_count 초과 시 cap."""
    sorted_j = sorted(
        [j for j in judgments if j.score >= threshold],
        key=lambda j: -j.score,
    )
    if max_count is not None:
        sorted_j = sorted_j[:max_count]
    return sorted([j.chunk_idx for j in sorted_j])


# ---------------------------------------------------------------------------
# Gemini text judge — wiring (2026-05-11)
# ---------------------------------------------------------------------------
#
# `make_acceptable_judge_caller()` 는 `_multimodal_judge.make_llm_caller()` 의
# text-only 대칭본. 팩토리 패턴 이유는 동일 — Gemini client lazy init (테스트가
# import 만 해도 외부 의존 비발동) + 호출마다 vision_usage_log 자동 기록.


def make_acceptable_judge_caller(
    *,
    model: str = _DEFAULT_JUDGE_MODEL,
    record_usage: bool = True,
) -> Callable[[str, str], str]:
    """Gemini text API 기반 judge_call_fn 팩토리.

    반환 함수: `(system_prompt, user_prompt) -> raw response str`.
    `_multimodal_judge.make_llm_caller` 와 대칭 — 차이는 image part 없음 (text
    part 2개: system, user) + source_type="acceptable_judge".

    `record_usage=True` (default) 시 호출 1건마다 vision_usage_log 에
    `source_type="acceptable_judge"` row 기록 (cost 누적 추적용). 단위 테스트는
    `record_usage=False` 로 우회 가능.

    실패 시 RuntimeError raise — `evaluate_acceptable` 의 try/except 가 graceful
    처리. 빈 응답 text → RuntimeError.
    """
    from google.genai import types

    from app.adapters.impl._gemini_common import get_client, with_retry
    from app.adapters.impl.gemini_vision import _parse_usage_metadata

    client = get_client()

    def judge_call_fn(system_prompt: str, user_prompt: str) -> str:
        # system prompt 는 user content 앞에 텍스트 part 로 붙여 단일 turn 처리
        # (_multimodal_judge 와 동일 방식). response_mime_type=application/json.
        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text=system_prompt),
                    types.Part.from_text(text=user_prompt),
                ],
            ),
        ]
        config = types.GenerateContentConfig(
            temperature=0.0,  # judge → deterministic
            response_mime_type="application/json",
        )

        def call() -> object:
            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
            text = response.text
            if text is None or not text.strip():
                raise RuntimeError(f"Gemini acceptable judge 응답이 비어있습니다: {response}")
            return response

        response = with_retry(call, label="acceptable_judge")

        if record_usage:
            try:
                from app.services import vision_metrics

                usage = _parse_usage_metadata(response, model=model)
                vision_metrics.record_call(
                    success=True,
                    source_type=_USAGE_SOURCE_TYPE,
                    usage=usage,
                )
            except Exception as exc:  # noqa: BLE001 — usage 기록 실패는 graceful
                logger.debug("acceptable_judge usage 기록 실패 (graceful): %s", exc)

        return response.text

    return judge_call_fn


def evaluate_acceptable(
    *,
    query: str,
    candidates: list[tuple[int, str]],
    judge_call_fn: Callable[[str, str], str],
    threshold: float = 0.5,
    max_count: int | None = 8,
    exclude: Iterable[int] = (),
) -> list[int]:
    """acceptable judge 메인 entry — DI 패턴 (`evaluate_multimodal` 대응).

    흐름: candidates empty → []. else build_judge_prompt → judge_call_fn →
    parse_judgment → select_acceptable → exclude 제거 → 반환.

    LLM 호출 실패 (judge_call_fn raise) 또는 parse 실패 (RuntimeError) → catch →
    [] 반환 (graceful — runner 가 해당 row skip 처리). 예외 종류별 logger.warning.

    `exclude`: 결과에서 제거할 chunk_idx (보통 relevant_chunks — relevant 와
    겹치는 idx 는 acceptable 에 안 넣음).
    """
    if not candidates:
        return []
    user_prompt = build_judge_prompt(query=query, candidates=candidates)
    try:
        raw = judge_call_fn(_SYSTEM_PROMPT, user_prompt)
    except Exception as exc:  # noqa: BLE001 — 외부 LLM 호출 실패 흡수
        logger.warning("acceptable judge LLM 호출 실패: %s", exc)
        return []
    try:
        judgments = parse_judgment(raw, expected_indices=[c[0] for c in candidates])
    except RuntimeError as exc:
        logger.warning("acceptable judge 응답 parse 실패: %s", exc)
        return []
    selected = select_acceptable(judgments, threshold=threshold, max_count=max_count)
    exclude_set = set(exclude)
    return [idx for idx in selected if idx not in exclude_set]
