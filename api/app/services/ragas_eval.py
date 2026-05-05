"""W25 D14 — RAGAS 단일 답변 평가 (Faithfulness + ResponseRelevancy).

run_ragas_auto.py 와 차이:
- 단일 (query, answer, contexts) 즉시 평가 (testset 생성 X)
- ground truth 없어도 동작 (Faithfulness/ResponseRelevancy 만)
- judge LLM = Gemini 2.5 Flash (기존 GeminiLLMProvider 와 같은 모델)

graceful: ragas/langchain_google_genai 의존성 누락 또는 GEMINI_API_KEY 부재 시 ImportError/RuntimeError → 호출부에서 catch + skipped 응답.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_JUDGE_MODEL = "gemini-2.5-flash"
_EMBEDDING_MODEL = os.environ.get("RAGAS_EMBEDDING_MODEL", "models/gemini-embedding-001")


@dataclass
class RagasMetrics:
    faithfulness: float | None = None
    answer_relevancy: float | None = None
    # ground truth 입력 시만 채워짐 (PoC v1 단계는 None)
    context_precision: float | None = None
    context_recall: float | None = None
    answer_correctness: float | None = None

    def to_dict(self) -> dict[str, float | None]:
        return {
            "faithfulness": self.faithfulness,
            "answer_relevancy": self.answer_relevancy,
            "context_precision": self.context_precision,
            "context_recall": self.context_recall,
            "answer_correctness": self.answer_correctness,
        }


@dataclass
class RagasEvalResult:
    metrics: RagasMetrics
    judge_model: str
    took_ms: int


class RagasUnavailable(RuntimeError):
    """RAGAS 의존성 누락 / API key 부재 / 평가 실패 - 호출부 graceful skip."""


def evaluate_single(
    *,
    query: str,
    answer: str,
    contexts: list[str],
) -> RagasEvalResult:
    """단일 답변에 대한 Faithfulness + ResponseRelevancy 측정.

    LLM judge = Gemini 2.5 Flash. contexts 가 비어 있으면 두 메트릭 모두 0/null.
    실패 시 RagasUnavailable raise — 호출부에서 catch.
    """
    start = time.monotonic()
    if not contexts:
        return RagasEvalResult(
            metrics=RagasMetrics(faithfulness=0.0, answer_relevancy=0.0),
            judge_model=_JUDGE_MODEL,
            took_ms=int((time.monotonic() - start) * 1000),
        )

    try:
        from datasets import Dataset
        from langchain_google_genai import (
            ChatGoogleGenerativeAI,
            GoogleGenerativeAIEmbeddings,
        )
        from ragas import EvaluationDataset, evaluate
        from ragas.embeddings import LangchainEmbeddingsWrapper
        from ragas.llms import LangchainLLMWrapper
        from ragas.metrics import (
            Faithfulness,
            LLMContextPrecisionWithoutReference,
            ResponseRelevancy,
        )
    except ImportError as exc:
        raise RagasUnavailable(f"의존성 누락: {exc}") from exc

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RagasUnavailable("GEMINI_API_KEY 미설정")

    try:
        judge_llm = LangchainLLMWrapper(
            ChatGoogleGenerativeAI(model=_JUDGE_MODEL, google_api_key=api_key)
        )
        judge_emb = LangchainEmbeddingsWrapper(
            GoogleGenerativeAIEmbeddings(model=_EMBEDDING_MODEL, google_api_key=api_key)
        )

        # 단일 row dataset
        ds = Dataset.from_dict(
            {
                "user_input": [query],
                "response": [answer],
                "retrieved_contexts": [contexts],
            }
        )
        result = evaluate(
            dataset=EvaluationDataset.from_hf_dataset(ds),
            metrics=[
                Faithfulness(),
                ResponseRelevancy(),
                LLMContextPrecisionWithoutReference(),
            ],
            llm=judge_llm,
            embeddings=judge_emb,
        )
        # result.scores 는 row 별 dict (단일 row 라 [0])
        scores = result.scores[0] if result.scores else {}
        metrics = RagasMetrics(
            faithfulness=_safe_float(scores.get("faithfulness")),
            answer_relevancy=_safe_float(scores.get("answer_relevancy")),
            # W25 D14 — "검색 적합도" — query+contexts 만으로 ranking 평가 (reference 불필요)
            context_precision=_safe_float(
                scores.get("llm_context_precision_without_reference")
            ),
        )
        return RagasEvalResult(
            metrics=metrics,
            judge_model=_JUDGE_MODEL,
            took_ms=int((time.monotonic() - start) * 1000),
        )
    except RagasUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("RAGAS evaluate 실패")
        raise RagasUnavailable(f"평가 실패: {exc}") from exc


def evaluate_context_precision_only(
    *,
    query: str,
    contexts: list[str],
) -> RagasEvalResult:
    """검색 적합도만 측정 (LLMContextPrecisionWithoutReference) — 답변 없이.

    /search 결과 페이지에서 LLM 답변 생성 비용 없이 "검색 chunks 가 query 에
    얼마나 잘 맞는가" 만 평가. judge LLM 호출 1개만 사용 → ~$0.003/평가.
    """
    start = time.monotonic()
    if not contexts:
        return RagasEvalResult(
            metrics=RagasMetrics(context_precision=0.0),
            judge_model=_JUDGE_MODEL,
            took_ms=int((time.monotonic() - start) * 1000),
        )

    try:
        from datasets import Dataset
        from langchain_google_genai import (
            ChatGoogleGenerativeAI,
            GoogleGenerativeAIEmbeddings,
        )
        from ragas import EvaluationDataset, evaluate
        from ragas.embeddings import LangchainEmbeddingsWrapper
        from ragas.llms import LangchainLLMWrapper
        from ragas.metrics import LLMContextPrecisionWithoutReference
    except ImportError as exc:
        raise RagasUnavailable(f"의존성 누락: {exc}") from exc

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RagasUnavailable("GEMINI_API_KEY 미설정")

    try:
        judge_llm = LangchainLLMWrapper(
            ChatGoogleGenerativeAI(model=_JUDGE_MODEL, google_api_key=api_key)
        )
        judge_emb = LangchainEmbeddingsWrapper(
            GoogleGenerativeAIEmbeddings(model=_EMBEDDING_MODEL, google_api_key=api_key)
        )
        # response 필드는 dummy — Faithfulness/Relevancy 호출 안 함
        ds = Dataset.from_dict(
            {
                "user_input": [query],
                "response": [""],
                "retrieved_contexts": [contexts],
            }
        )
        result = evaluate(
            dataset=EvaluationDataset.from_hf_dataset(ds),
            metrics=[LLMContextPrecisionWithoutReference()],
            llm=judge_llm,
            embeddings=judge_emb,
        )
        scores = result.scores[0] if result.scores else {}
        metrics = RagasMetrics(
            context_precision=_safe_float(
                scores.get("llm_context_precision_without_reference")
            ),
        )
        return RagasEvalResult(
            metrics=metrics,
            judge_model=_JUDGE_MODEL,
            took_ms=int((time.monotonic() - start) * 1000),
        )
    except RagasUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Context Precision evaluate 실패")
        raise RagasUnavailable(f"평가 실패: {exc}") from exc


def _safe_float(v) -> float | None:
    try:
        if v is None:
            return None
        f = float(v)
        if f != f:  # NaN
            return None
        return max(0.0, min(1.0, f))
    except Exception:  # noqa: BLE001
        return None
