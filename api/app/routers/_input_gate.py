"""POST /documents 수신 단계의 매직바이트 검증 헬퍼.

기획서 §11.3 단계 A · W2 명세 v0.3 §3.A AC.

확장자 화이트리스트 (`_ALLOWED_EXTENSIONS`) 가 통과한 다음, 실제 파일 앞부분의
매직바이트가 확장자에 맞는지 비교한다. 목표는 ".png 로 rename 한 exe" 같은
혼동 차단 — 깊은 파서 검증은 ingest pipeline 의 extract 스테이지가 담당.

라이브러리는 `filetype` (pure-Python, OS 의존성 0) 사용.
HWP 5.x 의 OLE2 (CFB) 시그니처는 filetype 1.2.0 매처 미보유 → 본 모듈에서 직접 검증.
"""

from __future__ import annotations

import filetype
from fastapi import HTTPException, status

from app.adapters.impl.hwpml_parser import is_hwpml_bytes

# OLE2 / Compound File Binary 시그니처 — HWP 5.x, 옛 Office (.doc/.xls/.ppt) 공통.
# filetype 1.2.0 미인식이라 본 모듈에서 직접 prefix 검증.
_OLE2_PREFIX = b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"

# 확장자 → 매직바이트 식별 결과로 허용되는 mime 집합.
#   - 비어있는 set() = 시그니처가 없는 포맷 (평문 .txt/.md, OLE2 .hwp) → 별도 처리
#   - 키가 없는 확장자는 _ALLOWED_EXTENSIONS 화이트리스트에서 이미 차단된 상태
_EXT_TO_MIMES: dict[str, set[str]] = {
    ".pdf":  {"application/pdf"},
    ".png":  {"image/png"},
    ".jpg":  {"image/jpeg"},
    ".jpeg": {"image/jpeg"},
    ".heic": {"image/heic", "image/heif"},
    # ZIP 컨테이너 (DOCX/HWPX/PPTX) — filetype 1.2.0+ 가 buf prefix 에 따라
    # 'application/zip' 또는 deep MIME (vnd.openxmlformats-…) 둘 다 반환 가능.
    # 양쪽 모두 허용 — 본 게이트 목적은 "exe 가 .docx 위장" 차단이지 deep 분류가 아님.
    ".docx": {
        "application/zip",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    },
    ".hwpx": {"application/zip"},
    ".pptx": {
        "application/zip",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    },
    # OLE2 (HWP 5.x) — filetype 미인식이라 _OLE2_PREFIX 로 별도 검증
    ".hwp":  set(),
    # 평문 — 시그니처 없음, 매직바이트 검증 스킵
    ".txt":  set(),
    ".md":   set(),
}

# filetype 가 안정적으로 식별하려면 ≥ 262 byte 권장. 안전 마진으로 4KB.
HEAD_BYTES = 4096


def validate_magic(*, ext: str, raw_head: bytes) -> None:
    """확장자 ↔ 매직바이트 일치 검증. 불일치 시 HTTP 400.

    호출자 책임: `ext` 가 `_ALLOWED_EXTENSIONS` 화이트리스트 통과 상태여야 한다.
    `raw_head` 는 파일 앞부분 ≥ 262 byte (`HEAD_BYTES` 권장).
    """
    expected = _EXT_TO_MIMES.get(ext)
    if expected is None:
        # 화이트리스트와 매핑이 어긋난 케이스 — 코드 버그.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"지원되지 않는 확장자입니다: {ext}",
        )

    # HWP — OLE2 (5.x) 또는 HWPML XML (법제처 export 등) 둘 다 허용.
    # 어느 변형인지 결정은 extract dispatcher 가 raw bytes prefix 로 분기.
    if ext == ".hwp":
        if raw_head.startswith(_OLE2_PREFIX):
            return
        if is_hwpml_bytes(raw_head):
            return
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="HWP 5.x(OLE2) 또는 HWPML(XML) 시그니처가 아닙니다.",
        )

    # 평문 (.txt/.md) — 시그니처 없음, 매직바이트 검증 스킵
    if not expected:
        return

    kind = filetype.guess(raw_head)
    if kind is None:
        # filetype 미인식 — Mach-O exe 같은 케이스 포함. 보수적 reject.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"파일 형식을 식별할 수 없습니다. 확장자({ext})와 "
                "일치하는 시그니처가 필요합니다."
            ),
        )

    if kind.mime not in expected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"확장자({ext})와 파일 내용({kind.mime})이 일치하지 않습니다."
            ),
        )
