"""S1.5 D3 — vision_need_score 휴리스틱 v2 (master plan §6 S1.5).

D1 (PoC) → D2 (분포 분석) → D3 (임계·가중 정정 + 신호 확장) 누적 산출물.

D2 분석 결정 7건 적용 (work-log 2026-05-07 S1.5 D2 §5):
1. **needs_vision = OR rule 채택** (composite score 보류). D1 OR rule 의 실 골든셋
   recall 5/6 (83.3%) 가 composite score 0.3 임계 (33%) 보다 우수.
2. density 임계 1e-3 유지 — sonata 카탈로그 신호 보호 (29 pages 중 20 = 69%).
3. **table 임계 0.5 → 0.3** — 데이터센터 PDF p.40 표 false negative 회복용 (단,
   p.40 자체는 table v2 fallback 으로 별도 회수).
4. **table 휴리스틱 v2** — line 의 single-span text 안 다중 공백 (``\\s{2,}``) 또는 ``\\t``
   분리 fallback. 한국어 PDF 의 cell 이 별도 span 으로 안 쪼개지는 PyMuPDF 한계 보강.
5. **entity 신호 deprecated** — 8 후보 패턴 본문 hit 2/115 → 가중 0 + OR rule 제외.
   regex 자체는 보존 (분석·디버깅 용도, false positive 0).
6. **6 신호 중 미측정 3종 추가** — image_area_ratio / text_quality / caption_score.
   master plan §6 S1.5 의 본 ship 신호 6종 완성.
7. composite score 함수 (`compute_score`) 는 향후 hybrid mode 진입 여지로 보존하되
   entity 가중치만 0 으로 회수해 다른 신호로 재분배.

회귀 영향 0
- 운영 파이프라인 (extract.py / pymupdf_parser.py / chunk.py) 미참조. 본 모듈은 PoC
  + 분석 스크립트 + 단위 테스트에서만 사용. S2 D1 본 ship 시 운영 hook.
- 외부 API 0, DB 0, 마이그 0.
- D1 API 호환 — `score_page()` / `PageScore.signal_kinds()` 시그니처 유지. v2 신호 3종은
  `PageScore` 필드로 추가 (default 값 0.0 → 기존 호출자 mock 영향 0).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# (a) entity regex — D2 결정 #5 deprecated. 코드 보존 (분석·디버깅).
# ---------------------------------------------------------------------------
_ENTITY_PATTERN = re.compile(
    r"(\[표\s*\d+\]?|\[그림\s*\d+\]?"
    r"|<표\s*\d+>|<그림\s*\d+>"
    r"|Figure\s*\d+|Table\s*\d+"
    r"|식\s*\(\s*\d+\s*\)|Eq\.\s*\(\s*\d+\s*\))",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# (b) table-like — line 한 개 내 span 수 임계 + line 비율 임계 + v2 fallback
# ---------------------------------------------------------------------------
_TABLE_MIN_SPANS_PER_LINE = 3
# D2 결정 #3 — 0.5 → 0.3 (보강, OR rule 임계).
_TABLE_LIKE_NEEDS_AT = 0.3

# v2 fallback (D2 결정 #4) — single-span line 안에서도 다중 공백·탭으로 column 추정.
_MULTISPACE_SPLIT = re.compile(r"\s{2,}|\t")
# fallback 으로 인정할 column 수 임계 (= 기본 span 임계와 동일)
_TABLE_FALLBACK_MIN_COLS = _TABLE_MIN_SPANS_PER_LINE


# ---------------------------------------------------------------------------
# (c) text_density — chars / pt². D2 결정 #2 — 1e-3 유지.
# ---------------------------------------------------------------------------
_DENSITY_NEEDS_AT = 1e-3


# ---------------------------------------------------------------------------
# (d) image_area_ratio — D3 신규. 페이지 안 image block 면적 합 / page 면적.
#     PyMuPDF dict schema 의 block.type == 1 (image). 0.30 이상이면 vision 후보.
# ---------------------------------------------------------------------------
_IMAGE_AREA_NEEDS_AT = 0.30


# ---------------------------------------------------------------------------
# (e) text_quality — D3 신규. 0.0 ~ 1.0 (1=정상, 0=깨짐).
#     printable 문자 비율로 추정. 0.40 이하면 OCR 필요 시그널.
# ---------------------------------------------------------------------------
_TEXT_QUALITY_NEEDS_AT = 0.40


# ---------------------------------------------------------------------------
# (f) caption_score — D3 신규. 0.0 ~ 1.0. caption 패턴 (≤80자 line + 표/그림/figure
#     keyword) 의 line 비율. 0.20 이상이면 그림 캡션 페이지 후보.
# ---------------------------------------------------------------------------
_CAPTION_NEEDS_AT = 0.20

_CAPTION_KEYWORDS = ("표", "그림", "도", "사진", "Figure", "Fig.", "Table", "Photo")
_CAPTION_MAX_LINE_LEN = 80


# ---------------------------------------------------------------------------
# composite score 가중치 (D2 분석 + D3 entity 회수).
# entity_density 는 deprecated → 0. text_density_inverse 가 회수분 흡수.
# 합 1.0 유지. 본 score 는 OR rule 과 별개로 분포 진단·hybrid 검토용.
# ---------------------------------------------------------------------------
DEFAULT_WEIGHTS: dict[str, float] = {
    "text_density_inverse": 0.30,  # D1 0.10 → D3 0.30 (entity 회수분 +0.20)
    "table_like_blocks": 0.25,
    "image_area_ratio": 0.20,
    "text_quality": 0.10,  # 1 - text_quality 로 가산 (낮을수록 vision)
    "caption_existence": 0.15,
    "entity_density": 0.0,  # D2 결정 #5 — deprecated
}


@dataclass(frozen=True)
class PageScore:
    """페이지별 vision_need_score 결과 (D3 — 6 신호 + composite score).

    D1 호환 필드 (page / text_chars / page_area_pt2 / text_density / entity_hits /
    table_like_score / needs_vision) 보존. v2 신호 3종은 default 0.0 으로 추가 —
    기존 D1 단위 테스트의 mock 호출 영향 0.
    """

    page: int
    text_chars: int
    page_area_pt2: float
    text_density: float
    entity_hits: int
    table_like_score: float
    needs_vision: bool
    # D3 신규 신호
    image_area_ratio: float = 0.0
    text_quality: float = 1.0
    caption_score: float = 0.0
    composite_score: float = 0.0
    # OR rule trigger 분해 — 디버깅·분석용
    triggers: tuple[str, ...] = field(default_factory=tuple)

    def signal_kinds(self) -> list[str]:
        """needs_vision 을 트리거한 신호 종류 (D1 호환 list 반환).

        D1 의 entity / table_like / low_density 외에 D3 신규 트리거 (image_area /
        text_quality_low / caption) 도 포함. entity 는 D3 OR rule 제외 — 다만 D1
        호환을 위해 entity_hits>0 이면 본 결과 안엔 표기 (signal_kinds 는 "어느
        신호가 hit 했는가" 의 개념적 답이지, OR rule 의 정확한 산출 기준 X).
        OR rule 산출 기준을 정확히 알고 싶으면 `triggers` 필드 사용.
        """
        return list(self.triggers)


def needs_vision(
    *,
    text_density: float,
    table_like_score: float,
    image_area_ratio: float = 0.0,
    text_quality: float = 1.0,
    caption_score: float = 0.0,
    page_area_pt2: float = 1.0,
) -> bool:
    """D3 OR rule — entity 제외, 6 신호 중 5종으로 vision 후보 판정.

    page_area_pt2 ≤ 0 이면 density 신호는 미발화 (D1 호환).
    """
    return any(_or_rule_triggers(
        text_density=text_density,
        table_like_score=table_like_score,
        image_area_ratio=image_area_ratio,
        text_quality=text_quality,
        caption_score=caption_score,
        page_area_pt2=page_area_pt2,
    ).values())


def needs_vision_breakdown(
    *,
    text_density: float,
    table_like_score: float,
    image_area_ratio: float = 0.0,
    text_quality: float = 1.0,
    caption_score: float = 0.0,
    page_area_pt2: float = 1.0,
) -> dict[str, bool]:
    """OR rule 신호별 trigger 결과 (디버깅·CSV 컬럼·시뮬레이션용)."""
    return dict(_or_rule_triggers(
        text_density=text_density,
        table_like_score=table_like_score,
        image_area_ratio=image_area_ratio,
        text_quality=text_quality,
        caption_score=caption_score,
        page_area_pt2=page_area_pt2,
    ))


def _or_rule_triggers(
    *,
    text_density: float,
    table_like_score: float,
    image_area_ratio: float,
    text_quality: float,
    caption_score: float,
    page_area_pt2: float,
) -> dict[str, bool]:
    """OR rule 항목별 boolean — `needs_vision` / `breakdown` 공통 source."""
    return {
        "low_density": page_area_pt2 > 0 and text_density < _DENSITY_NEEDS_AT,
        "table_like": table_like_score >= _TABLE_LIKE_NEEDS_AT,
        "image_area": image_area_ratio >= _IMAGE_AREA_NEEDS_AT,
        "text_quality_low": text_quality <= _TEXT_QUALITY_NEEDS_AT,
        "caption": caption_score >= _CAPTION_NEEDS_AT,
    }


def compute_score(
    *,
    text_density: float,
    table_like_score: float,
    image_area_ratio: float = 0.0,
    text_quality: float = 1.0,
    caption_score: float = 0.0,
    entity_hits: int = 0,
    page_area_pt2: float = 1.0,
    weights: dict[str, float] | None = None,
) -> float:
    """가중 합산 composite score (0.0 ~ 1.0).

    D3 default weights — entity 가중치 0 (deprecated), text_density_inverse 가
    entity 회수분을 흡수. 본 score 는 OR rule 산출과 별개. hybrid mode 진입 시
    임계 후보로 사용.

    text_quality 는 "낮을수록 vision" 이라 (1 - text_quality) 로 변환해 가산.
    text_density 도 동일 방향 — `1 - density / (2 * threshold)` 로 normalize.
    """
    w = weights if weights is not None else DEFAULT_WEIGHTS
    density_signal = (
        max(0.0, min(1.0, 1.0 - text_density / (2 * _DENSITY_NEEDS_AT)))
        if page_area_pt2 > 0
        else 0.0
    )
    table_signal = max(0.0, min(1.0, table_like_score))
    image_signal = max(0.0, min(1.0, image_area_ratio))
    quality_signal = max(0.0, min(1.0, 1.0 - text_quality))
    caption_signal = max(0.0, min(1.0, caption_score))
    entity_signal = 1.0 if entity_hits > 0 else 0.0

    score = (
        w.get("text_density_inverse", 0.0) * density_signal
        + w.get("table_like_blocks", 0.0) * table_signal
        + w.get("image_area_ratio", 0.0) * image_signal
        + w.get("text_quality", 0.0) * quality_signal
        + w.get("caption_existence", 0.0) * caption_signal
        + w.get("entity_density", 0.0) * entity_signal
    )
    return max(0.0, min(1.0, score))


def score_page(
    page_dict: dict[str, Any],
    *,
    page_num: int,
    page_area_pt2: float,
) -> PageScore:
    """단일 페이지의 vision_need_score 휴리스틱 6종을 계산.

    D1 호환 — 시그니처·반환 타입 (PageScore) 보존. v2 신호 3종 (image_area /
    text_quality / caption) + composite + OR rule triggers 추가.

    Args:
        page_dict: `fitz.Page.get_text("dict")` 결과 (또는 동일 schema mock)
        page_num: 1-based 페이지 번호
        page_area_pt2: 페이지 면적 (pt²). `page.rect.width * page.rect.height`.
    """
    features = _collect_text_features(page_dict)
    density = (features.text_chars / page_area_pt2) if page_area_pt2 > 0 else 0.0

    page_text = _flatten_text(page_dict)
    entity_hits = len(_ENTITY_PATTERN.findall(page_text))

    image_area = _signal_image_area_ratio(page_dict, page_area_pt2)
    text_quality = _signal_text_quality(page_text)

    triggers = tuple(
        kind
        for kind, hit in _or_rule_triggers(
            text_density=density,
            table_like_score=features.table_like_score,
            image_area_ratio=image_area,
            text_quality=text_quality,
            caption_score=features.caption_score,
            page_area_pt2=page_area_pt2,
        ).items()
        if hit
    )
    needs = bool(triggers)

    composite = compute_score(
        text_density=density,
        table_like_score=features.table_like_score,
        image_area_ratio=image_area,
        text_quality=text_quality,
        caption_score=features.caption_score,
        entity_hits=entity_hits,
        page_area_pt2=page_area_pt2,
    )

    return PageScore(
        page=page_num,
        text_chars=features.text_chars,
        page_area_pt2=page_area_pt2,
        text_density=density,
        entity_hits=entity_hits,
        table_like_score=features.table_like_score,
        needs_vision=needs,
        image_area_ratio=image_area,
        text_quality=text_quality,
        caption_score=features.caption_score,
        composite_score=composite,
        triggers=triggers,
    )


# ---------------------------------------------------------------------------
# feature collectors
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _TextFeatures:
    """`_collect_text_features` 의 다중 반환값 packaging."""

    text_chars: int
    table_like_score: float
    caption_score: float


def _collect_text_features(page_dict: dict[str, Any]) -> _TextFeatures:
    """페이지 dict 에서 text_chars + table_like + caption 3 신호 동시 추출.

    table_like_score = (column 후보 line 수) / (전체 non-empty line 수).
    column 후보 = (≥3 span 인 line) OR (single-span 인데 v2 fallback 으로 다중
    공백·탭 분리 시 ≥3 column 인 line). single-span 만 있는 한국어 PDF 대응.

    caption_score = (caption-like line 수) / (전체 non-empty line 수).
    caption-like = ≤80자 + 키워드 (표/그림/도/사진/Figure/Fig./Table/Photo).
    """
    text_chars = 0
    total_lines = 0
    multi_col_lines = 0
    caption_lines = 0

    for block in page_dict.get("blocks", []):
        if block.get("type", 0) != 0:  # 0 = text block (PyMuPDF)
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            non_empty = [s for s in spans if (s.get("text") or "").strip()]
            if not non_empty:
                continue
            total_lines += 1
            line_text = "".join(s.get("text", "") for s in non_empty)
            text_chars += sum(len(s.get("text", "")) for s in non_empty)

            if _is_multi_column_line(non_empty, line_text):
                multi_col_lines += 1
            if _is_caption_line(line_text):
                caption_lines += 1

    table_score = (multi_col_lines / total_lines) if total_lines > 0 else 0.0
    caption_score = (caption_lines / total_lines) if total_lines > 0 else 0.0
    return _TextFeatures(
        text_chars=text_chars,
        table_like_score=table_score,
        caption_score=caption_score,
    )


def _is_multi_column_line(non_empty_spans: list[dict[str, Any]], line_text: str) -> bool:
    """line 한 개가 column 후보인지 — span 수 또는 v2 fallback (다중 공백·탭).

    1. ≥3 span — D1 default. PyMuPDF 가 cell 별 span 분리한 경우 (영어 PDF 다수)
    2. single-span 인데 본문에 `\\s{2,}` 또는 `\t` 분리 시 ≥3 column — v2 fallback.
       한국어 PDF 의 cell 이 한 span 으로 합쳐지는 PyMuPDF 한계 보강.

    span 2개 같은 어중간한 경우는 fallback 안 보고 D1 정책 유지 (≥3 span 필요).
    """
    if len(non_empty_spans) >= _TABLE_MIN_SPANS_PER_LINE:
        return True
    if len(non_empty_spans) == 1:
        # v2 fallback
        cols = [c for c in _MULTISPACE_SPLIT.split(line_text) if c.strip()]
        return len(cols) >= _TABLE_FALLBACK_MIN_COLS
    return False


def _is_caption_line(line_text: str) -> bool:
    """caption-like 판정 — ≤80자 + 키워드 1개 이상.

    "[표 1]" / "<그림 2>" / "Figure 3" / "표 1. 데이터센터 현황" 등 cover.
    """
    text = line_text.strip()
    if not text or len(text) > _CAPTION_MAX_LINE_LEN:
        return False
    for kw in _CAPTION_KEYWORDS:
        if kw in text:
            return True
    return False


def _signal_image_area_ratio(page_dict: dict[str, Any], page_area_pt2: float) -> float:
    """페이지 안 image block 면적 합 / page 면적.

    PyMuPDF dict schema 의 block.type == 1 → image block. bbox = [x0, y0, x1, y1].
    page_area_pt2 ≤ 0 이면 0 반환 (이상치 보호).
    """
    if page_area_pt2 <= 0:
        return 0.0
    image_area = 0.0
    for block in page_dict.get("blocks", []):
        if block.get("type", 0) != 1:
            continue
        bbox = block.get("bbox") or block.get("box")
        if not bbox or len(bbox) != 4:
            continue
        x0, y0, x1, y1 = bbox
        w, h = max(0.0, x1 - x0), max(0.0, y1 - y0)
        image_area += w * h
    return min(1.0, image_area / page_area_pt2)


def _signal_text_quality(page_text: str) -> float:
    """text_quality (0=깨짐, 1=정상) — printable 문자 비율로 추정.

    한국어 (가-힣) + 영어 + 숫자 + ASCII 구두점 + 공백 = printable.
    그 외 (PUA / surrogate / 비인쇄 제어 등) 는 OCR 깨짐 신호. 빈 문자열은 1.0
    (페이지가 비어 있으면 vision 판정은 다른 신호 — image_area / density 가 함).
    """
    if not page_text:
        return 1.0
    printable = 0
    total = 0
    for ch in page_text:
        if ch.isspace():
            continue  # 공백은 카운트 제외 (의미 없음)
        total += 1
        if _is_printable(ch):
            printable += 1
    return (printable / total) if total > 0 else 1.0


def _is_printable(ch: str) -> bool:
    """단일 문자가 정상 printable 인지."""
    cp = ord(ch)
    # ASCII 인쇄 가능
    if 0x20 <= cp <= 0x7E:
        return True
    # 한글 음절 + 자모
    if 0xAC00 <= cp <= 0xD7A3:
        return True
    if 0x1100 <= cp <= 0x11FF or 0x3130 <= cp <= 0x318F:
        return True
    # CJK 통합 한자 + 호환 한자 (보수적)
    if 0x3400 <= cp <= 0x9FFF:
        return True
    # 기본 한국어 문장 부호
    if cp in (0x300C, 0x300D, 0x300E, 0x300F, 0x3001, 0x3002, 0x00B7, 0x00A0):
        return True
    # 일반 라틴 보충 (악센트 등) — 깨짐 아님
    if 0x00A1 <= cp <= 0x024F:
        return True
    return False


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
