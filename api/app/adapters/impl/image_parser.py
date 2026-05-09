"""이미지 파서 — `VisionCaptioner` 를 composition 해 ExtractionResult 를 생성.

W2 명세 v0.3 §3.D · §3.B (Vision 어댑터 분리 DE-19).

책임 분리 (vision.py 모듈 docstring 의 C-2 결정사항)
- ImageParser: EXIF transpose + 단변 1024px 다운스케일 + JPEG 인코딩 (포맷 통일)
- VisionCaptioner: 정규화된 bytes 만 받아 Gemini 호출

HEIC/HEIF 정책
- Gemini 2.5 Flash 가 HEIC 직접 지원 (DE-17) — Pillow 디코드 불요
- 다운스케일 스킵, raw bytes 그대로 VisionCaptioner 에 전달
- pillow-heif 등 추가 의존성 회피

ExtractionResult 매핑
- sections[0]: 분류 + caption ("[type] caption" 형식, section_title 에 분류 표기)
- sections[1]: ocr_text (있는 경우만)
- structured 는 chunk metadata 로 진입할 때 별도 처리 예정 (현재 raw_text 미포함)
"""

from __future__ import annotations

import io
import logging
from pathlib import PurePosixPath

from PIL import Image, ImageOps

from app.adapters.impl.gemini_vision import GeminiVisionCaptioner
from app.adapters.parser import ExtractedSection, ExtractionResult
from app.adapters.vision import VisionCaption, VisionCaptioner
from app.services import vision_cache, vision_metrics
from app.services.quota import is_quota_exhausted

logger = logging.getLogger(__name__)

_MAX_SHORT_SIDE = 1024  # 명세 §15.2 DE-06: max 1024px 단변

# 확장자 → mime fallback (UploadFile 의 content_type 이 누락된 경우)
_EXT_TO_MIME: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".webp": "image/webp",
}


class ImageParser:
    source_type = "image"

    def __init__(self, captioner: VisionCaptioner | None = None) -> None:
        self._captioner = captioner or GeminiVisionCaptioner()

    def can_parse(self, file_name: str, mime_type: str | None) -> bool:
        ext = PurePosixPath(file_name).suffix.lower()
        if ext in _EXT_TO_MIME:
            return True
        return bool(mime_type and mime_type.startswith("image/"))

    def parse(
        self,
        data: bytes,
        *,
        file_name: str,
        source_type: str | None = None,
        doc_id: str | None = None,
        page: int | None = None,
        sha256: str | None = None,
    ) -> ExtractionResult:
        """이미지 → ExtractionResult.

        `source_type` (W16 Day 4 #90):
            None 시 cls.source_type ('image') 사용. 호출자 (PDF 스캔 rerouting /
            PPTX rerouting / PPTX augment) 가 'pdf_scan' / 'pptx_rerouting' /
            'pptx_augment' 명시 → vision_usage_log 의 source_type 컬럼에 정확 기록.

        `doc_id` / `page` (Phase 1 S0 D1 — 마이그 014):
            pdf_vision_enrich 같은 페이지 단위 호출처가 명시 — vision_usage_log 의
            doc_id/page 컬럼에 기록. 모두 default None → 단독 이미지 호출 영향 0.

        `sha256` (Phase 1 S0 D2 — 마이그 015 vision_page_cache):
            PDF page-level 호출 (pdf_vision_enrich / incremental_vision) 만 전달 →
            (sha256, page, prompt_version) 키로 cache lookup → hit 시 Vision API 호출 0.
            None 이거나 page None 이면 cache skip → 단독 이미지 호출 영향 0.
        """
        ext = PurePosixPath(file_name).suffix.lower()
        guessed_mime = _EXT_TO_MIME.get(ext, "image/jpeg")
        warnings: list[str] = []
        effective_source_type = source_type or self.source_type

        # Phase 1 S0 D2 — vision_page_cache hit 시 fast path: Vision API 호출 0,
        # vision_metrics 도 skip (자연 절감 측정).
        cached_caption: VisionCaption | None = None
        if sha256 and page is not None:
            cached_caption = vision_cache.lookup(sha256, page)

        if cached_caption is not None:
            return self._compose_result(cached_caption, warnings=warnings)

        # HEIC/HEIF → Gemini 직접 전달 (Pillow 디코드 회피)
        if ext in (".heic", ".heif"):
            normalized_bytes = data
            normalized_mime = guessed_mime
        else:
            normalized_bytes, normalized_mime, norm_warnings = _normalize(data, guessed_mime)
            warnings.extend(norm_warnings)

        # W8 Day 4 — Vision 호출 카운트 (한계 #29). raise 도 error 로 기록 후 재 raise.
        # W11 Day 1 — quota 시점 추적 (한계 #38 lite) — fast-fail 시점만 정확 capture.
        # W15 Day 3 — DB write-through (vision_usage_log).
        # W16 Day 4 — source_type 명시 (한계 #90).
        # Phase 1 S0 D1 — caption.usage 전달 + doc_id/page 전달 (마이그 014).
        try:
            caption = self._captioner.caption(
                normalized_bytes, mime_type=normalized_mime
            )
        except Exception as exc:
            # P2 — fail path retry_attempt: gemini_vision 이 exc 에 attribute 첨부.
            vision_metrics.record_call(
                success=False,
                quota_exhausted=is_quota_exhausted(exc),
                error_msg=str(exc),
                source_type=effective_source_type,
                doc_id=doc_id,
                page=page,
                retry_attempt=getattr(exc, "_jetrag_retry_attempt", None),
            )
            raise
        # P2 — success path retry_attempt: caption.usage 의 retry_attempt 키 사용.
        retry_attempt = (caption.usage or {}).get("retry_attempt")
        vision_metrics.record_call(
            success=True,
            source_type=effective_source_type,
            usage=caption.usage,
            doc_id=doc_id,
            page=page,
            retry_attempt=retry_attempt,
        )

        # Phase 1 S0 D2 — 성공한 호출은 vision_page_cache 에 upsert.
        # ON CONFLICT DO NOTHING 으로 race 안전 (동시 호출 시 먼저 저장된 row 우선).
        if sha256 and page is not None:
            estimated_cost = (caption.usage or {}).get("estimated_cost")
            vision_cache.upsert(
                sha256,
                page,
                caption=caption,
                estimated_cost=estimated_cost if isinstance(estimated_cost, (int, float)) else None,
            )

        return self._compose_result(caption, warnings=warnings)

    def _compose_result(
        self,
        caption: VisionCaption,
        *,
        warnings: list[str],
    ) -> ExtractionResult:
        """`VisionCaption` → ExtractionResult 합성.

        cache hit / miss 모두 동일 sections 구조 보장. 분기 0 = 검색 결과 동등성.
        """

        sections: list[ExtractedSection] = []
        # caption section — 분류 + 한국어 한 줄 요약
        # S4-A D2 — caption 두 필드 (table_caption / figure_caption) 가 set 이면
        # section.metadata 로 부착. PDF vision enrich path 가 vision-derived chunk
        # metadata + text 합성에 활용. 둘 다 None 이면 키 미주입 (graceful — 기존
        # 비-vision 흐름과 동일하게 빈 dict 유지).
        # 2026-05-09 — caption_metadata 를 OCR / action_items section 에도 broadcast.
        # 같은 vision page 의 모든 sections 가 동일 caption metadata 공유 → chunks
        # 단계가 OCR chunk 에도 caption text 합성 + chunk.metadata 주입. caption gap
        # full 회수 + dense/lex 매칭 효과 4~5배 (기존 caption section 1 chunk 한정).
        caption_metadata: dict = {}
        if caption.table_caption is not None:
            caption_metadata["table_caption"] = caption.table_caption
        if caption.figure_caption is not None:
            caption_metadata["figure_caption"] = caption.figure_caption
        sections.append(
            ExtractedSection(
                text=f"[{caption.type}] {caption.caption}".strip(),
                page=None,
                section_title=f"이미지 분류: {caption.type}",
                bbox=None,
                metadata=dict(caption_metadata),
            )
        )
        # OCR section — 텍스트 있을 때만
        ocr_clean = caption.ocr_text.strip()
        if ocr_clean:
            sections.append(
                ExtractedSection(
                    text=ocr_clean,
                    page=None,
                    section_title="OCR 텍스트",
                    bbox=None,
                    metadata=dict(caption_metadata),
                )
            )

        # W13 Day 1 — US-07 화이트보드 action_items 별도 section.
        # structured.action_items 가 list 면 검색 가능한 형태로 변환 (불릿 라인).
        # 화이트보드 외 type (명함·차트·표) 의 structured 도 향후 확장 가능 (현재는 화이트보드만).
        action_items = _extract_action_items(caption.structured)
        if action_items:
            bullet_text = "\n".join(f"- {item}" for item in action_items)
            sections.append(
                ExtractedSection(
                    text=bullet_text,
                    page=None,
                    section_title="액션 아이템",
                    bbox=None,
                    metadata=dict(caption_metadata),
                )
            )

        raw_text = "\n\n".join(s.text for s in sections)
        return ExtractionResult(
            source_type=self.source_type,
            sections=sections,
            raw_text=raw_text,
            warnings=warnings,
            metadata={"vision_type": caption.type},  # content_gate 의 메신저대화 감지용
        )


def _normalize(data: bytes, mime: str) -> tuple[bytes, str, list[str]]:
    """EXIF transpose + 단변 1024px 다운스케일 + 포맷 통일.

    반환
    - (정규화 bytes, mime, warnings)
    - 디코드 실패 시 raw bytes 그대로 + warning. Vision 호출 실패는 별도 경로 (caller 가 raise).
    """
    warnings: list[str] = []
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"이미지 디코드 실패, raw bytes 그대로 사용: {exc}")
        logger.warning("이미지 디코드 실패: %s", exc)
        return data, mime, warnings

    try:
        # EXIF orientation 처리 (폰 카메라 회전 사진)
        transposed = ImageOps.exif_transpose(img)
        if transposed is not None:
            img = transposed
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"EXIF transpose 실패 (계속 진행): {exc}")
        logger.warning("EXIF transpose 실패: %s", exc)

    # 단변 다운스케일 — 단변 ≤ _MAX_SHORT_SIDE 보장
    min_side = min(img.width, img.height)
    if min_side > _MAX_SHORT_SIDE:
        ratio = _MAX_SHORT_SIDE / min_side
        new_size = (max(1, int(img.width * ratio)), max(1, int(img.height * ratio)))
        img = img.resize(new_size, Image.Resampling.LANCZOS)

    # 포맷 통일 — alpha 가 의미 있으면 PNG, 그 외엔 JPEG (Gemini 토큰 비용 절약)
    buf = io.BytesIO()
    has_meaningful_alpha = img.mode in ("RGBA", "LA") and _has_transparency(img)
    if has_meaningful_alpha:
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue(), "image/png", warnings

    if img.mode != "RGB":
        img = img.convert("RGB")
    img.save(buf, format="JPEG", quality=85, optimize=True)
    return buf.getvalue(), "image/jpeg", warnings


def _has_transparency(img: Image.Image) -> bool:
    """alpha 채널이 실제로 비-255 값을 포함하는지 빠른 검사."""
    if img.mode not in ("RGBA", "LA"):
        return False
    alpha = img.getchannel("A")
    extrema = alpha.getextrema()
    # extrema = (min, max). min < 255 이면 투명 픽셀 존재
    return extrema[0] < 255


def _extract_action_items(structured: dict | None) -> list[str]:
    """W13 Day 1 — US-07 회수: structured.action_items 추출 + 정규화.

    Gemini Vision 이 화이트보드 type 시 `{"action_items": [...]}` 반환 (gemini_vision._PROMPT).
    list of str 만 보존 — dict / None / 빈 문자열 항목 제외.
    """
    if not isinstance(structured, dict):
        return []
    raw = structured.get("action_items")
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, str):
            cleaned = item.strip()
            if cleaned:
                out.append(cleaned)
        elif isinstance(item, dict):
            # {task, owner, due_date} 같은 nested object 도 한 줄로 변환
            parts = [str(v).strip() for v in item.values() if v]
            if parts:
                out.append(" · ".join(parts))
    return out
