# Changelog

Jet-Rag MVP — W1 (2026-04-22) ~ W22 (2026-05-04 마감) 누적 변경 분류 (W23 Day 1 통합).

semver 적용 전 (v0.1 MVP 단계). 본 문서는 W 단위 진행 + Conventional Commits prefix 분류.

> **상세 컨텍스트**: 매 W 종합 핸드오프 (`work-log/2026-05-XX W{n} 종합 + W{n+1} 진입 핸드오프.md`)
> **commit 누적**: 252 (W1~W22)
> **단위 테스트**: 275/275 PASS (W22 마감)
> **한계 회수 누적**: 43건

---

## [Unreleased] — v0.1 MVP 진행 중

### W22 (2026-05-03~04) — 운영 진입 마찰 감소
- `docs(readme)` README.md W21 진척 + 기술 스택 + 운영 env 표 갱신
- `docs(env)` .env.example W15~W18 운영 env 4개 추가 (PERSIST_ENABLED/ASYNC, ERROR_MSG_MAX_LEN, QUERY_TEXT_HASH)
- `feat(scripts)` monitor_search_slo by_mode 분리 측정 + 영속화 baseline 갱신
- `test(vision_metrics)` record_call truncate 동적 적용 검증 (+2)

### W21 (2026-05-03) — golden 회귀 자동화 + edge case
- `feat(scripts)` golden_batch_smoke `--mode all` ablation + `--require-top1-min` CI gate
- `test(search)` mode='dense' + embed transient edge case 단위 테스트
- `docs(migrations)` W15+ 일괄 적용 가이드 + 운영 env 표
- `docs(scripts)` api/scripts/README.md 신규 (6개 스크립트 entry-point)

### W20 (2026-05-03) — 진정 ablation 측정 (한계 #74 회수)
- `feat(migrations)` 008 search_dense_only + search_sparse_only RPC (schema 100% 일관)
- `feat(api)` search.py mode 별 RPC 분기 + 응용 필터 skip + graceful fallback
- `feat(web)` MetricsTrendCard fetch error 재시도 버튼 (retryToken 패턴)
- `feat(web)` SearchSloCard by_mode p50 비교 bar 시각화 (CSS bar)

### W19 (2026-05-03) — race 방지 + cap pre-allocate
- `feat(web)` mode 토글 useTransition race 방지 (한계 #79 회수)
- `feat(api)` mode=dense/sparse 시 RPC top_k 2배 (한계 #75 회수)
- `feat(api)` doc_id 필터 시 RPC top_k 4배 (한계 #66 회수)
- `fix(web)` ablation mode 토글 mobile 가독성 폰트 (W14 #78 follow-up)

### W18 (2026-05-03) — interactivity + 안정성
- `feat(web)` MetricsTrendCard mode 토글 (search 카드 ablation 비교)
- `feat(api)` query_text SHA256 hash 화 사전 wiring (한계 #87 회수, DE-21 사전)
- `feat(api)` _persist_executor graceful shutdown atexit hook
- `test(e2e)` ImageParser class-level patch → 인스턴스 patch (한계 #65 회수)

### W17 (2026-05-03) — first-warn + 비동기 persist
- `feat(web)` MetricsTrendCard 'use client' + range 토글 interactivity
- `feat(web)` vision metric 시계열 카드 추가 (RPD 20 cap 가시성)
- `feat(api)` vision_metrics + search_metrics first-warn 패턴 (한계 #85 회수)
- `feat(api)` metrics persist ThreadPoolExecutor 비동기 fire-and-forget (한계 #88 회수, /search latency 보호)

### W16 (2026-05-03) — 추세 시각화 + 한계 회수 묶음
- `feat(migrations)` 007 추세 분석 RPC 2개 (search/vision 시계열 + epoch floor + zero-fill)
- `feat(api)` /stats/trend endpoint + 단위 테스트 (graceful migrations_pending)
- `feat(web)` MetricsTrendCard SVG sparkline (의존성 0)
- `feat(api+web)` 한계 #40·#84·#90 동시 회수 (mobile 가독성 / error_msg env / source_type enum)

### W15 (2026-05-03) — DB 영속화 ship
- `feat(migrations)` 005 vision_usage_log + 006 search_metrics_log SQL
- `feat(api)` vision_metrics + search_metrics DB write-through (graceful + env gate)
- `test(api)` 스캔 PDF max 페이지 cap e2e (한계 #64 회수)

### W14 (2026-05-03) — frontend ablation + monitor CI
- `feat(web)` ablation mode 토글 (hybrid/dense/sparse) frontend
- `feat(api)` search_metrics by_mode 분리 측정 (한계 #77 회수)
- `feat(ci)` monitor-search-slo.yml workflow + JET_RAG_API_BASE env (한계 #44 회수)

### W13 (2026-05-03) — 유저 스토리 8/8 완성
- `feat(api)` 화이트보드 action_items (US-07) — structured.action_items 추출
- `feat(api)` /search?mode= ablation 인프라 (KPI '하이브리드 +5pp 우세' 측정 가능)
- `feat(web)` SearchSloCard 신규

### W12 (2026-05-03) — US-08 frontend + KPI 노출
- `feat(web)` doc 페이지 검색 doc_id 자동 주입 (US-08 frontend, 한계 #67 회수)
- `feat(api)` /stats.ingest_slo_aggregate KPI 노출

### W11 (2026-05-03) — 자율 v2 + DoD 매트릭스
- `feat(api)` last_quota_exhausted_at 추가 (W11 Day 1, 한계 #38 lite 회수)
- `test(api)` extract HWPML/OLE2/스캔 PDF e2e (한계 #58·#59 회수)
- `feat(api)` /search?doc_id=X US-08 backend (한계 #67 backend ship)

### W10 (2026-05-03) — 자율 진행 v1 + e2e 완성
- `feat(api)` extract e2e + stats router e2e (e2e 8/9 → 9/9)
- `feat(web)` debug UI 가독성 + nested metadata pretty (한계 #16·#17 회수)
- `feat(api)` JSONB path 시뮬 (한계 #20 회수)

### W9 (2026-05-03) — quota 보호 강화
- `fix(api)` PPTX cap 정책 버그 fix (한계 #47 회수)
- `feat(api)` Vision fast-fail + tag_summarize fast-fail (한계 #49·#53 회수)
- `feat(api)` class-based quota 감지 (한계 #50 회수)
- `fix(web)` fallback badge mobile (한계 #33 회수)

### W8 (2026-05-03) — PPTX 추가 + Vision OCR
- `feat(api)` DE-68 PPTX parser ship (python-pptx)
- `feat(api)` PPTX Vision OCR rerouting (텍스트 0 슬라이드, 한계 #23 회수)
- `feat(api)` dedup Tier 2/3 e2e + tag_summarize e2e (한계 #26·#32 회수)
- `feat(api)` vision_metrics 모듈 + VisionUsageCard (한계 #29·#37 회수)

### W7 (2026-05-03) — 가시성 + e2e base
- `feat(api+web)` rrf_score + chunk metadata 노출 (W7 Day 1)
- `feat(web)` 홈 ChunksStatsCard (W7 Day 3)
- `feat(web)` 검색 debug mode (W7 Day 4)
- `test(api)` e2e 4 stage mock + content_gate (W7 Day 5·6)

### W6 (2026-05-02) — 청크 마킹 + golden v0.3
- `feat(api)` DE-65 chunks 555→1256 + extreme_short 회수
- `feat(api)` golden 평가셋 v0.3 (top-1 100%)

### W5 (2026-04-30 ~ 2026-05-01) — DOCX
- `feat(api)` DE-67 DocxParser ship + 청킹 4.3·4.6
- `feat(api)` 지원 doc_type 6종 (pdf·hwp·hwpx·docx·image·url)

### W4 (2026-04-29 ~ 2026-05-02) — 검색 품질
- `feat(api)` embed_query LRU cache (maxsize=512) — p95 < 200ms (cache hit)
- `feat(api)` PDF heading 휴리스틱 (99.84% section_title 채움)
- `feat(api)` chunk.py W4-Q-14 (4건 청킹 정책 변경)

### W3 (2026-04-29) — 검색 ship
- `feat(migrations)` 003 hybrid_search.sql (HNSW + simple FTS + RRF k=60)
- `feat(migrations)` 004 PGroonga 한국어 FTS 교체 (Mecab 형태소 분석)
- `feat(api)` /search endpoint + 하이브리드 RRF
- `feat(api)` chunk_filter stage (DE-62)
- `feat(web)` 홈 dashboard + 검색 페이지 ship

### W2 (2026-04-24 ~ 2026-04-28) — 인제스트 멀티포맷
- `feat(api)` 9 stage 인제스트 파이프라인 (extract→chunk→...→dedup)
- `feat(api)` 5종 파서 (pdf / hwpx / image / url / hwp) 디스패처
- `feat(api)` content_gate (PII / 워터마크 / 제3자 대화 감지)
- `feat(migrations)` 002 received_ms (SLO 측정)
- `feat(api)` Vision OCR (Gemini 2.5 Flash 내장)

### W1 (2026-04-22 ~ 2026-04-23) — 스캐폴드
- `feat(api)` FastAPI 스캐폴드 + 9 stage 파이프라인 뼈대
- `feat(web)` Next.js 16 + Tailwind v4 + shadcn/ui (new-york, neutral)
- `feat(migrations)` 001 init.sql (documents·chunks·ingest_jobs·ingest_logs + pgvector + RLS)

---

## 한계 회수 누적 (43건)

### W7~W14 (24건)
#15·#16·#17·#19·#20·#23·#26·#28·#29·#32·#33·#37·#38 lite·#44·#47·#49·#50·#53·#58·#59·#61·#67·#68·#77·#78·#83

### W15 (6건)
#34 vision_usage 휘발 / #61 search_metrics public reset / #62 ditto / #64 스캔 PDF cap e2e / #76 search_slo 휘발 / #81 monitor artifact 30일 한계

### W16 (3건)
#40 mobile 가독성 / #84 error_msg truncate env 변수화 / #90 vision_usage source_type enum 강제

### W17 (2건)
#85 first-warn 패턴 / #88 metrics persist 비동기 (검색 latency 보호)

### W18 (2건)
#65 ImageParser class-level patch → 인스턴스 patch / #87 query_text SHA256 hash 사전 wiring

### W19 (3건)
#66 doc_id 응용 layer 필터 부족 방지 / #75 ablation top_k 2배 / #79 mode 토글 useTransition race 방지

### W20 (1건)
#74 mode 별 RPC 분리 (008 split RPC + search.py 분기) — 진정 ablation 측정

### 명시 회복 (2건)
#67 doc 페이지 검색 input (W12 회수) / #69 헤더 글로벌 검색 (W12 HeaderSearch 이미 존재)

---

## 활성 한계 (12건, W22 마감)

| # | 한계 | 차단 |
|---|---|---|
| 38 본격 | Gemini SDK quota header | SDK 변경 시점 |
| 46 | frontend e2e (playwright) | 의존성 정책 검토 |
| 48 | augment 본 검증 | quota 회복 |
| 56·57 | google SDK 새 quota class / nested cause | SDK upgrade 시 |
| 70~73 | SLO 5 버킷 외 doc_type / sample N<30 / action_items 검증 | 자료 누적 |
| 80·82 | secrets 미설정 / actionlint | 사용자 액션 |
| 86 | graceful (W15 Day 3 사실상 처리) | 명시 회복 |
| 89 | tests/__init__.py env "0" → conftest 통일 | pytest 도입 시 |

---

## 운영 정책 누적 (57개, W3~W22)

각 W 핸드오프의 §6 참조 (가장 최근: `work-log/2026-05-04 W22 종합 + W23 진입 핸드오프.md`).

핵심 카테고리:
1. 자율 진행 v2 (W11 도입, W19 정책 갱신, **49 day 정착**)
2. 어댑터 레이어 분리 (Cloud→Local 전환 경로)
3. graceful + env gate 패턴 (모든 외부 의존성)
4. 외부 의존성 0 정책 (visualization·검증 인프라)
5. Server initial / Client refetch + race 방지 패턴
