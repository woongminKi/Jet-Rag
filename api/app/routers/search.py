"""W3 Day 2 — `/search` 라우터: 하이브리드 RRF (HNSW dense + PGroonga sparse).

- BGE-M3 `embed_query` → 1024 dim dense vector
- Postgres RPC `search_hybrid_rrf` → top 50 chunks + RRF 점수 + dense/sparse rank
- 메타 필터 4종 (`tags` · `doc_type` · `from_date` · `to_date`) 은 documents 단계에 WHERE
- doc_id 별 그룹화 (max RRF score) + 매칭 청크 최대 3건 (chunk_idx 오름차순 표시)
- `query_parsed` (S2 §7 투명성): has_dense · has_sparse · dense_hits · sparse_hits · fused
- HF API 실패 시 sparse-only fallback (RPC `search_sparse_only_pgroonga`)

명세:
    - work-log/2026-04-28 W3 스프린트 명세.md (v0.4 CONFIRMED, 항목 A)
    - work-log/2026-04-29 W3 스프린트 명세 v0.5.md §3.A (DE-60 PGroonga 교체)
RPC:
    - api/migrations/003_hybrid_search.sql (RRF k=60, dense=sparse=1.0)
    - api/migrations/004_pgroonga_korean_fts.sql (Mecab 형태소 + flags 필터)
"""

from __future__ import annotations

import logging
import os
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel

from app.adapters.impl.bge_reranker_hf import (
    get_reranker_provider,
    is_transient_reranker_error,
)
from app.adapters.impl.bgem3_hf_embedding import (
    get_bgem3_provider,
    is_transient_hf_error,
)
from app.config import get_settings
from app.db import get_supabase_client
from app.services import (
    intent_router,
    meta_filter_fast_path,
    mmr,
    reranker_cache,
    search_metrics,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["search"])

_MAX_QUERY_LEN = 200
# 검색 결과 카드의 본문 미리보기 개수 — list 모드 (doc_id 미지정) 에 적용.
# doc_id 명시 (단일 문서 스코프) 시 본 cap 을 우회 — 사용자가 그 문서의 모든 매칭 청크를 보고 싶다는 명시적 요청.
# (W25 D5 — `+89개 더 매칭 (이 문서에서 모두 보기)` Link 의 doc 페이지 매칭 청크 섹션 데이터 공급.)
_MAX_MATCHED_CHUNKS_PER_DOC = 3
# doc_id 스코프 시 응답 청크 최대 개수 — `_RPC_TOP_K_DOC_FILTER` (200) 와 동일 안전 상한.
# 한 문서에 매칭 청크 200개 이상은 RPC 가 잘라내 우리쪽 의도 (모두 보기) 도 자연 cap.
_MAX_MATCHED_CHUNKS_DOC_SCOPE = 200
# W25 D3 — snippet around 확장 (80 → 240).
# 매칭 위치 ±N자 본문. 사용자 검색 결과의 정보량 부족 (B-1) 해결 — wire 증가 ~3배 trade-off.
# 환경변수로 운영 중 조정 가능. p95 latency 측정 필요 (smoke 시 확인).
_SNIPPET_AROUND = int(os.environ.get("SEARCH_SNIPPET_AROUND", "240"))
_RRF_K = 60
_RPC_TOP_K = 50  # RPC 의 dense / sparse path 각각 상위 K
# W19 Day 2 한계 #75 — mode=dense/sparse 응용 layer 필터 후 부족 방지 cap.
# hybrid (default) 는 _RPC_TOP_K, mode 필터 시 2배 pre-allocate (latency 영향 미미).
_RPC_TOP_K_ABLATION = 100
# W19 Day 3 한계 #66 — doc_id 응용 layer 필터 시 부족 방지 cap (RPC 인자 추가 회피).
# doc_id 가 지정된 경우 RPC 결과 N건 중 일치만 통과 → 4배 pre-allocate.
_RPC_TOP_K_DOC_FILTER = 200
# 001_init.sql 의 doc_type CHECK 제약과 동일 — 화이트리스트 검증용
_DOC_TYPES = {"pdf", "hwp", "hwpx", "docx", "pptx", "image", "url", "txt", "md"}
# 503 응답의 Retry-After 헤더 — RFC 7231. HF cold start (5~20s) + 안전 마진.
_RETRY_AFTER_SECONDS = "60"

# W25 D4 Phase 2 — 표지 청크 가드 heuristic.
# 사용자 시나리오 ("소나타에서 제공하는 시트 종류 뭐가 있어?") 에서 sonata 카탈로그 p.1 의
# 6자 표지 청크 ("SONATA") 가 dense cos sim 비정상 우세로 top-1 진입 → 후처리 패널티로 회피.
# 보수적 안 (a) — text_len <= 30 AND (chunk_idx == 0 OR page == 1) 동시 만족 시에만 곱셈.
# (b)/(c) (chunk_filter 마킹·dense_rank 노출) 는 효과 측정 후 결정.
# 짧은 헤딩 (예: "결론") 은 chunk_idx>0 또는 page>1 이라 false positive 회피.
_COVER_GUARD_TEXT_LEN = 30
_COVER_GUARD_PENALTY = 0.3

# W25 D14+1 (S2) — BGE-reranker-v2-m3 cross-encoder rerank.
# RRF top-K (~50) → reranker score → 재정렬. opt-in ENV (default off).
# 활성 시 cover guard 곱셈 skip — cross-encoder 가 짧은 표지 청크 의미 매칭 약함을 직접 인식.
_RERANKER_ENABLED_DEFAULT = "false"
# S3 D4 — rerank 후보 cap (planner v0.1 §A). 50 → 20 축소.
# HF API pair latency 가 후보 수에 선형 — 20 cap 시 1회 호출 ~300ms 안정화.
# RRF top-K 가 cap 보다 클 때만 잘림 — 작을 때는 후보 그대로 사용.
# ENV `JETRAG_RERANKER_CANDIDATE_CAP` (default 20, 5~50 권장) 으로 운영 조정 가능.
_RERANKER_CANDIDATE_CAP = 20
_ENV_RERANKER_CAP = "JETRAG_RERANKER_CANDIDATE_CAP"
_RERANKER_CAP_MIN = 5
_RERANKER_CAP_MAX = 50

# S3 D4 — Free-tier degrade (planner v0.1 §C). vision_usage_log 재사용
# (`source_type='reranker_invoke'`, count rows). 월간 호출 횟수 ≥ 임계 시 reranker
# skip → RRF score 만으로 정렬 → path="degraded" 마킹. HF 자체 헤더는 비공식·불안정
# (사용자 결정 Q-S3-D4-1) 이라 자체 카운터 채택.
_ENV_RERANKER_MONTHLY_CAP = "JETRAG_RERANKER_MONTHLY_CAP_CALLS"
_ENV_RERANKER_DEGRADE_THRESHOLD = "JETRAG_RERANKER_DEGRADE_THRESHOLD"
_RERANKER_MONTHLY_CAP_DEFAULT = 1000
_RERANKER_DEGRADE_THRESHOLD_DEFAULT = 0.8

# vision_usage_log.source_type 식별자 — D3 'query_decomposition' 과 동일 패턴.
# budget_guard 가 source_type 으로 분리 SUM (vision 호출과 분리).
_USAGE_LOG_RERANKER_SOURCE_TYPE = "reranker_invoke"

# X-Reranker-Path 헤더 + meta 노출 라벨 (planner v0.1 §E).
# - cached   : reranker_cache hit → HF 호출 0
# - invoked  : 정상 HF 호출 → cache store
# - degraded : 월간 cap 초과 → HF skip + RRF 정렬
# - disabled : ENV off / candidates < 2 등 reranker 자체 미진입
_RERANKER_PATH_CACHED = "cached"
_RERANKER_PATH_INVOKED = "invoked"
_RERANKER_PATH_DEGRADED = "degraded"
_RERANKER_PATH_DISABLED = "disabled"

# W25 D14+1 (G) — doc-level embedding RRF 가산 (S4).
# documents.doc_embedding (1024 dim, summary+implications 또는 raw_text[:3000] 임베딩)
# 과 query_dense cosine sim 으로 doc-level rank 산출 → RRF 가산.
# chunks 단위 매칭만 보던 기존 점수에 doc 단위 의미 매칭 보강.
# opt-in ENV — default false (S2 reranker 회귀 학습 — 정량 baseline 후 default 결정).
# 효과는 multi-doc 검색에서 발휘 (doc-scope `?doc_id=...` 시 영향 0).
_DOC_EMBEDDING_RRF_ENABLED_DEFAULT = "false"

# W25 D14+1 D2 — Query expansion (도메인 동의어 사전).
# PGroonga sparse query 의 외래어/약어/한자어 0 hits 회귀를 동의어 추가로 회복.
# dense path 는 BGE-M3 가 의미적 처리 → expansion 미적용.
# opt-in ENV — default false (정량 baseline 후 default 결정).
_QUERY_EXPANSION_ENABLED_DEFAULT = "false"

# W25 D14+1 D4 — HyDE (Hypothetical Document Embedding).
# query → Gemini 가상 문단 → (query + 문단) embedding → dense path 검색.
# 짧은 키워드 query 의 의미적 매칭 강화. latency +1~2s, Gemini 호출 1회.
# opt-in ENV (default false). cache 강제 (같은 query 반복 시 Gemini 호출 0).
_HYDE_ENABLED_DEFAULT = "false"

# W25 D8 Phase 2 — 메뉴 footer 가드: 1차 시도 실패 → 롤백 / 후속 sprint 신호로 보존.
# 시도 결과 (work-log/2026-05-04 W25 D8 Phase 2 메뉴 footer 가드.md):
#   - 단순 패턴 매칭 → 정답 청크 (idx 37/38/43) 도 함께 깎임 → G-S-006 0.50→0.03 악화
#   - 비율 기반 (ratio >= 0.30) → 본문 + 메뉴 합산 청크 ratio 0.5~0.65 로 정답 보호 실패
#   - 정밀 패턴 (130자 고정 시퀀스) 도 동일 — 모든 페이지에 메뉴가 등장하여 변별력 부족이 본질
# 결론: 런타임 score 패널티로는 해결 불가. chunk 분리 정책 (Phase 2 차수 B) 또는 PGroonga
#       한국어 sparse 회복 (D) 으로 근본 해결해야 함.


# W25 D10 차수 D-a-2 — 한국어 조사 strip whitelist.
# Mecab 토크나이저가 "전폭은/전고는/디스플레이는" 같은 조사 결합 토큰을 분해 못 해
# vocab 부재 처리. 응용 layer 에서 끝 1자 조사 strip 으로 우회.
# whitelist (가장 흔한 1자 조사) — false positive 회피.
# "이" 는 외래어 명사 끝 (디스플레이/알고리즘 류) 충돌로 제외 → "회사이" 같은 case 는 보존 trade-off.
# 토큰 길이 >= 3 조건도 동일 의도 (짧은 단어 보호).
_KOREAN_PARTICLES_1 = frozenset(
    ["는", "은", "가", "을", "를", "도", "만", "에", "의"]
)
_PARTICLE_STRIP_MIN_LEN = 3


def _strip_korean_particle(token: str) -> str:
    """W25 D11 차수 D-a-2 — 한국어 조사 strip + trailing punctuation 정리.

    예: '전폭은' → '전폭', '디스플레이는' → '디스플레이', '길이가' → '길이',
        '전폭은?' → '전폭' (의문문 punctuation 도 함께 정리).
    토큰 길이 >= 3 일 때만 적용 (짧은 단어 false positive 회피).
    한글 끝 1자가 whitelist 에 있을 때만 strip — '얼마나/종류야' 같은 비조사 어미는 보존.
    """
    cleaned = token.rstrip("?!.,;:")
    if len(cleaned) < _PARTICLE_STRIP_MIN_LEN:
        return cleaned
    if cleaned[-1] in _KOREAN_PARTICLES_1:
        return cleaned[:-1]
    return cleaned


def _build_pgroonga_query(q: str, *, expansion_enabled: bool = False) -> str:
    """W25 D10/D11 차수 D-a + D-a-2 — PGroonga `&@~` multi-token AND → OR 변환 + 조사 strip.
    W25 D14+1 D2 — query expansion (도메인 동의어 사전) 옵션 추가.

    PGroonga query mode (`&@~`) 는 query 내 모든 토큰이 같은 chunk 에 모두 매칭돼야
    hit 가 잡힘 (AND 매칭). 사용자 자연어 query 는 3~5 단어라 한 단어만 vocab 부재여도
    전체 0 hits. OR 변환으로 한 단어라도 매칭하는 chunk 를 sparse 결과에 포함시켜
    RRF 가산이 가능하게 함 (dense path 와 정상 합산).

    expansion_enabled=True 시: 조사 strip 후 토큰별 동의어 (외래어/약어/한자어) 추가.
    예: "쏘나타 전장" → "쏘나타 OR sonata OR Sonata OR 전장 OR 전체길이 OR 길이"

    단일 토큰 query 도 expansion 적용 (동의어 추가 시 의미 있음).
    동의어 없는 토큰은 원본 그대로.
    """
    tokens = [_strip_korean_particle(t) for t in q.strip().split() if t]
    tokens = [t for t in tokens if t]  # strip 결과 빈 토큰 제외 (방어)
    if not tokens:
        return q.strip()
    if expansion_enabled:
        from app.services.query_expansion import build_pgroonga_query as _expand
        return _expand(" ".join(tokens))
    if len(tokens) <= 1:
        return tokens[0]
    return " OR ".join(tokens)


class MatchedChunk(BaseModel):
    chunk_id: str
    chunk_idx: int
    text: str
    page: int | None
    section_title: str | None
    highlight: list[list[int]]
    # W6 Day 5 추가 — 디버깅/투명성 가시성. backward compatible (기존 필드 변경 0).
    rrf_score: float | None = None  # 본 청크의 RRF 점수 (검색 결과 ranking 근거)
    metadata: dict | None = None  # chunk metadata (overlap_with_prev_chunk_idx 등)


class SearchHit(BaseModel):
    doc_id: str
    doc_title: str
    doc_type: str
    tags: list[str]
    summary: str | None
    created_at: str
    relevance: float  # 결과 집합 내 정규화 (top=1.0) — 프론트 % 표시용
    matched_chunk_count: int
    matched_chunks: list[MatchedChunk]


class QueryParsedInfo(BaseModel):
    """W3 S2 §7 투명성 — 검색 경로 진단.

    - has_dense: dense path 가 실행됐는가 (HF API 성공 여부)
    - has_sparse: sparse path 가 1건 이상 매칭됐는가
    - dense_hits: dense path 가 반환한 chunks 수 (sparse-only fallback 시 0)
    - sparse_hits: sparse path 가 반환한 chunks 수
    - fused: RRF 후 unique chunks 수 (= rpc 응답 row 수)
    - fallback_reason: HF API 실패 분류 (W3 Day 2 Phase 3 D-1 통합)
        - None: dense path 정상
        - "transient_5xx": 일시 오류 → sparse-only 로 본 응답 반환됨
        - "permanent_4xx" / "unknown": 본 응답 자체에는 등장 안 함 (503 raise 경로)
    """
    has_dense: bool
    has_sparse: bool
    dense_hits: int
    sparse_hits: int
    fused: int
    fallback_reason: str | None = None
    # W25 D14+1 (S2) — BGE-reranker 사용 여부 + 실패 분류.
    # default False (backward compatible — opt-in ENV off 또는 reranker 실패 시 RRF fallback).
    reranker_used: bool = False
    reranker_fallback_reason: str | None = None  # transient / permanent / None
    # S3 D4 — reranker path 라벨 (planner v0.1 §E).
    # cached / invoked / degraded / disabled — `X-Reranker-Path` 헤더와 동일.
    # 프론트가 stream 없이도 path 식별 가능 (X-Search-Path 와 동일 패턴).
    reranker_path: str = _RERANKER_PATH_DISABLED
    # W25 D14+1 (G/S4) — doc-level embedding RRF 가산 사용 여부.
    # 가산이 적용된 doc 수 (doc_embedding NULL 인 doc 제외).
    doc_embedding_rrf_used: bool = False
    doc_embedding_hits: int = 0
    # W25 D14+1 D4 — HyDE 사용 여부 + 실패 분류.
    hyde_used: bool = False
    hyde_fallback_reason: str | None = None


class SearchResponse(BaseModel):
    query: str
    total: int  # 매칭 doc 수 (메타 필터 적용 후)
    limit: int
    offset: int
    items: list[SearchHit]
    took_ms: int
    query_parsed: QueryParsedInfo  # W3 신규 — 기존 필드는 변경 X (backward compatible)
    # S3 D2 — 메타 필터 fast path 진입 시 진단 정보. None 이면 RAG path (기존 흐름).
    # 응답 헤더 X-Search-Path 와 동일 의미를 본문에도 노출 → 프론트가 stream 없이도 path 식별.
    meta: dict | None = None


@router.get("/search", response_model=SearchResponse)
def search(
    q: str = Query(
        ...,
        min_length=1,
        max_length=_MAX_QUERY_LEN,
        description="검색어 (자연어 / 키워드 모두 허용, 최대 200자)",
    ),
    limit: int = Query(10, ge=1, le=50),
    offset: int = Query(0, ge=0),
    tags: list[str] | None = Query(
        default=None,
        description="태그 필터 (반복 허용 — `?tags=A&tags=B` 시 A AND B 모두 일치)",
    ),
    doc_type: str | None = Query(
        default=None,
        description="doc_type 필터 (pdf · hwp · hwpx · docx · pptx · image · url · txt · md)",
    ),
    from_date: str | None = Query(
        default=None,
        description="created_at 시작 ISO 8601 (`2026-04-01` 또는 `2026-04-01T00:00:00Z`)",
    ),
    to_date: str | None = Query(
        default=None,
        description="created_at 종료 ISO 8601 (포함)",
    ),
    doc_id: str | None = Query(
        default=None,
        description=(
            "단일 문서 스코프 자연어 QA — 해당 doc 의 chunks 만 검색 (US-08, W11 Day 4). "
            "응용 layer 필터 (RPC 결과 후) — 마이그레이션 회피 trade-off."
        ),
    ),
    mode: str = Query(
        default="hybrid",
        description=(
            "검색 모드 — hybrid (default, dense + sparse RRF) / dense / sparse. "
            "ablation 측정용 (W13 Day 2 — KPI '하이브리드 +5pp 우세' 비교 인프라)."
        ),
    ),
    response: Response = None,  # type: ignore[assignment]
) -> SearchResponse:
    start_t = time.monotonic()
    client = get_supabase_client()
    settings = get_settings()
    user_id = settings.default_user_id

    # W25 D14 — 한국어 NFD/NFC 정규화 (DB title 이 NFC 인데 query 가 NFD 면 매칭 fail)
    clean_q = unicodedata.normalize("NFC", q.strip())
    if not clean_q:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="검색어가 비어있습니다.",
        )

    # ------------------------------------------------------------------
    # S3 D2 — 메타 필터 fast path 분기 (planner v0.1 §C).
    # - 단일 문서 스코프 (doc_id) / mode ablation 시에는 RAG path 강제 (의도 우선).
    # - is_meta_only 가 plan 반환 시 임베딩·RPC·reranker 호출 0 으로 바로 응답.
    # ------------------------------------------------------------------
    if doc_id is None and mode == "hybrid":
        plan = meta_filter_fast_path.is_meta_only(clean_q)
        if plan is not None:
            return _run_meta_fast_path(
                clean_q=clean_q,
                plan=plan,
                limit=limit,
                offset=offset,
                user_id=str(user_id),
                response=response,
                start_t=start_t,
            )
    if response is not None:
        response.headers["X-Search-Path"] = "rag"
    if doc_type is not None and doc_type not in _DOC_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"doc_type='{doc_type}' 가 유효하지 않습니다.",
        )
    # W11 Day 4 — doc_id 형식 검증 (UUID v4 / 비어있지 않은 문자열).
    # 잘못된 입력 보호 — 응용 layer 필터링이라 SQL injection 위험은 0.
    if doc_id is not None:
        doc_id = doc_id.strip()
        if not doc_id or len(doc_id) > 64:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="doc_id 형식이 유효하지 않습니다.",
            )
    # W13 Day 2 — mode 화이트리스트 (hybrid/dense/sparse) 검증
    if mode not in ("hybrid", "dense", "sparse"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"mode='{mode}' 가 유효하지 않습니다 (hybrid/dense/sparse).",
        )
    from_dt = _parse_iso_date(from_date, "from_date")
    to_dt = _parse_iso_date(to_date, "to_date")

    # ------------------------------------------------------------------
    # 1) dense embedding (HF API).
    #    - transient (5xx / network) → sparse-only fallback (degraded ranking 수용)
    #    - 영구 실패 (401/404/400 등 4xx) → 503 raise. silent degradation 방지.
    #      (한 달 동안 토큰 만료를 모르고 sparse-only 운영하는 위험 차단)
    #
    # W25 D14+1 D4 — HyDE 옵션:
    #   `JETRAG_HYDE_ENABLED=true` 시 query → Gemini hypothetical doc → (query + doc) 임베딩.
    #   실패 시 원본 query 임베딩으로 fallback (검색 자체 차단 X).
    # ------------------------------------------------------------------
    dense_vec: list[float] | None = None
    fallback_reason: str | None = None
    embed_cache_hit: bool = False
    hyde_used = False
    hyde_fallback_reason: str | None = None

    # HyDE 활성 시 query 변환 (Gemini)
    embed_input = clean_q
    hyde_enabled = (
        os.environ.get("JETRAG_HYDE_ENABLED", _HYDE_ENABLED_DEFAULT).lower() == "true"
    )
    if hyde_enabled:
        try:
            # Phase 1 S0 D2-A — factory 경유 (purpose=hyde). ENV 1줄 (JETRAG_LLM_PROVIDER)
            # 로 OpenAI/Gemini 전환. JETRAG_LLM_MODEL_HYDE 로 모델 override 가능.
            from app.adapters.factory import get_llm_provider
            from app.services.hyde import generate_hypothetical_doc
            llm = get_llm_provider("hyde")
            hypothetical = generate_hypothetical_doc(llm, clean_q)
            # query + hypothetical doc concat — query 의미 보존 + 가상 문단 의미 보강
            embed_input = f"{clean_q}\n{hypothetical}"
            hyde_used = True
        except Exception as exc:  # noqa: BLE001
            hyde_fallback_reason = "error"
            logger.warning("HyDE 실패 → 원본 query 로 fallback: %s", exc)

    try:
        provider = get_bgem3_provider()
        dense_vec = provider.embed_query(embed_input)
        # W4-Q-3 — embed_query 직후 LRU hit 여부 스냅샷.
        # race condition 한계 (provider 의 docstring 참조): 멀티 스레드 환경에서
        # 타 호출자가 사이에 끼어들면 hit/miss 가 뒤바뀌어 보일 수 있음. 메트릭 비율 측정 용도라 수용.
        embed_cache_hit = bool(getattr(provider, "_last_cache_hit", False))
    except Exception as exc:  # noqa: BLE001
        if is_transient_hf_error(exc):
            fallback_reason = "transient_5xx"
            logger.warning(
                "embed_query transient 실패 — sparse-only fallback 진입: %s", exc
            )
        else:
            # 영구 실패: 운영자가 알아채야 함. logger.exception 으로 stacktrace 보존.
            # 가시성 위해 metrics 에는 record (fallback_reason="permanent_4xx") 후 503 raise.
            logger.exception(
                "embed_query 영구 실패 — 검색 503 반환 (HF 토큰/엔드포인트 점검 필요)"
            )
            search_metrics.record_search(
                took_ms=int((time.monotonic() - start_t) * 1000),
                dense_hits=0,
                sparse_hits=0,
                fused=0,
                has_dense=False,
                fallback_reason="permanent_4xx",
                embed_cache_hit=False,
                mode=mode,
                query_text=clean_q,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="검색 일시 오류 — 임베딩 서비스에 연결할 수 없습니다. 잠시 후 다시 시도해주세요.",
                headers={"Retry-After": _RETRY_AFTER_SECONDS},
            ) from exc

    # ------------------------------------------------------------------
    # 2) 검색 (mode 별 RPC 분기 — W20 Day 2 한계 #74)
    # ------------------------------------------------------------------
    # W19 Day 2·3 — 응용 layer 필터 시 부족 방지 pre-allocate.
    # 우선순위: doc_id 필터 (#66, 4배) > mode ablation (#75, 2배) > default.
    # W20 Day 2 #74: 008 split RPC 적용 시 mode 별 응용 필터 불필요 → cap 만 의미.
    if doc_id is not None:
        rpc_top_k = _RPC_TOP_K_DOC_FILTER
    elif mode in ("dense", "sparse"):
        rpc_top_k = _RPC_TOP_K_ABLATION
    else:
        rpc_top_k = _RPC_TOP_K

    # W25 D10 차수 D-a — sparse path 의 PGroonga query 만 OR 변환. dense path 는 무관.
    # W25 D14+1 D2 — Query expansion (opt-in ENV `JETRAG_QUERY_EXPANSION`).
    query_expansion_enabled = (
        os.environ.get(
            "JETRAG_QUERY_EXPANSION",
            _QUERY_EXPANSION_ENABLED_DEFAULT,
        ).lower()
        == "true"
    )
    pg_q = _build_pgroonga_query(clean_q, expansion_enabled=query_expansion_enabled)

    # W20 Day 2 #74 — mode 별 RPC 분기 (008 split RPC). 008 미적용 시 graceful fallback.
    used_split_rpc = False
    if mode == "dense" and dense_vec is not None:
        try:
            rpc_resp = client.rpc(
                "search_dense_only",
                {
                    "query_dense": dense_vec,
                    "k_rrf": _RRF_K,
                    "top_k": rpc_top_k,
                    "user_id_arg": str(user_id),
                },
            ).execute()
            rpc_rows = rpc_resp.data or []
            used_split_rpc = True
        except Exception as exc:  # noqa: BLE001 — 008 미적용 fallback
            logger.debug("search_dense_only RPC 미적용 fallback: %s", exc)
            rpc_rows = None
    elif mode == "sparse":
        try:
            rpc_resp = client.rpc(
                "search_sparse_only",
                {
                    "query_text": pg_q,
                    "k_rrf": _RRF_K,
                    "top_k": rpc_top_k,
                    "user_id_arg": str(user_id),
                },
            ).execute()
            rpc_rows = rpc_resp.data or []
            used_split_rpc = True
        except Exception as exc:  # noqa: BLE001 — 008 미적용 fallback
            logger.debug("search_sparse_only RPC 미적용 fallback: %s", exc)
            rpc_rows = None
    else:
        rpc_rows = None

    # mode=hybrid 또는 split RPC 미적용 fallback → 기존 search_hybrid_rrf 호출.
    if rpc_rows is None:
        if dense_vec is not None:
            rpc_resp = client.rpc(
                "search_hybrid_rrf",
                {
                    "query_text": pg_q,
                    "query_dense": dense_vec,
                    "k_rrf": _RRF_K,
                    "top_k": rpc_top_k,
                    "user_id_arg": str(user_id),
                },
            ).execute()
            rpc_rows = rpc_resp.data or []
        else:
            rpc_rows = _sparse_only_fallback(client, pg_q, user_id, rpc_top_k)

    # W11 Day 4 — 단일 문서 스코프 (US-08): RPC 결과 중 해당 doc_id 만 보존.
    # 응용 layer 필터 — RPC 결과 N 개 중 doc_id 일치만 통과 → 자연스럽게 dense·sparse·fused 카운트도 갱신.
    if doc_id is not None:
        rpc_rows = [r for r in rpc_rows if r.get("doc_id") == doc_id]

    # W13 Day 2 — ablation mode 응용 layer 필터 (008 split RPC 미사용 시에만 적용).
    # split RPC 사용 시 RPC 자체가 mode 분리 → 응용 필터 skip (한계 #74 회수).
    if not used_split_rpc:
        if mode == "dense":
            rpc_rows = [r for r in rpc_rows if r.get("dense_rank") is not None]
        elif mode == "sparse":
            rpc_rows = [r for r in rpc_rows if r.get("sparse_rank") is not None]

    # ------------------------------------------------------------------
    # 2-b) chunks 본문 통합 fetch — cover guard meta + reranker 입력 + 응답 조립 한 번에.
    #     W25 D14+1 (S2) — 기존엔 cover guard fetch (id, chunk_idx, page, text) 와
    #     응답 조립 fetch (id, doc_id, chunk_idx, page, section_title, text, metadata)
    #     가 분리. reranker 도입 시 candidate top-K 의 본문이 필요 → 한 번 fetch 로 통합.
    # ------------------------------------------------------------------
    candidate_chunk_ids: list[str] = []
    seen_cids: set[str] = set()
    for r in rpc_rows:
        cid = r["chunk_id"]
        if cid not in seen_cids:
            seen_cids.add(cid)
            candidate_chunk_ids.append(cid)

    chunks_by_id: dict[str, dict] = {}
    if candidate_chunk_ids:
        chunks_full_resp = (
            client.table("chunks")
            .select("id, doc_id, chunk_idx, page, section_title, text, metadata")
            .in_("id", candidate_chunk_ids)
            .execute()
        )
        chunks_by_id = {c["id"]: c for c in (chunks_full_resp.data or [])}

    cover_guard_meta: dict[str, dict] = {
        cid: {
            "chunk_idx": c.get("chunk_idx"),
            "page": c.get("page"),
            "text_len": len(c.get("text") or ""),
        }
        for cid, c in chunks_by_id.items()
    }

    def _is_cover_chunk(chunk_id: str) -> bool:
        meta = cover_guard_meta.get(chunk_id)
        if not meta:
            return False
        if meta["text_len"] > _COVER_GUARD_TEXT_LEN:
            return False
        return meta["chunk_idx"] == 0 or meta["page"] == 1

    # ------------------------------------------------------------------
    # 2-c) W25 D14+1 (S2) + S3 D4 — BGE-reranker cross-encoder 재정렬 (opt-in).
    #     활성 + candidates 2건 이상 시 다음 분기를 거친다 (planner v0.1 §F):
    #       1) reranker_cache hit → HF 호출 0, path=cached, RRF score 대체.
    #       2) free-tier degrade — 월간 호출 횟수 ≥ 임계 (default 80%) 시
    #          path=degraded, RRF score 그대로 정렬.
    #       3) cap 적용 — candidates[:_RERANKER_CANDIDATE_CAP] (default 20).
    #       4) HF 호출 → 성공 시 path=invoked, cache store + usage_log 기록.
    #       5) 실패 → RRF score 그대로 사용 (검색 자체 차단 X).
    #     query_parsed.reranker_used / reranker_fallback_reason / reranker_path
    #     + Response.headers["X-Reranker-Path"] 로 진단 노출.
    # ------------------------------------------------------------------
    reranker_enabled = (
        os.environ.get("JETRAG_RERANKER_ENABLED", _RERANKER_ENABLED_DEFAULT).lower()
        == "true"
    )
    reranker_used = False
    reranker_fallback_reason: str | None = None
    reranker_path = _RERANKER_PATH_DISABLED
    # cache hit / invoked 시 채워짐 — cover guard 가드 / MMR 후처리 진입 조건에 사용.
    reranker_score_by_id: dict[str, float] | None = None

    if reranker_enabled and len(rpc_rows) > 1 and candidate_chunk_ids:
        # 1) cache lookup — hit 시 HF 호출 skip + path=cached.
        cache_candidate_ids = candidate_chunk_ids[:_resolve_reranker_cap()]
        cached_scores = reranker_cache.lookup(clean_q, cache_candidate_ids)
        if cached_scores is not None:
            reranker_score_by_id = cached_scores
            for r in rpc_rows:
                cid = r["chunk_id"]
                if cid in cached_scores:
                    r["rrf_score"] = cached_scores[cid]
            reranker_path = _RERANKER_PATH_CACHED
        elif _is_reranker_degraded():
            # 2) free-tier degrade — HF 호출 skip, RRF score 유지.
            reranker_path = _RERANKER_PATH_DEGRADED
            logger.info("reranker 월간 호출 한도 임박 — degraded path 진입")
        else:
            # 3) cap 적용 + 4) HF 호출.
            rerank_pairs: list[tuple[str, str]] = []
            for cid in cache_candidate_ids:
                text = (chunks_by_id.get(cid) or {}).get("text") or ""
                rerank_pairs.append((cid, text))
            try:
                provider = get_reranker_provider()
                scores = provider.rerank(clean_q, rerank_pairs)
                score_by_id = {
                    cid: float(s) for (cid, _), s in zip(rerank_pairs, scores)
                }
                for r in rpc_rows:
                    cid = r["chunk_id"]
                    if cid in score_by_id:
                        r["rrf_score"] = score_by_id[cid]
                reranker_used = True
                reranker_path = _RERANKER_PATH_INVOKED
                reranker_score_by_id = score_by_id
                # cache store — 다음 동일 (query, chunks) 호출 시 HF skip.
                reranker_cache.store(clean_q, cache_candidate_ids, score_by_id)
                # vision_usage_log 에 invoke 1건 기록 — degrade 카운터의 SUM 기반.
                _record_reranker_invoke()
            except Exception as exc:  # noqa: BLE001
                if is_transient_reranker_error(exc):
                    reranker_fallback_reason = "transient"
                else:
                    reranker_fallback_reason = "permanent"
                logger.warning(
                    "reranker 호출 실패 → RRF fallback (reason=%s): %s",
                    reranker_fallback_reason,
                    exc,
                )

    # X-Reranker-Path 헤더 노출 (planner v0.1 §E). meta_fast / 0건 응답 분기 모두
    # 본 시점 이후로 통과하므로 본 위치에서 1회 set 면 충분.
    if response is not None:
        response.headers["X-Reranker-Path"] = reranker_path

    dense_hits = sum(1 for r in rpc_rows if r.get("dense_rank") is not None)
    sparse_hits = sum(1 for r in rpc_rows if r.get("sparse_rank") is not None)
    query_parsed = QueryParsedInfo(
        has_dense=dense_vec is not None,
        has_sparse=sparse_hits > 0,
        dense_hits=dense_hits,
        sparse_hits=sparse_hits,
        fused=len(rpc_rows),
        fallback_reason=fallback_reason,
        reranker_used=reranker_used,
        reranker_fallback_reason=reranker_fallback_reason,
        reranker_path=reranker_path,
        hyde_used=hyde_used,
        hyde_fallback_reason=hyde_fallback_reason,
    )

    if not rpc_rows:
        took_ms = int((time.monotonic() - start_t) * 1000)
        search_metrics.record_search(
            took_ms=took_ms,
            dense_hits=dense_hits,
            sparse_hits=sparse_hits,
            fused=0,
            has_dense=dense_vec is not None,
            fallback_reason=fallback_reason,
            embed_cache_hit=embed_cache_hit,
                mode=mode,
                query_text=clean_q,
        )
        return SearchResponse(
            query=clean_q,
            total=0,
            limit=limit,
            offset=offset,
            items=[],
            took_ms=took_ms,
            query_parsed=query_parsed,
        )

    # ------------------------------------------------------------------
    # 3) doc_id 별 RRF 그룹 (max score) + chunk_id 별 max score 집계
    # W25 D3 — chunk_id dedupe (C-1a). dense path + sparse path 가 같은 chunk_id 를
    # 별개 row 로 반환할 수 있어 list 누적 시 matched_chunk_count 가 부풀려짐.
    # dict[chunk_id, max_score] 로 누적해 unique chunk 수 = matched_chunk_count 보장.
    # ------------------------------------------------------------------
    doc_score: dict[str, float] = {}
    doc_chunk_scores: dict[str, dict[str, float]] = defaultdict(dict)
    for r in rpc_rows:
        doc_id = r["doc_id"]
        chunk_id = r["chunk_id"]
        score = float(r["rrf_score"])
        # W25 D4 Phase 2 — 표지 청크 가드: 짧은 chunk_idx=0 또는 page=1 청크 score 패널티.
        # W25 D14+1 (S2) — reranker 활성 시 cross-encoder 가 본질 처리 → 곱셈 skip.
        # S3 D4 — cache hit (path=cached) 도 cross-encoder score 가 그대로 들어와
        # 같은 이유로 곱셈 skip. degraded / disabled / RRF fallback 시에는 여전히 적용.
        cover_guard_skip = reranker_used or reranker_path == _RERANKER_PATH_CACHED
        if not cover_guard_skip and _is_cover_chunk(chunk_id):
            score *= _COVER_GUARD_PENALTY
        doc_score[doc_id] = max(doc_score.get(doc_id, 0.0), score)
        existing = doc_chunk_scores[doc_id].get(chunk_id)
        if existing is None or score > existing:
            doc_chunk_scores[doc_id][chunk_id] = score

    candidate_doc_ids = list(doc_score.keys())

    # ------------------------------------------------------------------
    # 4) documents 메타 fetch + 메타 필터 4종 적용
    # W25 D14+1 (G/S4) — doc_embedding 도 함께 fetch (doc-level RRF 가산용).
    # 추가 query 0 — 기존 fetch 의 select 확장. 1024-dim 1024*8B = 8KB / doc, 50 docs ≈ 400KB.
    # ------------------------------------------------------------------
    docs_query = (
        client.table("documents")
        .select("id, title, doc_type, tags, summary, created_at, doc_embedding")
        .in_("id", candidate_doc_ids)
        .eq("user_id", user_id)
        .is_("deleted_at", "null")
    )
    if doc_type:
        docs_query = docs_query.eq("doc_type", doc_type)
    if tags:
        # GIN tags @> ARRAY[...] — 모든 요청 태그 포함 (AND)
        docs_query = docs_query.contains("tags", tags)
    if from_dt:
        docs_query = docs_query.gte("created_at", from_dt.isoformat())
    if to_dt:
        docs_query = docs_query.lte("created_at", to_dt.isoformat())
    docs_meta: dict[str, dict] = {
        d["id"]: d for d in (docs_query.execute().data or [])
    }

    # ------------------------------------------------------------------
    # 4-b) W25 D14+1 (G/S4) — doc-level embedding RRF 가산.
    #     dense_vec (query) 와 doc_embedding (summary+implications 임베딩) cosine sim →
    #     candidate docs 내 rank → 1/(k_rrf+rank) 를 doc_score 에 가산.
    #     chunks 단위 매칭에 doc 단위 의미 매칭 보강 — 카탈로그/표 chunks 가
    #     약한 doc 도 doc 요약 수준에선 강한 매칭이면 살아남음.
    #     doc_embedding NULL 인 doc 은 가산 0 (graceful skip).
    # ------------------------------------------------------------------
    doc_embedding_rrf_enabled = (
        os.environ.get(
            "JETRAG_DOC_EMBEDDING_RRF",
            _DOC_EMBEDDING_RRF_ENABLED_DEFAULT,
        ).lower()
        == "true"
    )
    doc_embedding_rrf_used = False
    doc_embedding_hits = 0

    if doc_embedding_rrf_enabled and dense_vec is not None and docs_meta:
        cosine_by_doc: dict[str, float] = {}
        for did, meta in docs_meta.items():
            emb = meta.get("doc_embedding")
            if not emb:
                continue
            # Supabase pgvector 응답이 string ("[1.0,2.0,...]") 또는 list — 둘 다 처리.
            if isinstance(emb, str):
                try:
                    emb = [float(x) for x in emb.strip("[]").split(",")]
                except ValueError:
                    continue
            if not isinstance(emb, list) or len(emb) != 1024:
                continue
            sim = _cosine(dense_vec, emb)
            if sim is not None:
                cosine_by_doc[did] = sim

        if cosine_by_doc:
            # W25 D14+1 D3 — ablation 결과 (golden_v0.5_auto 43건 multi-doc):
            # k=10, weight=2.0 sweet spot — top-1 +2.32pp (0.9070 → 0.9302), MRR +1.55pp.
            # k=60 (chunks RRF 와 동일) 또는 weight 너무 크면 (5.0) 효과 0 — chunks RRF 와의 균형 중요.
            weight = float(os.environ.get("JETRAG_DOC_EMBEDDING_RRF_WEIGHT", "2.0"))
            k_rrf = int(os.environ.get("JETRAG_DOC_EMBEDDING_RRF_K", "10"))
            sorted_by_cos = sorted(
                cosine_by_doc.items(), key=lambda x: x[1], reverse=True
            )
            for rank, (did, _sim) in enumerate(sorted_by_cos, start=1):
                doc_score[did] = doc_score.get(did, 0.0) + weight * (1.0 / (k_rrf + rank))
            doc_embedding_rrf_used = True
            doc_embedding_hits = len(cosine_by_doc)

    # query_parsed 갱신 (4-b 결과 반영)
    query_parsed = QueryParsedInfo(
        has_dense=query_parsed.has_dense,
        has_sparse=query_parsed.has_sparse,
        dense_hits=query_parsed.dense_hits,
        sparse_hits=query_parsed.sparse_hits,
        fused=query_parsed.fused,
        fallback_reason=query_parsed.fallback_reason,
        reranker_used=query_parsed.reranker_used,
        reranker_fallback_reason=query_parsed.reranker_fallback_reason,
        reranker_path=query_parsed.reranker_path,
        doc_embedding_rrf_used=doc_embedding_rrf_used,
        doc_embedding_hits=doc_embedding_hits,
        hyde_used=query_parsed.hyde_used,
        hyde_fallback_reason=query_parsed.hyde_fallback_reason,
    )

    # ------------------------------------------------------------------
    # 5) RRF 점수 내림차순 정렬 + 페이지네이션
    # ------------------------------------------------------------------
    sorted_doc_ids = sorted(
        docs_meta.keys(), key=lambda did: doc_score[did], reverse=True
    )

    # ------------------------------------------------------------------
    # 5-b) S3 D4 — MMR 다양성 후처리 (cross_doc only, planner v0.1 §D).
    #     intent_router 가 T1_cross_doc 발화 시에만 적용 — 단일 doc query 는
    #     다양성보다 relevance 우선이라 skip. doc_embedding (1024d) 이 docs_meta
    #     에 있으면 cosine sim 기반 다양성 항 활성, 없으면 sim=0 → relevance 정렬
    #     보존 (회귀 0).
    # ------------------------------------------------------------------
    if (
        not mmr.is_disabled()
        and len(sorted_doc_ids) > 1
        and doc_id is None
        and _is_cross_doc_query(clean_q)
    ):
        doc_embeddings_by_id: dict[str, list[float]] = {}
        for did in sorted_doc_ids:
            emb_raw = (docs_meta.get(did) or {}).get("doc_embedding")
            emb_vec = _coerce_embedding(emb_raw)
            if emb_vec is not None:
                doc_embeddings_by_id[did] = emb_vec
        sorted_doc_ids = mmr.rerank(
            sorted_doc_ids,
            relevance=doc_score,
            embeddings_by_id=doc_embeddings_by_id,
            top_k=len(sorted_doc_ids),  # 전체 재정렬 → 페이지네이션은 그대로.
        )

    total_docs = len(sorted_doc_ids)
    page_doc_ids = sorted_doc_ids[offset : offset + limit]

    if not page_doc_ids:
        took_ms = int((time.monotonic() - start_t) * 1000)
        search_metrics.record_search(
            took_ms=took_ms,
            dense_hits=dense_hits,
            sparse_hits=sparse_hits,
            fused=len(rpc_rows),
            has_dense=dense_vec is not None,
            fallback_reason=fallback_reason,
            embed_cache_hit=embed_cache_hit,
                mode=mode,
                query_text=clean_q,
        )
        return SearchResponse(
            query=clean_q,
            total=total_docs,
            limit=limit,
            offset=offset,
            items=[],
            took_ms=took_ms,
            query_parsed=query_parsed,
        )

    # ------------------------------------------------------------------
    # 6) 페이지의 매칭 청크 본문 fetch (각 doc 의 RRF top N)
    # W25 D5 — doc_id 명시 시 cap 우회 (모든 unique 청크 본문 fetch).
    # 안전 상한 (_MAX_MATCHED_CHUNKS_DOC_SCOPE) 적용 — RPC 200건과 동일.
    # ------------------------------------------------------------------
    # NOTE: 위 line 3)·5) 의 `for r in rpc_rows: doc_id = ...` 루프에서 함수 파라미터 `doc_id`
    # 가 shadow 됨 (기존 코드 패턴). 본 cap 결정은 함수 진입 시 파라미터 값을 의도하므로
    # `is_doc_scope` 를 본 함수 진입 시 캡처된 사실 (rpc_top_k 결정 분기) 로부터 재구성한다.
    is_doc_scope = rpc_top_k == _RPC_TOP_K_DOC_FILTER
    chunk_cap = (
        _MAX_MATCHED_CHUNKS_DOC_SCOPE
        if is_doc_scope
        else _MAX_MATCHED_CHUNKS_PER_DOC
    )
    # W25 D14+1 (S2) — chunks 본문은 2-b) 단계에서 이미 candidate top-K 전체 fetch 됨.
    # selected_chunk_ids 는 페이지 내 응답 표시용 (chunks_by_id 의 부분집합) — 추가 fetch 불필요.
    selected_chunk_ids: list[str] = []
    for did in page_doc_ids:
        top_ids = sorted(
            doc_chunk_scores[did].items(), key=lambda x: x[1], reverse=True
        )[:chunk_cap]
        selected_chunk_ids.extend(cid for cid, _ in top_ids)
    # chunk_id → rrf_score 매핑 (페이지 내 응답에서만 사용).
    # doc_chunk_scores 가 이미 chunk_id 별 max 로 dedupe 됨 (W25 D3) — 단순 복사.
    chunk_rrf: dict[str, float] = {}
    for doc_id in page_doc_ids:
        chunk_rrf.update(doc_chunk_scores[doc_id])

    # ------------------------------------------------------------------
    # 7) 응답 조립 (relevance 는 결과 집합 내 정규화 — top=1.0)
    # ------------------------------------------------------------------
    top_score = doc_score[sorted_doc_ids[0]] if sorted_doc_ids else 1.0
    normalize = top_score if top_score > 0 else 1.0

    items: list[SearchHit] = []
    for doc_id in page_doc_ids:
        meta = docs_meta[doc_id]
        all_matches = doc_chunk_scores[doc_id]  # dict[chunk_id, max_score]
        matched_count = len(all_matches)  # unique chunk 수 (W25 D3 dedupe)
        # W25 D5 — list 모드는 top 3 미리보기 (chunk_idx 오름차순), doc 스코프는 모든 매칭 (score 내림차순).
        # doc 스코프는 사용자가 명시적으로 "모두 보기" 진입 — score 순이 의도.
        top_ids = [
            cid
            for cid, _ in sorted(
                all_matches.items(), key=lambda x: x[1], reverse=True
            )[:chunk_cap]
        ]
        if is_doc_scope:
            # score 내림차순 (id 순서 보존) — doc 페이지가 관련도 순으로 매칭 청크 표시
            top_chunks = [
                chunks_by_id[cid] for cid in top_ids if cid in chunks_by_id
            ]
        else:
            # list 모드 — chunk_idx 오름차순 (UX 일관: 본문 등장 순서대로 노출)
            top_chunks = sorted(
                (chunks_by_id[cid] for cid in top_ids if cid in chunks_by_id),
                key=lambda c: c["chunk_idx"],
            )

        matched_chunks = []
        for c in top_chunks:
            snippet, highlights = _make_snippet_with_highlights(
                c.get("text") or "", clean_q
            )
            chunk_meta = c.get("metadata") or None
            matched_chunks.append(
                MatchedChunk(
                    chunk_id=c["id"],
                    chunk_idx=c["chunk_idx"],
                    text=snippet,
                    page=c.get("page"),
                    section_title=c.get("section_title"),
                    highlight=highlights,
                    rrf_score=chunk_rrf.get(c["id"]),
                    metadata=chunk_meta if chunk_meta else None,
                )
            )

        items.append(
            SearchHit(
                doc_id=doc_id,
                doc_title=meta.get("title") or "",
                doc_type=meta.get("doc_type") or "",
                tags=meta.get("tags") or [],
                summary=meta.get("summary"),
                created_at=meta.get("created_at") or "",
                relevance=round(min(1.0, doc_score[doc_id] / normalize), 4),
                matched_chunk_count=matched_count,
                matched_chunks=matched_chunks,
            )
        )

    took_ms = int((time.monotonic() - start_t) * 1000)
    search_metrics.record_search(
        took_ms=took_ms,
        dense_hits=dense_hits,
        sparse_hits=sparse_hits,
        fused=len(rpc_rows),
        has_dense=dense_vec is not None,
        fallback_reason=fallback_reason,
        embed_cache_hit=embed_cache_hit,
                mode=mode,
                query_text=clean_q,
    )
    return SearchResponse(
        query=clean_q,
        total=total_docs,
        limit=limit,
        offset=offset,
        items=items,
        took_ms=took_ms,
        query_parsed=query_parsed,
    )


# ---------------------- helpers ----------------------


def _run_meta_fast_path(
    *,
    clean_q: str,
    plan: meta_filter_fast_path.MetaFilterPlan,
    limit: int,
    offset: int,
    user_id: str,
    response: Response | None,
    start_t: float,
) -> SearchResponse:
    """S3 D2 — 메타 필터 fast path 실행 + SearchResponse 조립.

    임베딩/RPC/reranker 호출 0. documents SELECT 1회.
    응답 schema 는 RAG path 와 동일 (matched_chunks 는 빈 list — 메타만 매칭이라
    개별 청크 매칭 정보는 없음).
    """
    rows = meta_filter_fast_path.run(plan, user_id=user_id)
    paged = rows[offset : offset + limit]

    items: list[SearchHit] = []
    for r in paged:
        items.append(
            SearchHit(
                doc_id=r["id"],
                doc_title=r.get("title") or "",
                doc_type=r.get("doc_type") or "",
                tags=r.get("tags") or [],
                summary=r.get("summary"),
                created_at=r.get("created_at") or "",
                relevance=1.0,  # 메타 매칭은 boolean — 동일 점수
                matched_chunk_count=0,
                matched_chunks=[],
            )
        )

    took_ms = int((time.monotonic() - start_t) * 1000)
    if response is not None:
        response.headers["X-Search-Path"] = "meta_fast"

    return SearchResponse(
        query=clean_q,
        total=len(rows),
        limit=limit,
        offset=offset,
        items=items,
        took_ms=took_ms,
        query_parsed=QueryParsedInfo(
            has_dense=False,
            has_sparse=False,
            dense_hits=0,
            sparse_hits=0,
            fused=len(rows),
        ),
        meta={
            "path": "meta_fast",
            "matched_kind": plan.matched_kind,
            "tags": list(plan.tags),
            "title_ilike": plan.title_ilike,
            "date_range": (
                [plan.date_range[0].isoformat(), plan.date_range[1].isoformat()]
                if plan.date_range
                else None
            ),
        },
    )


def _resolve_reranker_cap() -> int:
    """S3 D4 — `JETRAG_RERANKER_CANDIDATE_CAP` 해석. invalid 시 default 20.

    range [_RERANKER_CAP_MIN, _RERANKER_CAP_MAX] 밖이면 default — planner v0.1
    "5~50 권장" 가드. 음수 / 비숫자도 default 로 회복.
    """
    raw = os.environ.get(_ENV_RERANKER_CAP)
    if raw is None or raw == "":
        return _RERANKER_CANDIDATE_CAP
    try:
        value = int(raw)
    except ValueError:
        return _RERANKER_CANDIDATE_CAP
    if value < _RERANKER_CAP_MIN or value > _RERANKER_CAP_MAX:
        return _RERANKER_CANDIDATE_CAP
    return value


def _is_reranker_degraded() -> bool:
    """S3 D4 — 월간 reranker 호출 횟수가 임계 (default 80%) 도달 여부.

    `vision_usage_log` 의 `source_type='reranker_invoke'` row 가 최근 30일
    이내 ``JETRAG_RERANKER_MONTHLY_CAP_CALLS * JETRAG_RERANKER_DEGRADE_THRESHOLD``
    이상이면 degrade. DB 부재 / 마이그 014 미적용 시 graceful False (가드 비활성).
    """
    cap = _env_int(_ENV_RERANKER_MONTHLY_CAP, _RERANKER_MONTHLY_CAP_DEFAULT)
    threshold = _env_float(
        _ENV_RERANKER_DEGRADE_THRESHOLD, _RERANKER_DEGRADE_THRESHOLD_DEFAULT
    )
    if cap <= 0 or threshold <= 0.0:
        return False
    used = _count_reranker_invokes_last_30d()
    if used is None:
        return False
    return used >= cap * threshold


def _record_reranker_invoke() -> None:
    """`vision_usage_log` 에 reranker invoke 1건 기록 — degrade 카운터 SUM 기반.

    D3 `query_decomposer._record_usage` 와 동일 패턴. DB 부재 시 graceful skip.
    estimated_cost 는 NULL — reranker 는 무료 티어 호출 수만 카운트.
    """
    try:
        client = get_supabase_client()
        client.table("vision_usage_log").insert(
            {
                "success": True,
                "quota_exhausted": False,
                "source_type": _USAGE_LOG_RERANKER_SOURCE_TYPE,
            }
        ).execute()
    except Exception as exc:  # noqa: BLE001 — DB 부재 graceful
        logger.debug("reranker invoke 기록 실패 (graceful): %s", exc)


def _count_reranker_invokes_last_30d() -> int | None:
    """최근 30일 내 `source_type='reranker_invoke'` row 수. 실패 시 None.

    SUM 이 아닌 COUNT — 자체 호출 횟수 카운터 (사용자 결정 Q-S3-D4-1).
    `success=true` 만 집계 (실패 호출은 quota 차감 없음).
    """
    try:
        from datetime import timedelta

        client = get_supabase_client()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        resp = (
            client.table("vision_usage_log")
            .select("call_id", count="exact")
            .eq("source_type", _USAGE_LOG_RERANKER_SOURCE_TYPE)
            .eq("success", True)
            .gte("called_at", cutoff)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 — DB 부재 graceful
        logger.debug("reranker invoke COUNT 실패 (graceful): %s", exc)
        return None
    # supabase-py count="exact" → resp.count 노출. 호환 위해 fallback 도 유지.
    count = getattr(resp, "count", None)
    if count is not None:
        return int(count)
    return len(resp.data or [])


def _is_cross_doc_query(query: str) -> bool:
    """S3 D4 — intent_router T1_cross_doc 트리거 여부.

    MMR 적용 범위 한정 (사용자 결정 Q-S3-D4-2). intent_router 는 외부 API 0
    이라 latency 영향 무시 가능. 빈 query 등 ValueError graceful False.
    """
    try:
        decision = intent_router.route(query)
    except ValueError:
        return False
    return "T1_cross_doc" in decision.triggered_signals


def _coerce_embedding(raw) -> list[float] | None:
    """pgvector 응답 (str "[1.0,2.0,...]" 또는 list) → list[float]. 실패 시 None.

    `doc_embedding_rrf_used` 분기와 동일 패턴 — 1024 dim 검증.
    """
    if not raw:
        return None
    if isinstance(raw, str):
        try:
            return [float(x) for x in raw.strip("[]").split(",")]
        except ValueError:
            return None
    if isinstance(raw, list) and len(raw) == 1024:
        return [float(x) for x in raw]
    return None


def _env_int(key: str, default: int) -> int:
    """ENV → int. invalid / 음수 시 default."""
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if value < 0:
        return default
    return value


def _env_float(key: str, default: float) -> float:
    """ENV → float. invalid / 음수 시 default."""
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if value < 0.0:
        return default
    return value


def _cosine(a: list[float], b: list[float]) -> float | None:
    """W25 D14+1 (G) — 두 벡터의 cosine similarity. 의존성 0 (numpy 미사용).

    1024 dim × 50 docs 정도면 수 ms 이내. 0 벡터 발생 시 None 반환 (가산 skip).
    """
    if len(a) != len(b):
        return None
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a <= 0.0 or norm_b <= 0.0:
        return None
    return dot / ((norm_a ** 0.5) * (norm_b ** 0.5))


def _sparse_only_fallback(
    client, q: str, user_id: str, top_k: int
) -> list[dict]:
    """HF API 실패 시 — PGroonga 한국어 형태소 매칭만으로 검색.

    W3 v0.5 §3.A (DE-60) 적용 — `search_sparse_only_pgroonga` RPC 호출.
    PostgREST 가 PGroonga `&@~` 연산자를 직접 노출하지 않으므로 RPC 캡슐화.

    이전 (003 simple FTS) 의 한계 (ts_rank 정렬 미노출, E-6) 를 본 RPC 가 해결:
        - 정렬 보장 (`ORDER BY pgroonga_score(...) DESC`)
        - deleted_at IS NULL 필터를 RPC 내부에서 적용 (E-4 일관)
        - flags.filtered_reason 자동 제외 (DE-62)
    """
    rpc_resp = client.rpc(
        "search_sparse_only_pgroonga",
        {
            "query_text": q,
            "user_id_arg": str(user_id),
            "top_k": top_k,
        },
    ).execute()
    rows = rpc_resp.data or []
    out: list[dict] = []
    for r in rows:
        rank = int(r["sparse_rank"])
        out.append(
            {
                "chunk_id": r["chunk_id"],
                "doc_id": r["doc_id"],
                "rrf_score": 1.0 / (_RRF_K + rank),
                "dense_rank": None,
                "sparse_rank": rank,
            }
        )
    return out


def _parse_iso_date(value: str | None, field: str) -> datetime | None:
    """`YYYY-MM-DD` 또는 ISO 8601 datetime 을 tz-aware datetime 으로 파싱.

    날짜만 입력하면 UTC 0시로 간주.
    """
    if not value:
        return None
    try:
        if len(value) == 10:  # YYYY-MM-DD
            return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
        normalized = (
            value.replace("Z", "+00:00") if value.endswith("Z") else value
        )
        dt = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field}='{value}' 가 ISO 8601 형식이 아닙니다.",
        ) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _make_snippet_with_highlights(
    text: str, query: str, around: int = _SNIPPET_AROUND
) -> tuple[str, list[list[int]]]:
    """매칭 위치 ±around 로 자른 스니펫 + 그 스니펫 내 매칭 구간 [start, end] 리스트.

    리터럴 부분 문자열 매칭만 — 하이브리드 RRF 결과의 chunks 가 항상 q 를
    리터럴로 포함하지는 않으므로, 매칭 0건이면 본문 앞부분만 반환 (highlight=[]).
    """
    if not text or not query:
        return text[: around * 2], []

    text_lower = text.lower()
    q_lower = query.lower()
    q_len = len(query)

    first_idx = text_lower.find(q_lower)
    if first_idx == -1:
        return text[: around * 2], []

    start = max(0, first_idx - around)
    end = min(len(text), first_idx + q_len + around)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    snippet = f"{prefix}{text[start:end]}{suffix}"

    snippet_lower = snippet.lower()
    highlights: list[list[int]] = []
    pos = 0
    while True:
        hit = snippet_lower.find(q_lower, pos)
        if hit == -1:
            break
        highlights.append([hit, hit + q_len])
        pos = hit + q_len
    return snippet, highlights
