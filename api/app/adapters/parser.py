from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class ExtractedSection:
    text: str
    page: int | None = None
    section_title: str | None = None
    bbox: tuple[float, float, float, float] | None = None  # x0, y0, x1, y1 (PDF 좌표)


@dataclass(frozen=True)
class ExtractionResult:
    source_type: str  # "pdf" | "hwpx" | "hwp" | "docx" | "image" | "url"
    sections: list[ExtractedSection]
    raw_text: str
    warnings: list[str] = field(default_factory=list)  # 부분 실패 메시지


class DocumentParser(Protocol):
    """포맷별 문서 파서. 구현체마다 source_type 하나 담당.

    업로드 바이트를 Storage 에 저장한 후 다시 다운로드해 `parse()` 에 넘기는 흐름이므로,
    파일 경로가 아닌 `bytes` + 원본 파일명을 받는다.
    """

    source_type: str

    def can_parse(self, file_name: str, mime_type: str | None) -> bool: ...

    def parse(self, data: bytes, *, file_name: str) -> ExtractionResult: ...
