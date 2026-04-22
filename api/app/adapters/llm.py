from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


class LLMProvider(Protocol):
    """LLM 텍스트 생성 공급자. Gemini 2.0 Flash(기본) · OpenAI(스텁) · Ollama(v2)."""

    def complete(
        self,
        messages: list[ChatMessage],
        *,
        images: list[bytes] | None = None,
        temperature: float = 0.2,
        json_mode: bool = False,
    ) -> str: ...
