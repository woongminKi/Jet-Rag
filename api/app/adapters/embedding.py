from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class EmbeddingResult:
    dense: list[float]
    sparse: dict[str, float]  # token(str) → weight (BGE-M3 lexical weights)


class EmbeddingProvider(Protocol):
    """임베딩 공급자. BGE-M3 via HF Inference(기본) · OpenAI(스텁) · Upstage Solar(v2)."""

    dense_dim: int

    def embed(self, text: str) -> EmbeddingResult: ...

    def embed_batch(self, texts: list[str]) -> list[EmbeddingResult]: ...
