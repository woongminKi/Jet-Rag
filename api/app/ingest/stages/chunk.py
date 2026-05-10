"""Chunk 스테이지 — 기획서 §10.5 청킹 3단계 + W4-Q-14 정책 본격 변경.

- 1차: 섹션/헤딩 기준 분할 → PDF 는 이미 PyMuPDFParser 가 block 단위로 나눠 준다
- 2차: 800자 초과 섹션 → 문장 경계로 분할 (한국어 포함 휴리스틱)
- 3차: 200자 미만 섹션은 인접과 병합 (같은 페이지 내에서만, 최대 1,000자)
- 최대 1,000자/토큰 상한 준수. 인접 청크 사이 100자 prefix overlap 적용.

W4-Q-14 적용 항목 (청킹 정책 검토 §6 의 1·2·3·5):
- 4.1: lookbehind char class 일반화 — `(?<=[가-힣)\]][.!?])\s+` 단일 패턴으로 모든 한국어
       음절 + 닫는 괄호 뒤 문장부호를 커버. 종결어미 화이트리스트 불필요.
- 4.2: 숫자/영문 직후 `. ` 자연 차단 (lookbehind 가 한국어/괄호만 통과) + 법령 인용
       (`yyyy. m. d. 선고`) 패턴 placeholder 마스킹 (이중 안전망)
- 4.4: `_split_by_sentence` 결과 인접 조각에 마지막 ~100자 prefix overlap +
       `metadata["overlap_with_prev_chunk_idx"]` 메타 기록
- 4.5: 3차 병합 시 section_title 우선순위 swap — 병합되는 쪽 (section) 의
       title 이 더 의미 있을 가능성에 가중 (KPI §13.1 section_title 채움 비율 직접 영향)

W5 추가 (4.3 따옴표/괄호 보호 — W4 이월 회수):
- 청크 경계가 odd-count `"` 또는 `(`/`[`/`{`/`「`/`『` 이면 다음 sent 흡수해서 짝 맞춤
- `_MAX_SIZE` 위반 시 fallback (현재 동작 유지) — 강제 cut
- 인용문 중간 분리 방지 (`대법원은 "...인정한다." 라고 판결했다.` 등)

W5 Day 3 추가 (4.6 표 청크 격리 — 청킹 정책 검토 §4.6):
- `_merge_short_sections` 에서 표 셀 의심 패턴 (짧고 숫자/특수문자 비율 ↑ 또는 ` | `
  separator 포함) 감지 시 인접 본문과 병합 차단
- 표 데이터가 일반 본문 의미와 섞이는 false positive 회피
- chunk_filter 의 table_noise 룰과 책임 분리 — 본 단계는 분리, filter 는 검색 제외

메타 보존: chunk_idx · doc_id · page · section_title · char_range · bbox · metadata
"""

from __future__ import annotations

import re
import unicodedata

from app.adapters.parser import ExtractedSection, ExtractionResult
from app.adapters.vectorstore import ChunkRecord
from app.ingest.jobs import stage

_STAGE = "chunk"

_TARGET_SIZE = 800
_MAX_SIZE = 1000
_MIN_MERGE_SIZE = 200
_OVERLAP_SIZE = 100  # W4-Q-14 4.4 — 인접 청크 prefix overlap 길이 (한국어 RAG 표준 50~150자)

# 4.1 + 4.2 — lookbehind fixed-width 한계 회피 + false split 보호.
# 이전 패턴: `(?<=[다요까죠습니다])[.!?]?\s+` (character class 단일 char 매칭, "습니다" 어절 인식 X)
#
# 신 패턴 (단일 alternation 통합) — 직전 2 chars 가 `[가-힣)\]]` + `[.!?]` 일 때만 split.
# - 직전 1 char 가 한국어 음절(가-힣) 또는 닫는 괄호(`)` `]`) → 한국어 종결어미 모두 커버
#   (다·요·까·죠·습·니·네·군·지·... 등 모든 한국어 음절 일반화. char class 화이트리스트 불필요)
# - 직전 1 char 가 숫자/영문/약어 → fail → split 안 함
#   (`Section 1. ` / `vs. ` / `et al. ` / `2025. 7. 9.` 등 false split 차단)
# - 매칭은 `\s+` 만, lookbehind 는 zero-width → 마침표 자체는 좌측 청크에 보존됨
#
# 주의: leftmost 매칭 우선 — alternation 좌측 우선보다 위치 우선. 따라서 `[.!?]` 를
# 매칭 본문에 포함하면 안 됨 (이전 구현 버그: `[.!?]\s+` 매칭이 lookbehind alt 보다 1 char
# 왼쪽에서 매칭되어 마침표 소실).
_SENTENCE_END = re.compile(
    # (a) 한국어/괄호 + 문장부호 뒤 공백 — fixed 2-char lookbehind, `\s+` 만 매칭
    r"(?<=[가-힣\)\]][.!?])\s+"
    # (b) 문단 break
    r"|\n\s*\n"
)

# 4.2 보강 — 법령 인용 패턴 사전 마스킹 후 split 회피용.
# 매칭 시 임시 placeholder 로 치환해 split 우회 → 분할 후 복원.
# 패턴: `숫자. 숫자. 숫자.` (날짜) — `2025. 7. 9.` `2024. 12. 31.` 등
_LEGAL_DATE_PATTERN = re.compile(r"(\d{2,4})\.\s+(\d{1,2})\.\s+(\d{1,2})\.")
_LEGAL_DATE_PLACEHOLDER = "\x00LEGALDATE\x01"  # 정규식·일반 문서에 등장 불가능한 마커


def run_chunk_stage(
    job_id: str, *, doc_id: str, extraction: ExtractionResult
) -> list[ChunkRecord]:
    """Extract 결과를 받아 청크 레코드 리스트로 변환. 저장은 load 스테이지에서."""
    with stage(job_id, _STAGE):
        split = _split_long_sections(extraction.sections)
        merged = _merge_short_sections(split)
        return _to_chunk_records(doc_id=doc_id, sections=merged)


# ---------------------- 2차: 긴 섹션 분할 ----------------------


def _split_long_sections(sections: list[ExtractedSection]) -> list[ExtractedSection]:
    out: list[ExtractedSection] = []
    for section in sections:
        if len(section.text) <= _MAX_SIZE:
            out.append(section)
            continue
        for piece_text in _split_by_sentence(section.text):
            out.append(
                ExtractedSection(
                    text=piece_text,
                    page=section.page,
                    section_title=section.section_title,
                    bbox=section.bbox,  # 분할 조각은 원 bbox 를 공유 (근사)
                    # 2026-05-09 — split 시 section.metadata 보존 (S4-A D2 caption
                    # 전파 회귀 fix). 이전엔 default 빈 dict 로 채워져 vision-derived
                    # OCR section 의 table_caption/figure_caption 이 split chunks
                    # 전체에서 손실. dict() 복사로 frozen 격리.
                    metadata=dict(section.metadata),
                )
            )
    return out


def _split_by_sentence(text: str) -> list[str]:
    """문장 경계 기준으로 `_TARGET_SIZE` 근방으로 분할.

    W4-Q-14 적용:
    - 4.2: 법령 인용 (`yyyy. m. d.`) 패턴은 placeholder 로 마스킹 후 split → 복원
    - 4.4: 분할 직후 인접 조각에 마지막 ~100자 prefix overlap 부여
    """
    # 4.2 — 법령 인용 마스킹
    masked, restore_map = _mask_legal_dates(text)

    sentences = _SENTENCE_END.split(masked)
    pieces: list[str] = []
    current = ""
    for sent in sentences:
        if not sent:
            continue
        if current and len(current) + len(sent) + 1 > _TARGET_SIZE:
            # W5 4.3 — current 가 odd-count 따옴표/괄호 이면 다음 sent 흡수해서 짝 맞춤.
            #   _MAX_SIZE 위반 시 fallback (강제 cut)
            if (
                _is_unbalanced_quote_or_paren(current)
                and len(current) + len(sent) + 1 <= _MAX_SIZE
            ):
                current = f"{current} {sent}".strip()
            else:
                pieces.append(current.strip())
                current = sent
        else:
            current = f"{current} {sent}".strip() if current else sent
    if current:
        pieces.append(current.strip())

    # 여전히 `_MAX_SIZE` 초과하는 조각은 강제 분할 (문장 경계 없는 긴 텍스트 대비)
    bounded: list[str] = []
    for piece in pieces:
        if len(piece) <= _MAX_SIZE:
            bounded.append(piece)
            continue
        for i in range(0, len(piece), _TARGET_SIZE):
            bounded.append(piece[i : i + _TARGET_SIZE])

    # 4.2 — 법령 인용 복원
    restored = [_restore_legal_dates(p, restore_map) for p in bounded]

    # 4.4 — 인접 청크 prefix overlap 적용
    return _apply_overlap(restored)


def _mask_legal_dates(text: str) -> tuple[str, list[str]]:
    """법령 인용 (yyyy. m. d.) 패턴을 placeholder 로 치환해 split 회피.

    반환: (마스킹된 텍스트, 원본 매칭 리스트 — 복원 시 순서대로 사용)
    """
    matches: list[str] = []

    def _capture(match: re.Match[str]) -> str:
        matches.append(match.group(0))
        return f"{_LEGAL_DATE_PLACEHOLDER}{len(matches) - 1}{_LEGAL_DATE_PLACEHOLDER}"

    masked = _LEGAL_DATE_PATTERN.sub(_capture, text)
    return masked, matches


def _restore_legal_dates(text: str, matches: list[str]) -> str:
    """`_mask_legal_dates` 의 placeholder 를 원본 매칭으로 복원."""
    if not matches:
        return text
    pattern = re.compile(
        re.escape(_LEGAL_DATE_PLACEHOLDER) + r"(\d+)" + re.escape(_LEGAL_DATE_PLACEHOLDER)
    )
    return pattern.sub(lambda m: matches[int(m.group(1))], text)


def _is_unbalanced_quote_or_paren(text: str) -> bool:
    """청크 끝이 인용문/괄호 중간인지 검사 (W5 4.3 따옴표/괄호 보호).

    검사 대상:
    - `"` (ASCII double quote) 갯수가 홀수
    - `(`/`[`/`{`/`「`/`『` 갯수 > `)`/`]`/`}`/`」`/`』` 갯수
    - `'` (single quote) 는 apostrophe 와 구분 어려움 → skip
    - 한국어 따옴표 `"` `"` `'` `'` 는 여는/닫는 짝이 명확해 갯수 비교 가능
    """
    # double quote (ASCII)
    if text.count('"') % 2 == 1:
        return True
    # 한국어 인용 따옴표 (여는/닫는 명시)
    if text.count("\u201c") > text.count("\u201d"):  # " > "
        return True
    if text.count("\u2018") > text.count("\u2019"):  # ' > '
        return True
    # 괄호 짝
    pairs = [
        ("(", ")"),
        ("[", "]"),
        ("{", "}"),
        ("\u300c", "\u300d"),  # 「 」
        ("\u300e", "\u300f"),  # 『 』
    ]
    for opener, closer in pairs:
        if text.count(opener) > text.count(closer):
            return True
    return False


def _apply_overlap(pieces: list[str]) -> list[str]:
    """인접 청크 사이에 마지막 `_OVERLAP_SIZE` 자 prefix overlap 부여.

    W4-Q-14 4.4 — 한 문장이 두 청크에 걸치면 dense 임베딩 양쪽 다 의미 부족 → 검색 score
    저하 방지. 한국어 RAG 표준 권고 50~150자 중간값 100자 채택.

    엣지:
    - 첫 조각은 prefix 없음
    - 이전 조각이 `_OVERLAP_SIZE` 미만이면 전체를 prefix 로 사용 (의미 손실 < 인덱싱 비용)
    - overlap 적용 후 `_MAX_SIZE` 초과하면 prefix 길이 축소 (max 보장 우선)
    """
    if len(pieces) <= 1:
        return pieces
    out: list[str] = [pieces[0]]
    for i in range(1, len(pieces)):
        prev = pieces[i - 1]
        cur = pieces[i]
        prefix = prev[-_OVERLAP_SIZE:] if len(prev) > _OVERLAP_SIZE else prev
        # _MAX_SIZE 보장 — overlap + 분리자 공백 포함 초과하면 prefix 축소.
        # 합성 길이 = len(prefix) + 1(공백) + len(cur)
        budget = _MAX_SIZE - len(cur) - 1
        if budget <= 0:
            out.append(cur)  # 현재 청크가 이미 _MAX_SIZE-1 이상이면 overlap 생략
            continue
        if len(prefix) > budget:
            prefix = prefix[-budget:]
        out.append(f"{prefix} {cur}".strip() if prefix else cur)
    return out


# ---------------------- 3차: 짧은 섹션 병합 ----------------------


def _looks_like_table_cell(text: str) -> bool:
    """W5 Day 3 4.6 — 표 셀로 의심되는 짧은 패턴 (병합 차단용).

    판정 기준 (어느 하나라도 충족):
    - DocxParser/MarkdownParser 의 표 텍스트 (` | ` separator) 포함
    - 길이 < 30자 AND 숫자/특수문자 비율 ≥ 0.5 (표 셀 통상 패턴)

    chunk_filter 의 table_noise 룰과 책임 분리 — 본 함수는 _merge_short_sections 에서
    인접 본문과의 병합 차단용 (분리). table_noise 는 검색 제외용.
    """
    if " | " in text:
        return True
    stripped = text.strip()
    if len(stripped) >= 30:
        return False
    non_ws = sum(1 for c in stripped if not c.isspace())
    if non_ws == 0:
        return False
    digit_punct = sum(
        1 for c in stripped
        if c.isdigit() or (not c.isalnum() and not c.isspace())
    )
    return digit_punct / non_ws >= 0.5


def _merge_short_sections(sections: list[ExtractedSection]) -> list[ExtractedSection]:
    merged: list[ExtractedSection] = []
    buf: ExtractedSection | None = None
    for section in sections:
        if buf is None:
            buf = section
            continue
        # W5 Day 3 4.6 — 표 셀 의심 패턴은 병합 차단 (본문 의미 오염 방지)
        if _looks_like_table_cell(buf.text) or _looks_like_table_cell(section.text):
            merged.append(buf)
            buf = section
            continue
        can_merge = (
            len(buf.text) < _MIN_MERGE_SIZE
            and buf.page == section.page
            and len(buf.text) + len(section.text) + 2 <= _MAX_SIZE
        )
        if can_merge:
            # 2026-05-09 — merge 시 양쪽 metadata dict-merge (section 우선) 로 보존.
            # S4-A D2 caption 전파 회귀 fix — 이전엔 default 빈 dict 로 채워져 caption
            # metadata 손실. 같은 vision page 의 sections 는 동일 caption 공유라
            # 충돌 시 section 우선이 안전 (vision_incremental flag 등도 보존).
            merged_metadata: dict = {**buf.metadata, **section.metadata}
            buf = ExtractedSection(
                text=f"{buf.text}\n\n{section.text}",
                page=buf.page,
                # W4-Q-14 4.5 — section.section_title 우선. 병합되는 쪽이 더 의미 있는 title
                # 일 가능성 (buf 가 None title 의 짧은 청크일 가능성 ↑) → KPI §13.1 채움 비율 ↑
                section_title=section.section_title or buf.section_title,
                bbox=None,  # 병합 시 bbox 합성은 근사가 어려워 None
                metadata=merged_metadata,
            )
        else:
            merged.append(buf)
            buf = section
    if buf is not None:
        merged.append(buf)
    return merged


# ---------------------- 레코드 변환 ----------------------


_VISION_TITLE_PREFIX = "(vision)"  # S4-A D2 — vision-derived chunk 식별 prefix


def _is_vision_derived(section: ExtractedSection) -> bool:
    """S4-A D2 — chunk text 합성 + caption metadata 주입 진입 조건.

    명세 §C 분기 — 둘 중 하나라도 참:
    - section.metadata 의 `vision_incremental` 키가 truthy (incremental reingest path)
    - section_title 이 `(vision)` prefix 로 시작 (extract.py vision_enrich path)

    둘 다 충족하지 않는 일반 chunk 는 기존 동작 100% 유지.
    """
    if section.metadata.get("vision_incremental"):
        return True
    title = section.section_title or ""
    return title.startswith(_VISION_TITLE_PREFIX)


def _compose_vision_text(
    base_text: str,
    *,
    table_caption: str | None,
    figure_caption: str | None,
) -> str:
    """S4-A D2 — vision-derived chunk text 합성 (명세 §C).

    규칙:
    - 둘 다 None → base_text 그대로 (skip)
    - 한쪽만 set → 해당 한 줄만 부착
    - 양쪽 set → 두 줄 모두 부착 (table 먼저)
    - 빈 줄 1개로 분리

    포맷: `{base}\n\n[표: {table}]\n[그림: {figure}]`
    """
    extras: list[str] = []
    if table_caption:
        extras.append(f"[표: {table_caption}]")
    if figure_caption:
        extras.append(f"[그림: {figure_caption}]")
    if not extras:
        return base_text
    return base_text + "\n\n" + "\n".join(extras)


def _to_chunk_records(
    *, doc_id: str, sections: list[ExtractedSection]
) -> list[ChunkRecord]:
    """청크 레코드 변환 — W4-Q-14 4.4 overlap 메타 기록.

    `metadata["overlap_with_prev_chunk_idx"]` — overlap 이 적용된 청크는 이전 청크의 idx
    를 기록 (디버깅·검색 결과 출처 추적용). overlap 없는 첫 청크는 메타 미기록.

    W25 D12 B3 (section_title prepend) 시도 후 dry-run 결과로 SKIP — 격차 정답 청크 (39, 43,
    44) 의 본문 head 가 이미 title 포함되어 prepend 회피, 효과 없음 + 인접 chunks (45, 46)
    prepend 로 G-S-009 더 악화 위험. work-log/2026-05-04 W25 D12 답변 생성 PoC.md 참조.

    S4-A D2 — vision-derived chunk (vision_incremental flag 또는 `(vision)` prefix)
    한정으로 `table_caption` / `figure_caption` 을 chunk.metadata 주입 + text 풍부화.
    v1 cache row 는 두 필드 None → 합성 skip 으로 기존 동작 보존.
    """
    records: list[ChunkRecord] = []
    for idx, section in enumerate(sections):
        metadata: dict = {}
        if idx > 0:
            # 2차 분할 결과 인접 조각이 모두 overlap 대상 — 단순화를 위해 모든 i>0 에 표시
            # TODO (W4 P3): 정확하게는 split 인접만 overlap, merge 결과 인접은 미적용.
            # 현재는 idx>0 에 일괄 표시 (디버깅 가시성 우선). 정확화는 split 단계에서
            # overlap 적용 여부를 ExtractedSection 메타에 전파 후 여기서 참조 권장.
            metadata["overlap_with_prev_chunk_idx"] = idx - 1

        # S4-A D2 — vision-derived chunk 한정 caption 메타 주입 + text 합성.
        # 일반 chunk 는 분기 미진입 → 기존 동작 100% 유지.
        table_caption: str | None = None
        figure_caption: str | None = None
        if _is_vision_derived(section):
            table_caption = section.metadata.get("table_caption")
            figure_caption = section.metadata.get("figure_caption")
            # None 이 아닌 경우만 chunk.metadata 에 주입. 양쪽 None 이면 키 자체 미주입.
            if table_caption is not None:
                metadata["table_caption"] = table_caption
            if figure_caption is not None:
                metadata["figure_caption"] = figure_caption
            # vision_incremental 플래그는 incremental path 호환을 위해 그대로 보존.
            if section.metadata.get("vision_incremental"):
                metadata["vision_incremental"] = True

        synthesized = _compose_vision_text(
            section.text,
            table_caption=table_caption,
            figure_caption=figure_caption,
        )

        # W25 D14+1 D1 — 한국어 NFC 정규화 (인제스트단).
        # query 측은 W25 D14 (commit 5eed8d4) 로 NFC 강제. chunks.text 도 NFC 강제로
        # sparse path (PGroonga Mecab 형태소) 의 NFD 매칭 fail 회피.
        # dense_vec (BGE-M3) 도 NFC 입력으로 인덱싱·검색 일관성 보장.
        # idempotent — 이미 NFC 인 텍스트는 변화 없음 (PDF/DOCX/PPTX 등).
        # 효과 큰 case: HWP/HWPX (한컴 파서가 NFD 로 추출하는 경향).
        text_nfc = unicodedata.normalize("NFC", synthesized)
        title_nfc = (
            unicodedata.normalize("NFC", section.section_title)
            if section.section_title
            else section.section_title
        )

        # 2026-05-10 — S4-B 엔티티 추출 (룰 기반, master plan §6 P1).
        # chunks.metadata.entities = {"dates": [...], "amounts": [...],
        # "percentages": [...], "identifiers": [...]}
        # 빈 entities (모든 카테고리 비어있음) 시 키 자체 미주입 — metadata 부풀림 회피.
        # 외부 의존성 0 (정규식만). search filter / boost 는 별도 sprint (ENV opt-in).
        try:
            from app.services.entity_extract import extract_entities

            entities = extract_entities(text_nfc)
            if not entities.is_empty():
                metadata["entities"] = entities.to_dict()
        except Exception:  # noqa: BLE001 — chunk 저장 차단 회피
            pass

        records.append(
            ChunkRecord(
                doc_id=doc_id,
                chunk_idx=idx,
                text=text_nfc,
                page=section.page,
                section_title=title_nfc,
                bbox=section.bbox,
                char_range=(0, len(text_nfc)),
                metadata=metadata,
                # dense_vec=None, sparse_json={} — Day 5 에 채움
            )
        )
    return records
