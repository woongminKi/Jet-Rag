"""Content gate 스테이지 — PII / 워터마크 / 메신저 감지 (W2 §3.G).

8-stage 파이프라인의 chunk → content_gate → tag_summarize 자리.
저장만 하고 차단·경고 UI 는 W4 (S3 문서 상세) 에서 소비.

검출 항목 (DE-21 정책 + DE-44 결정)
- 주민등록번호: 13자리 + 앞 6자리 YYMMDD 유효성 (DE-21 b 경량)
- 카드번호: 4자리 그룹 4번 패턴 (16~19자리, DE-21 a 단순 매칭)
  - 한국 전화번호 (010-1234-5678 = 11자리) 와 자릿수로 명확히 구분
- 워터마크 키워드: 대외비 · 내부자료 · 보안 · CONFIDENTIAL · INTERNAL (대소문자 무시)
- 메신저 대화: ImageParser 가 ExtractionResult.metadata.vision_type 에 채워둔 값
- 계좌번호: **MVP 에서 제외** (DE-44 — 한국 은행 패턴 너무 다양해 오탐 위험. W3+ 재도입)

저장 스키마 (B-6)
- 문서 수준: `documents.flags.has_pii / has_watermark / third_party` (boolean)
  + `flags.watermark_hits: string[]` (검출 키워드 보존)
- chunk 수준: `chunks.metadata.pii_ranges: [[start, end], ...]`
  + `chunks.metadata.watermark_hits: string[]`
"""

from __future__ import annotations

import dataclasses
import logging
import re
from typing import Any

from app.adapters.parser import ExtractionResult
from app.adapters.vectorstore import ChunkRecord
from app.db import get_supabase_client
from app.ingest.jobs import stage

logger = logging.getLogger(__name__)

_STAGE = "content_gate"

# === PII 정규식 ===
# 주민등록번호: YYMMDD-NXXXXXX (대시 선택)
_RRN_PATTERN = re.compile(r"\b(\d{6})[-\s]?(\d{7})\b")

# 카드번호: 4자리×4그룹 (16~19자리). 마지막 그룹은 4~7자리 (Visa 16, Amex 15·17 등 변형 수용)
# 대시·공백 구분자 허용. 전화번호 (11자리) 와 명확히 분리.
_CARD_PATTERN = re.compile(
    r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4,7}\b"
)

# === 워터마크 키워드 ===
_WATERMARK_KEYWORDS = (
    "대외비",
    "내부자료",
    "보안",
    "CONFIDENTIAL",
    "INTERNAL",
)
_WATERMARK_PATTERN = re.compile(
    "|".join(re.escape(k) for k in _WATERMARK_KEYWORDS),
    re.IGNORECASE,
)


def run_content_gate_stage(
    job_id: str,
    *,
    doc_id: str,
    chunks: list[ChunkRecord],
    extraction: ExtractionResult,
) -> tuple[list[ChunkRecord], dict[str, Any]]:
    """chunks 의 metadata 에 pii_ranges/watermark_hits 부착, doc flags 머지.

    반환: (업데이트된 chunks, doc_flags_update dict)
    """
    with stage(job_id, _STAGE):
        has_pii_doc = False
        has_watermark_doc = False
        watermark_hits_doc: set[str] = set()
        updated_chunks: list[ChunkRecord] = []

        for chunk in chunks:
            pii_ranges = _detect_pii(chunk.text)
            watermark_hits = _detect_watermark(chunk.text)

            if pii_ranges:
                has_pii_doc = True
            if watermark_hits:
                has_watermark_doc = True
                watermark_hits_doc.update(watermark_hits)

            new_metadata = dict(chunk.metadata)
            if pii_ranges:
                new_metadata["pii_ranges"] = pii_ranges
            if watermark_hits:
                new_metadata["watermark_hits"] = watermark_hits

            updated_chunks.append(
                dataclasses.replace(chunk, metadata=new_metadata)
            )

        # 메신저 대화 (이미지) — ExtractionResult.metadata.vision_type 참조
        vision_type = (extraction.metadata or {}).get("vision_type")
        third_party = vision_type == "메신저대화"

        flags_update: dict[str, Any] = {
            "has_pii": has_pii_doc,
            "has_watermark": has_watermark_doc,
            "third_party": third_party,
        }
        if watermark_hits_doc:
            flags_update["watermark_hits"] = sorted(watermark_hits_doc)

        _merge_doc_flags(doc_id, flags_update)

        logger.info(
            "content_gate: doc=%s has_pii=%s has_watermark=%s third_party=%s "
            "chunks_with_pii=%d chunks_with_watermark=%d",
            doc_id,
            has_pii_doc,
            has_watermark_doc,
            third_party,
            sum(1 for c in updated_chunks if "pii_ranges" in c.metadata),
            sum(1 for c in updated_chunks if "watermark_hits" in c.metadata),
        )

        return updated_chunks, flags_update


# ---------------------- 내부 헬퍼 ----------------------


def _detect_pii(text: str) -> list[list[int]]:
    """텍스트에서 PII 매칭 위치 → [[start, end], ...] (정렬·중복 제거)."""
    ranges_raw: list[tuple[int, int]] = []

    # 주민번호 — 앞 6자리 YYMMDD 검증 (DE-21 b 경량)
    for m in _RRN_PATTERN.finditer(text):
        if _is_valid_yymmdd(m.group(1)):
            ranges_raw.append((m.start(), m.end()))

    # 카드번호 — 단순 매칭 (DE-21 a)
    for m in _CARD_PATTERN.finditer(text):
        ranges_raw.append((m.start(), m.end()))

    # 정렬 + 중복 제거
    deduped = sorted(set(ranges_raw))
    return [[s, e] for s, e in deduped]


def _is_valid_yymmdd(yymmdd: str) -> bool:
    """주민번호 앞 6자리가 유효 날짜인지 (월 1~12, 일 1~31). 윤년·말일 정밀 체크는 W3+."""
    if len(yymmdd) != 6 or not yymmdd.isdigit():
        return False
    mm = int(yymmdd[2:4])
    dd = int(yymmdd[4:6])
    return 1 <= mm <= 12 and 1 <= dd <= 31


def _detect_watermark(text: str) -> list[str]:
    """워터마크 키워드 매칭 결과 (정렬·중복 제거, 매칭된 표기 보존)."""
    hits: set[str] = set()
    for m in _WATERMARK_PATTERN.finditer(text):
        hits.add(m.group())
    return sorted(hits)


def _merge_doc_flags(doc_id: str, updates: dict[str, Any]) -> None:
    """기존 documents.flags 와 select-then-update 머지 (다른 flags 보존)."""
    client = get_supabase_client()
    resp = (
        client.table("documents")
        .select("flags")
        .eq("id", doc_id)
        .limit(1)
        .execute()
    )
    existing = (resp.data[0].get("flags") if resp.data else None) or {}
    merged = {**existing, **updates}
    client.table("documents").update({"flags": merged}).eq("id", doc_id).execute()
