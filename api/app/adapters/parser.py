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
    """포맷별 문서 파서. 구현체마다 source_type 하나 담당."""

    source_type: str

    def can_parse(self, file_path: str, mime_type: str | None) -> bool: ...

    def parse(self, file_path: str) -> ExtractionResult: ...
