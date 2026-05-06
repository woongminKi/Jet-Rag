"""Vision 캡셔너 Protocol.

이미지 파일 한 장 → `VisionCaption` 으로 변환한다. W2 이전 Gemini 2.5 Flash 가 유일한
Vision 공급자이며, OpenAI Vision 스텁은 W6 어댑터 스왑 시연 때 추가한다.

기획서 §10.4 의 단일 호출 4필드 JSON 프롬프트가 표준 계약. 이 Protocol 은 그 계약을
코드 레벨에서 박아둔다. `raw_confidence` 등 확장 필드는 W2 Day 2 Gemini 실응답 실측 후
v0.3 에서 결정 예정 (QA 검수 B-1).

ImageParser 가 EXIF transpose + orientation tag 제거 + 다운스케일 을 담당한 후
"정규화된 bytes" 를 전달하는 계약 — VisionCaptioner 구현체는 EXIF 를 다시 건드리지 않는다
(QA 검수 C-2).
"""

from dataclasses import dataclass
from typing import Literal, Protocol

VisionCategory = Literal[
    "문서", "스크린샷", "메신저대화", "화이트보드", "명함", "차트", "표", "기타"
]


@dataclass(frozen=True)
class VisionCaption:
    type: VisionCategory
    ocr_text: str
    caption: str
    structured: dict | None = None
    # Phase 1 S0 D1 — Gemini usage_metadata 파싱 결과 (마이그 014 컬럼과 매핑).
    # 키: prompt_tokens / image_tokens / output_tokens / thinking_tokens /
    #     estimated_cost / model_used. 미지원 SDK / 응답 누락 시 None.
    usage: dict | None = None


class VisionCaptioner(Protocol):
    def caption(self, image_bytes: bytes, *, mime_type: str) -> VisionCaption: ...
