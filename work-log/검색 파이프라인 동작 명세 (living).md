# 검색 파이프라인 동작 명세 (living)

> **Living document** — 검색 모델·인제스트 stage·검색 로직·표시 정책이 변경 또는 고도화될 때마다 갱신.
> 마지막 갱신: 2026-05-04 (W25 D8 — Phase 2 메뉴 footer 가드 시도 → 롤백)
> 버전: v0.2.1

---

## 갱신 정책

- **Sprint 마감 시 본 문서 갱신 의무** — work-log 핸드오프 작성 후 본 문서에 반영
- **§9 변경 이력** 섹션에 한 줄 추가 (`날짜 / W·Day / 변경 요약 / 영향 범위 / commit`)
- 본문 §1~§8 의 표·다이어그램은 현재 시점 정확값 으로 유지 (이전 값은 §9 이력으로 이동)
- 코드 라인 번호 인용 시 commit 명시 (예: `search.py:40 (commit 72e14ca)`) — 향후 line shift 대비

---

## 0. 전체 파이프라인 (3단계)

```
[1] 업로드     [2] 인제스트 9 stage     [3] 검색 (Hybrid RRF)
PDF 등  →  detect → extract → chunk → ... → DB  ←→  자연어 질문 → top-N
```

---

## 1. 인제스트 9 stage (확장자 무관 공통 흐름)

| Stage | 동작 | 핵심 모듈 | 변경 빈도 |
|---|---|---|---|
| **1. detect** | magic bytes + 확장자 → `doc_type` 결정 | `api/app/ingest/stages/detect.py` | 낮음 |
| **2. content_gate** | 빈 / sha256 dedupe 거부 | `content_gate.py` | 낮음 |
| **3. extract** | **확장자별 parser → 텍스트 + page 정보** (§2 매트릭스) | `adapters/impl/*_parser.py` | 중간 |
| **4. chunk** | 800자 target / 200자 min / page 경계 분리 | `chunk.py` (`_TARGET_SIZE=800` `_MIN_MERGE_SIZE=200`) | 낮음 (정책 변경 시 재인덱싱) |
| **5. chunk_filter** | `extreme_short`(≤20자) / `table_noise` 마킹 | `chunk_filter.py` (`_EXTREME_SHORT_LEN=20` `_SHORT_LINE_RATIO_TH=0.90`) | 낮음 |
| **6. embed** | BGE-M3 (HF API) → **1024-dim 벡터** chunk 별 | `bgem3_hf_embedding.py` | 중간 (모델 교체 시) |
| **7. tag_summarize** | Gemini 2.0 Flash → 자동 태그 + 요약 (LLM 1회/doc) | `gemini_llm.py` | 중간 (프롬프트 갱신) |
| **8. doc_embed** | 요약 기반 문서 단위 임베딩 1024-dim | `doc_embed.py` | 낮음 |
| **9. load (persist)** | Supabase Postgres + Storage 저장 | `supabase_*.py` | 낮음 |

→ 결과: `documents`(메타) + `chunks`(text + 1024-dim 벡터 + flags + page) 테이블에 저장.

---

## 2. 확장자 별 추출 (extract stage) 차이

| 확장자 | Parser | page 정보 | OCR | 특이점 |
|---|---|---|---|---|
| **PDF** (텍스트) | PyMuPDF (`pymupdf_parser.py`) | ✅ 정확한 페이지 | 본문 추출 우선 | 페이지별 텍스트 + heading 휴리스틱 (W4) |
| **PDF** (스캔) | PyMuPDF → text<5자 감지 → Vision fallback | ✅ | ✅ Gemini Vision | quota 보호 cap (W9) |
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

### 3.1 `chunks` 테이블 (`api/migrations/001_init.sql`)

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
| `metadata` | jsonb | `overlap_with_prev_chunk_idx` 등 |

### 3.2 `documents` 테이블

| 컬럼 | 용도 |
|---|---|
| `id` | UUID PK |
| `title` / `doc_type` / `tags` | 메타 |
| `summary` | LLM 요약 |
| `doc_embedding` | vector(1024), 요약 기반 |
| `sha256` | dedupe (`UNIQUE(user_id, sha256)`) |

---

## 4. 검색 (질문 입력 → 결과 표시)

### 4.1 입력 처리

`/search?q=...&mode=hybrid&doc_id=...&limit=10&offset=0&tags=...&doc_type=...`
- `api/app/routers/search.py:130`
- limit: `Query(10, ge=1, le=50)` — 최대 50

### 4.2 RPC 단계 — Hybrid RRF (002·003·004·008 마이그레이션)

```
질문 "소나타 시트 종류"
     ↓
[A] BGE-M3 임베딩 (1024-dim, HF API, LRU cache 적용 — W4)
     ↓                                  ↓
[B] Dense 검색                  [C] Sparse 검색
    pgvector cosine                 PGroonga `&@~` (한국어 Mecab 토크나이저)
    chunks.dense_vec <=> query      chunks.text 자연어 매칭
    rank_dense (1~50)               rank_sparse (1~50)
                  ↓
[D] RRF Fusion (003_hybrid_search.sql / 004_pgroonga_korean_fts.sql)
    score = 1/(60 + rank_dense) + 1/(60 + rank_sparse)
                  ↓
[E] doc 단위 그룹 (search.py:348-355)
    doc_score[doc_id] = max(chunk_score)
                  ↓
[F] 표지 가드 (W25 D4 — search.py:386-414)
    text_len ≤ 30 AND (chunk_idx=0 OR page=1) → score × 0.3
                  ↓
[G] chunk_id dedupe (W25 D3 — search.py:348-355)
    dense+sparse path 동일 chunk → dict[chunk_id, max_score] 1번만
                  ↓
[H] 응답 조립 (search.py:481-538)
    - matched_chunks cap:
        * list 모드 (doc_id 미명시): 3 (`_MAX_MATCHED_CHUNKS_PER_DOC`)
        * doc 스코프 (doc_id 명시): 200 (`_MAX_MATCHED_CHUNKS_DOC_SCOPE`, W25 D5)
    - 정렬:
        * list = chunk_idx asc (본문 등장 순서)
        * doc 스코프 = score desc (관련도 순)
    - relevance = doc_score / top_doc_score (top-1 항상 100%, 상대 점수)
    - snippet = 매칭 위치 ±240자 (W25 D3, env `SEARCH_SNIPPET_AROUND`)
```

### 4.3 표지 가드 상세 (W25 D4)

| 조건 (모두 만족) | 처리 |
|---|---|
| `text_len ≤ _COVER_GUARD_TEXT_LEN` (= 30) | `score *= _COVER_GUARD_PENALTY` (= 0.3) |
| AND (`chunk_idx == 0` OR `page == 1`) | |

**효과** (실측 SQL, SONATA 99 chunks):
- 가드 매칭: 1/99 (1.0%) — `chunk_idx=0, page=1, text_len=6, "SONATA"` 표지만
- DOCX false positive: 0건
- top-1 chunk: p.1 표지 → p.22 시트 정보 페이지로 이동

---

## 5. 클라이언트 표시

### 5.1 검색 결과 카드 (`web/src/components/jet-rag/result-card.tsx`)

- doc 단위 (`hits` 배열)
- matched_chunks 3개 표시 (cap)
- `매칭 강도 N%` ⓘ 툴팁 (`relevance-label.tsx`, W25 D3-fix client island)
  - **단위: doc 단위 정규화** — 결과 집합 내 최강 doc = 100%
  - 툴팁: "이 결과 집합 내에서의 상대적 매칭 강도예요. 정답 신뢰도와는 다릅니다."
- `+N개 더 매칭` → `<Link href="/doc/${docId}?q=...">` (W25 D3·D5)

### 5.2 doc 페이지 `?q=...` (`web/src/app/doc/[id]/page.tsx` `MatchedChunksSection`)

- 92개 모두 표시 (cap 200)
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

---

## 6. 확장자 × 검색 적합도 매트릭스

| doc_type | dense 적합 | sparse(PGroonga) 적합 | 표지 가드 | 노이즈 risk |
|---|---|---|---|---|
| PDF (텍스트) | 높음 | 한국어 Mecab 매칭률 가변 | ✅ 적용 | 표/캡션·메타광고 |
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
- **표지 가드 (W25 D4)** — 짧은 표지 청크 자동 후순위
- **외부 의존성 0** (BGE-M3 HF API + Gemini Flash 만 외부)
- **Server initial fetch + Client refetch (W17 RSC 패턴)** — SSR HTML 즉시 표시

### 한계 (현재 ship 시점, W25 D6 마감)
1. **PGroonga 한국어 sparse 매칭률 가변** — `소나타 시트 종류` 같은 의문문은 0건 매칭 케이스 발생 (실측: SONATA 카탈로그). Dense 단독 ranking → 노이즈 가능. **후속 큐 D2-search (P1)**.
2. **답변 생성 (LLM RAG answer) 미구현** — 검색 결과 카드 + chunk 표시까지만. 사용자가 직접 청크 읽고 답 추출. v1.5 결정.
3. **`매칭 강도 100%` = 상대 점수** — top-1 항상 100%, 절대 정답률 아님. **Ragas 측정 (Phase 1 mini-Ragas) 진입 결정 W25 D7**.
4. **`page=null` doc_type 은 표지 가드 영향 없음** — DOCX/HWPX 등은 가드 우회. 단일 페이지 PDF 본문이 가드 false positive 가능 (현재 0건 측정).
5. **chunk_filter 의 false positive risk** — 짧은 헤딩 (예: "결론") 이 `extreme_short` 로 잘못 제외 가능. 회귀 테스트로 보호 중.
6. **doc 페이지 `HeroSearch` input 의 `?q=` prefill 누락** — UX 마찰 (D5-input 후속 큐).
7. **PDF 카탈로그 메뉴 footer 노이즈 (W25 D8 측정)** — SONATA 카탈로그 99 chunks 중 ~70% 청크에 메뉴 footer 가 본문과 합쳐져 등장 → 변별력 부족이 mini-Ragas 4건 격차 (G-S-001/005/006/008) 의 공통 원인. 런타임 score 가드 (W25 D8 시도) 는 정답 청크와 노이즈 청크를 분리 못함 → 롤백. **차수 (B) chunk 분리 정책 또는 (D) PGroonga 한국어 sparse 회복** 으로 근본 해결 필요.

---

## 8. KPI 측정 도구 (현재 ship)

| 도구 | 용도 | 위치 | 상태 |
|---|---|---|---|
| `golden_batch_smoke.py` | 20건 query → expected doc_id top-1/3 hit + p95 latency + mode ablation | `api/scripts/` | ship (W4 → W21) |
| **mini-Ragas (Phase 1)** | **10 QA → Context Recall / Precision (검색만)** | `evals/run_ragas.py` | **ship (W25 D7)** |
| Ragas Phase 2 (계획) | 135 QA / Faithfulness / Answer Relevancy / Answer Correctness (LLM judge) | `evals/run_ragas.py` 확장 | LLM answer 어댑터 도입 후 |
| Manual mini-golden | 사용자가 정답 아는 자료에서 직접 점수 매기기 | (도구 X, 사용자 액션) | 보조 |
| `make eval` | mini-Ragas 통합 entry-point (DoD ③) | `Makefile` (root) | ship (W25 D7) |

### 8.1 mini-Ragas 첫 측정 결과 (W25 D7)

데이터셋: SONATA 카탈로그 (1 doc, 99 chunks) × 10 QA — `evals/golden_v0.4_sonata.csv`.
사용자 의도: "매칭 강도 100%" 가 항상 나오는 게 진짜 정확한지 정량 확인.

| 메트릭 | 값 | 사용자 기대 |
|---|---|---|
| Context Recall@10 (평균) | **1.000 (100%)** | 70~90% → **상회** |
| Context Precision@10 (평균) | **0.730** | (기대 미설정) |
| latency p95 | 624ms | < 1000ms 정상 |

precision 격차 (0.05~1.00) — 일부 query (G-S-005 트림 / G-S-008 디스플레이) 에서 정답 청크가
top-1 이 아닌 4~10위 진입. 후속 ranking 개선 신호 (snippet annotation / heading boost 등).

상세: `2026-05-04 ragas-mini-result.md` + `2026-05-04 W25 D7 Ragas Phase 1 mini 도입.md`.

---

## 9. 변경 이력

| 날짜 | W·Day | 변경 요약 | 영향 범위 | commit |
|---|---|---|---|---|
| 2026-05-04 | W25 D8 | Phase 2 메뉴 footer 가드 시도 → **롤백** (G-S-006 0.50→0.03 악화) — 차수 (B) chunk 분리 / (D) PGroonga 회복 후보로 후속 sprint 이관 | search.py 변경 0 (시도 + 회귀) / 시도 결과 주석만 보존 | (롤백, commit 0) |
| 2026-05-04 | W25 D7 | mini-Ragas (Phase 1) ship — Context Recall/Precision + `make eval` + ragas/datasets 의존성 첫 추가 | KPI 측정 / Makefile / pyproject.toml | (이번 sprint) |
| 2026-05-04 | W25 D3~D6 | snippet 240 / chunk_id dedupe / 매칭 강도 라벨 / 표지 가드 / doc 페이지 ?q= / % 표시 통일 | search.py + UI | `72e14ca` |
| 2026-05-04 | W25 D1·D2 | `/docs` 라우트 + 태그 destination 통일 + 최근 추가 카드 인터랙션 | UI (검색 외) | `77a2ae2` |
| 2026-05-03 | W21 | golden CI gate + edge case | 회귀 보호 | (다수) |
| 2026-05-03 | W20 | 진정 ablation (008 split RPC) | RPC dense/sparse 분리 측정 | (다수) |
| 2026-05-03 | W19 | useTransition race + RPC top_k cap | 검색 latency / race | (다수) |
| (이하 W3~W18 변경은 [매주 핸드오프](README.md) 참조) | | | | |

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
| `2026-05-04 W25 D3~D6 검색 결과 UX 종합 핸드오프.md` | 최신 sprint 변경 |
| `evals/README.md` | KPI 측정 + Ragas 진입 가이드 |
| `api/migrations/README.md` | DB 스키마 + 마이그레이션 적용 가이드 |
| `api/app/adapters/README.md` | 어댑터 Protocol 가이드 (5종) |
| `web/AGENTS.md` | Next.js 16 RSC 패턴 5종 |

---

## 11. 갱신 트리거 체크리스트

다음 변경 발생 시 본 문서 갱신 의무:

- [ ] **인제스트 stage 변경** (정책 / 새 stage / 제거) → §1
- [ ] **신규 확장자 지원** (parser 신설) → §2, §6
- [ ] **chunk 정책 변경** (size, filter 룰) → §1, §6
- [ ] **임베딩 모델 변경** (BGE-M3 → 다른 모델) → §1, §3, §6
- [ ] **검색 RPC 변경** (RRF 가중치, sparse 룰, 가드 정책) → §4
- [ ] **검색 응답 schema 변경** (cap, 정렬, snippet) → §4, §5
- [ ] **클라이언트 표시 정책** (라벨, 단위, 시각화) → §5
- [ ] **답변 생성 도입** (LLM RAG answer) → §0, §4, §7, §8
- [ ] **KPI 측정 도구 변경** (Ragas 도입, golden 갱신) → §8
- [ ] **외부 의존성 변경** → §7

→ 갱신 시 §9 변경 이력에 한 줄 추가, 본문 §1~§8 의 영향 부분 갱신.
