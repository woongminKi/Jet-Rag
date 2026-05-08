"""Phase 1 S0 D2 — `vision_page_cache` lookup / upsert 헬퍼.

배경
    master plan §7.2 — PDF vision_enrich 가 reingest 마다 같은 페이지를 다시
    호출 → 비용·latency 누적. `(sha256, page, prompt_version)` 3-tuple 캐시로
    호출 0 회 보장.

설계
    - `_VISION_PROMPT_VERSION` 모듈 상수 — Gemini Vision prompt (`gemini_vision._PROMPT`)
      가 본질적으로 바뀌면 bump → 자동 invalidate.
    - lookup hit 시 `VisionCaption` 복원 (4필드: type/ocr_text/caption/structured).
    - upsert 는 `ON CONFLICT (sha256, page, prompt_version) DO NOTHING` 으로
      동시성 안전 (race 시 먼저 저장된 row 우선).
    - DB 부재 / 마이그 015 미적용 시 graceful — `vision_metrics` 와 동일 패턴.
    - 호출처는 PDF page-level (extract / incremental) — 단독 이미지 호출은 사용 X.

호출 위치
    - `app.ingest.stages.extract._enrich_pdf_with_vision` — 모든 페이지 루프
    - `app.ingest.incremental._vision_pages_with_sweep` — 누락 페이지 sweep

cache hit 시 vlog 정합성
    `vision_metrics.record_call` 은 `ImageParser.parse` 안에서만 호출 → hit 시 parse
    자체를 skip 하므로 vlog insert 0 (절감 측정 자연 달성).
"""

from __future__ import annotations

import logging
import os

from app.adapters.vision import VisionCaption

logger = logging.getLogger(__name__)

# ENV override 가능 — 운영자가 실험적으로 prompt 변경 + 새 캐시 키로 강제 invalidate 시.
# 기본값 'v2' = S4-A D1 (2026-05-09) — table_caption / figure_caption 2필드 추가 시점.
# v1 row 는 감사 목적으로 DB 에 보존 (DELETE X). v2 lookup 은 v1 row 와 매칭 안 됨 →
# cold-start reingest 시 일시적 비용 spike 발생 (운영 메모: work-log 참고).
_VISION_PROMPT_VERSION = os.environ.get("JETRAG_VISION_PROMPT_VERSION", "v2").strip() or "v2"

# 캐시 lookup / upsert 활성 여부 — 기본 활성. 단위 테스트나 cold start 회복 시 "0" 으로 disable.
_CACHE_ENV_KEY = "JETRAG_VISION_CACHE_ENABLED"

# 첫 1회만 warn (이후 debug) — 마이그 015 미적용 환경 노이즈 방지 (vision_metrics 패턴).
_first_warn_logged: bool = False


def get_prompt_version() -> str:
    """현재 prompt_version 상수. 단위 테스트가 monkey-patch 가능하도록 함수로 노출."""
    return _VISION_PROMPT_VERSION


def is_enabled() -> bool:
    """ENV 토글. 단위 테스트는 "0" 으로 cache 자체를 disable 가능."""
    return os.environ.get(_CACHE_ENV_KEY, "1") != "0"


def lookup(sha256: str, page: int) -> VisionCaption | None:
    """`(sha256, page, prompt_version)` 매칭 row → `VisionCaption` 복원.

    miss / 비활성 / DB 실패 시 None — 호출자는 정상 vision API 호출로 fallback.
    """
    if not is_enabled():
        return None
    if not sha256 or page is None:
        return None

    try:
        # lazy import — 단위 테스트가 supabase 의존성 없이도 동작 가능하도록.
        from app.db import get_supabase_client

        client = get_supabase_client()
        resp = (
            client.table("vision_page_cache")
            .select("result")
            .eq("sha256", sha256)
            .eq("page", page)
            .eq("prompt_version", get_prompt_version())
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 — DB 부재 / 마이그 015 미적용 graceful
        _warn_first(f"vision_page_cache lookup 실패 (graceful): {exc}")
        return None

    rows = resp.data or []
    if not rows:
        return None

    raw = rows[0].get("result")
    return _deserialize(raw)


def upsert(
    sha256: str,
    page: int,
    *,
    caption: VisionCaption,
    estimated_cost: float | None = None,
) -> None:
    """`(sha256, page, prompt_version)` 키로 row insert.

    `ON CONFLICT DO NOTHING` 으로 동시성 race 안전. 동일 키 재호출은 no-op.
    DB 실패는 graceful — 다음 reingest 시 다시 시도.
    """
    if not is_enabled():
        return
    if not sha256 or page is None:
        return

    row = {
        "sha256": sha256,
        "page": page,
        "prompt_version": get_prompt_version(),
        "result": _serialize(caption),
        "estimated_cost": estimated_cost,
    }
    try:
        from app.db import get_supabase_client

        client = get_supabase_client()
        # supabase-py 는 PostgREST 의 Prefer: resolution=ignore-duplicates 로 ON CONFLICT
        # DO NOTHING 동등 동작 — `on_conflict` 컬럼 명시 + ignore_duplicates=True.
        (
            client.table("vision_page_cache")
            .upsert(
                row,
                on_conflict="sha256,page,prompt_version",
                ignore_duplicates=True,
            )
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 — DB 부재 / 015 미적용 graceful
        _warn_first(f"vision_page_cache upsert 실패 (graceful): {exc}")


def _serialize(caption: VisionCaption) -> dict:
    """VisionCaption → JSONB row. usage 는 별도 컬럼 영역이라 미저장.

    S4-A D1 — table_caption / figure_caption 추가 (None 도 그대로 직렬화 →
    JSONB 의 null 보존). v2 prompt_version 과 함께 새 row 로 적재.
    """
    return {
        "type": caption.type,
        "ocr_text": caption.ocr_text,
        "caption": caption.caption,
        "structured": caption.structured,
        "table_caption": caption.table_caption,
        "figure_caption": caption.figure_caption,
    }


def _deserialize(raw: object) -> VisionCaption | None:
    """JSONB → VisionCaption. 잘못된 row 는 graceful None (강제 schema migration X).

    S4-A D1 — v1 row (table_caption/figure_caption 키 부재) 도 graceful 복원
    가능하지만, 실제로는 v2 lookup 이 v1 row 와 prompt_version 에서 미스매치 →
    여기까지 도달하지 않음. 그래도 외부에서 직접 _deserialize 호출 시 안전을 위해
    str/None 안전 캐스팅 유지.
    """
    if not isinstance(raw, dict):
        return None
    cap_type = raw.get("type")
    ocr_text = raw.get("ocr_text") or ""
    caption_text = raw.get("caption") or ""
    structured = raw.get("structured")
    if not isinstance(cap_type, str):
        return None

    # S4-A D1 — table_caption / figure_caption 안전 캐스팅 (str/None).
    table_caption = raw.get("table_caption")
    if not isinstance(table_caption, str) or not table_caption.strip():
        table_caption = None
    figure_caption = raw.get("figure_caption")
    if not isinstance(figure_caption, str) or not figure_caption.strip():
        figure_caption = None

    # VisionCategory Literal 검증은 호출자 (image_parser) 가 type 을 그대로 사용 → 느슨하게 통과.
    return VisionCaption(
        type=cap_type,  # type: ignore[arg-type]
        ocr_text=ocr_text,
        caption=caption_text,
        structured=structured if isinstance(structured, dict) else None,
        usage=None,  # cache hit 은 새 호출이 없으므로 usage 없음.
        table_caption=table_caption,
        figure_caption=figure_caption,
    )


def _warn_first(msg: str) -> None:
    """첫 1회만 warn, 이후는 debug — vision_metrics 의 _first_persist_warn_logged 패턴."""
    global _first_warn_logged
    if not _first_warn_logged:
        _first_warn_logged = True
        logger.warning(
            "%s — 마이그 015(vision_page_cache) 적용 후 자동 회복.", msg
        )
    else:
        logger.debug(msg)


def _reset_first_warn_for_test() -> None:
    """단위 테스트용 — 첫 warn flag 초기화."""
    global _first_warn_logged
    _first_warn_logged = False
