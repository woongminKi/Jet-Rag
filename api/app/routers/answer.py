"""W25 D12 — `/answer` 라우터: LLM RAG 답변 생성 PoC.

흐름
----
1) `/search` 와 동일 RPC (`search_hybrid_rrf`) 로 top-K chunks 수집
   - dense (BGE-M3) + sparse (PGroonga OR query) RRF
   - 단일 문서 스코프 (`doc_id`) 또는 전 doc 스코프 모두 지원
2) chunks 본문을 한국어 prompt 로 구성 → factory 가 결정한 LLM 호출
3) 답변 + 출처 chunk_id 반환

설계 결정 (PoC, W25 D12 자율 결정 — work-log 명시):
- Q1 endpoint 분리 — /answer (search 와 분리, quota 보호)
- Q2 search 로직 재사용 — search router 호출 대신 직접 RPC 호출 (PoC minimal,
  search router 600줄의 검색·필터 로직 재활용은 v1.5 통합 시점)
- Q3 prompt 한국어 + faithfulness 보장 — 검색 결과에 없는 내용 추측 금지
- Q4 출처 명시 — 응답에 sources: [{chunk_id, doc_id, doc_title, chunk_idx, page}]
- Q5 model — factory.get_llm_provider("answer") 가 결정 (master plan §4 = 2.0-flash)
- Q6 동기 호출 — streaming 은 v1.5 이후
- Q7 search 0건 → "제공된 자료에서 관련 정보를 찾지 못했습니다" 답변 (LLM 호출 회피)
- D2-D 갱신 — 응답의 model 필드는 LLM 인스턴스의 model property 동적 표시.

명세
- 의존성 추가 0 (기존 LLMProvider + supabase RPC + bgem3 어댑터 재사용)
- 마이그레이션 0
"""

from __future__ import annotations

import logging
import time
import unicodedata
from functools import lru_cache

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from app.adapters.factory import get_llm_provider
from app.adapters.impl.bgem3_hf_embedding import (
    get_bgem3_provider,
    is_transient_hf_error,
)
from app.adapters.llm import ChatMessage, LLMProvider
from app.config import get_settings
from app.db import get_supabase_client
from app.routers.search import _build_pgroonga_query
from app.services.quota import is_quota_exhausted

logger = logging.getLogger(__name__)
router = APIRouter(tags=["answer"])

_MAX_QUERY_LEN = 200
_DEFAULT_TOP_K = 5
_MAX_TOP_K = 10
_RRF_K = 60
_RPC_TOP_K = 50
# D2-D — 응답 schema `model` 필드는 LLM 인스턴스 `model` property 동적 표시.
# 검색 결과 0 (LLM 호출 회피) 시 호출 회피로 인스턴스를 만들지 않으므로 fallback 필요.
_LLM_MODEL_FALLBACK = "gemini-2.0-flash"
# 청크 본문 prompt 주입 시 chunks 개당 최대 글자 (긴 chunk 절단). prompt token 폭주 방지.
_CHUNK_TEXT_MAX = 1200


def _resolve_model_label(llm: LLMProvider | None) -> str:
    """응답 schema 표시용 모델 ID — provider 인스턴스의 model 속성 우선.

    검색 0건으로 LLM 호출 회피 시 None → factory 가 결정할 default 모델로 fallback.
    Protocol 에 model 속성이 없을 수도 있어 getattr default.
    """
    if llm is None:
        return _LLM_MODEL_FALLBACK
    return getattr(llm, "model", None) or getattr(llm, "_model", None) or _LLM_MODEL_FALLBACK


# Phase 1 S0 D2-A — module-level singleton 제거 + lazy factory 경유.
# ENV (JETRAG_LLM_PROVIDER) 1줄로 OpenAI/Gemini 전환. JETRAG_LLM_MODEL_ANSWER override 가능.
@lru_cache(maxsize=1)
def _get_llm() -> LLMProvider:
    return get_llm_provider("answer")


class AnswerSource(BaseModel):
    chunk_id: str
    doc_id: str
    doc_title: str | None
    chunk_idx: int
    page: int | None
    section_title: str | None
    score: float
    snippet: str  # chunk 본문 앞부분 (UI 표시용)


class QueryParsedInfo(BaseModel):
    has_dense: bool
    has_sparse: bool
    dense_hits: int
    sparse_hits: int
    fused: int


class AnswerResponse(BaseModel):
    query: str
    answer: str
    sources: list[AnswerSource]
    has_search_results: bool
    model: str
    took_ms: int
    query_parsed: QueryParsedInfo


def _gather_chunks(
    *, query: str, doc_id: str | None, top_k: int, user_id: str
) -> tuple[list[dict], dict]:
    """검색 RPC 호출 → top_k chunks (chunks 본문 + documents 메타) + query_parsed.

    /search 라우터의 RPC 호출과 동일 패턴. dense fail (transient HF) 시 sparse-only.
    PoC 단계 — search 의 메타 필터·mode 분기는 미사용 (단일 query, 단일 user, doc_id 옵션).
    """
    client = get_supabase_client()
    pg_q = _build_pgroonga_query(query)

    dense_vec: list[float] | None = None
    try:
        dense_vec = get_bgem3_provider().embed_query(query)
    except Exception as exc:  # noqa: BLE001
        if is_transient_hf_error(exc):
            logger.warning("answer: HF transient → sparse-only fallback: %s", exc)
        else:
            logger.exception("answer: HF 영구 실패 — 503")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="검색 일시 오류 — 임베딩 서비스에 연결할 수 없습니다.",
                headers={"Retry-After": "60"},
            ) from exc

    if dense_vec is not None:
        rpc = client.rpc(
            "search_hybrid_rrf",
            {
                "query_text": pg_q,
                "query_dense": dense_vec,
                "k_rrf": _RRF_K,
                "top_k": _RPC_TOP_K,
                "user_id_arg": user_id,
            },
        ).execute()
    else:
        rpc = client.rpc(
            "search_sparse_only_pgroonga",
            {"query_text": pg_q, "user_id_arg": user_id, "top_k": _RPC_TOP_K},
        ).execute()
    rows = rpc.data or []

    if doc_id:
        rows = [r for r in rows if r.get("doc_id") == doc_id]

    dense_hits = sum(1 for r in rows if r.get("dense_rank") is not None)
    sparse_hits = sum(1 for r in rows if r.get("sparse_rank") is not None)
    query_parsed = {
        "has_dense": dense_vec is not None,
        "has_sparse": sparse_hits > 0,
        "dense_hits": dense_hits,
        "sparse_hits": sparse_hits,
        "fused": len(rows),
    }

    rows = rows[:top_k]
    if not rows:
        return [], query_parsed

    chunk_ids = [r["chunk_id"] for r in rows]
    chunks_resp = (
        client.table("chunks")
        .select("id,doc_id,chunk_idx,text,page,section_title")
        .in_("id", chunk_ids)
        .execute()
    )
    chunks_by_id = {c["id"]: c for c in (chunks_resp.data or [])}
    doc_ids = list({r["doc_id"] for r in rows})
    docs_resp = (
        client.table("documents")
        .select("id,title")
        .in_("id", doc_ids)
        .execute()
    )
    docs_by_id = {d["id"]: d for d in (docs_resp.data or [])}

    enriched: list[dict] = []
    for r in rows:
        c = chunks_by_id.get(r["chunk_id"])
        if not c:
            continue
        d = docs_by_id.get(r["doc_id"])
        enriched.append(
            {
                "chunk_id": r["chunk_id"],
                "doc_id": r["doc_id"],
                "doc_title": (d or {}).get("title"),
                "chunk_idx": c["chunk_idx"],
                "text": c["text"],
                "page": c.get("page"),
                "section_title": c.get("section_title"),
                "score": float(r.get("rrf_score") or 0.0),
            }
        )
    return enriched, query_parsed


def _build_messages(query: str, chunks: list[dict]) -> list[ChatMessage]:
    """LLM prompt 구성 — 한국어 + faithfulness 보장.

    설계 의도:
    - system: 검색 결과 외 내용 추측 금지, 한국어 답변, 출처 [N] 인라인 인용
    - user: 질문 + 번호 매겨진 chunks 본문
    """
    system = (
        "당신은 사용자의 개인 지식베이스에서 검색된 자료를 바탕으로 한국어로 답변하는 어시스턴트입니다. "
        "다음 규칙을 반드시 지키세요:\n"
        "1. 답변은 반드시 제공된 '검색 결과' 안의 내용만 사용하세요. 외부 지식이나 추측을 절대 추가하지 마세요.\n"
        "2. 검색 결과에 답변할 내용이 없으면 '제공된 자료에서 해당 정보를 찾지 못했습니다.' 라고만 답하세요.\n"
        "3. 답변 문장 끝에 출처 번호를 [1], [2] 와 같이 인라인으로 표시하세요.\n"
        "4. 한국어로 간결하게 답변하세요 (5문장 이내 권장)."
    )
    parts: list[str] = [f"질문: {query}", "", "검색 결과:"]
    for i, c in enumerate(chunks, start=1):
        text = (c.get("text") or "").strip()
        if len(text) > _CHUNK_TEXT_MAX:
            text = text[:_CHUNK_TEXT_MAX] + "..."
        title = c.get("doc_title") or "(제목 없음)"
        page = c.get("page")
        page_str = f" p.{page}" if page else ""
        parts.append(f"[{i}] {title}{page_str}\n{text}")
    user_content = "\n\n".join(parts)
    return [
        ChatMessage(role="system", content=system),
        ChatMessage(role="user", content=user_content),
    ]


@router.get("/answer", response_model=AnswerResponse)
def answer(
    q: str = Query(..., min_length=1, max_length=_MAX_QUERY_LEN, description="질문 (한국어)"),
    top_k: int = Query(_DEFAULT_TOP_K, ge=1, le=_MAX_TOP_K, description="LLM 에 전달할 검색 결과 chunks 수"),
    doc_id: str | None = Query(default=None, description="단일 문서 스코프 (W11 doc_id 필터)"),
) -> AnswerResponse:
    start_t = time.monotonic()
    settings = get_settings()
    user_id = str(settings.default_user_id)
    # W25 D14 — 한국어 NFD/NFC 정규화 (DB title/chunk 이 NFC 인데 query 가 NFD 면 매칭 fail)
    clean_q = unicodedata.normalize("NFC", q.strip())
    if not clean_q:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="질문이 비어있습니다.",
        )

    chunks, query_parsed = _gather_chunks(
        query=clean_q, doc_id=doc_id, top_k=top_k, user_id=user_id
    )

    if not chunks:
        # 검색 결과 0 → LLM 호출 회피 (quota 보호 + 명확한 답변 형식)
        return AnswerResponse(
            query=clean_q,
            answer="제공된 자료에서 해당 정보를 찾지 못했습니다.",
            sources=[],
            has_search_results=False,
            model=_resolve_model_label(None),
            took_ms=int((time.monotonic() - start_t) * 1000),
            query_parsed=QueryParsedInfo(**query_parsed),
        )

    messages = _build_messages(clean_q, chunks)
    llm = _get_llm()
    try:
        llm_text = llm.complete(messages, temperature=0.2)
    except Exception as exc:  # noqa: BLE001
        if is_quota_exhausted(exc):
            logger.warning("answer: Gemini quota 소진 — 503")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="답변 생성 일시 오류 — 일일 quota 가 소진되었습니다. 잠시 후 다시 시도해주세요.",
                headers={"Retry-After": "3600"},
            ) from exc
        logger.exception("answer: LLM 호출 실패")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="답변 생성 일시 오류 — 잠시 후 다시 시도해주세요.",
            headers={"Retry-After": "60"},
        ) from exc

    sources = [
        AnswerSource(
            chunk_id=c["chunk_id"],
            doc_id=c["doc_id"],
            doc_title=c.get("doc_title"),
            chunk_idx=c["chunk_idx"],
            page=c.get("page"),
            section_title=c.get("section_title"),
            score=c["score"],
            snippet=(c.get("text") or "")[:200],
        )
        for c in chunks
    ]

    return AnswerResponse(
        query=clean_q,
        answer=llm_text.strip(),
        sources=sources,
        has_search_results=True,
        model=_resolve_model_label(llm),
        took_ms=int((time.monotonic() - start_t) * 1000),
        query_parsed=QueryParsedInfo(**query_parsed),
    )


# ============================================================
# POST /answer/feedback — W25 D14 사용자 피드백 (👍/👎 + 옵션 코멘트)
# ============================================================

class AnswerFeedbackRequest(BaseModel):
    query: str
    answer_text: str
    helpful: bool
    comment: str | None = None
    doc_id: str | None = None
    sources_count: int = 0
    model: str | None = None


class AnswerFeedbackResponse(BaseModel):
    feedback_id: int | None
    skipped: bool = False
    note: str | None = None


# 마이그 011 (answer_feedback) 미적용 시 첫 실패 후 비활성 — 백엔드 부하 0
_feedback_disabled = False


def _disable_feedback(reason: Exception) -> None:
    global _feedback_disabled
    if not _feedback_disabled:
        _feedback_disabled = True
        logger.warning(
            "answer_feedback INSERT 첫 실패 — 이번 프로세스 동안 비활성 "
            "(마이그 011 적용 후 백엔드 재시작 시 회복): %s",
            reason,
        )


def reset_feedback_disabled() -> None:
    """단위 테스트 용 — 모듈 flag 리셋."""
    global _feedback_disabled
    _feedback_disabled = False


@router.post("/answer/feedback", response_model=AnswerFeedbackResponse)
def submit_answer_feedback(payload: AnswerFeedbackRequest) -> AnswerFeedbackResponse:
    """답변에 대한 사용자 피드백 저장 (W25 D14).

    답변 자체는 stateless 라 query+answer_text 보존. 향후 RAGAS 정성 ground truth +
    답변 품질 회귀 추적용. 마이그 011 미적용 시 graceful skip.
    """
    if _feedback_disabled:
        return AnswerFeedbackResponse(
            feedback_id=None,
            skipped=True,
            note="answer_feedback 테이블 미존재 — 마이그 011 적용 필요",
        )

    settings = get_settings()
    try:
        client = get_supabase_client()
        resp = (
            client.table("answer_feedback")
            .insert(
                {
                    "user_id": str(settings.default_user_id),
                    "doc_id": payload.doc_id,
                    "query": payload.query,
                    "answer_text": payload.answer_text,
                    "helpful": payload.helpful,
                    "comment": payload.comment,
                    "sources_count": payload.sources_count,
                    "model": payload.model,
                }
            )
            .execute()
        )
        feedback_id = (resp.data or [{}])[0].get("id")
        return AnswerFeedbackResponse(feedback_id=feedback_id)
    except Exception as exc:  # noqa: BLE001
        _disable_feedback(exc)
        return AnswerFeedbackResponse(
            feedback_id=None,
            skipped=True,
            note="피드백 저장 일시 실패 — 마이그 011 미적용 가능",
        )


# ============================================================
# /answer/eval-ragas — W25 D14 RAGAS 정량 평가 (캐시 + 측정)
# ============================================================

class RagasEvalRequest(BaseModel):
    query: str
    answer_text: str
    doc_id: str | None = None
    contexts: list[str]  # 평가용 출처 본문 (보통 sources 의 chunk text)


class RagasMetricsModel(BaseModel):
    faithfulness: float | None = None
    answer_relevancy: float | None = None
    context_precision: float | None = None
    context_recall: float | None = None
    answer_correctness: float | None = None


class RagasEvalResponse(BaseModel):
    metrics: RagasMetricsModel
    judge_model: str | None
    took_ms: int | None
    cached: bool = False
    skipped: bool = False
    note: str | None = None
    created_at: str | None = None


_ragas_eval_disabled = False


def _disable_ragas_eval(reason: Exception) -> None:
    global _ragas_eval_disabled
    if not _ragas_eval_disabled:
        _ragas_eval_disabled = True
        logger.warning(
            "answer_ragas_evals INSERT 첫 실패 — 이번 프로세스 동안 비활성 "
            "(마이그 012 적용 후 백엔드 재시작 시 회복): %s",
            reason,
        )


def reset_ragas_eval_disabled() -> None:
    """단위 테스트 용."""
    global _ragas_eval_disabled
    _ragas_eval_disabled = False


def _query_ragas_cache(client, *, query: str, doc_id: str | None):
    """가장 최근 (query, doc_id) 매칭 row 1건 반환 (없으면 None)."""
    try:
        q = (
            client.table("answer_ragas_evals")
            .select("metrics, model_judge, took_ms, created_at")
            .eq("query", query)
            .order("created_at", desc=True)
            .limit(1)
        )
        if doc_id:
            q = q.eq("doc_id", doc_id)
        else:
            q = q.is_("doc_id", "null")
        resp = q.execute()
        rows = resp.data or []
        return rows[0] if rows else None
    except Exception as exc:  # noqa: BLE001
        _disable_ragas_eval(exc)
        return None


@router.get("/answer/eval-ragas", response_model=RagasEvalResponse)
def get_ragas_eval(
    query: str = Query(..., min_length=1, max_length=_MAX_QUERY_LEN),
    doc_id: str | None = Query(default=None),
) -> RagasEvalResponse:
    """캐시 조회 — 같은 query + doc_id 의 가장 최근 평가 결과 반환 (없으면 빈 응답)."""
    if _ragas_eval_disabled:
        return RagasEvalResponse(
            metrics=RagasMetricsModel(),
            judge_model=None,
            took_ms=None,
            skipped=True,
            note="answer_ragas_evals 테이블 미존재 — 마이그 012 적용 필요",
        )
    import unicodedata as _u

    clean_q = _u.normalize("NFC", query.strip())
    client = get_supabase_client()
    row = _query_ragas_cache(client, query=clean_q, doc_id=doc_id)
    if not row:
        return RagasEvalResponse(
            metrics=RagasMetricsModel(),
            judge_model=None,
            took_ms=None,
            cached=False,
        )
    metrics_dict = row.get("metrics") or {}
    return RagasEvalResponse(
        metrics=RagasMetricsModel(**metrics_dict),
        judge_model=row.get("model_judge"),
        took_ms=row.get("took_ms"),
        cached=True,
        created_at=row.get("created_at"),
    )


@router.post("/answer/eval-ragas", response_model=RagasEvalResponse)
def submit_ragas_eval(payload: RagasEvalRequest) -> RagasEvalResponse:
    """RAGAS 평가 실행 + DB 저장. 캐시 hit 시 재호출 회피."""
    if _ragas_eval_disabled:
        return RagasEvalResponse(
            metrics=RagasMetricsModel(),
            judge_model=None,
            took_ms=None,
            skipped=True,
            note="answer_ragas_evals 테이블 미존재 — 마이그 012 적용 필요",
        )

    import unicodedata as _u
    from app.services.ragas_eval import RagasUnavailable, evaluate_single

    clean_q = _u.normalize("NFC", payload.query.strip())
    settings = get_settings()
    client = get_supabase_client()

    # 캐시 우선 — 같은 query+answer_text+doc_id 매칭 시 재사용
    cached = _query_ragas_cache(client, query=clean_q, doc_id=payload.doc_id)
    if cached:
        metrics_dict = cached.get("metrics") or {}
        return RagasEvalResponse(
            metrics=RagasMetricsModel(**metrics_dict),
            judge_model=cached.get("model_judge"),
            took_ms=cached.get("took_ms"),
            cached=True,
            created_at=cached.get("created_at"),
        )

    # 평가 실행
    try:
        result = evaluate_single(
            query=clean_q, answer=payload.answer_text, contexts=payload.contexts,
        )
    except RagasUnavailable as exc:
        return RagasEvalResponse(
            metrics=RagasMetricsModel(),
            judge_model=None,
            took_ms=None,
            skipped=True,
            note=f"RAGAS 평가 불가: {exc}",
        )

    # DB 저장 (graceful)
    metrics_dict = result.metrics.to_dict()
    try:
        client.table("answer_ragas_evals").insert(
            {
                "user_id": str(settings.default_user_id),
                "doc_id": payload.doc_id,
                "query": clean_q,
                "answer_text": payload.answer_text,
                "contexts": payload.contexts,
                "metrics": metrics_dict,
                "model_judge": result.judge_model,
                "took_ms": result.took_ms,
            }
        ).execute()
    except Exception as exc:  # noqa: BLE001
        _disable_ragas_eval(exc)
        # 저장 실패해도 평가 결과는 반환
        return RagasEvalResponse(
            metrics=RagasMetricsModel(**metrics_dict),
            judge_model=result.judge_model,
            took_ms=result.took_ms,
            cached=False,
            note="평가 결과 캐시 저장 실패 — 마이그 012 미적용 가능",
        )

    return RagasEvalResponse(
        metrics=RagasMetricsModel(**metrics_dict),
        judge_model=result.judge_model,
        took_ms=result.took_ms,
        cached=False,
    )


# ============================================================
# /search/eval-precision — W25 D14 검색 적합도만 측정 (LLM 호출 1개)
# ============================================================

class SearchPrecisionRequest(BaseModel):
    query: str
    contexts: list[str]
    doc_id: str | None = None


@router.get("/search/eval-precision", response_model=RagasEvalResponse)
def get_search_precision(
    query: str = Query(..., min_length=1, max_length=_MAX_QUERY_LEN),
    doc_id: str | None = Query(default=None),
) -> RagasEvalResponse:
    """캐시 조회 — 검색 적합도만 측정한 row (answer_text="" sentinel)."""
    if _ragas_eval_disabled:
        return RagasEvalResponse(
            metrics=RagasMetricsModel(),
            judge_model=None,
            took_ms=None,
            skipped=True,
            note="answer_ragas_evals 테이블 미존재 — 마이그 012 적용 필요",
        )
    import unicodedata as _u

    clean_q = _u.normalize("NFC", query.strip())
    client = get_supabase_client()
    # answer_text = "" sentinel 매칭 (검색 전용 row)
    try:
        q = (
            client.table("answer_ragas_evals")
            .select("metrics, model_judge, took_ms, created_at")
            .eq("query", clean_q)
            .eq("answer_text", "")
            .order("created_at", desc=True)
            .limit(1)
        )
        if doc_id:
            q = q.eq("doc_id", doc_id)
        else:
            q = q.is_("doc_id", "null")
        rows = (q.execute().data or [])
        row = rows[0] if rows else None
    except Exception as exc:  # noqa: BLE001
        _disable_ragas_eval(exc)
        row = None

    if not row:
        return RagasEvalResponse(
            metrics=RagasMetricsModel(),
            judge_model=None,
            took_ms=None,
            cached=False,
        )
    metrics_dict = row.get("metrics") or {}
    return RagasEvalResponse(
        metrics=RagasMetricsModel(**metrics_dict),
        judge_model=row.get("model_judge"),
        took_ms=row.get("took_ms"),
        cached=True,
        created_at=row.get("created_at"),
    )


@router.post("/search/eval-precision", response_model=RagasEvalResponse)
def submit_search_precision(payload: SearchPrecisionRequest) -> RagasEvalResponse:
    """검색 적합도 (Context Precision) 만 측정 + 캐시.

    LLM judge 호출 1개 → ~$0.003/평가. 답변 생성 (Faithfulness/Relevancy) 호출 X.
    """
    if _ragas_eval_disabled:
        return RagasEvalResponse(
            metrics=RagasMetricsModel(),
            judge_model=None,
            took_ms=None,
            skipped=True,
            note="answer_ragas_evals 테이블 미존재 — 마이그 012 적용 필요",
        )
    import unicodedata as _u
    from app.services.ragas_eval import (
        RagasUnavailable,
        evaluate_context_precision_only,
    )

    clean_q = _u.normalize("NFC", payload.query.strip())
    settings = get_settings()
    client = get_supabase_client()

    # 캐시 우선 조회 (검색 전용 row — answer_text="")
    try:
        q = (
            client.table("answer_ragas_evals")
            .select("metrics, model_judge, took_ms, created_at")
            .eq("query", clean_q)
            .eq("answer_text", "")
            .order("created_at", desc=True)
            .limit(1)
        )
        if payload.doc_id:
            q = q.eq("doc_id", payload.doc_id)
        else:
            q = q.is_("doc_id", "null")
        cached_rows = q.execute().data or []
        if cached_rows:
            cached = cached_rows[0]
            return RagasEvalResponse(
                metrics=RagasMetricsModel(**(cached.get("metrics") or {})),
                judge_model=cached.get("model_judge"),
                took_ms=cached.get("took_ms"),
                cached=True,
                created_at=cached.get("created_at"),
            )
    except Exception as exc:  # noqa: BLE001
        _disable_ragas_eval(exc)
        return RagasEvalResponse(
            metrics=RagasMetricsModel(),
            judge_model=None,
            took_ms=None,
            skipped=True,
            note="캐시 조회 실패 — 마이그 012 미적용 가능",
        )

    # 실측정
    try:
        result = evaluate_context_precision_only(query=clean_q, contexts=payload.contexts)
    except RagasUnavailable as exc:
        return RagasEvalResponse(
            metrics=RagasMetricsModel(),
            judge_model=None,
            took_ms=None,
            skipped=True,
            note=f"RAGAS 평가 불가: {exc}",
        )

    metrics_dict = result.metrics.to_dict()
    try:
        client.table("answer_ragas_evals").insert(
            {
                "user_id": str(settings.default_user_id),
                "doc_id": payload.doc_id,
                "query": clean_q,
                "answer_text": "",  # sentinel — 검색 전용 row
                "contexts": payload.contexts,
                "metrics": metrics_dict,
                "model_judge": result.judge_model,
                "took_ms": result.took_ms,
            }
        ).execute()
    except Exception as exc:  # noqa: BLE001
        _disable_ragas_eval(exc)

    return RagasEvalResponse(
        metrics=RagasMetricsModel(**metrics_dict),
        judge_model=result.judge_model,
        took_ms=result.took_ms,
        cached=False,
    )
