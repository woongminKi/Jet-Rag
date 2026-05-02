"""Chunk 자동 필터링 룰 — G(3) 휴리스틱 마킹 (W3 v0.5 §3.G + W4-Q-15).

배경 (work-log/2026-04-29 청킹 정책 검토.md)
- chunk.py 본격 변경은 W4-Q-14 로 deferred. W3 안에서는 (1) 마이그레이션 004 의 flags
  컬럼 + (2) 본 모듈의 자동 마킹 + (3) backfill 스크립트로 가시성·검색 품질만 확보.
- diagnose_chunk_quality (G(1)) 의 휴리스틱 정의를 그대로 사용 — 임계값만 강화.

휴리스틱 (G(1) 와 정합)
- table_noise: 짧은 라인 ≥ _SHORT_LINE_RATIO_TH AND 숫자/특수문자 ≥ _DIGIT_PUNCT_RATIO_TH
- header_footer: 같은 doc 안에서 동일 텍스트 ≥ _HEADER_FOOTER_REPEAT_TH 회 + len < _HEADER_FOOTER_MAX_LEN
- empty: text.strip() == "" — W4-Q-15 (c) 추가, dead branch 보호 (chunk.py 가 빈 단락 skip 하지만
  향후 DOCX/PPTX 등 새 파서에서 빈 청크 발생 가능성 대비 인프라)
- extreme_short: 1 ≤ len(text.strip()) < _EXTREME_SHORT_LEN — W6 Day 3 추가, 표 셀 격리 부작용
  (단일 숫자/짧은 토큰 청크가 검색 ranking 차지) 회수. DE-65 본 적용 후 G-015 fail 분석 결과.

임계값 강화 (G(1) 대비 — 사용자 결정 2026-04-29)
- short_line_ratio: 0.70 → 0.90 (G(1) 진단용은 0.70, 본 자동 마킹은 0.90)
- digit_punct_ratio: 0.50 → 0.70 (마찬가지)
- 사유: 자동 마킹은 검색에서 제외되므로 false positive 비용이 진단보다 큼. 보수적으로.

마킹 결과
- chunk.flags["filtered_reason"] = "table_noise" | "header_footer" | "empty"
- search_hybrid_rrf RPC 의 WHERE `flags->>'filtered_reason' IS NULL` 가 자동 제외.

가시성 (W4-Q-15 G-2 보강)
- stage 로그에 마킹 카테고리별 카운트 + 비율 노출 (W3 Day 4 ship)
- W4-Q-15 추가: empty 카운트 노출 + 전체 마킹 비율 5% 초과 시 WARNING (false positive risk
  early signal)

위치 (파이프라인 내)
- chunk → **chunk_filter** → content_gate → tag_summarize → ...
- content_gate 가 metadata.pii_ranges / watermark_hits 만 추가 (flags 미터치) 라
  순서 정합 — flags 마킹은 보존됨.
"""

from __future__ import annotations

import dataclasses
import logging
import re
from collections import Counter

from app.adapters.vectorstore import ChunkRecord
from app.ingest.jobs import stage

logger = logging.getLogger(__name__)

_STAGE = "chunk_filter"

# ---------------------- 휴리스틱 임계값 (자동 마킹용 — G(1) 보다 강화) ----------------------

# 짧은 라인 기준 (chars). G(1) 진단과 동일 — 표 셀 통상 길이.
_SHORT_LINE_LEN = 30
# table_noise 자동 마킹 — G(1) (0.70 / 0.50) 보다 보수적 (false positive 비용 ↑).
_SHORT_LINE_RATIO_TH = 0.90
_DIGIT_PUNCT_RATIO_TH = 0.70

# header_footer 자동 마킹 — G(1) 진단과 동일 (이쪽은 보수적이라 강화 불필요).
_HEADER_FOOTER_REPEAT_TH = 3
_HEADER_FOOTER_MAX_LEN = 100

# W6 Day 3 — 표 셀 격리 부작용 청크 자동 마킹.
# 한국어/영문 알파벳 0 + 길이 < _EXTREME_SHORT_LEN → 검색 결과로 의미 0 ("2", "2,800" 등).
# 한국어/영문 1자라도 있으면 의미 있을 가능성 보존 ("변제충당" 4자 보존, "2,800원" 5자 보존).
_EXTREME_SHORT_LEN = 20

# 한국어/영문 글자 vs 숫자/특수문자 분류 (G(1) 동일 정규식).
_DIGIT_PUNCT_PATTERN = re.compile(r"[\d\W_]", re.UNICODE)


def run_chunk_filter_stage(
    job_id: str, *, doc_id: str, chunks: list[ChunkRecord]
) -> list[ChunkRecord]:
    """chunks 에 자동 필터링 룰 적용 — flags["filtered_reason"] 마킹 후 반환.

    부수효과: stage 로그만 (DB chunks 갱신은 load 스테이지에서). 호출 순서는
    pipeline.py 가 책임 — chunk 직후, content_gate 직전.
    """
    with stage(job_id, _STAGE):
        if not chunks:
            return chunks

        # 1) header_footer 후보 — 같은 doc 안에서 반복 짧은 텍스트.
        header_footer_texts = _detect_header_footer_texts(chunks)

        # 2) chunk 별 마킹.
        filtered_count: Counter[str] = Counter()
        out: list[ChunkRecord] = []
        for chunk in chunks:
            reason = _classify_chunk(chunk, header_footer_texts)
            if reason is None:
                out.append(chunk)
                continue
            new_flags = dict(chunk.flags)
            # 기존 flags 보존 (W4+ 다른 마커 추가 가능성 대비).
            new_flags["filtered_reason"] = reason
            out.append(dataclasses.replace(chunk, flags=new_flags))
            filtered_count[reason] += 1

        total = len(chunks)
        filter_ratio = (sum(filtered_count.values()) / total) if total else 0.0
        logger.info(
            "chunk_filter: doc=%s total=%d table_noise=%d header_footer=%d "
            "empty=%d extreme_short=%d filter_ratio=%.3f",
            doc_id,
            total,
            filtered_count["table_noise"],
            filtered_count["header_footer"],
            filtered_count["empty"],
            filtered_count["extreme_short"],
            filter_ratio,
        )
        # W4-Q-15 G-2 보강 — 5% 초과 시 false positive risk early signal
        if filter_ratio > 0.05:
            logger.warning(
                "chunk_filter: doc=%s 마킹 비율 %.1f%% > 5%% — false positive risk 검토 필요",
                doc_id,
                filter_ratio * 100,
            )
        return out


# ---------------------- 휴리스틱 ----------------------


def _classify_chunk(
    chunk: ChunkRecord, header_footer_texts: set[str]
) -> str | None:
    """단일 chunk 의 필터링 사유 — None 이면 통과."""
    text = chunk.text or ""
    stripped = text.strip()

    # W4-Q-15 (c) — 빈 청크 마킹. chunk.py 가 빈 단락 skip 해 dead branch 이지만
    # DOCX/PPTX 등 신규 파서 도입 시 빈 청크 발생 risk 대비 인프라.
    if not stripped:
        return "empty"

    # W6 Day 3 — 표 셀 격리 부작용 회수. 한국어/영문 알파벳 0 + 짧은 청크 (단일 숫자/통화·표 셀 등)
    # 이 검색 ranking 차지하는 케이스 (DE-65 본 적용 후 G-015 fail 분석) 차단.
    if len(stripped) < _EXTREME_SHORT_LEN and not _has_meaningful_letter(stripped):
        return "extreme_short"

    # header_footer 우선 — 짧은 반복 텍스트는 표보다 헤더/푸터 의도가 강함.
    if stripped in header_footer_texts:
        return "header_footer"

    # table_noise — 길이 0 / 너무 짧은 청크는 분류 의미 없음 (다른 단계에서 처리).
    if len(text) < 50:
        return None

    short_line_ratio, digit_punct_ratio = _line_metrics(text)
    if (
        short_line_ratio >= _SHORT_LINE_RATIO_TH
        and digit_punct_ratio >= _DIGIT_PUNCT_RATIO_TH
    ):
        return "table_noise"

    return None


def _has_meaningful_letter(text: str) -> bool:
    """한국어 음절 또는 영문 알파벳 1자라도 포함하면 True (의미 있는 단어 가능성)."""
    return any(c.isalpha() or "\uAC00" <= c <= "\uD7A3" for c in text)


def _line_metrics(text: str) -> tuple[float, float]:
    """(short_line_ratio, digit_punct_ratio) — G(1) 의 compute_chunk_metrics 와 동일 정의."""
    lines = text.split("\n")
    line_count = len(lines)
    if line_count == 0:
        return 0.0, 0.0
    short_lines = sum(1 for ln in lines if len(ln.strip()) < _SHORT_LINE_LEN)
    short_line_ratio = short_lines / line_count

    digit_punct_count = sum(
        1 for ch in text if _DIGIT_PUNCT_PATTERN.match(ch)
    )
    non_ws_total = sum(1 for ch in text if not ch.isspace())
    digit_punct_ratio = (
        digit_punct_count / non_ws_total if non_ws_total else 0.0
    )

    return short_line_ratio, digit_punct_ratio


def _detect_header_footer_texts(chunks: list[ChunkRecord]) -> set[str]:
    """같은 doc 안에서 동일 짧은 텍스트가 임계값 이상 반복되면 헤더/푸터 의심.

    chunks 는 한 번에 단일 doc_id 입력 (pipeline 호출 단위). G(1) 의
    detect_header_footer_candidates 와 같은 알고리즘 — 다만 본 단계는 doc 내부만.
    """
    text_counts: Counter[str] = Counter()
    for chunk in chunks:
        t = (chunk.text or "").strip()
        if t and len(t) < _HEADER_FOOTER_MAX_LEN:
            text_counts[t] += 1
    return {
        t for t, n in text_counts.items() if n >= _HEADER_FOOTER_REPEAT_TH
    }
