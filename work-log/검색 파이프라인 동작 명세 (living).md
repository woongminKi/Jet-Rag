# 검색 파이프라인 동작 명세 (living)

> **Living document** — 검색 모델·인제스트 stage·검색 로직·표시 정책이 변경 또는 고도화될 때마다 갱신.
> 마지막 갱신: 2026-05-13 (M0-a W-15 — S3 / S4-A·B / cross_doc P0·P1 / W-6 eval 가드 반영, 1차 갱신)
> 버전: v0.3
>
> ⚠️ **갱신 단계**: 본 v0.3 은 PRD `2026-05-12 검색 정확도 80% 달성 PRD.md` §3 W-15 의 **1차 갱신** (S3·S4·cross_doc P0/P1·W-6 까지).
> M1 의 W-1(a) — `query_decomposer` 의 `/search` 노출 + W-3 chunk text augmentation + W-4 전체 클린 재인제스트 후 = **2차 갱신 예정** (그때 §4.0·§7·§9 재정비).

---

## 갱신 정책

- **Sprint 마감 시 본 문서 갱신 의무** — work-log 핸드오프 작성 후 본 문서에 반영
- **§9 변경 이력** 섹션에 한 줄 추가 (`날짜 / W·Day / 변경 요약 / 영향 범위 / commit`)
- 본문 §1~§8 의 표·다이어그램은 현재 시점 정확값 으로 유지 (이전 값은 §9 이력으로 이동)
- 코드 라인 번호 인용 시 commit 명시 (예: `search.py:40 (commit 72e14ca)`) — 향후 line shift 대비
- 코드 라인 번호는 **모듈·상수 이름 우선** — line 은 부수적 (line shift 잦음)

---

## 0. 전체 파이프라인 (3단계)

```
[1] 업로드     [2] 인제스트 9 stage     [3] 검색 (의도 라우팅 → Hybrid RRF)
PDF 등  →  detect → extract → chunk → ... → DB  ←→  자연어 질문
                                                    → intent_router (룰)
                                                    → meta_filter fast path? (메타만)
                                                    → (paid) query decomposition? (/answer 만)
                                                    → Hybrid RRF → (reranker: 운영 OFF) → (MMR: cross_doc만) → top-N
```

검색 단계는 W25 이후 **순수 RRF 단일 경로**에서 **의도 라우팅 + 보조 손잡이 다단**으로 확장됨 (S3·S4·cross_doc P0/P1). 단 **운영 default 동작은 여전히 "Hybrid RRF + 표지/TOC 가드 + doc 그룹"** 이고, reranker·MMR·entity_boost·decomposition 은 모두 default OFF 또는 좁은 발화 범위라 평균 query 경로는 v0.2 와 사실상 동일하다. (§4.0 참조 — "default 경로 vs 손잡이별 발화 조건" 분리.)

---

## 1. 인제스트 9 stage (확장자 무관 공통 흐름)

| Stage | 동작 | 핵심 모듈 | 변경 빈도 |
|---|---|---|---|
| **1. detect** | magic bytes + 확장자 → `doc_type` 결정 | `api/app/ingest/stages/detect.py` | 낮음 |
| **2. content_gate** | 빈 / sha256 dedupe 거부 | `content_gate.py` | 낮음 |
| **3. extract** | **확장자별 parser → 텍스트 + page 정보** (§2 매트릭스). vision 호출 시 표/그림 **caption 추출 동반** (S4-A — `chunks.metadata.caption`). | `adapters/impl/*_parser.py` | 중간 |
| **4. chunk** | 800자 target / 200자 min / page 경계 분리. S4-A 시 표/그림 chunk 에 caption 신호 부착 (`metadata.caption`). | `chunk.py` (`_TARGET_SIZE=800` `_MIN_MERGE_SIZE=200`) | 낮음 (정책 변경 시 재인덱싱) |
| **5. chunk_filter** | `extreme_short`(≤20자) / `table_noise` 마킹 | `chunk_filter.py` (`_EXTREME_SHORT_LEN=20` `_SHORT_LINE_RATIO_TH=0.90`) | 낮음 |
| **5.5 entity_extract** | 룰 기반 entity 추출 (dates / amounts / percentages / identifiers) → `chunks.metadata.entities`. **검색 영향 없음** — query 측 entity 매칭 모수가 거의 0 이라 `entity_boost` 가 default OFF (§4.4). | `services/entity_extract.py` (ingest 통합은 `chunk.py`) | 낮음 |
| **6. embed** | BGE-M3 (HF API) → **1024-dim 벡터** chunk 별 (`embed_passages` / `embed_batch`). HF free-tier cold-start (scale-to-zero) 시 3회 retry + 지수 백오프 + 서버 `Retry-After` 존중. | `bgem3_hf_embedding.py` | 중간 (모델 교체 시) |
| **7. tag_summarize** | Gemini 2.0 Flash → 자동 태그 + 요약 (LLM 1회/doc) | `gemini_llm.py` | 중간 (프롬프트 갱신) |
| **8. doc_embed** | 요약 기반 문서 단위 임베딩 1024-dim | `doc_embed.py` | 낮음 |
| **9. load (persist)** | Supabase Postgres + Storage 저장 | `supabase_*.py` | 낮음 |

→ 결과: `documents`(메타) + `chunks`(text + 1024-dim 벡터 + flags + page + `metadata.caption`/`metadata.entities`) 테이블에 저장.
→ **현재 DB 상태 (2026-05-13)**: chunks 2469 row / `metadata.entities` 보유 441 row (S4-B 백필 `evals/backfill_chunk_entities.py --apply`) / vision_page_cache 191 row.

---

## 2. 확장자 별 추출 (extract stage) 차이

| 확장자 | Parser | page 정보 | OCR | 특이점 |
|---|---|---|---|---|
| **PDF** (텍스트) | PyMuPDF (`pymupdf_parser.py`) | ✅ 정확한 페이지 | 본문 추출 우선 | 페이지별 텍스트 + heading 휴리스틱 (W4) |
| **PDF** (스캔) | PyMuPDF → text<5자 감지 → Vision fallback | ✅ | ✅ Gemini Vision | quota 보호 cap (W9). vision 호출 시 표/그림 caption 동반 추출 (S4-A) |
| **이미지** (PNG/JPG/WEBP) | `image_parser.py` → Gemini Vision | ❌ (page=null) | ✅ Gemini 2.0 Flash 캡셔닝 + OCR | 메신저/문서/화이트보드 분류 (W8) |
| **HWP** (구버전, OLE) | `hwp_parser.py` (olefile + bodytext stream) | ❌ (page=null) | ❌ | 한글 5.x 포맷, 표 병합 휴리스틱 |
| **HWPX** (신버전, ZIP+XML) | `hwpx_parser.py` | ❌ (page=null) | ❌ | XML namespace 파싱, 표 행 단위 추출 |
| **DOCX** | `docx_parser.py` (python-docx) | ❌ (page=null) | ❌ | 단락 + 표 + 헤딩 |
| **PPTX** | `pptx_parser.py` (python-pptx) | ✅ 슬라이드 번호 = page | ❌ | 슬라이드 텍스트 + 노트 (W8) |
| **TXT/MD** | 단순 read | ❌ (page=null) | ❌ | 그대로 |
| **URL** | `url_parser.py` (readability) | ❌ (page=null) | ❌ | HTML → 본문 추출 (W2) |

→ **page=null 인 doc_type 은 표지 가드 (§4.3) 영향 없음** (`page=1` 조건 fail). 가드는 PDF/PPTX 만.

---

## 3. DB 스키마 (검색 관련)

### 3.1 `chunks` 테이블 (`api/migrations/001_init.sql` + 후속)

| 컬럼 | 타입 | 용도 |
|---|---|---|
| `id` | UUID PK | chunk 식별 |
| `doc_id` | UUID FK | 부모 문서 |
| `chunk_idx` | int | 문서 내 순서 (0부터) |
| `text` | text | 본문 (chunk 단위) |
| `dense_vec` | vector(1024) | BGE-M3 임베딩 |
| `page` | int | 페이지 번호 (PDF/PPTX), nullable |
| `section_title` | text | heading 추출 결과 |
| `flags` | jsonb | `is_cover`(미구현) / `filtered_reason`(extreme_short, table_noise 등) |
| `metadata` | jsonb | `overlap_with_prev_chunk_idx` / `caption`(S4-A — 표/그림 caption 신호) / `entities`(S4-B 룰 기반 — dates·amounts·percentages·identifiers, 441/2469 백필됨 / 검색 영향 없음 §4.4) |

### 3.2 `documents` 테이블

| 컬럼 | 용도 |
|---|---|
| `id` | UUID PK |
| `title` / `doc_type` / `tags` | 메타 |
| `summary` | LLM 요약 |
| `doc_embedding` | vector(1024), 요약 기반 |
| `sha256` | dedupe (`UNIQUE(user_id, sha256)`) |

### 3.3 보조 테이블 (검색·인제스트 부수)

| 테이블 | 마이그 | 용도 |
|---|---|---|
| `embed_query_cache` | 016 | **query → dense 벡터 영구 캐시** — key = `(sha256(NFC(text.strip())), model_id)`, value = 1024-dim. eval 재현성 + HF cold-start 부수 완화. in-process LRU(512) → 영구 캐시 → HF 의 2단 룩업 (`bgem3_hf_embedding.embed_query`). ENV `JETRAG_EMBED_QUERY_CACHE`(기본 `1`, `0` 으로 disable). |
| `vision_usage_log` | 014 | vision quota 카운터 — `source_type`/`model_used` 컬럼으로 reranker invoke(`reranker_invoke`) / query decomposition(`query_decomposition`) / vision 호출을 분리 집계 (degrade·budget cap 산출). |
| `vision_page_cache` | 015 | PDF 페이지 → vision 결과 캐시 (재인제스트 시 paid 절감, 191건). |

---

## 4. 검색 (질문 입력 → 결과 표시)

### 4.0 검색 경로 개요 — default 동작 vs 손잡이별 발화 조건

`/search` 의 **운영 default 동작**은 ① intent_router 룰 1회(외부 API 0) → ② meta filter fast path 진입 시 즉시 응답 / 아니면 → ③ Hybrid RRF RPC → ④ 표지·TOC 가드 + doc 그룹 + dedupe → ⑤ 응답 조립 (matched_chunks cap·정렬·snippet) 이다. v0.2 의 RRF 경로 + 가드는 그대로다.

추가 손잡이들은 **default OFF 또는 좁은 발화 범위**:

| 손잡이 | default | 발화 조건 | 효과 |
|---|---|---|---|
| **meta_filter fast path** (§4.5) | ON (룰) | query 가 순수 메타 필터 (날짜·태그·doc명 단독 + 의문 동사구 잔존 X) | 임베딩·RPC·reranker 0 → documents SELECT 1회 |
| **HyDE** | OFF | ENV `JETRAG_HYDE_ENABLED=true` | query → Gemini hypothetical doc → (query+doc) 임베딩 |
| **query expansion** | OFF | ENV `JETRAG_QUERY_EXPANSION=true` | query 변형 후 RPC (`query_expansion.py`) |
| **paid query decomposition** (§4.6) | OFF · **`/answer` 전용** | ENV `JETRAG_PAID_DECOMPOSITION_ENABLED=true` + intent_router `needs_decomposition=True` | sub-query 2~5개 → RRF merge. **`/search` 미배선** (W-1(a)에서 노출 예정) |
| **entity_boost** (§4.4) | OFF (사실상 무효) | ENV `JETRAG_ENTITY_BOOST=true` | query↔chunk entity 매칭 시 RRF score × `JETRAG_ENTITY_BOOST_FACTOR`(1.10). 단 query 측 entity 모수 ≈ 0 → ablation 효과 0 |
| **vision_adjacent_boost** | OFF | ENV `JETRAG_VISION_ADJACENT_BOOST=true` | 같은 page 의 caption chunk 가 candidates 에 있으면 인접 chunk score propagate |
| **doc_embedding RRF 가산** | OFF | ENV `JETRAG_DOC_EMBEDDING_RRF=true` | `documents.doc_embedding` ↔ query_dense cosine → doc-level rank 가산 |
| **BGE reranker** (§4.7) | **OFF (운영 확정)** | ENV `JETRAG_RERANKER_ENABLED=true` | RRF top-K → cross-encoder 재정렬. **D6 결론: 회복 DB 에서 net-negative → 운영 default OFF 확정** |
| **MMR 다양성** (§4.8) | ON (단 cross_doc 만) | intent_router `T1_cross_doc` 발화 + doc_id 미지정 + ≥2 doc + `JETRAG_MMR_DISABLE!=1` | doc 다양성 재정렬 (한 doc 의 청크 독점 방지) |
| **cross_doc-class chunk cap 8** (§4.9) | ON | intent_router 의 `T1_cross_doc`/`T2_compare`/`T7_multi_target` 중 하나 발화 + doc_id 미지정 | matched_chunks doc 당 cap 3 → 8 |

### 4.1 입력 처리

`/search?q=...&mode=hybrid&doc_id=...&limit=10&offset=0&tags=...&doc_type=...&from_date=...&to_date=...`
- `api/app/routers/search.py` (`search()` 핸들러)
- `limit`: `Query(10, ge=1, le=50)` — 최대 50
- `mode`: `hybrid`(default, dense+sparse RRF) / `dense` / `sparse` — `dense`/`sparse` 는 split RPC(`search_hybrid_rrf` 호출 대신 분리 RPC + 응용 layer 필터). KPI #7(하이브리드 우세) ablation 인프라가 이를 활용 예정.
- query: NFC 정규화 + `_MAX_QUERY_LEN`(200) 초과 시 거부.

### 4.2 RPC 단계 — Hybrid RRF (002·003·004·008 마이그레이션)

```
질문 "소나타 시트 종류"
     ↓
intent_router.route()  ← 룰 1회 (외부 API 0) — triggered_signals / needs_decomposition / confidence
     ↓ (meta-only 면 §4.5 fast path 로 즉시 응답)
[A] BGE-M3 임베딩 (1024-dim, HF API) — embed_query 2단 캐시: in-process LRU(512) → embed_query_cache(DB, 마이그 016) → HF
     ↓                                  ↓
[B] Dense 검색                  [C] Sparse 검색
    pgvector cosine                 PGroonga `&@~` (한국어 Mecab 토크나이저)
    chunks.dense_vec <=> query      chunks.text 자연어 매칭
    rank_dense (1~50)               rank_sparse (1~50)
                  ↓
[D] RRF Fusion (003_hybrid_search.sql / 004_pgroonga_korean_fts.sql)
    score = 1/(60 + rank_dense) + 1/(60 + rank_sparse)
                  ↓
[D'] (opt-in) BGE reranker — RRF top-K(≤cap, default 20) → cross-encoder score 대체. 운영 default OFF (§4.7).
                  ↓
[E] doc 단위 그룹
    doc_score[doc_id] = max(chunk_score)
                  ↓
[F] 표지 가드 (W25 D4)
    text_len ≤ 30 AND (chunk_idx=0 OR page=1) → score × 0.3   (reranker 활성 시 skip)
                  ↓
[F'] TOC 가드 — 목차 패턴 chunk penalty (ENV `JETRAG_TOC_GUARD_ENABLED`, default ON)
                  ↓
[G] chunk_id dedupe — dense+sparse path 동일 chunk → dict[chunk_id, max_score] 1번만
                  ↓
[G'] (opt-in) doc_embedding RRF 가산 / entity_boost / vision_adjacent_boost — 전부 default OFF (§4.0)
                  ↓
[H] doc_score desc 정렬 → (cross_doc + 다중 doc 면) MMR 재정렬 (§4.8) → 페이지네이션
                  ↓
[I] 응답 조립
    - matched_chunks cap:
        * list 모드 (doc_id 미명시) 기본: 3 (`_MAX_MATCHED_CHUNKS_PER_DOC`)
        * list 모드 + cross_doc-class query (T1/T2/T7): 8 (`_MAX_MATCHED_CHUNKS_PER_DOC_CROSS_DOC` — S4-A P1, §4.9)
        * doc 스코프 (doc_id 명시): 200 (`_MAX_MATCHED_CHUNKS_DOC_SCOPE`, W25 D5)
    - 정렬:
        * list (일반) = chunk_idx asc (본문 등장 순서)
        * list + cross_doc-class = RRF score desc (관련도 순)
        * doc 스코프 = RRF score desc (관련도 순)
    - relevance = doc_score / top_doc_score (top-1 항상 100%, 상대 점수)
    - snippet = 매칭 위치 ±240자 (env `SEARCH_SNIPPET_AROUND`)
```

### 4.3 표지 가드 상세 (W25 D4)

| 조건 (모두 만족) | 처리 |
|---|---|
| `text_len ≤ _COVER_GUARD_TEXT_LEN` (= 30) | `score *= _COVER_GUARD_PENALTY` (= 0.3) |
| AND (`chunk_idx == 0` OR `page == 1`) | |

- **reranker 활성** (`reranker_used` or `reranker_path == cached`) 시 표지 가드 곱셈 skip — cross-encoder 가 본질적으로 처리하므로 이중 적용 방지.
- **TOC 가드** (`_TOC_GUARD_ENABLED_ENV` = `JETRAG_TOC_GUARD_ENABLED`, default ON): 목차/색인 패턴 chunk 는 키워드 매칭이 강해 RRF top 을 차지하는데, 표지 가드는 `text_len ≤ 30` 조건에 안 걸리므로 별도 TOC penalty 로 후순위화. 회귀 시 ENV `false` 로 즉시 회복.

### 4.4 entity_boost — S4-B (default OFF, 사실상 무효)

- `chunk.py` ingest 시 `entity_extract.extract_entities()` 가 chunk 의 dates/amounts/percentages/identifiers 를 `metadata.entities` 에 적재 (441/2469 백필됨).
- 검색 시 `JETRAG_ENTITY_BOOST=true` 면 query 의 entity 와 chunk 의 entity 가 매칭되는 chunk 의 RRF score 에 `JETRAG_ENTITY_BOOST_FACTOR` (default 1.10, [0.5,3.0] 클램프 — `_parse_factor_env`) 를 곱함.
- **2026-05-12 ablation 결론**: golden_v2 183 query 중 `extract_entities(query)` non-empty 1건뿐(G-U-019 "12%"), 그마저 정답 chunk 매칭 0 → baseline(OFF)/factor 1.10/1.50/2.00 metric 전부 동일 (R@10·top-1·nDCG·MRR Δ = 0.0000). **→ entity_boost = cross_doc 약점 레버 아님, production default OFF 확정** (코드/ENV 무변경). entity_boost 자체를 켜고 끄는 별도 토글 없이 factor 만 조정 — factor 1.10(미설정) ⇒ `JETRAG_ENTITY_BOOST=true` 일 때만 곱셈 진입이라 사실상 default OFF.

### 4.5 meta_filter fast path — S3 D2

- `meta_filter_fast_path.is_meta_only(query)` → `MetaFilterPlan | None`. 룰 detector (정규식 + 키워드, 외부 API 0).
- 진입 조건: query 가 ① 날짜 표현(`어제`, `2025년 3월` 등) ② `#태그` ③ doc-suffix 명사구(`문서`/`보고서`/`자료`/`회의록`/`기획서`/`파일`/`리포트`/`요약` 등 명시적 suffix) 중 하나 이상만 매칭 + **의문/서술 동사구 잔존 X**. 명사 단독("결론", "소나타 시트 종류")은 RAG path 로 fallback (회귀 0).
- 매칭 우선순위: `date_range` > `tags` > `title_ilike`. 여러 종류 동시 매칭 시 executor 가 AND 결합.
- 진입 시: `meta_filter_fast_path.run(plan, user_id=...)` → `documents` SELECT 1회. 임베딩·RPC·reranker 0. 응답 schema 는 RAG path 와 동일 (`matched_chunks` 는 빈 list — 메타만 매칭).
- `/search` 와 `/answer` 양쪽이 진입점에서 호출.

### 4.6 query_decomposer (paid LLM decomposition) — S3 D3 · **현재 `/answer` 전용**

- `query_decomposer.decompose(query, intent_router_decision)` → `QueryDecomposition(subqueries, cost_usd, cached, skipped_reason)`.
- **gating 4중**: ① `decision.needs_decomposition=False` → skip ② ENV `JETRAG_PAID_DECOMPOSITION_ENABLED!=true` → skip (default OFF) ③ LRU cache(200건) hit → cost 0 / cached=True ④ budget cap 초과(`JETRAG_DECOMPOSITION_MONTHLY_CAP_USD`, default 0.30 — `vision_usage_log` 의 `source_type='query_decomposition'` SUM) → skip + reason. 전부 통과 시에만 LLM 호출.
- LLM: factory `purpose="decomposition"` → Gemini 2.5 Flash-Lite (무료 RPD), temperature 0.1, max_output 200 tokens. 출력 = JSON array (2~5 sub-query, 각 30자 이내). markdown fence/prefix 포함해도 첫 `[...]` 만 추출. 파싱 실패/길이 이탈/LLM raise → 빈 tuple + 한국어 reason (graceful — 호출자는 단일 query 검색으로 fallback, 회귀 0).
- **배선 상태**: `api/app/routers/answer.py` 가 `intent_router.route(clean_q)` 후 `query_decomposer.decompose(...)` 호출 → `decomp.subqueries` 가 있으면 `_gather_chunks_with_decomposition` (원본 query top_k=20 + sub-query 별 top_k → RRF merge), 없으면 `_gather_chunks` (기존). `meta.decomposed_subqueries` / `decomposition_cost_usd` / `decomposition_cached` 로 노출. **`/search` 라우터는 decomposer 를 import 하지 않음** — M1 의 W-1(a) 에서 `/search` 옵션으로 노출 예정 (cross_doc 진짜 fix 후보 — §8.4).

### 4.7 BGE reranker — S2/S3 D4 · **운영 default OFF (D6 확정)**

- `JETRAG_RERANKER_ENABLED=true` (default `false`) 시: RRF top-K → ① `reranker_cache` hit → HF 호출 0, `path=cached`, RRF score 대체 ② 월간 cap(`JETRAG_RERANKER_MONTHLY_CAP_CALLS` × `JETRAG_RERANKER_DEGRADE_THRESHOLD`, default 1000×0.8 — `vision_usage_log` 의 `source_type='reranker_invoke'` 30일 COUNT) 임박 → HF skip, `path=degraded` ③ cap 적용 candidates[:cap](default 20, `JETRAG_RERANKER_CANDIDATE_CAP` [5,50]) → BGE-reranker-v2-m3 cross-encoder score → 재정렬, `path=invoked` ④ 실패 → RRF score 그대로 (`reranker_fallback_reason` = transient/permanent). 응답 헤더 `X-Reranker-Path`.
- **D6 결론 (2026-05-12, S3 100% 종결)**: sample-report `dense_vec` 복구 후 회복 DB 에서 실 BGE-reranker 재측정 → **net-negative** (reranker 의 가치 ∝ base 품질에 반비례 — base 가 이미 좋으면 reranker 가 망침). 또한 HF free-tier reranker P95 = 196s = 운영 불가. **→ production default `JETRAG_RERANKER_ENABLED=false` 확정.** 코드·ENV 옵션·캐시·degrade 경로는 보존 (v1.5 self-host BGE-M3 검토 시 재활용). `/answer` 는 reranker 미진입 → `X-Reranker-Path` 항상 `disabled`.

### 4.8 MMR 다양성 — S3 D4 (cross_doc 한정 4중 가드)

- `mmr.rerank(candidate_ids, relevance=doc_score, embeddings_by_id=..., top_k=...)` — `score(c) = λ·rel(q,c) − (1−λ)·max_{s∈S} sim(c,s)`, λ default 0.7 (`JETRAG_MMR_LAMBDA` [0,1]).
- **4중 가드** (전부 만족 시에만 적용): ① `mmr.is_disabled()` False (`JETRAG_MMR_DISABLE!=1`) ② `len(sorted_doc_ids) > 1` ③ `doc_id is None` (단일-doc 스코프 아님) ④ `_is_cross_doc_query(clean_q)` = intent_router `T1_cross_doc` 발화 (T2/T7 은 cross_doc-class chunk cap §4.9 만, MMR 은 T1 한정 — 사용자 결정 Q-S3-D4-2).
- sim 은 `documents.doc_embedding` (1024-dim) cosine. cache miss(None) chunk 는 `sim=0` → diversity term 0 → relevance 만 반영 (외부 호출 0, 회귀 0). λ=1.0 → 순수 relevance 정렬.

### 4.9 cross_doc-class chunk cap — S4-A P1

- intent_router 의 `triggered_signals` ∩ `_CROSS_DOC_CLASS_SIGNALS`(`{T1_cross_doc, T2_compare, T7_multi_target}`) 가 비어있지 않으면 (`_is_cross_doc_class_query`) doc 당 matched_chunks cap 을 `_MAX_MATCHED_CHUNKS_PER_DOC`(3) → `_MAX_MATCHED_CHUNKS_PER_DOC_CROSS_DOC`(8) 로 확대 + 해당 doc 의 chunk 를 RRF score desc 로 정렬해 반환. 응답 schema 불변 (matched_chunks 길이만 doc 당 최대 8).
- 동기: 비교/대조 query 는 doc 마다 근거 청크가 여럿 자연스럽고, eval(`run_s4_a_d4_breakdown_eval.py`) 에서 cross_doc cell 의 정답 chunk 가 doc 당 3 cap 에 탈락하던 문제 완화. (eval 측은 추가로 doc-balanced 라운드로빈 머지 — §8.3.)
- intent_router `_is_cross_doc_query` (T1 전용, MMR 용) ⊂ `_is_cross_doc_class_query` (T1·T2·T7) — 후자가 더 넓음.

---

## 5. 클라이언트 표시

> 클라이언트 표시 정책은 W25 D6 이후 변경 없음 (S3·S4·cross_doc P0/P1 은 백엔드만 — `matched_chunks` 길이/정렬만 영향, 카드 UI·라벨 정책 불변).

### 5.1 검색 결과 카드 (`web/src/components/jet-rag/result-card.tsx`)

- doc 단위 (`hits` 배열)
- matched_chunks 표시 (cap: 일반 3 / cross_doc-class query 시 최대 8 — §4.9)
- `매칭 강도 N%` ⓘ 툴팁 (`relevance-label.tsx`, W25 D3-fix client island)
  - **단위: doc 단위 정규화** — 결과 집합 내 최강 doc = 100%
  - 툴팁: "이 결과 집합 내에서의 상대적 매칭 강도예요. 정답 신뢰도와는 다릅니다."
- `+N개 더 매칭` → `<Link href="/doc/${docId}?q=...">` (W25 D3·D5)

### 5.2 doc 페이지 `?q=...` (`web/src/app/doc/[id]/page.tsx` `MatchedChunksSection`)

- 모두 표시 (cap 200)
- 정렬: score desc (관련도 순)
- `매칭 강도 N%` 텍스트만
  - **단위: chunk 단위 정규화** — top chunk = 100%
  - 헤더 ⓘ 툴팁: "이 문서 안 청크들 중 가장 강한 매칭 대비 상대 강도예요."
- 검색어 highlight (`Highlighted` 컴포넌트, snippet 매칭 위치 ±240자 안)

### 5.3 멘탈 모델 일관성 (W25 D6)

| 위치 | 라벨 | 단위 | 100% 의미 |
|---|---|---|---|
| 검색 결과 카드 | `매칭 강도 100%` + 막대 | doc 단위 | 결과 집합 내 최강 doc |
| doc 페이지 청크 | `매칭 강도 100%` 텍스트만 | chunk 단위 | 이 doc 안 최강 청크 |

라벨 동일 → 학습 비용 0. 단위 차이는 ⓘ 툴팁으로 안내.

### 5.4 `/answer` meta (참고 — UI 미구현)

`/answer` 응답의 `meta` 에 `low_confidence` / `router_signals` / `router_confidence` / `decomposed_subqueries` / `decomposition_cost_usd` / `decomposition_cached` 가 노출됨 (`web/src/lib/api/types.ts` `AnswerMeta`). UI 분기(cross-doc CTA 강조 등)는 M3(W-9) 에서 구현 예정.

---

## 6. 확장자 × 검색 적합도 매트릭스

| doc_type | dense 적합 | sparse(PGroonga) 적합 | 표지 가드 | 노이즈 risk |
|---|---|---|---|---|
| PDF (텍스트) | 높음 | 한국어 Mecab 매칭률 가변 | ✅ 적용 | 표/캡션·메타광고 (caption 신호 보강 — S4-A) |
| PDF (스캔) | 중간 (Vision OCR 정확도 의존) | 낮음 (OCR 오류) | ✅ 적용 | OCR noise |
| 이미지 | 중간 | 낮음 (캡셔닝 결과 텍스트만) | ❌ (page=null) | 캡션 단순 |
| HWP/HWPX | 높음 | Mecab 강함 (한글 자료) | ❌ (page=null) | 표 잡음 (chunk_filter 처리) |
| DOCX | 높음 | Mecab 매칭 가능 | ❌ | 헤딩 누락 시 잘못된 청크 |
| PPTX | 중간 (슬라이드 짧음) | 짧은 텍스트 약함 | ✅ (page=슬라이드) | 캡션·헤딩 |
| TXT/MD | 높음 | Mecab 강함 | ❌ | 적음 |
| URL | 중간 (HTML 노이즈) | Mecab 매칭 가능 | ❌ | 광고·메뉴·푸터 |

---

## 7. 핵심 특이점 / 한계 (현재 시점)

### 강점
- **Hybrid (dense + sparse) RRF** — dense 단독 한계 (표지 우세) + sparse 단독 한계 (동의어 못 잡음) 상호 보완
- **표지 가드 + TOC 가드** — 짧은 표지·목차 청크 자동 후순위
- **의도 라우팅 (S3)** — meta-only query 는 임베딩·RPC 0 으로 즉답 / cross-doc query 는 chunk cap 확대 + MMR 다양성
- **embed query 2단 캐시 + warmup ping** — eval 재현성 + HF free-tier cold-start 부수 완화
- **외부 의존성 0** — BGE-M3 HF API + Gemini Flash (+ decomposer 의 Flash-Lite, default OFF) 만 외부
- **Server initial fetch + Client refetch (W17 RSC 패턴)** — SSR HTML 즉시 표시

### 한계 (현재 ship 시점)
1. **PGroonga 한국어 sparse 매칭률 가변** — 의문문은 0건 매칭 케이스 발생. Dense 단독 ranking → 노이즈 가능. PRD W-2(동의어 후보 사전 — S4-D) / v1.5 PGroonga 회복으로 대응.
2. **답변 생성 (LLM RAG answer)** — `/answer` 라우터는 ship 됐으나 extractive summary 자동 표시·cross-doc CTA UI 등은 M3(W-9) — 검색 결과 카드 + chunk 표시까지가 현 UX 의 주 경로.
3. **`매칭 강도 100%` = 상대 점수** — top-1 항상 100%, 절대 정답률 아님. KPI 측정: golden_v2 183 row + `run_s4_a_d4_breakdown_eval.py` (§8) / mini-Ragas / KPI #4·#5·#8·#9 는 M3.
4. **`page=null` doc_type 은 표지 가드 영향 없음** — DOCX/HWPX 등은 가드 우회.
5. **chunk_filter 의 false positive risk** — 짧은 헤딩이 `extreme_short` 로 잘못 제외 가능. 회귀 테스트로 보호.
6. **PDF 카탈로그 메뉴 footer 노이즈** — 본문과 합쳐진 메뉴 footer 가 변별력 부족. PRD W-3(chunk text augmentation) + W-4(클린 재인제스트)로 근본 해결 예정.
7. **cross_doc query 가 9 qtype 중 최약** — R@10 0.4424 / top-1 0.3333 (P1 후 baseline, §8). reranker 도 entity_boost 도 못 고침 (둘 다 ablation 으로 레버 아님 판명). 진짜 fix = M1 의 paid LLM decomposition `/search` 노출(W-1(a)) 또는 doc-scoped RPC 마이그(W-1(b)). $0 기계 분해 시도(P2)는 net-negative → 폐기·미머지.
8. **entity_boost 무효** — 룰 기반 entity 추출 적재(441/2469)는 됐으나 query 측 entity 매칭 모수 ≈ 0 → 검색 영향 0 (§4.4). LLM 보강(persons/orgs/products — W-7)은 ROI 낮아 보류.
9. **HF free-tier scale-to-zero** — 임베딩 모델이 무사용 시 잠들어 첫 호출 cold-start (sample-report 1038 chunk embed ~8.3분 전례). 완화: lifespan warmup ping + `Retry-After` 존중 + embed_query_cache. 전체 재인제스트(W-4) 시엔 doc 1~2개씩 분할 + warmup 선행이 명시 조건.
10. **`JETRAG_INTENT_ROUTER_THRESHOLD` ENV 부재** — confidence 임계는 `answer.py` 의 하드코딩 `_LOW_CONFIDENCE_THRESHOLD = 0.75` 뿐 (ENV 화 안 됨). `intent_router.route()` 의 confidence_score = `1.0 − 0.15·len(signals)` (T6 발화 시 추가 −0.3, [0,1] 클램프). § 잔존 이슈 — ENV 화 여부는 추후 결정.

---

## 8. KPI 측정 도구 (현재 ship)

| 도구 | 용도 | 위치 | 상태 |
|---|---|---|---|
| **`run_s4_a_d4_breakdown_eval.py`** | **golden_v2 183 row → R@10(graded) / nDCG@10 / MRR / top-1 hit + qtype·doc_type breakdown + P95 latency. = DoD 게이트용 (KPI #6①).** RRF-only 강제(`JETRAG_RERANKER_ENABLED=false` + `JETRAG_MMR_DISABLE=1`) + embed-cache ON(`JETRAG_EMBED_QUERY_CACHE=1`), paid LLM 0. | `evals/` | **ship — W-6 가드 반영 (`ddef019`)** |
| `golden_batch_smoke.py` | 20건 query → expected doc_id top-1/3 hit + p95 latency + mode ablation | `api/scripts/` | ship (W4 → W21) |
| mini-Ragas (Phase 1) | 10 QA → Context Recall / Precision (검색만) | `evals/run_ragas.py` | ship (W25 D7) |
| `run_s3_d5_search_stack_eval.py` | S3 검색 스택(intent_router·meta_filter·reranker·mmr) ablation | `evals/` | ship (S3 D5) |
| `eval_retrieval_metrics.py` | sonata 단일/멀티 doc 검색 메트릭 | `evals/` | ship |
| `monitor_search_slo.py` | `make slo` — P95·embed cache_hit·mode 분포 텔레메트리 (KPI #10) | `api/scripts/` | ship |
| Ragas Phase 2 / KPI #4·#5·#8·#9 (Faithfulness/Answer Relevancy/출처 일치율/환각률) | LLM judge | `evals/run_ragas.py` 확장 | M3 (W-9) |

### 8.1 측정 방법론 (`run_s4_a_d4_breakdown_eval.py`)

- **데이터셋**: `evals/golden_v2.csv` 183 row. qtype 9종(exact_fact / fuzzy_memory / vision_diagram / synonym_mismatch / out_of_scope / cross_doc / ...) × doc_type(pdf/hwpx/hwp/pptx/docx + cross_doc U-row).
- **scoring**: `services/retrieval_metrics.py` — `recall_at_10` = (정답 chunk set ∪ acceptable chunk set ∩ predicted_top10) / 정답 set, graded. `ChunkKey = Union[int, tuple[str, int]]` — 단일-doc 은 `int chunk_idx`, cross_doc 은 `(alias, chunk_idx)` 튜플 (§8.2). qtype별 breakdown 포함.
- **W-6 가드 (commit `ddef019`, 2026-05-12 — 측정 신뢰성 위생, `2026-05-12 M0-a 측정 신뢰성 정비.md` W-6 절)**:
  - **dense_vec NULL preflight**: 측정 시작 직전 `_warn_stale_dense_vec()` 가 `chunks.dense_vec IS NULL` 카운트 → 0 이면 `[INFO] OK`, >0 이면 `[WARN]` + doc_id별 카운트. `--fail-on-null-dense-vec` 플래그 시 NULL>0 이면 종료코드 2 (sample-report 2026-05-12 dense_vec NULL 사고 재발 방지). 접속 실패 시 -1 (eval 안 막음).
  - **`doc_match_fail` 2 row zeroing (DECISION-6)**: 라벨이 있는데 런타임에서 target items `[]` → `recall_at_10 / ndcg_at_10 / mrr = 0.0`, `top1_hit = False`, `doc_match_fail_zeroed = True` 로 채워 분모(`n_chunk_evaluable`)에 **포함** (G-U-018 `law sample2|law sample3`, G-U-027 `승인글 템플릿1|승인글 템플릿3` — `|` separator U-row). 직전에는 R@10 None → 분모 제외 → overall inflate 였음.
  - **`no_ground_truth` 7 row 별도 버킷**: 라벨 자체가 비어있는 row(G-U-005 / G-U-007 / G-U-028 / G-U-029 / G-U-030 / G-N-004 / G-N-005)는 `recall_at_10` None 유지 → 분모 제외 + raw json 에 id 목록 카운트만.
  - raw json: `cells[].doc_match_fail_zeroed` / `cells[].no_ground_truth` / top-level `doc_match_fail_zeroed_ids` / `no_ground_truth_ids` / `n_doc_match_fail_zeroed` / `n_no_ground_truth` / `n_null_dense_vec`.
  - `_format_markdown` 의 `n_golden` 동적화 (`"157 row"` 하드코딩 제거 — golden 행 수 변동 대비). `run_s3_d5_search_stack_eval.py` / `eval_retrieval_metrics.py` 의 헤더 csv명도 동적화 (cosmetic, 측정값 영향 0).
- **측정 환경 명시**: `_MEASUREMENT_ENV` 가 측정 시작 시 `JETRAG_RERANKER_ENABLED=false` + `JETRAG_MMR_DISABLE=1` 을 강제 set (운영 코드 변경 0 — ENV 토글만으로 RRF-only) + embed-cache ON. → **eval 수치 = RRF-only baseline** (운영 default 와 일치 — 운영도 reranker default OFF, MMR 은 cross_doc 한정인데 eval 의 cross_doc 분기는 alias_map 직접 선별이라 MMR 무관).

### 8.2 cross_doc 골든셋 라벨 — P0 (alias-bound)

- golden_v2 의 cross_doc 9 row 의 `relevant_chunks` / `acceptable_chunks` 라벨은 **`alias:chunk_idx` 형식** (예: `law2:10`, `resume1:0`). alias → ground-truth `doc_id` + title prefix 매핑은 `evals/cross_doc_alias_map.json` (alias 11개) 가 단일 출처 (build/eval 양쪽).
- P0 이전엔 cross_doc 라벨이 doc 비-bound(전역 chunk_idx) 라 doc 경계가 모호했음 → `(alias, chunk_idx)` 튜플로 doc-bound 재라벨 + `retrieval_metrics.py` 의 `ChunkKey` generic 화 + `run_s4_a_d4_breakdown_eval.py` 의 cross_doc 분기(`_pick_cross_doc_items` — search 응답 items 중 alias_map.doc_id 인 것만 선별, title prefix 미사용 / `_parse_chunk_label` 이 미등록 alias 면 ValueError 로 라벨 오타 조기 검출). G-A-075 qtype 정정 + `retrieval_metrics` generic 화 동반.

### 8.3 cross_doc chunk cap + 라운드로빈 머지 — P1

- **런타임 (`search.py`)**: cross_doc-class query (T1/T2/T7) 한정 doc 당 chunk cap 3 → 8 (`_MAX_MATCHED_CHUNKS_PER_DOC_CROSS_DOC` / `_CROSS_DOC_CLASS_SIGNALS` / `_is_cross_doc_class_query`), `matched_chunks` RRF desc 정렬 (§4.9).
- **eval (`run_s4_a_d4_breakdown_eval.py`)**: cross_doc row 는 `_pick_cross_doc_items` 로 target doc 선별 후 `_round_robin_cross_doc_chunks` 로 doc-balanced 라운드로빈 머지(doc1 ch → doc2 ch → doc1 ch ... 순) → `[:10]` 컷. alias_map 미등록 doc_id item 은 skip. 단일-doc row 는 기존 `_pick_target_items`(title prefix 매칭) 경로 유지.
- intent_router 커버리지 보강 (P1): cross_doc query 9 row 중 3/9 만 `T1`/`needs_decomposition` 발화하던 문제 → `_T1_CROSS_DOC_PAIR` / `_T1_CROSS_DOC_PAIR2` / `_T1_CROSS_DOC_PLURAL` 보조 regex + `_DOC_NOUN` 화이트리스트(`자료|문서|보고서|안내서|규정|내규|이력서|포트폴리오|포폴|템플릿|판결|계획|사업|매뉴얼|카탈로그|논문`) + `_T2_COMPARE_STEM`(`다르[게지]|다른[가지]|다릅|상이`) 추가 → 9/9 발화. (§ `intent_router.py` 7 trigger 표는 §8.5.)

### 8.4 baseline 수치 (현재 — W-6 적용)

| 측정 | R@10 | top-1 hit | 비고 |
|---|---:|---:|---|
| **overall** (golden_v2 183 row, `n_chunk_evaluable`=176) | **0.6841** | **0.7955** | W-6 DECISION-6 적용 (직전 0.6920 / 0.8046 = 분모 174 기준 — `doc_match_fail` 2 row zeroing 으로 분모 176 → 산술 환산 차이, 검색 path 변동 아님). P95 latency ~240ms. |
| **cross_doc** (9 row, alias-bound) | **0.4424** | **0.3333** | P1 후 (cap 8 + 라운드로빈 + intent_router 커버리지). P0 직후 0.2856 → P1 0.4424 (+55%). 여전히 9 qtype 중 최약. |

- cross_doc 진짜 fix 후보 (M1+): paid LLM query decomposition 의 `/search` 노출(PRD W-1(a) — DECISION-2, ~$0.005/eval) 또는 doc-scoped RPC 마이그(W-1(b) — `search_hybrid_rrf` 에 doc_id 인자, US-08 회귀 테스트 동반). $0 기계 분해(P2)는 doc-scoped 가 진짜 doc-RRF 가 아니라 R@10 0.4424→0.3610 net-negative → 코드 폐기·미머지.
- **"검색 정확도 80% 달성" 판정 시점**: baseline top-1(0.7955)이 0.80 미달이므로 달성 판정은 baseline 이 아니라 **PRD M2 게이트(top-1 ≥ 0.80) 통과 시점** (M1 cross_doc decomposition + 동의어 사전 + M2 chunk augment + 클린 재인제스트 후). DECISION-1 기준값(top-1 ≥ 0.80) 자체는 불변.

### 8.5 intent_router 7 trigger (현재 — `services/intent_router.py`)

| # | Signal id | 룰 |
|---|---|---|
| T1 | `T1_cross_doc` | `_T1_CROSS_DOC` (`(자료\|문서\|보고서).{0,15}(랑\|와\|과\|및).{0,15}(자료\|문서)`) **OR** P1 보조 3종: `_T1_CROSS_DOC_PAIR`(`NP1 (와\|과\|랑) NP2 …{0,15}… 문서류명사`) / `_T1_CROSS_DOC_PAIR2`(`문서류명사… (와\|과\|랑) NP2`) / `_T1_CROSS_DOC_PLURAL`(`문서류명사들 (에서\|에\|중에서\|중에\|중)`) — `_DOC_NOUN` 화이트리스트 §8.3 |
| T2 | `T2_compare` | 키워드 OR (`차이`/`비교`/`vs`/`달라`/`대비`) **OR** `_T2_COMPARE_STEM` regex (`다르[게지]\|다른[가지]\|다릅\|상이`) |
| T3 | `T3_causal` | 키워드 OR (`왜`/`이유`/`때문`/`원인`/`어째서`) |
| T4 | `T4_change` | 키워드 OR (`달라진`/`바뀐`/`변경`/`수정된`/`업데이트`) |
| T5 | `T5_long_query` | char ≥ 40 또는 whitespace token ≥ 12 |
| T6 | `T6_low_confidence` | 모호 표현 (`그거`/`그때`/`그 `(trailing space)/`어디였더라`/`뭐였지`/`어떻게 됐더라`) |
| T7 | `T7_multi_target` | T1 미발화 + `count("랑") + count("과") ≥ 2` |

- **`needs_decomposition`** = `(T1 or T2 or T3 or T7) or (T5 and T6)`. T4·T5·T6 단독은 분해 불필요.
- **`confidence_score`** = `max(0, min(1, 1.0 − 0.15·len(triggered_signals)))`, T6 발화 시 추가 `−0.3`. (ENV 임계 없음 — `answer.py` 에서 `< 0.75` → `meta.low_confidence=true` 마킹용으로만 사용. §7-10 잔존.)
- query NFC 정규화 + 내부 다중 공백 단일화 후 매칭. 외부 API 0 / DB 0 / 의존성 0.

---

## 9. 변경 이력

| 날짜 | W·Day | 변경 요약 | 영향 범위 | commit |
|---|---|---|---|---|
| 2026-05-13 | M0-a W-15 | living spec 1차 갱신 — §0·§4 에 S3(intent_router 7 trigger / meta_filter fast path / query_decomposer `/answer` 전용 / reranker 운영 OFF·D6 / MMR cross_doc 4중 가드 / cross_doc-class chunk cap 8) + S4-A·B(caption / entity_boost default OFF / embed_query_cache 마이그 016 / HF cold-start 완화) 추가, §8 에 측정 방법론(W-6 가드 — DECISION-6 zeroing / no_ground_truth 버킷 / dense_vec preflight) + cross_doc P0/P1 + baseline 수치 추가, §7 한계 갱신. **2차 갱신 = M1 W-1(a) decomposition `/search` 노출 + W-3/W-4 후 예정** | spec 문서만 (코드 변경 0) | (문서) |
| 2026-05-04 | W25 D8 | Phase 2 메뉴 footer 가드 시도 → **롤백** (G-S-006 0.50→0.03 악화) — 차수 (B) chunk 분리 / (D) PGroonga 회복 후보로 후속 sprint 이관 | search.py 변경 0 (시도 + 회귀) / 시도 결과 주석만 보존 | (롤백, commit 0) |
| 2026-05-04 | W25 D7 | mini-Ragas (Phase 1) ship — Context Recall/Precision + `make eval` + ragas/datasets 의존성 첫 추가 | KPI 측정 / Makefile / pyproject.toml | (W25 sprint) |
| 2026-05-04 | W25 D3~D6 | snippet 240 / chunk_id dedupe / 매칭 강도 라벨 / 표지 가드 / doc 페이지 ?q= / % 표시 통일 | search.py + UI | `72e14ca` |
| 2026-05-04 | W25 D1·D2 | `/docs` 라우트 + 태그 destination 통일 + 최근 추가 카드 인터랙션 | UI (검색 외) | `77a2ae2` |
| 2026-05-03 | W21 | golden CI gate + edge case | 회귀 보호 | (다수) |
| 2026-05-03 | W20 | 진정 ablation (008 split RPC) | RPC dense/sparse 분리 측정 | (다수) |
| 2026-05-03 | W19 | useTransition race + RPC top_k cap | 검색 latency / race | (다수) |
| (이하 W3~W18 변경은 [매주 핸드오프](README.md) 참조 / S3·S4·cross_doc P0·P1 의 개별 commit 은 `2026-05-12 종합 마감 + 2026-05-13 진입 핸드오프.md` §2 표 참조) | | | | |

> **참고**: S3(D1~D6) / S4-A(P1) / S4-B(entity_boost ablation) / cross_doc P0·P1 / W-6 의 개별 commit hash 는 본 문서가 1차 갱신 시점에 누적 정리 — 상세는 핸드오프 `2026-05-12 종합 마감 + 2026-05-13 진입 핸드오프.md` + work-log 들 (`2026-05-12 DB chunk 정합성 감사 — sample-report dense_vec NULL 발견.md` §9~§12, `2026-05-12 M0-a 측정 신뢰성 정비.md` W-6 절) 참조.

향후 변경 시 추가:
- 날짜 / sprint
- 변경 핵심 (1줄)
- 영향 범위: stage / RPC / UI / 정책
- commit hash

---

## 10. 관련 문서

| 문서 | 목적 |
|---|---|
| `2026-04-22 개인 지식 에이전트 기획서 v0.1.md` | 마스터 (페르소나·KPI·DoD) |
| `2026-05-12 검색 정확도 80% 달성 PRD.md` | **현행 master plan** — M0~M3 마일스톤, 본 spec 갱신의 W-15 출처 |
| `2026-05-12 종합 마감 + 2026-05-13 진입 핸드오프.md` | S3·S4·cross_doc P0/P1 commit 표 + 현 DB/테스트 상태 |
| `2026-05-12 DB chunk 정합성 감사 — sample-report dense_vec NULL 발견.md` | cross_doc P0/P1/P2 진단·정정 (§9~§12) + sample-report 복구 |
| `2026-05-12 M0-a 측정 신뢰성 정비.md` | W-6 eval 가드 (DECISION-6 / dense_vec preflight) + W-15 절 |
| `evals/README.md` | KPI 측정 + Ragas 진입 가이드 |
| `api/migrations/README.md` | DB 스키마 + 마이그레이션 적용 가이드 (016 = embed_query_cache) |
| `api/app/adapters/README.md` | 어댑터 Protocol 가이드 (5종) |
| `web/AGENTS.md` | Next.js 16 RSC 패턴 5종 |

---

## 11. 갱신 트리거 체크리스트

다음 변경 발생 시 본 문서 갱신 의무:

- [ ] **인제스트 stage 변경** (정책 / 새 stage / 제거) → §1
- [ ] **신규 확장자 지원** (parser 신설) → §2, §6
- [ ] **chunk 정책 변경** (size, filter 룰, caption/entity 적재) → §1, §3, §6
- [ ] **임베딩 모델 변경** (BGE-M3 → 다른 모델) → §1, §3, §6 (embed_query_cache 의 model_id 도)
- [ ] **검색 RPC 변경** (RRF 가중치, sparse 룰, 가드 정책, doc-scoped 인자) → §4
- [ ] **의도 라우팅 변경** (intent_router trigger / meta_filter 룰 / decomposer gating·배선 / reranker default / MMR 가드) → §4.0~§4.9
- [ ] **검색 응답 schema 변경** (cap, 정렬, snippet) → §4, §5
- [ ] **클라이언트 표시 정책** (라벨, 단위, 시각화) → §5
- [ ] **답변 생성 UX 변경** (extractive summary / cross-doc CTA / decomposer `/search` 노출) → §0, §4.6, §5.4, §7, §8
- [ ] **KPI 측정 도구 변경** (eval 가드, golden 갱신, Ragas 확장) → §8
- [ ] **외부 의존성 변경** → §7

→ 갱신 시 §9 변경 이력에 한 줄 추가, 본문 §1~§8 의 영향 부분 갱신.
