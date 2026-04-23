"""Chunk 스테이지 — 기획서 §10.5 청킹 3단계.

- 1차: 섹션/헤딩 기준 분할 → PDF 는 이미 PyMuPDFParser 가 block 단위로 나눠 준다
- 2차: 800자 초과 섹션 → 문장 경계로 분할 (한국어 포함 휴리스틱)
- 3차: 200자 미만 섹션은 인접과 병합 (같은 페이지 내에서만, 최대 1,000자)
- 최대 1,000자/토큰 상한 준수. 섹션 경계 넘는 경우만 100자 overlap (Day 4.5 에 추가 예정)

메타 보존: chunk_idx · doc_id · page · section_title · char_range · bbox · metadata
"""

from __future__ import annotations

import re

from app.adapters.parser import ExtractedSection, ExtractionResult
from app.adapters.vectorstore import ChunkRecord
from app.ingest.jobs import stage

_STAGE = "chunk"

_TARGET_SIZE = 800
_MAX_SIZE = 1000
_MIN_MERGE_SIZE = 200

# 한국어·영문 문장 경계 휴리스틱
# - `.` `!` `?` 뒤 공백/줄바꿈
# - `다.` `요.` `까?` `죠.` — 한국어 종결어미 뒤 공백
# - 문단 break (`\n\n`)
_SENTENCE_END = re.compile(
    r"(?<=[.!?])\s+"
    r"|(?<=[다요까죠습니다])[.!?]?\s+"
    r"|\n\s*\n"
)


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
                )
            )
    return out


def _split_by_sentence(text: str) -> list[str]:
    """문장 경계 기준으로 `_TARGET_SIZE` 근방으로 분할. 문장 경계가 너무 없으면 강제 분할."""
    sentences = _SENTENCE_END.split(text)
    pieces: list[str] = []
    current = ""
    for sent in sentences:
        if not sent:
            continue
        if current and len(current) + len(sent) + 1 > _TARGET_SIZE:
            pieces.append(current.strip())
            current = sent
        else:
            current = f"{current} {sent}".strip() if current else sent
    if current:
        pieces.append(current.strip())

    # 여전히 `_MAX_SIZE` 초과하는 조각은 강제 분할 (문장 경계 없는 긴 텍스트 대비)
    final: list[str] = []
    for piece in pieces:
        if len(piece) <= _MAX_SIZE:
            final.append(piece)
            continue
        for i in range(0, len(piece), _TARGET_SIZE):
            final.append(piece[i : i + _TARGET_SIZE])
    return final


# ---------------------- 3차: 짧은 섹션 병합 ----------------------


def _merge_short_sections(sections: list[ExtractedSection]) -> list[ExtractedSection]:
    merged: list[ExtractedSection] = []
    buf: ExtractedSection | None = None
    for section in sections:
        if buf is None:
            buf = section
            continue
        can_merge = (
            len(buf.text) < _MIN_MERGE_SIZE
            and buf.page == section.page
            and len(buf.text) + len(section.text) + 2 <= _MAX_SIZE
        )
        if can_merge:
            buf = ExtractedSection(
                text=f"{buf.text}\n\n{section.text}",
                page=buf.page,
                section_title=buf.section_title or section.section_title,
                bbox=None,  # 병합 시 bbox 합성은 근사가 어려워 None
            )
        else:
            merged.append(buf)
            buf = section
    if buf is not None:
        merged.append(buf)
    return merged


# ---------------------- 레코드 변환 ----------------------


def _to_chunk_records(
    *, doc_id: str, sections: list[ExtractedSection]
) -> list[ChunkRecord]:
    records: list[ChunkRecord] = []
    for idx, section in enumerate(sections):
        records.append(
            ChunkRecord(
                doc_id=doc_id,
                chunk_idx=idx,
                text=section.text,
                page=section.page,
                section_title=section.section_title,
                bbox=section.bbox,
                char_range=(0, len(section.text)),
                # dense_vec=None, sparse_json={} — Day 5 에 채움
            )
        )
    return records
