"""S2 D3 — 운영 모드 (fast / default / precise) → vision page cap 매핑.

master plan §6 S2 D3. 사용자 결정 (Q-S2-1, Q-S2-1b, Q-S2-1e):
- default: page_cap = settings.vision_page_cap_per_doc (ENV default 50)
- fast: page_cap = JETRAG_VISION_MODE_FAST_CAP (default 10) — 메모/짧은 문서용
- precise: page_cap = 0 (페이지 무제한, 비용 한도까지)

전역 kill switch 우선 (A 안 채택):
- ENV `JETRAG_VISION_PAGE_CAP_PER_DOC=0` 또는 음수 → mode 무관 항상 0 (무한)
- 운영자가 cost cap 만으로 제어하고 싶을 때의 회복 토글.

mode 자체는 doc-level (chunk-level 미도입). 같은 doc 의 reingest 는
이전 `flags.ingest_mode` 를 default 로 prefill (router 책임).
"""

from __future__ import annotations

import os
from typing import Literal

IngestMode = Literal["fast", "default", "precise"]

# UI/router 검증용 상수 — 새 모드 추가 시 본 set 만 갱신.
INGEST_MODES: tuple[IngestMode, ...] = ("fast", "default", "precise")

_FAST_MODE_CAP_DEFAULT = 10
_FAST_MODE_CAP_ENV = "JETRAG_VISION_MODE_FAST_CAP"


def _parse_int(env_key: str, default: int) -> int:
    raw = os.environ.get(env_key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def resolve_page_cap(mode: IngestMode, settings) -> int:
    """mode + settings → 최종 page_cap.

    Args:
        mode: "fast" | "default" | "precise"
        settings: app.config.Settings (vision_page_cap_per_doc 만 참조)

    Returns:
        page_cap: extract.py / incremental.py 의 page cap 검사에 그대로 전달.
        - 0 또는 음수 = 무한 (page cap 영향 0)
        - 양수 = 단일 doc 안 vision call 페이지 cap

    우선순위 (A 안):
        1) settings.vision_page_cap_per_doc <= 0 → 0 (전역 kill switch)
        2) mode == "fast" → JETRAG_VISION_MODE_FAST_CAP (default 10)
        3) mode == "default" → settings.vision_page_cap_per_doc
        4) mode == "precise" → 0 (페이지 무제한)
    """
    if settings.vision_page_cap_per_doc <= 0:
        return 0
    if mode == "fast":
        return _parse_int(_FAST_MODE_CAP_ENV, _FAST_MODE_CAP_DEFAULT)
    if mode == "default":
        return settings.vision_page_cap_per_doc
    if mode == "precise":
        return 0
    raise ValueError(f"unknown ingest mode: {mode!r}")
