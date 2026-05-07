"""S1.5 D1 PoC — 페이지별 `vision_need_score` 측정 (master plan §6 S1.5).

목적
- PDF 페이지 단위로 "Vision 보강이 필요해 보이는가" 를 hand-tuned 휴리스틱
  3종으로 한 번 점수화한다. **D1 PoC** 의 산출은 **분포** 자체이며, D2/D3 에서
  실제 사용자 11 docs 데이터를 기반으로 임계·가중을 조정한다.

휴리스틱 (master plan §6 S1.5 default — 사전 조정 X)
- (a) entity regex — `[표 N]`, `[그림 N]`, `<표 N>`, `Figure N`, `Table N`, `식 (N)`
  같은 표·그림·식 reference. hit count.
- (b) table-like 휴리스틱 — `page.get_text("dict")` 의 line 단위 span 배치를 보고
  "동일 y 좌표에 ≥3 span 이 가로로 spread" 한 line 비율을 측정. 0~1 점수.
  (column alignment proxy. PyMuPDF drawings 검출은 비용↑ → 본 PoC 에선 미포함)
- (c) text_density — `text_chars / page_area_pt²`. 임계 1e-3 (default) 미만이면
  텍스트 layer 가 sparse → Vision 후보. A4 (842×595 ≈ 501,490 pt²) 기준으로
  ~500 chars 이하면 신호.

`needs_vision` = (a) hit > 0 OR (b) ≥ 0.5 OR (c) < 1e-3 — OR 합산. 가중 조정은 D2.

회귀 영향 0
- 운영 파이프라인 (extract.py / pymupdf_parser.py) 미참조. 본 모듈은 PoC 스크립트
  (`scripts/poc_vision_need_score.py`) 와 단위 테스트에서만 사용한다.
- 외부 API 0, DB 0, 마이그 0.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


# (a) entity regex — 한국어 + 영어 표/그림/식 reference. case-insensitive.
# sniff: 사용자 PDF 의 본문에서 흔히 보이는 패턴만 — false positive 회피 우선.
_ENTITY_PATTERN = re.compile(
    r"(\[표\s*\d+\]?|\[그림\s*\d+\]?"
    r"|<표\s*\d+>|<그림\s*\d+>"
    r"|Figure\s*\d+|Table\s*\d+"
    r"|식\s*\(\s*\d+\s*\)|Eq\.\s*\(\s*\d+\s*\))",
    re.IGNORECASE,
)

# (b) table-like — line 한 개 내 span 수 임계 + line 비율 임계
# y 좌표 동일 line 안에 horizontal span 이 ≥3 spread 면 column 후보.
_TABLE_MIN_SPANS_PER_LINE = 3
_TABLE_LIKE_THRESHOLD = 0.5  # 점수 ≥ 0.5 면 needs_vision 신호

# (c) text_density 임계 — chars / pt². master plan §6 S1.5 default.
# A4 (842×595 ≈ 501,490 pt²) 기준 ~500 chars 이하면 sparse → Vision 후보.
_DENSITY_THRESHOLD = 1e-3


@dataclass(frozen=True)
class PageScore:
    """페이지별 vision_need_score PoC 결과.

    D1 PoC 단계에선 boolean 도 함께 제공하나, 실제 분포 측정의 핵심은 raw 값
    (`entity_hits`, `table_like_score`, `text_density`) 이다.
    """

    page: int
    text_chars: int
    page_area_pt2: float
    text_density: float  # chars / pt² (page_area > 0 일 때만 의미)
    entity_hits: int
    table_like_score: float  # 0.0 ~ 1.0
    needs_vision: bool

    def signal_kinds(self) -> list[str]:
        """needs_vision 을 트리거한 신호 종류 (debug/aggregation 용)."""
        kinds: list[str] = []
        if self.entity_hits > 0:
            kinds.append("entity")
        if self.table_like_score >= _TABLE_LIKE_THRESHOLD:
            kinds.append("table_like")
        if self.page_area_pt2 > 0 and self.text_density < _DENSITY_THRESHOLD:
            kinds.append("low_density")
        return kinds


def score_page(
    page_dict: dict[str, Any],
    *,
    page_num: int,
    page_area_pt2: float,
) -> PageScore:
    """단일 페이지의 vision_need_score 휴리스틱 3종을 계산.

    Args:
        page_dict: `fitz.Page.get_text("dict")` 결과 (또는 동일 schema mock)
        page_num: 1-based 페이지 번호
        page_area_pt2: 페이지 면적 (pt²). `page.rect.width * page.rect.height`.

    Returns: `PageScore` — raw 점수 + needs_vision boolean.
    """
    text_chars, table_score = _collect_text_features(page_dict)

    density = (text_chars / page_area_pt2) if page_area_pt2 > 0 else 0.0
    page_text = _flatten_text(page_dict)
    entity_hits = len(_ENTITY_PATTERN.findall(page_text))

    needs = (
        entity_hits > 0
        or table_score >= _TABLE_LIKE_THRESHOLD
        or (page_area_pt2 > 0 and density < _DENSITY_THRESHOLD)
    )

    return PageScore(
        page=page_num,
        text_chars=text_chars,
        page_area_pt2=page_area_pt2,
        text_density=density,
        entity_hits=entity_hits,
        table_like_score=table_score,
        needs_vision=needs,
    )


def _collect_text_features(
    page_dict: dict[str, Any],
) -> tuple[int, float]:
    """페이지 dict 에서 text_chars + table_like_score 동시 추출.

    table_like_score = (≥3 span 을 가진 line 수) / (전체 non-empty line 수).
    line 단위 순회는 한 번이면 충분 → 두 신호 동시 산출.
    """
    text_chars = 0
    total_lines = 0
    multi_span_lines = 0

    for block in page_dict.get("blocks", []):
        if block.get("type", 0) != 0:  # 0 = text block (PyMuPDF)
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            non_empty = [s for s in spans if (s.get("text") or "").strip()]
            if not non_empty:
                continue
            total_lines += 1
            text_chars += sum(len(s.get("text", "")) for s in non_empty)
            if len(non_empty) >= _TABLE_MIN_SPANS_PER_LINE:
                multi_span_lines += 1

    table_score = (multi_span_lines / total_lines) if total_lines > 0 else 0.0
    return text_chars, table_score


def _flatten_text(page_dict: dict[str, Any]) -> str:
    """페이지 dict 에서 entity regex 스캔용 평문 추출.

    line 단위 join — span 사이 줄바꿈 보존하면 `<표 1>` 처럼 splittable 한 패턴이
    누락될 수 있어 한 line 안의 span 은 직접 concat.
    """
    parts: list[str] = []
    for block in page_dict.get("blocks", []):
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", []):
            line_text = "".join(s.get("text", "") for s in line.get("spans", []))
            if line_text:
                parts.append(line_text)
    return "\n".join(parts)
