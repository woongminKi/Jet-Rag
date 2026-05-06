"""W25 D14 — RAGAS 단일 답변 평가 (Faithfulness + ResponseRelevancy).

run_ragas_auto.py 와 차이:
- 단일 (query, answer, contexts) 즉시 평가 (testset 생성 X)
- ground truth 없어도 동작 (Faithfulness/ResponseRelevancy 만)
- judge LLM = Gemini 2.0 Flash (master plan §4 — answer/ragas_judge 동일 모델)

graceful: ragas/langchain_google_genai 의존성 누락 또는 GEMINI_API_KEY 부재 시 ImportError/RuntimeError → 호출부에서 catch + skipped 응답.

Phase 1 S0 D2-A — RAGAS judge 는 LangChain wrapper (langchain_google_genai)
경유라 factory 직접 채택 X (factory 는 Gemini SDK 네이티브). ENV 분기로 향후
v1.5 OpenAI judge 추가 시 명시적 NotImplementedError 시나리오만 미리 고지.

D2-D — _JUDGE_MODEL 을 안정 모델 (gemini-2.5-flash) 로 회복. 2.0-flash 가
신규 사용자에게 deprecated 라 동작 불가. ENV `RAGAS_JUDGE_MODEL` 로 override 가능.
factory 의 `JETRAG_LLM_MODEL_RAGAS_JUDGE` 와 분리한 이유: ragas judge 는
LangChain wrapper 경유라 factory pipeline 외 경로.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_JUDGE_MODEL = os.environ.get("RAGAS_JUDGE_MODEL", "gemini-2.5-flash")
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

    # Phase 1 S0 D2-A — provider 분기 (RAGAS judge). 현재 Gemini 만 지원.
    # OpenAI judge 는 v1.5 에서 langchain_openai.ChatOpenAI + OpenAI embeddings
    # 어댑터 추가 시 활성화 — 구현 전까지는 NotImplementedError 시나리오로 명시.
    provider = os.environ.get("JETRAG_LLM_PROVIDER", "gemini").strip().lower()
    if provider == "openai":
        raise RagasUnavailable(
            "RAGAS judge OpenAI 분기 미구현 — v1.5 에서 추가. "
            "JETRAG_LLM_PROVIDER=gemini 로 사용하거나 ragas_eval.py 의 LangChain "
            "wrapper 분기를 추가하세요."
        )
    if provider != "gemini":
        raise RagasUnavailable(
            f"RAGAS judge: 알 수 없는 provider {provider!r}. gemini 또는 openai (미구현) 만 지원."
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
        from ragas.metrics import Faithfulness, ResponseRelevancy
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

        ds = Dataset.from_dict(
            {
                "user_input": [query],
                "response": [answer],
                "retrieved_contexts": [contexts],
            }
        )
        # W25 D14 — Faithfulness/ResponseRelevancy 만 LLM judge.
        # context_precision 은 BGE-M3 휴리스틱 (LLMContextPrecisionWithoutReference 가
        # Gemini 한국어에서 0점 false negative 일관 → 별도 계산).
        result = evaluate(
            dataset=EvaluationDataset.from_hf_dataset(ds),
            metrics=[Faithfulness(), ResponseRelevancy()],
            llm=judge_llm,
            embeddings=judge_emb,
        )
        scores = result.scores[0] if result.scores else {}

        # 검색 적합도 — BGE-M3 cosine (별도 함수 재사용)
        try:
            heur = evaluate_context_precision_only(query=query, contexts=contexts)
            ctx_precision = heur.metrics.context_precision
        except RagasUnavailable:
            ctx_precision = None

        metrics = RagasMetrics(
            faithfulness=_safe_float(scores.get("faithfulness")),
            answer_relevancy=_safe_float(scores.get("answer_relevancy")),
            context_precision=ctx_precision,
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


_HEURISTIC_JUDGE_LABEL = "bge-m3-cosine"


def evaluate_context_precision_only(
    *,
    query: str,
    contexts: list[str],
) -> RagasEvalResult:
    """검색 적합도 — BGE-M3 임베딩 cosine similarity 기반 휴리스틱 (W25 D14 갱신).

    원래 RAGAS LLMContextPrecisionWithoutReference 사용했지만 한국어 query/contexts
    에서 Gemini 2.5 Flash/Pro 모두 일관되게 0.0 반환 (영어 prompt + LLM 의 한국어
    "유용성" 판정 false negative). 동일 데이터로 BGE-M3 cosine 은 의미적 유사도
    0.5~0.9 정상 반환 → 휴리스틱으로 교체.

    알고리즘:
      1) BGE-M3 로 query + 각 context dense embedding (1024 dim)
      2) cosine similarity 계산
      3) ranking 가중 평균 — top-k 위치 가중 (검색 결과 ranking 보존)

    장점: LLM judge 비용 0, 빠름 (~1~2초, BGE-M3 HF API), 한국어 정확.
    한계: 임베딩 모델의 한계 그대로 — 의미적 매칭만, "useful for answer" 같은
    추론 X. 그래도 사용자 직관 ("관련 chunk 가 검색됐나") 에 잘 fit.
    """
    start = time.monotonic()
    if not contexts:
        return RagasEvalResult(
            metrics=RagasMetrics(context_precision=0.0),
            judge_model=_HEURISTIC_JUDGE_LABEL,
            took_ms=int((time.monotonic() - start) * 1000),
        )

    try:
        from app.adapters.impl.bgem3_hf_embedding import get_bgem3_provider
    except ImportError as exc:
        raise RagasUnavailable(f"BGE-M3 어댑터 누락: {exc}") from exc

    try:
        provider = get_bgem3_provider()
        q_vec = provider.embed_query(query)
        # embed_batch — 1회 HF API call 로 모든 contexts 처리 (latency ↓)
        ctx_results = provider.embed_batch(contexts)
        ctx_vecs = [r.dense for r in ctx_results]
    except Exception as exc:  # noqa: BLE001
        logger.exception("BGE-M3 embed 실패")
        raise RagasUnavailable(f"임베딩 실패: {exc}") from exc

    # cosine similarity (dim 동일 가정)
    import math

    def _cos(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) or 1.0
        nb = math.sqrt(sum(x * x for x in b)) or 1.0
        return dot / (na * nb)

    sims = [max(0.0, _cos(q_vec, cv)) for cv in ctx_vecs]
    # ranking 가중 평균 — k 번째 chunk weight = 1/log2(k+2) (DCG 패턴)
    weights = [1.0 / math.log2(i + 2) for i in range(len(sims))]
    weighted_sum = sum(s * w for s, w in zip(sims, weights))
    weight_total = sum(weights) or 1.0
    score = weighted_sum / weight_total

    logger.info(
        "context_precision (heuristic): score=%.3f sims=%s (query=%r, n=%d)",
        score,
        [f"{s:.2f}" for s in sims[:5]],
        query,
        len(sims),
    )

    return RagasEvalResult(
        metrics=RagasMetrics(context_precision=_safe_float(score)),
        judge_model=_HEURISTIC_JUDGE_LABEL,
        took_ms=int((time.monotonic() - start) * 1000),
    )


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
