"""HWP 5.x 구포맷 파서 — 2단계 fallback 전략.

W2 명세 v0.3 §3.F. HWP 5.x 는 OLE2 (CFB) 컨테이너.

추출 전략 (DE-52, 2026-04-28 발견)
1. **1차**: `hwp5txt` CLI subprocess (pyhwp) — 정상 HWP 의 깔끔한 텍스트 출력
2. **2차 fallback**: `olefile` + `zlib` + record 파싱 — pyhwp 의 SummaryInformation
   파싱 버그 (`KeyError: 2` in `msoleprops.py`) 로 fail 하는 한국 정부 발행 HWP
   대응. BodyText/Section* 스트림을 직접 디코드해 PARA_TEXT (tagid=67) record 추출.

법제처/국가법령정보센터 등 한국 정부에서 발행하는 HWP 들은 SummaryInformation 의
PropertySet 에 `vt_type=2` (VT_I2) 같은 일부 코드를 사용하는데, pyhwp 0.1b15 는 이를
처리 못 함. fallback 으로 본문은 정상 추출.

설계
- 1차/2차 모두 timeout/safety 보장
- 임시 파일에 bytes 를 쓴 후 CLI 에 경로 전달 (1차)
- olefile 은 BytesIO 에서 직접 (2차)
- 둘 다 실패 시 명확한 에러 메시지 (사용자가 PDF/HWPX 로 변환 후 재시도 가능 안내)
"""

from __future__ import annotations

import io
import logging
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path, PurePosixPath

import olefile

from app.adapters.parser import ExtractedSection, ExtractionResult

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 30

# HWP 5 record tagid (HWPTAG_BEGIN=16 + offset)
_HWPTAG_PARA_HEADER = 66
_HWPTAG_PARA_TEXT = 67

# inline ctrl chars 가 차지하는 추가 byte 수 (UTF-16 16-bit + 14 bytes 추가 정보 = 16 bytes)
_INLINE_CTRL_CODES = frozenset(
    list(range(0x01, 0x0A)) + [0x0B, 0x0C] + list(range(0x0E, 0x20))
)


class Hwp5Parser:
    source_type = "hwp"

    def can_parse(self, file_name: str, mime_type: str | None) -> bool:
        ext = PurePosixPath(file_name).suffix.lower()
        return ext == ".hwp"

    def parse(self, data: bytes, *, file_name: str) -> ExtractionResult:
        warnings: list[str] = []

        # 1차: hwp5txt CLI
        text: str | None = None
        try:
            text = _hwp_to_text_via_cli(data, file_name=file_name)
        except RuntimeError as cli_exc:
            warnings.append(f"hwp5txt CLI 실패, olefile fallback 시도: {cli_exc}")
            logger.info(
                "hwp5txt CLI 실패 → olefile fallback 진입 (file=%s): %s",
                file_name,
                str(cli_exc)[:200],
            )
            # 2차: olefile + record 파싱
            try:
                text = _hwp_to_text_via_olefile(data)
            except Exception as ole_exc:
                raise RuntimeError(
                    f"HWP 추출 실패 — 1차 hwp5txt: {cli_exc} ; "
                    f"2차 olefile fallback: {ole_exc}. "
                    "이 파일을 PDF 또는 HWPX 로 변환 후 다시 업로드해 주세요."
                ) from ole_exc

        if not text or not text.strip():
            return ExtractionResult(
                source_type=self.source_type,
                sections=[],
                raw_text="",
                warnings=warnings + [
                    "HWP 추출 결과가 빈 문자열입니다 (텍스트 없음 또는 추출 실패)."
                ],
            )

        # 단락 분할 — \\n\\n 우선, fallback \\n
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if len(paragraphs) <= 1:
            paragraphs = [p.strip() for p in text.split("\n") if p.strip()]

        sections = [
            ExtractedSection(text=p, page=None, section_title=None, bbox=None)
            for p in paragraphs
        ]
        return ExtractionResult(
            source_type=self.source_type,
            sections=sections,
            raw_text=text.strip(),
            warnings=warnings,
        )


# ---------------------- 1차: hwp5txt CLI ----------------------


def _hwp_to_text_via_cli(data: bytes, *, file_name: str) -> str:
    cli_path = Path(sys.executable).parent / "hwp5txt"
    if not cli_path.exists():
        raise RuntimeError(
            f"hwp5txt CLI 를 찾을 수 없습니다: {cli_path} (pyhwp 설치 확인)"
        )

    with tempfile.NamedTemporaryFile(suffix=".hwp", delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name

    try:
        try:
            result = subprocess.run(
                [str(cli_path), tmp_path],
                capture_output=True,
                timeout=_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"hwp5txt timeout ({_TIMEOUT_SECONDS}s 초과): {file_name}"
            ) from exc

        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(
                f"hwp5txt 변환 실패 (rc={result.returncode}): {file_name}: {stderr.strip()[:200]}"
            )
        return result.stdout.decode("utf-8", errors="replace")
    finally:
        try:
            Path(tmp_path).unlink()
        except OSError:
            logger.warning("HWP 임시 파일 삭제 실패: %s", tmp_path)


# ---------------------- 2차: olefile + record 파싱 fallback ----------------------


def _hwp_to_text_via_olefile(data: bytes) -> str:
    """SummaryInformation 우회 — BodyText/Section* 직접 zlib + record 파싱."""
    parts: list[str] = []
    with io.BytesIO(data) as buf:
        ole = olefile.OleFileIO(buf)
        try:
            # FileHeader 의 36번째 byte 첫 비트 = 압축 플래그
            fh = ole.openstream("FileHeader").read()
            is_compressed = bool(fh[36] & 0x01) if len(fh) >= 37 else True

            sections = sorted(
                s for s in ole.listdir()
                if len(s) == 2 and s[0] == "BodyText" and s[1].startswith("Section")
            )
            for s in sections:
                stream_path = "/".join(s)
                raw = ole.openstream(stream_path).read()
                if is_compressed:
                    try:
                        raw = zlib.decompress(raw, -15)  # raw deflate
                    except zlib.error as exc:
                        logger.warning(
                            "section %s zlib 해제 실패 (스킵): %s", stream_path, exc
                        )
                        continue
                parts.append(_parse_section_records(raw))
        finally:
            ole.close()

    return "\n".join(p for p in parts if p)


def _parse_section_records(raw: bytes) -> str:
    """HWP 5 record 형식 파싱 → 단락별 텍스트 join.

    레코드 헤더 4 byte (little-endian uint32):
    - bits 0-9   tagid
    - bits 10-19 level
    - bits 20-31 size — 0xFFF 이면 다음 4 bytes 가 실제 size
    """
    paragraphs: list[str] = []
    pos, n = 0, len(raw)
    while pos + 4 <= n:
        header = int.from_bytes(raw[pos:pos + 4], "little")
        tagid = header & 0x3FF
        size = (header >> 20) & 0xFFF
        pos += 4
        if size == 0xFFF:
            if pos + 4 > n:
                break
            size = int.from_bytes(raw[pos:pos + 4], "little")
            pos += 4
        if pos + size > n:
            break
        payload = raw[pos:pos + size]
        pos += size

        if tagid == _HWPTAG_PARA_TEXT:
            text = _decode_para_text(payload)
            if text.strip():
                paragraphs.append(text)

    return "\n".join(paragraphs)


def _decode_para_text(payload: bytes) -> str:
    """PARA_TEXT payload → UTF-16LE 문자열. inline ctrl char 는 16 byte 차지하므로 스킵."""
    chars: list[str] = []
    pos, n = 0, len(payload)
    while pos + 2 <= n:
        ch = int.from_bytes(payload[pos:pos + 2], "little")
        if ch in _INLINE_CTRL_CODES:
            pos += 16  # ctrl(2) + 추가 정보(14)
        elif ch == 0x00:
            pos += 2  # null skip
        else:
            chars.append(chr(ch))
            pos += 2
    return "".join(chars)


def _hwp_to_text(data: bytes, *, file_name: str) -> str:
    """`hwp5txt` CLI 를 subprocess 로 호출. 실패 시 RuntimeError raise."""
    cli_path = Path(sys.executable).parent / "hwp5txt"
    if not cli_path.exists():
        raise RuntimeError(
            f"hwp5txt CLI 를 찾을 수 없습니다: {cli_path} (pyhwp 설치 확인)"
        )

    with tempfile.NamedTemporaryFile(suffix=".hwp", delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name

    try:
        try:
            result = subprocess.run(
                [str(cli_path), tmp_path],
                capture_output=True,
                timeout=_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"hwp5txt timeout ({_TIMEOUT_SECONDS}s 초과): {file_name}"
            ) from exc

        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(
                f"hwp5txt 변환 실패 (rc={result.returncode}): {file_name}: {stderr.strip()}"
            )
        return result.stdout.decode("utf-8", errors="replace")
    finally:
        try:
            Path(tmp_path).unlink()
        except OSError:
            logger.warning("HWP 임시 파일 삭제 실패: %s", tmp_path)
