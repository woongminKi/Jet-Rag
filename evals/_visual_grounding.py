"""visual_grounding metric — vision-derived chunks 와 답변 text 의 의미 매칭.

motivation
----------
RAGAS Faithfulness 는 답변의 claim 을 chunks text 와 비교하여 검증. 단 vision-
derived chunks (OCR 텍스트 + `[문서]` / `[표]` 메타 prefix) 의 본문 일부 는 숫자/
형식 데이터 (G-A-919 같은 표 데이터) 라 LLM judge 가 의미 추출 어려움.

이 metric 은 보조 지표:
- vision-derived chunks 의 메타 caption (예: `[문서] 경제전망 보고서의 목차를
  보여주는 문서`) 추출
- 답변 text 와 caption 의 cosine similarity (BGE-M3) 계산
- 답변이 vision content 의 의미를 반영하는지 측정

range: [0.0, 1.0]. 1.0 = 완벽 매칭, 0.5 = 약한 매칭, < 0.3 = 무관.

설계 원칙
- vision chunk 가 contexts 에 없으면 score = None (skip)
- BGE-M3 의존 (이미 search.py 에서 사용 중) — 외부 추가 의존성 0
- Faithfulness 와 직교 — 두 metric 같이 보면 더 정확한 vision QA 평가
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# vision OCR 메타 prefix 추출 — `[문서]` / `[표]` 등 + 첫 줄
_META_PREFIX_PATTERN = re.compile(r"^(\[[^\]]+\][^\n]*)", re.MULTILINE)


def extract_vision_captions(contexts: list[str]) -> list[str]:
    """contexts 에서 vision OCR 메타 caption (첫 줄) 추출.

    `[문서] X 를 보여주는 문서` / `[표] Y` 패턴 매칭. 여러 chunk 의 caption 이
    있을 수 있으니 list 반환 (dedup).
    """
    captions: list[str] = []
    seen: set[str] = set()
    for ctx in contexts:
        m = _META_PREFIX_PATTERN.match(ctx.strip())
        if m:
            cap = m.group(1).strip()
            if cap and cap not in seen:
                seen.add(cap)
                captions.append(cap)
    return captions


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


@dataclass
class VisualGroundingResult:
    score: float | None
    n_captions: int
    matched_caption: str | None
    sims: list[float]


def compute_visual_grounding(
    *,
    answer: str,
    contexts: list[str],
    embed_fn,
) -> VisualGroundingResult:
    """답변 ↔ vision caption max cosine.

    embed_fn(text) -> vector list[float]. BGE-M3 provider 의 embed_query 사용 권장.
    vision caption 0 건이면 score=None (해당 metric 평가 불가, vision context 없음).
    """
    captions = extract_vision_captions(contexts)
    if not captions:
        return VisualGroundingResult(
            score=None, n_captions=0, matched_caption=None, sims=[]
        )
    if not answer or not answer.strip():
        return VisualGroundingResult(
            score=0.0, n_captions=len(captions), matched_caption=None, sims=[]
        )
    try:
        ans_vec = embed_fn(answer)
    except Exception as exc:  # noqa: BLE001
        logger.warning("answer embed 실패: %s", exc)
        return VisualGroundingResult(
            score=None, n_captions=len(captions), matched_caption=None, sims=[]
        )
    sims: list[float] = []
    best_idx = -1
    best_sim = -1.0
    for i, cap in enumerate(captions):
        try:
            cap_vec = embed_fn(cap)
        except Exception as exc:  # noqa: BLE001
            logger.warning("caption embed 실패 (%r): %s", cap[:40], exc)
            sims.append(0.0)
            continue
        sim = max(0.0, cosine(ans_vec, cap_vec))
        sims.append(sim)
        if sim > best_sim:
            best_sim = sim
            best_idx = i
    score = max(sims) if sims else 0.0
    matched = captions[best_idx] if best_idx >= 0 else None
    return VisualGroundingResult(
        score=score,
        n_captions=len(captions),
        matched_caption=matched,
        sims=sims,
    )
