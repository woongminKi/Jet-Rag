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
- per row: 1 LLM call (5~10 candidates 일괄 평가) ~$0.005~0.015
- 178 rows full 보완: ~$1.0~$2.7 (큰 cost — cap 권고)
- 본 helper 는 인프라만 ship — 실 적용은 별도 sprint
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


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
