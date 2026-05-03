# Jet-Rag

> 한국 직장인을 위한 멀티포맷 RAG 기반 개인 지식 에이전트.
>
> "정리하지 않아도, 기억의 단편으로 꺼내 쓰는 앱."

**상태**: v0.1 MVP 개발 중 (W7 Day 6 완료, 2026-05-03 기준 / 6주 일정 + W7 추가 6일 / W4·W5·W6 ship 마감, W7 가성비 sprint 진행 중)
**목적**: 포트폴리오 프로젝트. 공공·대기업 비IT 실무자가 일상적으로 받는 HWP/HWPX·PDF·DOCX·이미지·URL 자료를 자연어로 역검색.

---

## 문제

한국 직장인은 하루에 HWP·PDF·스크린샷·URL 20건을 받지만 일주일 뒤엔 무엇을 받았는지도, 어디에 있는지도 기억하지 못한다. 기존 도구(Notion AI / Mem / Apple Notes / Obsidian / Evernote)는 **HWP 미지원 + 한국어 RAG 취약 + 공공·대기업 보안 정책과 충돌**로 이 페르소나를 커버하지 못한다.

## 해결 접근

1. **멀티포맷 인제스트** — HWP/HWPX·PDF·DOCX·이미지·URL 5경로
2. **Vision 캡셔닝 + OCR 2-pass** — 표·다이어그램·화이트보드까지 검색 가능화
3. **하이브리드 검색** — BM25 + Vector + RRF + 메타 필터
4. **쿼리 라우팅** — "지난달"·"이 파일만" 같은 자연어 제약을 스코프/필터로 변환
5. **Ragas 평가 루프** — "잘 되는 척"이 아니라 수치로 증명

## 기술 스택 (MVP, 2026-05-03 W7 Day 6 기준)

| 레이어 | 선택 |
|---|---|
| Backend | FastAPI (Python 3.12, uv) |
| Frontend | Next.js 16 + Tailwind v4 + shadcn/ui (new-york, neutral) + Noto Sans KR |
| DB / Storage | Supabase (Postgres + pgvector HNSW + Storage) |
| Sparse FTS | **PGroonga** TokenBigram (W3 Day 4) — 한국어 어절 sparse 검색, simple FTS 대체 |
| 임베딩 | BGE-M3 via Hugging Face Inference Providers (dense 1024) + **in-process LRU cache** (W4 Day 1, maxsize=512) |
| 생성 LLM | Gemini 2.5 Flash (무료 티어 RPD 20) |
| Vision / OCR | Gemini 2.5 Flash 내장 (W2 도입 완료) |
| 평가 | golden 평가셋 v0.3 (W6 Day 4 — G-020 정정) — top-1/top-3 모두 20/20 (100%) |
| 어댑터 스텁 | OpenAI (LLM / Embedding / Vision 각 1개, W6 스왑 시연) |
| 호스팅 | Railway (BE) · Vercel (FE) — 배포는 W6 |

**어댑터 레이어 설계** (`api/app/adapters/`) 로 Cloud→Local 전환 경로 확보. v2 는 Ollama + LanceDB 로컬 전환.

## 레포 구조

```
Jet-Rag/
├── api/         # FastAPI 백엔드 (W1 Day 2~)
├── web/         # Next.js 프론트엔드 (W1 Day 6~)
├── docs/        # ADR · 아키텍처 노트 · v0 와이어프레임 (참조 자료)
├── evals/       # Ragas 평가 셋 / 러너 (W5~)
└── work-log/    # 일자별 작업 로그 + 기획서
```

## 기획 문서 + 핸드오프

| 문서 | 목적 |
|---|---|
| `work-log/2026-04-22 개인 지식 에이전트 기획서 v0.1.md` | 마스터 (페르소나·KPI·아키텍처) |
| `work-log/2026-04-29 W3 스프린트 명세 v0.5.md` | W3 마스터 명세 (CONFIRMED) |
| `work-log/2026-05-02 W4 스프린트 명세 v0.1.md` | W4 마스터 명세 (CONFIRMED) |
| `work-log/2026-05-03 작업 이어가기 핸드오프.md` | **W7 진입 핸드오프** — W4·W5·W6 종합 + W7 Day 1~3 |
| `work-log/2026-05-03 W7 Day 4 — 홈 chunks 분포 + 검색 디버그 모드.md` | W7 Day 4 |
| `work-log/2026-05-03 W7 Day 5 — e2e 인제스트 mock 4-stage.md` | W7 Day 5 |
| `work-log/2026-05-02 golden 평가셋 v0.2.md` | 회귀 base + DOCX placeholder |
| `work-log/2026-05-02 W4 Day 5 마감 + W4 종합.md` | W4 종합 + DE 매트릭스 |
| `work-log/YYYY-MM-DD W{n} Day{n} 마감 …md` | 일자별 작업 로그 |

---

## 개발

### 사전 요구 사항

| 도구 | 용도 |
|---|---|
| Python 3.12 + [uv](https://docs.astral.sh/uv/) | 백엔드 |
| Node.js 20+ + pnpm | 프론트 |
| Git + gh CLI | 형상 관리 |
| Supabase 프로젝트 | DB + Storage |
| Gemini API 키 (Google AI Studio) | LLM |
| Hugging Face 토큰 (Read 권한) | 임베딩 |

집 / 다른 컴퓨터 셋업 절차는 `work-log/2026-04-23 작업 이어가기 가이드.md` 참고.

### 환경 변수

```bash
# 레포 루트
cp .env.example .env
# 편집기로 SUPABASE_URL / SUPABASE_KEY / SUPABASE_SERVICE_ROLE_KEY / GEMINI_API_KEY / HF_API_TOKEN 입력
```

```bash
# 프론트 (web/)
cd web
cp .env.example .env.local
# NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 (기본값)
```

### 백엔드 (API) 실행

```bash
cd api
uv sync                                  # 첫 실행 시 의존성 설치
uv run uvicorn app.main:app --reload     # http://localhost:8000
```

- 헬스: <http://localhost:8000/health>
- OpenAPI Swagger UI: <http://localhost:8000/docs>
- 시스템 통계 한눈에: <http://localhost:8000/stats>

### 프론트 (web) 실행

```bash
cd web
pnpm install                             # 첫 실행 시
pnpm dev                                 # http://localhost:3000
```

- 홈 (S1): <http://localhost:3000>
- 검색 (S2): <http://localhost:3000/search?q=반도체>
- 인제스트 (S6): <http://localhost:3000/ingest>

> **두 서버를 동시에 띄워야** 프론트가 백엔드 API 를 호출할 수 있다. 터미널 두 개 또는 `tmux` 권장.

### Supabase 초기 셋업 (첫 1회)

1. [Supabase](https://supabase.com) 프로젝트 생성
2. SQL Editor → 마이그레이션 4건 순서대로 적용:
   - `api/migrations/001_init.sql`
   - `api/migrations/002_documents_received_ms.sql`
   - `api/migrations/003_hybrid_search.sql` (HNSW + simple FTS + pg_trgm + RPC)
   - `api/migrations/004_pgroonga_korean_fts.sql` (PGroonga + chunks.flags + 한국어 sparse RPC 재작성)
3. Storage → New bucket: `documents` (Private)
4. Settings → API → service_role 키 복사 → `.env` 의 `SUPABASE_SERVICE_ROLE_KEY` 에 입력
5. 적용 후 검증 SQL: `api/migrations/README.md` 참조

---

## 현재 가용 기능 (2026-05-03 W7 Day 6 마감)

### 백엔드 엔드포인트
- `POST /documents` — 멀티파트 업로드 (**PDF/HWP/HWPX/DOCX**/PPTX/이미지/TXT/MD/URL, 최대 50MB), 매직바이트 검증 (filetype + DNS rebinding 방어), SHA-256 dedup, 9스테이지 파이프라인 비동기 시작
- `POST /documents/{id}/reingest` — 기존 doc chunks/메타 reset 후 재처리
- `GET /documents` — 최신순 리스트 (tags/summary/flags/chunks_count/latest_job_status 포함)
- `GET /documents/{id}/status` — 인제스트 진행 상태 + 스테이지 로그
- `GET /search?q=` — **하이브리드 검색** (PGroonga sparse + pgvector dense + RRF k=60) + doc 그룹화 + relevance + matched_chunks + meta filter (doc_type/date_from/date_to) + `query_parsed` 응답 + **chunk rrf_score · metadata** (W6 Day 5)
- `GET /stats` — 시스템 통계 + **search_slo** (p50/p95/avg + cache_hit_rate + fallback breakdown, ring buffer 500건) + **chunks 분포** (effective vs filtered breakdown, W7 Day 3)

### 인제스트 파이프라인 (8 스테이지, W3 chunk_filter 추가)
```
extract → chunk → chunk_filter → content_gate → tag_summarize → load → embed → doc_embed → dedup
```

### 지원 파서 (W5 마감 기준 6종)
- **PyMuPDFParser** (PDF) — block 단위 + bbox + page + **dict 모드 + heading 휴리스틱** (W4 Day 2: page median × 1.15 font size + inline 패턴 + sticky propagate, KPI section_title 99.84%)
- **HwpxParser** (HWPX) — section/paragraph + **heading sticky propagate** (W3 Day 5)
- **HwpmlParser** (HWPML XML, doc_type='hwp')
- **Hwp5Parser** (HWP 5.x OLE2, doc_type='hwp')
- **DocxParser** (DOCX, **W5 Day 2 신규**) — `iter_inner_content` 로 paragraph/table 순서 보존 + heading sticky + 표 ` | ` separator
- **ImageParser** (PNG/JPEG/HEIC, Vision 캡셔닝 + 스캔 PDF rerouting)
- **UrlParser** (web 클립, trafilatura)

### 청킹 정책 (chunk.py, W4-Q-14 + W5 Day 1·3 적용)
- 한국어 종결어미 char class 일반화 (`(?<=[가-힣)\]][.!?])\s+`)
- 숫자/영문 직후 `. ` false split 보호 + 법령 인용 (`yyyy. m. d.`) placeholder 마스킹
- 100자 prefix overlap (인접 split 청크)
- section_title 우선순위 swap (병합 시 section 우선)
- 따옴표/괄호 보호 (W5 Day 1) — odd-count 시 다음 sent 흡수
- 표 청크 격리 (W5 Day 3) — `_looks_like_table_cell` 감지 후 본문 병합 차단

### 노이즈 자동 필터링 (chunk_filter, W3 Day 4 + W4-Q-15)
- `table_noise` (짧은 라인 ≥ 0.9 + 숫자/특수문자 ≥ 0.7)
- `header_footer` (동일 doc 내 동일 짧은 텍스트 ≥ 3회)
- `empty` (W4 Day 4)
- 가시성: stage 로그 + 마킹 비율 > 5% WARNING
- search_hybrid_rrf RPC 의 WHERE `flags->>'filtered_reason' IS NULL` 자동 제외

### 보안 (W2 Day 4 + W3 Day 4)
- `_url_gate` — DNS rebinding 방어 (multi-IP recheck) + 사설 IP/loopback/IPv6 (`::1`) 차단
- `_input_gate` — 매직바이트 검증 (확장자 vs 실제 파일 시그니처)
- `content_gate` — PII 패턴 마킹 (metadata.pii_ranges)

### 프론트 화면
- **S1 홈** (`/`) — Hero 검색 + 최근 추가 + 인기 태그 + 문서 통계 + **청크 분포 카드** (effective vs filtered SVG 도넛 + breakdown, W7 Day 4)
- **S2 검색** (`/search?q=`) — 결과 카드 + 매칭 청크 하이라이트 + relevance + 503 에러 처리 + **rrf score · overlap 메타** (W7 Day 1) + **debug mode `?debug=1`** (chunk raw 메타 펼침, W7 Day 4)
- **S6 인제스트** (`/ingest`) — 드래그앤드롭 + 9스테이지 실시간 진행 (1.5s 폴링)

### KPI 충족 현황 (W7 Day 6 기준)
| KPI | 목표 | 결과 | 충족 |
|---|---|---|---|
| HWPX section_title | ≥ 30% | 100% | ✅ |
| **PDF section_title** | ≥ 30% | **99.84%** | ✅ |
| **출처 일치율** | ≥ 0.95 | **1.0 (golden 20/20)** | ✅ 초과 |
| P95 검색 응답 (cache hit) | ≤ 500ms (자체) / ≤ 3s (절대) | **169ms** | ✅ |
| chunk_filter e2e | — | filtered 40.76% (DE-65 후 의도) | ✅ |
| **단위 테스트 회귀** | 0 | **164/164 PASS** | ✅ |

---

## KPI 목표 (발표 카드)

> HWP 인제스트 ≥95% · Ragas Faithfulness ≥0.85 · 출처 일치율 ≥95% · P95 응답 ≤3초

## 운영 정책 (W3 + W4 + W5 + W6 + W7 누적 표준)

1. **graceful degrade** — 휴리스틱 fail 시 fallback (HwpxParser → DocxParser → PyMuPDFParser 모두 채택)
2. **인메모리 LRU 캐시** — Redis 도입 전 기본 패턴 (BGEM3HFEmbeddingProvider, W4 Day 1)
3. **dry-run before 본 적용** — chunks 변동 시 항상 dry-run 리포트 + 사용자 confirm. realistic 모드 우선 (W6 Day 4)
4. **chunk_filter 책임 분리** — 표 청크는 chunk.py 4.6 으로 분리 + chunk_filter table_noise 로 검색 제외
5. **trust-but-verify** — senior-developer 산출물 직접 smoke 검증 필수
6. **마이그레이션 적용** — Supabase MCP 가 read-only → DDL 은 Studio 직접 적용. 자세한 절차는 `api/migrations/README.md`
7. **DE-65 같은 본 적용 후 즉시 효과 측정** — golden batch + diagnose 재실행 → trade-off 발견 → 회수 작업 (extreme_short)
8. **search 응답 메타 투명성** — rrf_score + metadata 노출, frontend ResultCard 시각화 (W7 Day 1) + debug mode 펼침 (W7 Day 4)
9. **e2e mock 패턴** — `unittest.mock.patch` 5+곳 namespace 가로채기로 stage 함수 시그니처 변경 0 (W7 Day 5·6)

## 포트폴리오 공개 규칙

- `.env` · API 키 일체 비커밋 (`.gitignore` 엄수)
- 평가 데이터셋은 공공·합성 자료만 (실업무 자료 금지)
- 개인 업로드 샘플은 repo 외부에 보관 (`assets/` 는 gitignored)

## 라이선스

[MIT](./LICENSE)
