from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class ExtractedSection:
    text: str
    page: int | None = None
    section_title: str | None = None
    bbox: tuple[float, float, float, float] | None = None  # x0, y0, x1, y1 (PDF 좌표)
    # S4-A D2 — vision-derived section 의 caption 메타 전파용. PDF vision enrich
    # path 에서 ImageParser 가 `table_caption` / `figure_caption` 을 부착, chunk
    # 생성 단계가 chunk.metadata + text 합성에 활용. 비-vision 섹션은 빈 dict.
    # frozen + factory dict — 인스턴스마다 독립된 dict, 필드 재할당만 차단.
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ExtractionResult:
    source_type: str  # "pdf" | "hwpx" | "hwp" | "docx" | "image" | "url"
    sections: list[ExtractedSection]
    raw_text: str
    warnings: list[str] = field(default_factory=list)  # 부분 실패 메시지
    # 파서별 메타. ImageParser → {"vision_type": ...}, 그 외 파서는 미사용 가능.
    # content_gate 등 후속 스테이지가 cross-parser 메타에 의존할 때 escape hatch.
    metadata: dict = field(default_factory=dict)


class DocumentParser(Protocol):
    """포맷별 문서 파서. 구현체마다 source_type 하나 담당.

    업로드 바이트를 Storage 에 저장한 후 다시 다운로드해 `parse()` 에 넘기는 흐름이므로,
    파일 경로가 아닌 `bytes` + 원본 파일명을 받는다.
    """

    source_type: str

    def can_parse(self, file_name: str, mime_type: str | None) -> bool: ...

    def parse(self, data: bytes, *, file_name: str) -> ExtractionResult: ...
