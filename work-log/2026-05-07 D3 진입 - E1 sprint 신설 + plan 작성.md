# 2026-05-07 D3 진입 — E1 sprint 신설 + plan 작성

> 프로젝트: Jet-Rag
> 작성일: 2026-05-07 (계속 업데이트되는 마스터)
> 작성자: Claude (Explore + senior-planner 협업, senior-developer 미진입)
> 목적: 오늘(2026-05-07) Jet-Rag 작업의 종합 마스터. 추가 작업 발생 시 새 파일이 아니라 본 문서에 계속 업데이트.

---

## 0. 한 줄 요약

> **D3 마감 — 20 commit push** (`a5cfc2a`→`74770da`) + **S1 D3·D4·D5 ship 진입** (실 query 로그 시각화 + answer_feedback 통합 분석 대시보드 + **v1 골든셋 baseline 정량 측정**). baseline 회복 + E1 sprint plan 신설 (ETA "3분" vs 실측 6~7분 격상) + S0 D4 자동 POST 제거 (비용 누수 fix) + S0 D4 P2 follow-up 3건 + S1 D1 잔여 (`auto_goldenset.py` v2 + 30 신규 테스트) + **E2 fixture 인프라 1·2·3·4·5차 ship** (`assets/public/` 8건 git 추적 + 5단계 우선순위 5개 형식 (PDF/HWPX/HWP/PPTX/DOCX) 자동 인식 + **negative path 회귀 (D 그룹) + frontend 가독성 보강**) + **E1 1차 ship 일부 즉시 진입** (인제스트 진행 표시 3대 증상 일괄 fix — ETA 정적 / progress bar 1칸 / stage 카운터 1/8 vs 9) + **S1 D2 ship — 자동 골든셋 100+ 확장 + v1 통합 + retrieval fallback chain v1 우선** + **S1 D3 ship — 실 query 로그 시각화 대시보드** + **S1 D4 ship — answer_feedback 통합 분석 대시보드** + **S1 D5 ship — v1 골든셋 baseline 정량 측정** (`evals/run_s1_d5_baseline.py` 신규 ~500 LOC + sampling 결정성 단위 테스트 5건 + `evals/results/.gitignore` + 실측정 30 sample seed=42 stratified — scenario A 2.5-flash 12/30 성공 503 high demand · B 2.5-flash-lite 30/30 성공, 휴리스틱 Δ (B-A)=+6.17pp · latency 0.19× · 비용 0.16~0.33×, 권고는 **n=12 신뢰도 낮음 → S1.1 reliable 100+ 후 결정**). 단위 테스트 460 → **528 통과 / skipped 0 / 회귀 0**. assets/ **14/14 cover** (12건 정상 회귀 + 2건 거부 동작 회귀). E1 진단·완료 ship 은 PDF 보유 다른 컴퓨터에서 진입. S1 진척률 35→**100%** (D5 ship 완료).

---

## 1. 오늘 ship 누적

### 1.1 timeline

| 시점 | 작업 | 효과 |
|---|---|---|
| 진입 | 2026-05-06 핸드오프 정독 | D1~D2 누적 마스터 (17 commit) 파악, 다음 후보 (D2-B / D4 / S1 D5) 확보 |
| baseline | `uv run python -m unittest discover tests` | **460 통과 (skipped 7) — 회귀 0** |
| baseline | Supabase MCP `execute_sql` ×2 | vision_usage_log / search_metrics_log / vision_page_cache 3개 테이블 + 15컬럼 정합 ✅ |
| 사용자 보고 | "어제 PDF 업로드 ETA 3분 → 실측 6~7분" | 신규 sprint E1 격상 결정 |
| 메모리 | `project_e1_eta_latency.md` + MEMORY.md 인덱스 1줄 | 다른 컴퓨터 진입 작업 문서마다 E1 항목 누락 방지 (사용자 명시 요청) |
| Explore | ETA + 인제스트 timing 코드 정독 | 백엔드 `eta.py:133`, 9단계 직렬, vision 페이지 순차 — 부정확 3대 가설 + latency 가설 + 측정 SQL 초안 5건 |
| senior-planner | E1 plan 작성 | 목표/DoD, 진단 SQL 5건, 개선 후보 7개 (E1-A1~A7), 1차/2차/3차 ship, 회귀·정합성, 사용자 결정 5건 |
| ship | `work-log/2026-05-07 E1 인제스트 ETA latency sprint plan.md` 신규 | 다른 컴퓨터 1장 진입용 — §10 진단 결과 칸은 reingest 후 채움 |
| ship | `work-log/2026-05-07 D3 진입.md` 신규 (본 문서) | 오늘 종합 마스터 (계속 업데이트) |
| commit/push | `a5cfc2a` (origin/main) | 두 work-log 파일 push 완료 |
| ship | 본 문서 §4 신규 (Master plan §6 sprint 진행 현황 비교) | E1 외 전체 진척률 한눈에 — S0 ~40%, S1 ~15%, S1.5/S2/S3/S4/S5 0% |
| ship | **S0 D4 — `/search/eval-precision` 자동 POST 제거** (senior-developer 구현, trust-but-verify 통과) | `web/src/components/jet-rag/search-precision-card.tsx` — mount 시 GET 캐시 조회만 → 캐시 미스 시 'idle' phase + "측정" 버튼 (사용자 클릭 시만 LLM judge POST). `tsc --noEmit` + `pnpm lint` 0 error. props 시그니처 무변경 → 사용처 영향 0 |
| commit/push | `dc3be36` (origin/main) | S0 D4 본 ship |
| ship | **S0 D4 P2 follow-up 3건 일괄 fix** (senior-qa 리포트 → senior-developer 구현) | aria-label Label-in-Name + KRW 표기, handleMeasure race guard (useRef cancelRef), mobile responsive (flex-col sm:flex-row + shadcn Button) |
| commit/push | `79aa455` (origin/main) | P2 follow-up |
| ship | **S1 D1 잔여 — `auto_goldenset.py` v2 갱신** (senior-developer 구현) | 12 컬럼 통합 schema (v0.5+v0.6) + 9 query_type 룰 분류 + must_include/source_hint/expected_answer_summary 추출 + 5건 negative query 사전 정의 + DoD 분포 측정 + 단위 테스트 30건 신규. 460 → **490 통과 회귀 0**. v0.5 보존 (옵션 A) |
| ship | **E2 1차 ship — 테스트 fixture 인프라 보강 (Hybrid)** (senior-developer 구현) | `assets/public/` 신설 + 공개 라이센스 PDF 3건 git 추적 (KOGL 1유형 2건 + 사용자 명시 공공데이터 1건, 총 10.6 MB) + `.gitignore` negative pattern (`/assets/*` + `!/assets/public/**`) + `assets/public/README.md` 신규 + `test_pymupdf_heading.py` 경로 갱신 (repo root 자동 인식, public/private 분리). **490 통과 / skipped 7 → 5 (public 2건 CI 자동 회귀 진입), 회귀 0**. 다른 컴퓨터는 `git pull` 만으로 fixture 동기화 |
| ship | **E2 2차 ship — assets/ 직속 자동 진입** (senior-developer 구현, 사용자 지적 반영) | `_pdf_path()`·`_hwpx_path()` 우선순위 변경: `assets/public/` → **`<repo>/assets/` 직속 (자동, .gitignore 로 다른 컴퓨터엔 부재)** → ENV 폴백. `test_hwpx_heading.py` 도 같은 패턴으로 마이그 (`_DEFAULT_HWPX_DIR` 잘못된 경로 제거, public/private 분리). **사용자 PC: 490 통과 / skipped 5 → 0 (PDF private 2 + HWPX private 2 + 알파 1 자동 진입, 모두 통과)**. 다른 컴퓨터·CI: assets/ 직속 부재 → 자동 skip 유지, 회귀 차단 0 |
| ship | **E2 3차 ship — 라이센스 5건 마이그 + repo 루트 직속 자동 인식 + HWP 테스트 신규** (senior-developer 구현) | (1) `assets/` 직속 5건 (`law sample3.pdf`·`law_sample2.pdf`·`직제_규정.hwpx`·`한마음생활체육관_운영_내규.hwpx`·`law_sample1.hwp`) → `assets/public/` 이동 (저작권법 §7 + KOGL 1유형). (2) `_pdf_path()`·`_hwpx_path()` 5단계 우선순위 — `<repo>/<name>` 루트 직속 단계 신설 (다른 컴퓨터 자동 인식). (3) `test_hwp_heading.py` 신규 작성 (Hwp5Parser 회귀, 4 테스트). (4) `assets/public/README.md` 8건 표 + 5단계 우선순위 + 다른 컴퓨터 4 시나리오. **사용자 PC: 490 → 494 통과 / skipped 0 / 회귀 0**. 다른 컴퓨터·CI 자동 회귀 진입 자료 8건 (PDF 4 + HWPX 2 + HWP 1 + 사용자 명시 sample-report 1) |
| ship | **E2 4차 ship — 미진입 자료 5건 (A·B·C) 회귀 진입** (senior-developer 구현, 사용자 점검 반영) | 사용자 점검 — assets/ 14건 중 7건 미진입 발견. (A) `sample-report.pdf` 가 `assets/public/` 에 git 추적되지만 `_PUBLIC_PDF_FILES` 미등록 → 1줄 추가 (KPI 평균 4건→5건 자동 합산). (B) `test_pptx_parser.py` 에 `PptxParserRealAssetTest` 신규 + 5단계 우선순위 (`JETRAG_TEST_PPTX_DIR` ENV) — 메모리 합성 binary 한계 회복. (C) `test_docx_parser.py` 에 `DocxParserRealAssetTest` 신규 + 5단계 우선순위 (`JETRAG_TEST_DOCX_DIR` ENV) — `승인글 템플릿1·3.docx` 회귀 진입. **사용자 PC: 494 → 496 통과 / skipped 0 / 회귀 0** (PPTX 1 + DOCX 1 신규, sample-report 는 KPI 평균에 자동 합산). 다른 컴퓨터·CI: 부재 시 자동 skip, 회귀 차단 0. assets/ 14건 중 12건 자동 회귀 진입 (남은 2건은 D — HWP OLE2 아님, 처리 불가) |
| ship | **E2 5차 ship — 마지막 2건 (D 그룹) negative path 회귀 + 사용자 안내 메시지 검증·보강** (senior-developer 구현, 사용자 점검 반영) | (1) `test_hwp_heading.py` 에 `Hwp5ParserRejectNonOle2Test` 신규 — `cosmetic_law_sample.hwp`/`law sample2.hwp` 두 비-OLE2 자료에 대해 `RuntimeError` raise + 안내 메시지 키워드 (`OLE2` 또는 `PDF`+`HWPX`) 검증 (subTest 2건). (2) backend 메시지 그대로 유지 — 이미 한국어 안내 + 변환 옵션 명시 (정보 손실 0). (3) frontend `upload-item.tsx`/`doc/[id]/page.tsx` 의 `error_msg` 카드에 `break-words` 클래스 추가 — 긴 한국어 안내가 카드 영역 밖 튀어나가는 것 방지. **사용자 PC: 496 → 497 통과 / skipped 0 / 회귀 0**. 다른 컴퓨터·CI 시뮬레이션 (negative fixture 가림): `skipped=2 / OK` 자동 회복. assets/ **14/14 cover** 확정 (12건 정상 회귀 + 2건 거부 동작 회귀) |
| ship | **E1 1차 ship 일부 즉시 진입 — 인제스트 진행 표시 3대 증상 일괄 fix** (Explore root cause + senior-developer 구현, 사용자 명시 "큰 혼란") | 증상 ① ETA 정적 (4분 표시 후 1분 경과해도 4분) — `eta.py:compute_remaining_ms()` 에 `stage_progress={current,total,unit}` 인자 추가 + 현재 stage 의 `(1-ratio)` 분해. 증상 ② progress bar 1칸 (extract 26/29 진척해도 첫 칸만) — `stage-progress.tsx` 칸별 `width:${pct}%` 부분 색칠 (현재 stage 만 stage_progress 비율). 증상 ③ stage 카운터 1/8 vs backend 9 stage — `web/lib/stages.ts` STAGE_ORDER + StageValue 에 `chunk_filter` 추가 (라벨 "청크 필터"). 카운터 표시 "1/9 · 13/29 페이지" 동시 노출. `documents.py` 두 호출처 (944·1027) 도 `stage_progress` 전달. **단위 테스트 497 → 502 통과 / skipped 0 / 회귀 0** (5건 신규: with_stage_progress / progress_full / no_progress / invalid_progress / chunk_filter 정합). web tsc + lint 0 error |
| ship | **S1 D2 — 자동 골든셋 100+ 확장 + v1 통합 + retrieval fallback chain 갱신** (senior-developer 구현, master plan §6 정합) | (A) `auto_goldenset.py` 실 실행 (`--chunks-per-doc 10`) → 11 docs × ~10 chunks + 5 negative = **128 row** v0.7 auto CSV 생성. Gemini 2.5-flash, 503 retry 6회 (모두 회복), quota 약 ~123 호출 소진. query_type 9 분포: exact_fact 104 / out_of_scope 5 / summary 6 / table_lookup 5 / numeric_lookup 4 / synonym_mismatch 3 / cross_doc 1 / **fuzzy_memory 0 + vision_diagram 0** (DoD 7/9 — query 자연 생성 한계, 후속 sprint 의 prompt 다양화로 보완). (B) **사용자 자료 노출 정책 (b) 비식별화 채택** — `_PUBLIC_DOC_STEMS` set + `is_public_doc_title()` (NFC + 공백/밑줄 정규화 + 25자 prefix 매칭) → public 7건 raw 유지 (70 rows), private 6건 비식별화 (53 rows: source_chunk_text+expected_answer_summary 빈 값). `--allow-private-source-text` ENV opt-in / `--redact-existing` 후처리 (Gemini 재호출 0). (C) **`build_golden_v1.py` 신규** — auto 128 + user 32 → 중복 query 3건 (NFC 정규화 비교, user 우선) → **`golden_v1.csv` 157 row** (12 컬럼). user 7컬럼 → 12컬럼 padding (id `G-U-***`, doc_id 빈, ...). (D) `eval_retrieval_metrics.py` `_GOLDEN_FALLBACK_CHAIN = (v1, v0.7, v0.5, v0.4)` + utf-8-sig CSV 로드 (BOM 호환) — 자동 v1 우선 진입 시 123 retrieval entry 평가 가능 (G-U-*** 32건은 doc_id 빈 값으로 자동 skip, run_v06_user_answer.py 가 별도 담당). **502 통과 / skipped 0 / 회귀 0**. 사이드 이펙트: `run_ragas.py`/`run_phase2_d_diagnosis.py` 는 v0.4 sonata 한정 평가 유지, `run_v06_user_answer.py` 는 v0.6 user 한정 — 모두 변경 영향 0 |
| ship | **S1 D3 — 실 query 로그 시각화 대시보드** (senior-developer 구현, master plan §6 정합) | (A) **백엔드 신규 endpoint** `GET /admin/queries/stats?range={7d\|14d\|30d}` — `search_metrics_log` (mig 006) 직접 SELECT, KST 일별 GROUP BY (zero-fill), 9 query_type 분포 (`evals/auto_goldenset.classify_query_type` 재사용 — `test_auto_goldenset.py:21` sys.path 보정 패턴), 최근 10건 실패 케이스 분류 (`fallback_reason` 우선 → permanent_4xx/transient_5xx, `fused==0` → no_hits). 마이그 006 미적용 환경 graceful → `error_code='migrations_pending'`, evals 모듈 import 실패 → `error_code='classify_unavailable'`. RPC 신설 X (단순 GROUP BY 만 필요). (B) **프론트엔드 신규 페이지** `/admin/queries` — Server Component 첫 fetch (`getAdminQueriesStats('7d')`) + Client Component (`QueriesDashboard`) range 토글. `useTransition` race 방지 (web/AGENTS.md §3), useEffect 안 동기 setState 0 (§2 lint). 4 섹션: 요약 4 카드 (총 query / 성공률 / 평균 latency / 실패 건수) / 일별 SVG `<polyline>` sparkline (외부 의존성 0, AGENTS §4) / 9 query_type 분포 표 (CSS `width%` bar) / 실패 케이스 리스트 (FailureBadge 색 분기). graceful — migrations_pending / 0 row / classify_unavailable / 백엔드 미기동 4 시나리오 모두 별도 카드. mobile-first responsive (`grid-cols-2 sm:grid-cols-4`). (C) **단위 테스트 8건 신규** — `test_admin_queries.py` (basic 매핑 / 빈 row zero-fill / range=30d / 4 실패 분류 + cap 10 / DB raise graceful). 502→**510 통과 / skipped 0 / 회귀 0**. tsc + lint 0 error. (D) 사이드 이펙트: 다른 admin 페이지 미존재 (단일 페이지). 권한 — single-user MVP 라 인증 없음 (production 진입 시 별도 sprint). **DoD: 1주 누적 후 실 query 분포 확인 → S1 D5 모델 회귀 측정의 사전 자료** |
| ship | **S1 D4 — answer_feedback 통합 분석 대시보드** (senior-developer 구현, master plan §6 정합) | (A) **백엔드 신규 endpoint** `GET /admin/feedback/stats?range={7d\|14d\|30d}` — `answer_feedback` (mig 011) 직접 SELECT, KST 일별 GROUP BY (zero-fill, up/down/total), rating 분포 + satisfaction_rate (sample 0건 시 None), 코멘트 4 카테고리 룰 분류 (`classify_comment` 신규), 최근 10건 코멘트 (코멘트 첨부된 것만). 마이그 011 미적용 환경 graceful → `error_code='migrations_pending'`. **코멘트 분류는 룰 기반 (LLM 호출 0, 비용 0)** — 매칭 우선순위 source > search > answer > other (출처 명시 시 가장 구체적 신호이므로 최우선). 본 룰은 D3 와 동일 — 1주 누적 후 실 사용자 코멘트 패턴으로 검증. (B) **프론트엔드 신규 페이지** `/admin/feedback` — Server Component 첫 fetch (`getAdminFeedbackStats('7d')`) + Client Component (`FeedbackDashboard`) range 토글. queries-dashboard 와 동일 패턴 (web/AGENTS.md §1~5 정합). 5 섹션: 헤더 + range 토글 / 요약 4 카드 (총 피드백 / 만족률 / 코멘트 비율 / 부정 평가) / 일별 SVG `<polyline>` sparkline (total + 부정 비율 텍스트) / 코멘트 카테고리 4 분포 표 (CSS bar) / 최근 코멘트 리스트 (RatingBadge + CategoryBadge + `break-words` 본문). graceful — migrations_pending / 0 row / 백엔드 미기동 / 코멘트 0건 4 시나리오 모두 별도 카드. (C) **단위 테스트 13건 신규** — `test_admin_feedback.py` (classify 룰 7건 — 4 카테고리 + 우선순위 + empty / basic 매핑 / 빈 row zero-fill / range parsing 3종 / 빈 코멘트 제외 / 최근 cap 10 / DB raise graceful). 510→**523 통과 / skipped 0 / 회귀 0**. tsc + lint 0 error. (D) 사이드 이펙트: `/admin/queries` 와 동일 라우터 (`app/routers/admin.py`) — 모듈 docstring 통합, helper 재사용 (`KST`, `_RANGE_TO_DAYS`, `_parse_recorded_at_kst`). 권한 — single-user MVP 라 인증 없음. 신규 의존성 0 (Badge 등 기존 shadcn 만). **DoD: 1주 누적 후 사용자 평가 누적 신호 확인** |
| ship | **S1 D5 — v1 골든셋 baseline 정량 측정** (senior-developer 직접 ship — 사용자 4건 초안 승인 후 8단계 작업 순서 수행, 별도 work-log [`2026-05-07 S1 D5 골든셋 v1 baseline 정량 측정.md`](./2026-05-07%20S1%20D5%20%EA%B3%A8%EB%93%A0%EC%85%8B%20v1%20baseline%20%EC%A0%95%EB%9F%89%20%EC%B8%A1%EC%A0%95.md)) | (A) **`evals/run_s1_d5_baseline.py` 신규 (~500 LOC)** — `GeminiLLMProvider` 직접 인스턴스화 (factory + lru_cache 우회, 측정 1회성 정합) + RAGAS Faithfulness + ResponseRelevancy + context_precision (BGE-M3 휴리스틱) 캡처 + retrieval Recall@10·MRR@10·nDCG@10 + 답변 휴리스틱 must_include·doc_hit·out_of_scope. `sample_golden(rows, *, sample_size, seed, stratified)` — `random.Random(seed)` 격리 PRNG 로 결정성 sampling. (B) **`api/tests/test_s1_d5_sampling.py` 신규 (5 테스트)** — same_seed_same_sample / different_seed_diff / size_exceeds_population (cap) / size_zero / stratified_by_query_type. 523→**528 통과 / skipped 0 / 회귀 0**. (C) **`evals/results/.gitignore` 신규** — `*` + `!.gitignore` (negative pattern). 측정 결과물 git 추적 X (재현성은 seed=42 로 확보). (D) **실측정** — sample 30 seed=42 stratified, scenario A `gemini-2.5-flash` (현재 default) 12/30 성공 + 18 503 high demand 실패 (graceful 부분 결과 저장). scenario B `gemini-2.5-flash-lite` 30/30 성공. **휴리스틱 평균 Δ (B-A) = +6.17pp** (must_include +9.5pp + doc_hit +2.9pp). latency 0.19× (1.7s vs 8.9s) + 비용 0.16~0.33× (output ratio). RAGAS judge 별도 sample 5 측정 — 사용자 가드레일 10~15분 안에 ResponseRelevancy 신호 일부 캡처. (E) **권고** — n=12 통계 신뢰도 매우 낮음 (CI ±15pp+) 으로 본 1회로 factory 변경 비권고. **다음 sprint S1.1 (가칭) — reliable 100+ sample (503 회복 시간대)** 후 결정. quota ~$0.16 사용 (가드레일 $0.50 1/3) |

### 1.2 변경 파일

| 종류 | 경로 |
|---|---|
| 신규 (work-log) | `work-log/2026-05-07 E1 인제스트 ETA latency sprint plan.md` |
| 신규 (work-log) | `work-log/2026-05-07 D3 진입 - E1 sprint 신설 + plan 작성.md` (본 문서) |
| 신규 (memory, git 추적 X) | `~/.claude/projects/.../memory/project_e1_eta_latency.md` |
| 갱신 (memory, git 추적 X) | `~/.claude/projects/.../memory/MEMORY.md` |

### 1.3 코드 변경

| 파일 | 변경 | 의의 |
|---|---|---|
| `web/src/components/jet-rag/drop-zone.tsx` (버그 fix) | div `onClick={() => inputRef.current?.click()}` 1줄 제거 | **사용자 보고 fix** — 파일 다이얼로그가 두 번 열리던 버그. div onClick + input absolute inset-0 native click 이중 trigger 였음. input 이 div 영역 전체를 덮으니 native click 만으로 충분 — drag-drop (onDrop) 그대로 보존 |
| `web/src/app/globals.css` (버그 fix) | `--destructive-foreground` 를 `--destructive` 와 같은 oklch(0.55 0.2 25) → oklch(0.99 0 0) (흰색) — light + dark 두 모드 모두 | **사용자 보고 fix** — RAGAS 47점 (destructive 톤) 의 점수 텍스트가 배경에 묻혀 안 보임. shadcn 표준 위반 (success/warning foreground 와 비교 시 destructive 만 잘못 설정). destructive 배경 위 모든 텍스트 가독성 회복 (Button variant=destructive 등 영향 범위) |
| `web/src/components/jet-rag/search-precision-card.tsx` | useEffect 안 자동 POST 제거, 'idle' phase + "측정" 버튼 추가, useMemo / handler 분리 (React 19 lint 정합) | **S0 D4 ship** — 비용 누수 fix (mount 시 LLM judge 자동 호출 0) |
| `web/src/components/jet-rag/search-precision-card.tsx` (P2 fix) | sr-only span + aria-describedby (Label-in-Name), useRef cancelRef (race guard), shadcn Button + flex-col responsive | **S0 D4 P2 follow-up** — senior-qa 리포트 3건 close |
| `evals/auto_goldenset.py` (v1 → v2) | 12 컬럼 schema, 9 query_type 룰 분류, must_include/source_hint 추출, 5건 negative 사전 정의, DoD 분포 측정 | **S1 D1 잔여** ship — S1 D2 (자동 100+ 확장) 의 선행 |
| `api/tests/test_auto_goldenset.py` (신규) | 30 테스트 (분류 10 / 추출 8 / negative 5 / schema 4 등) | 회귀 보호 |
| `.gitignore` | `/assets/` → `/assets/*` + `!/assets/public/` + `!/assets/public/**` (negative pattern) | **E2 1차 ship** — 공개 fixture 만 git 추적, 직속 비공개 자료는 ignore 유지 |
| `assets/public/` (신규 디렉토리) | KOGL 1유형 2건 + 사용자 명시 공공데이터 1건 = PDF 3건, 약 10.6 MB | 모든 컴퓨터·CI 자동 회귀 보호 |
| `assets/public/README.md` (신규) | 자료 표 / 라이센스 / 추가 절차 / 비공개 ENV 안내 / 다른 컴퓨터 진입 1줄 | 새 자료 추가 시 검토 절차 의무화 |
| `api/tests/test_pymupdf_heading.py` | 하드코딩 `_DEFAULT_PDF_DIR` → repo root 자동 인식 + public/private fixture 분리 + ENV `JETRAG_TEST_PDF_DIR` 폴백 | dict fallback 테스트가 CI 환경에서도 자동 실행 (skip 7→5) |
| `api/tests/test_pymupdf_heading.py` (E2 2차) | `_pdf_path()` 우선순위 추가: public → **`assets/` 직속 자동** → ENV → 부재 skip | 사용자 PC 에서 ENV 매뉴얼 없이 private 2건 자동 진입 |
| `api/tests/test_hwpx_heading.py` (E2 2차) | `_DEFAULT_HWPX_DIR` 잘못된 경로 제거 + repo root 자동 인식 + `_PUBLIC_HWPX_FILES`·`_PRIVATE_HWPX_FILES` 분리 + 같은 우선순위 적용 | HWPX 2건 (`직제_규정`·`한마음생활체육관_운영_내규`) 사용자 PC 자동 회귀, 다른 컴퓨터는 자동 skip |
| `assets/public/` (E2 3차) | 사용자 라이센스 5건 마이그: `law sample3.pdf`·`law_sample2.pdf`·`직제_규정.hwpx`·`한마음생활체육관_운영_내규.hwpx`·`law_sample1.hwp` → 모든 컴퓨터·CI 자동 회귀 (총 8건, 약 11 MB) | 저작권법 §7 (대법원 판결·결정) + KOGL 1유형 (대전시설관리공단) 모두 자유 이용 가능 |
| `assets/public/README.md` (E2 3차) | 자료 표 8건 row + 5단계 우선순위 표 + 다른 컴퓨터 4 시나리오 | 새 자료 추가 시 senior-developer 가 fixture 변수 갱신 의무 명시 |
| `api/tests/test_pymupdf_heading.py` (E2 3차) | `_pdf_path()` 5단계 우선순위 — `<repo>/<name>` 루트 직속 신설 + `_PUBLIC_PDF_FILES` 4건 (law sample 2건 추가) | 다른 컴퓨터 (자료 repo 루트 직속) 자동 인식 — ENV 0 줄 |
| `api/tests/test_hwpx_heading.py` (E2 3차) | `_hwpx_path()` 5단계 + `_PUBLIC_HWPX_FILES` 2건 (모두 public 이동, private 0건) | HWPX 모든 컴퓨터·CI 자동 회귀 |
| `api/tests/test_hwp_heading.py` (E2 3차, 신규) | Hwp5Parser 회귀 4 테스트 — `can_parse` 2건 + bad input 1건 + 실 자산 (`law_sample1.hwp`) 1건. 같은 5단계 우선순위 (`JETRAG_TEST_HWP_DIR`) | HWP 의 OLE2 추출 회귀 보호 진입 |
| `api/tests/test_pymupdf_heading.py` (E2 4차) | `_PUBLIC_PDF_FILES` 에 `sample-report.pdf` 1줄 추가 | A 수정 — 사용자 명시 공공데이터 자료가 `assets/public/` 에 추적되지만 KPI 평균 산출에서 누락이었음. 4건 → 5건 평균에 자동 합산 |
| `api/tests/test_pptx_parser.py` (E2 4차) | 5단계 우선순위 `_pptx_path()` + `_PUBLIC_PPTX_FILES`/`_PRIVATE_PPTX_FILES` 변수 + `PptxParserRealAssetTest` 신규 (1 테스트, picture-only 자산도 schema 정합 검증) | B 수정 — 기존엔 메모리 합성 binary 만 사용. 실 PPTX (`브랜딩_스튜디오앤드오어.pptx`) 사용자 PC 자동 회귀 진입, `JETRAG_TEST_PPTX_DIR` ENV 폴백 |
| `api/tests/test_docx_parser.py` (E2 4차) | 5단계 우선순위 `_docx_path()` + `_PUBLIC_DOCX_FILES`/`_PRIVATE_DOCX_FILES` 변수 + `DocxParserRealAssetTest` 신규 (1 테스트) | C 수정 — 실 DOCX (`승인글 템플릿1·3.docx`) 사용자 PC 자동 회귀 진입. `JETRAG_TEST_DOCX_DIR` ENV 폴백 |
| `api/tests/test_hwp_heading.py` (E2 5차) | `Hwp5ParserRejectNonOle2Test` 신규 + `_NEGATIVE_HWP_FILES` 상수 + `_PRIVATE_HWP_FILES` 주석 보강 (negative path 별도 처리 명시) | D 그룹 close — 비-OLE2 HWP 2건 (`cosmetic_law_sample.hwp`·`law sample2.hwp`) 거부 동작 + 한국어 안내 메시지 회귀 보호 (subTest 2건). 자료 부재 시 자동 skipTest (CI 호환) |
| `web/src/components/jet-rag/upload-item.tsx` (E2 5차) | `error_msg` 카드 `<p>` 에 `break-words` 1단어 추가 | 긴 한국어 안내 ("이 파일을 PDF 또는 HWPX 로 변환 후 다시 업로드해 주세요" 등) 가 카드 영역 밖으로 튀지 않도록 가독성 보강 |
| `web/src/app/doc/[id]/page.tsx` (E2 5차) | `error_msg` 카드 `<p>` 에 `break-words` 1단어 추가 | 문서 상세 페이지에서도 동일 가독성 보강 (upload-item.tsx 와 일관) |
| `api/app/ingest/eta.py` (**E1 1차 ship 일부**) | `compute_remaining_ms()` 시그니처에 `stage_progress: dict \| None = None` 추가 + `_current_stage_remaining_ms()` 신규 (current/total ratio 분해, total<=0 / 타입 불일치 시 fallback) + 모듈 docstring "E1 1차 ship" 절 추가 | **증상 ① fix** — ETA 정적 해소. cache 는 medians 만 (5분 TTL 그대로), `stage_progress` 는 매 호출 신선 반영. 호환성: `stage_progress=None` 시 기존 동작 유지 |
| `api/app/routers/documents.py` (**E1 1차 ship 일부**) | `compute_remaining_ms()` 호출 2곳 (944 active-docs / 1027 batch-status) 에 `stage_progress=row.get("stage_progress")` 전달 | ETA 가 매 polling 마다 stage_progress 반영. select 쿼리는 이미 `stage_progress` 컬럼 포함 (마이그 010, `_INGEST_JOBS_BASE_COLUMNS` + 컬럼 미존재 시 graceful skip 로직) |
| `api/tests/test_ingest_eta.py` (**E1 1차 ship 일부**) | 5건 신규: `test_compute_remaining_ms_with_stage_progress` (13/29 정확 ratio 검증) / `test_compute_remaining_ms_progress_full` (current==total 시 0+이후) / `test_compute_remaining_ms_no_progress` (None 호환) / `test_compute_remaining_ms_invalid_progress` (4 invalid case subTest) / `test_chunk_filter_in_stage_order` (web 정합) | 회귀 보호 — 502 통과, 회귀 0 |
| `web/src/lib/api/types.ts` (**E1 1차 ship 일부**) | `StageValue` union 에 `'chunk_filter'` 추가 | 증상 ③ fix — backend 9 stage 와 타입 정합 회복 |
| `web/src/lib/stages.ts` (**E1 1차 ship 일부**) | `STAGE_ORDER` 에 `'chunk_filter'` 추가 (8→9), `STAGE_LABELS.chunk_filter = '청크 필터'` 신규, 정합 코멘트 추가 | **증상 ③ fix** — stage 카운터 1/9 정합. 라벨 "청크 필터" (노이즈 청크 제거의 짧은 한국어) |
| `web/src/components/jet-rag/stage-progress.tsx` (**E1 1차 ship 일부**) | progress bar 칸별 `width:${pct}%` 부분 색칠 (외곽 `bg-muted` overflow-hidden + 내부 `bg-primary` width%), `computeCurrentBarPct()` 신규 helper, 카운터 "1/9 · 13/29 페이지" 동시 표시 | **증상 ② fix** — 현재 stage 칸이 stage_progress 비율로 부분 색칠. stage_progress 없는 stage 는 50% indeterminate. 완료 100% / 미도달 0% / failed 100%+destructive 색 |
| `evals/auto_goldenset.py` (**S1 D2**) | (1) `_PUBLIC_DOC_STEMS` frozenset 8건 + `_normalize_title()` (NFC + 공백→밑줄 + 25자 prefix) + `is_public_doc_title()` 신규. (2) main() 의 row 생성 분기 — `expose_raw_text = allow_private OR is_public` 으로 source_chunk_text·expected_answer_summary 채움 여부 결정. (3) `--allow-private-source-text` / `--redact-existing` CLI 옵션 신규. (4) `redact_existing_csv()` 함수 — 기존 v0.7 CSV 의 비식별화 정책만 idempotent 재적용 (Gemini 재호출 0, DB chunks 조회로 raw 복원). | **사용자 자료 노출 방지** + Gemini quota 절약 후처리 |
| `evals/build_golden_v1.py` (**S1 D2 신규**) | auto + user 통합 — `_load_csv_rows()` (utf-8-sig BOM) + `_pad_user_row()` (7→12 컬럼) + `merge_golden()` (NFC 정규화 + lower + 공백 정리 후 user 우선 dedupe) + 12 컬럼 출력. stdout 통계 (auto+user-dup=merged) | v0.6 user 와 v0.7 auto 한 schema 로 통합, retrieval 평가 + answer 평가 모두 한 CSV |
| `evals/golden_v0.7_auto.csv` (**S1 D2 신규**) | 128 row, 12 컬럼, public 7 doc raw / private 6 doc 비식별화, query_type 7/9 분포 (DoD 부분 cover) | retrieval 평가 base |
| `evals/golden_v1.csv` (**S1 D2 신규**) | 157 row (32 user + 125 auto, 중복 3건 제거), 12 컬럼 통합 schema | eval_retrieval_metrics + run_v06_user_answer 공용 base |
| `evals/eval_retrieval_metrics.py` (**S1 D2**) | `_GOLDEN_CSV_V1` / `_GOLDEN_CSV_V07` 상수 + `_GOLDEN_FALLBACK_CHAIN` (v1 → v0.7 → v0.5 → v0.4) + `_load_golden()` utf-8-sig 로드 + main() fallback 자동 선택 | retrieval 평가 시 v1 자동 진입 |
| `api/app/routers/admin.py` (**S1 D3 신규**) | `GET /admin/queries/stats?range={7d\|14d\|30d}` endpoint. `search_metrics_log` 직접 SELECT + KST 일별 zero-fill GROUP BY + 9 query_type 분포 (`auto_goldenset.classify_query_type` lazy import, sys.path 보정) + 최근 10건 실패 분류. graceful — `migrations_pending` / `classify_unavailable` 두 error_code | S1 D3 실 query 로그 대시보드 백엔드 |
| `api/app/routers/__init__.py` (**S1 D3**) | `admin_router` import + `__all__` 등록 | router 레지스트리 일관성 |
| `api/app/main.py` (**S1 D3**) | `from app.routers import admin_router` + `app.include_router(admin_router)` | FastAPI app 에 endpoint 마운트 |
| `api/tests/test_admin_queries.py` (**S1 D3 신규**) | 8 단위 테스트 — basic 매핑 / 빈 row zero-fill / range=30d / 4 실패 분류 (permanent_4xx, transient_5xx, no_hits, cap 10) / DB raise graceful. mock 기반, Supabase env 없이도 실행 | 회귀 보호 — 502→510 통과 |
| `web/src/lib/api/types.ts` (**S1 D3**) | `AdminRange` / `AdminFailureReason` / `AdminDailyBucket` / `AdminFailedSample` / `AdminQueriesStatsResponse` 타입 신규 | 프론트 fetcher · UI 정합 타입 |
| `web/src/lib/api/index.ts` (**S1 D3**) | `getAdminQueriesStats(range)` fetcher 신규 (`apiGet` 재사용) | client/server 양측 호출 진입점 |
| `web/src/app/admin/queries/page.tsx` (**S1 D3 신규**) | Server Component, `getAdminQueriesStats('7d').catch(() => null)` graceful, `<QueriesDashboard initialStats=...>` 렌더. 백엔드 미기동 시 전용 안내 카드 | `/admin/queries` 진입점 |
| `web/src/components/jet-rag/admin/queries-dashboard.tsx` (**S1 D3 신규**) | Client Component (`'use client'`). range 토글 (`useTransition` race 방지) + initialStats 우선 + fetched fallback. 4 섹션: SummaryCards / DailySparklineCard (SVG polyline) / QueryTypeDistributionCard (CSS bar) / FailedSamplesCard. 4 graceful 분기 (loading / fetchError / migrations_pending / classify_unavailable / 0 row) | 외부 의존성 0 (recharts 미사용, AGENTS §4) |
| `api/app/routers/admin.py` (**S1 D4 확장**) | 모듈 docstring 두 endpoint 모두 안내. `classify_comment(text)` 신규 (4 카테고리 룰 기반, source > search > answer > other 우선순위, LLM 호출 0). `GET /admin/feedback/stats` endpoint + DTO 5종 (`FeedbackDailyBucket` / `FeedbackComment` / `AdminFeedbackStatsResponse`) + helper 2 (`_build_feedback_daily_buckets` / `_build_comment_analysis`). queries 의 `KST`·`_RANGE_TO_DAYS`·`_parse_recorded_at_kst` 재사용 | S1 D4 — answer_feedback 통합 분석 백엔드 |
| `api/tests/test_admin_feedback.py` (**S1 D4 신규**) | 13 단위 테스트 — classify 룰 7건 (4 카테고리 + 우선순위 source>search>answer + empty/whitespace) / basic 매핑 / 빈 row zero-fill / range parsing 3종 (7d/14d/30d) / 빈 코멘트 제외 / 최근 cap 10 (코멘트 15건이라도 10건만) / DB raise graceful. mock 기반, Supabase env 없이 실행 | 510→523 통과, 회귀 0 |
| `web/src/lib/api/types.ts` (**S1 D4 확장**) | `AdminFeedbackRating` / `AdminFeedbackCategory` / `AdminFeedbackDailyBucket` / `AdminFeedbackComment` / `AdminFeedbackStatsResponse` 타입 5종 신규 | 프론트 fetcher · UI 정합 |
| `web/src/lib/api/index.ts` (**S1 D4 확장**) | `getAdminFeedbackStats(range)` fetcher 신규 (`apiGet` 재사용) | client/server 양측 호출 진입점 |
| `web/src/app/admin/feedback/page.tsx` (**S1 D4 신규**) | Server Component, `getAdminFeedbackStats('7d').catch(() => null)` graceful, `<FeedbackDashboard initialStats=...>` 렌더. 백엔드 미기동 시 전용 안내 카드 | `/admin/feedback` 진입점 |
| `web/src/components/jet-rag/admin/feedback-dashboard.tsx` (**S1 D4 신규**) | Client Component (`'use client'`). range 토글 (`useTransition` race 방지) + initialStats 우선 + fetched fallback. 5 섹션: DashboardHeader / SummaryCards (총 피드백·만족률·코멘트 비율·부정 평가) / DailySparklineCard (SVG polyline + 부정 비율 텍스트) / CommentCategoriesCard (CSS bar 4 카테고리) / RecentCommentsCard (RatingBadge + CategoryBadge + `break-words`). 5 graceful 분기 (loading / fetchError / migrations_pending / 0 row / 코멘트 0건) | queries-dashboard 와 동일 패턴, 외부 의존성 0 |
| `evals/run_s1_d5_baseline.py` (**S1 D5 신규, ~500 LOC**) | `sample_golden(rows, *, sample_size, seed, stratified)` 결정성 sampling (`random.Random(seed)` 격리 PRNG, stratified by query_type) / `_measure_one()` retrieval+answer+RAGAS 통합 / `_run_scenario()` `GeminiLLMProvider(model=...)` 직접 인스턴스화 (factory 우회 — 측정 1회성 정합, lru_cache `_get_llm()` 우회) / `_format_baseline_md()` 비교 표 + 권고 분기 (lite ≥-2pp / hybrid -2~-5pp / flash 유지 ≤-5pp) | S1 마무리 — v1 골든셋 (157 row) 첫 정량 measurement. 사용자 승인 4건 모두 반영 |
| `api/tests/test_s1_d5_sampling.py` (**S1 D5 신규**) | 5 테스트 — same seed → same sample / diff seed → diff sample / size > population → cap / size = 0 → 빈 / stratified by query_type → 비율 보존 (70/30 → 7/3 ±1) | sampling 결정성 회귀 보호 — `evals/results/` git ignore 가능 정합성 |
| `evals/results/.gitignore` (**S1 D5 신규**) | `*` + `!.gitignore` (negative pattern) | 측정 결과물 git 추적 X (시점별 갱신 → diff 노이즈 회피). 정책은 다른 컴퓨터에 동기화 — 재현성은 seed=42 sampling 으로만 확보 |

---

## 2. baseline 검증 결과

### 2.1 단위 테스트

```text
Ran 460 tests in 14.837s
OK (skipped=7)
```

D1~D2 누적 460 통과 유지. 회귀 0.

### 2.2 Supabase 마이그 정합성

```sql
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('vision_usage_log', 'search_metrics_log', 'vision_page_cache')
ORDER BY table_name;
-- 결과: search_metrics_log, vision_page_cache, vision_usage_log (3 rows ✅)
```

```sql
SELECT column_name FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'vision_usage_log'
ORDER BY ordinal_position;
-- 결과: 15 컬럼 (call_id, called_at, success, error_msg, quota_exhausted, source_type,
--        doc_id, page, prompt_tokens, image_tokens, output_tokens, thinking_tokens,
--        retry_attempt, estimated_cost, model_used) ✅
```

마이그 005~008/014/015 모두 적용 상태 확인 → 다른 컴퓨터에서도 동일.

### 2.3 환경 변수

사용자 보고: `.env` 값 모두 추가 완료. 키 항목 (확인용):
- `JETRAG_PDF_VISION_ENRICH=true`
- `JETRAG_GEMINI_RETRY=3` (D2-C 후 503 회복용)
- 기타 SUPABASE / GEMINI / HF / OPENAI 키

---

## 3. E1 sprint 신설 — 핵심 (상세는 별도 plan 파일)

### 3.1 트리거

사용자 보고 — 어제(2026-05-05) PDF 업로드 시 화면 "남은 시간 3분" 표시 → 실측 6~7분. ETA / 실측 ratio ≈ 0.4 (2배 어긋남) + latency 자체 부담.

### 3.2 plan 핵심 요약

- **목표**: ETA 표시/실측 ratio **0.7~1.3** (50p PDF), 50p PDF p50 **≤ 3분** / p95 **≤ 5분**
- **부정확 3대 원인 가설**: ① vision 페이지 수 변동성 미반영, ② cold start fallback (extract=5000ms) 부족, ③ 5분 TTL 캐시 진부화
- **latency 가설**: vision 페이지 순차 (concurrency=0), 503 retry 백오프 누적, vision_page_cache lookup 미통합
- **개선 후보 7개**: E1-A1 (ETA 공식 분해, 1순위) / E1-A2 (페이지 동시 호출, 2순위) / E1-A3 (vision_page_cache lookup = D2-B 흡수, 3순위) / E1-A4 (TTL 단축, 5순위) / E1-A5 (fallback 정정 + sample<3 ETA 미노출, 4순위) / E1-A6 (백오프 cap, 6순위) / E1-A7 (SSE, deferred)
- **권고 ship 순서**: 1차 = 진단 + E1-A1+A4+A5 (정확도 핵심, 1.5~2일) → 2차 = E1-A3+A2 (latency, D2-B 흡수, 2~3일) → 3차 = 옵션·deferred
- **2026-05-07 즉시 진입 — E1-A1 일부 ship** (사용자 명시 "큰 혼란"): 인제스트 진행 표시 3대 증상 일괄 fix. ① ETA 정적 (4분 표시 후 1분 경과해도 4분) → `compute_remaining_ms` 에 `stage_progress` 인자 + 분해. ② progress bar 1칸 → 칸별 `width%` 부분 색칠. ③ 카운터 1/8 vs 9 stage → web `STAGE_ORDER` 에 `chunk_filter` 추가. 단위 테스트 502 통과 / 회귀 0. **나머지 E1-A1 (vision 페이지 동시성 분리·sample<3 미노출 등) + A4 + A5 는 다른 컴퓨터 진단 후 진입**

### 3.3 별도 plan 파일

→ [`2026-05-07 E1 인제스트 ETA latency sprint plan.md`](./2026-05-07%20E1%20%EC%9D%B8%EC%A0%9C%EC%8A%A4%ED%8A%B8%20ETA%20latency%20sprint%20plan.md)

별도 파일에 진단 SQL 5건 (S1~S5), 9단계 진입 절차, §10 진단 결과 채움 칸 포함.

---

## 4. Master plan §6 sprint 진행 현황 (E1 외 전체)

본 절은 [`2026-05-06 무료유료 모델 전략 통합 plan + 다른 컴퓨터 핸드오프.md`](./2026-05-06%20%EB%AC%B4%EB%A3%8C%EC%9C%A0%EB%A3%8C%20%EB%AA%A8%EB%8D%B8%20%EC%A0%84%EB%9E%B5%20%ED%86%B5%ED%95%A9%20plan%20+%20%EB%8B%A4%EB%A5%B8%20%EC%BB%B4%ED%93%A8%ED%84%B0%20%ED%95%B8%EB%93%9C%EC%98%A4%ED%94%84.md) §6 의 6 sprint plan 과 실제 ship 결과를 대조한 표. 2026-05-07 시점 스냅샷.

### 4.1 S0 — 비용 계측 / 캐시 / cap (목표 1주, 약 **40% 진척**)

| Day | 작업 | 상태 | 근거 |
|---|---|---|---|
| D1 | 마이그 014 (vision_usage_log 9컬럼) + record_call usage | ✅ | commit `bd17fb4` + P2 follow-up `c717ea8` |
| D2 | 마이그 015 (vision_page_cache 신규) | ✅ | commit `9501b32` |
| D2 | `_enrich_pdf_with_vision` lookup→miss→upsert 통합 | 🟡 부분 | 마이그만 적용, 코드 미통합 = D2-B 잔여 (E1-A3 와 동일 작업) |
| D3 | sweep 3→2 + retry 3→1 곱셈 제거 | ✅ | commit `2fa8c3c`, `92c6132`, `1373a3a` (D2-C) |
| D3 | 데이터 기반 budget 초기값 | ❌ | 1주 누적 데이터 필요 |
| D4 | doc/일별 cost cap (`budget_guard.py`) | ❌ | `api/app/services/` 에 파일 없음 |
| D4 | `/search/eval-precision` 자동 POST 제거 | ✅ **2026-05-07 ship** | `search-precision-card.tsx` — mount useEffect 는 GET 만, `handleMeasure` 사용자 클릭 시만 POST. tsc + lint 0 error |
| D5 | vision 24h cap + Google AI Studio cross-check | ❌ | 1주 누적 데이터 필요 |

### 4.2 S1 — 골든셋 v1 + 실 query 로그 (목표 1주, **100% 진척** — D5 ship 완료)

| Day | 작업 | 상태 | 근거 |
|---|---|---|---|
| D1 | 사용자 draft 골든셋 33 entry | ✅ | commit `0cdcea4` — `evals/golden_v0.6_user.csv` |
| D1 | `auto_goldenset.py` v2 갱신 | ✅ **2026-05-07 ship** | 12 컬럼 schema + 9 query_type + must_include/source_hint + negative 5건 + 30 단위 테스트 |
| D2 | 자동 100+ 확장 (`golden_v0.7_auto.csv`) | ✅ **2026-05-07 ship** | 128 row (123 auto + 5 negative). Gemini 2.5-flash, 비식별화 정책 (b) 적용 (public 7 raw / private 6 비식별화). DoD 7/9 query_type cover (fuzzy_memory + vision_diagram 미충족 — query 자연 생성 한계) |
| D2 | 통합 `golden_v1.csv` | ✅ **2026-05-07 ship** | 157 row (32 user + 125 auto, 중복 3건 user 우선 제거). `build_golden_v1.py` 신규. retrieval 평가 entry 123건 + answer 평가 entry 32건 |
| D2.5 | `eval_retrieval_metrics.py` fallback chain | ✅ **2026-05-07 ship** | v1 → v0.7 → v0.5 → v0.4 자동 선택, utf-8-sig BOM 호환 |
| D3 | 실 query 로그 대시보드 | ✅ **2026-05-07 ship** | `GET /admin/queries/stats` 신규 + `/admin/queries` 페이지 신규. search_metrics_log 일별 GROUP BY + 9 query_type 분포 + 최근 10건 실패 케이스. 8 단위 테스트, tsc + lint 0 error. **DoD: 1주 누적 후 실 query 분포 확인 (S1 D5 사전 자료)** |
| D4 | answer_feedback 통합 분석 | ✅ **2026-05-07 ship** | `GET /admin/feedback/stats` 신규 + `/admin/feedback` 페이지 신규. `answer_feedback` (mig 011) 일별 GROUP BY + rating 분포 + 코멘트 4 카테고리 룰 분류 (source > search > answer > other 우선순위, LLM 호출 0) + 최근 10건 코멘트. 13 단위 테스트, tsc + lint 0 error. **DoD: 1주 누적 후 사용자 평가 누적 신호 확인** |
| D5 | 골든셋 v1 baseline 정량 측정 | ✅ **2026-05-07 ship** | `evals/run_s1_d5_baseline.py` 신규 + sampling 결정성 단위 테스트 5건 + `evals/results/.gitignore`. 실측정 30 sample seed=42 stratified — A 2.5-flash 12/30 성공 (18 503) / B 2.5-flash-lite 30/30 성공. **휴리스틱 Δ (B-A) = +6.17pp + latency 0.19× + 비용 0.16~0.33×**. 권고 — n=12 신뢰도 낮음 → S1.1 reliable 100+ 후 결정. quota ~$0.16. 단위 테스트 523→**528**, skipped 0, 회귀 0 |

### 4.3 S1.5 / S2 / S3 / S4 / S5 — **0% 미진입**

`api/app/services/` 에 `budget_guard.py`, `intent_router.py`, `meta_filter.py`, `decomposition.py`, `reranker.py` 모두 없음. 본격 진입 신호 없음.

### 4.4 Master plan 외 추가 ship (보강)

| 작업 | 효과 | 의의 |
|---|---|---|
| `factory.py` (LLMAdapter 추상화) | `JETRAG_LLM_PROVIDER` 1줄 ENV 전환 | Codex 권고 + senior-planner — paid LLM 채택 안전 인프라 |
| 모델 정정 (2.0 deprecated → 2.5-flash + 2.5-flash-lite) | master plan §4.1 정합 회복 | D2-D 정정 |
| P2 `retry_attempt` 컬럼 활성화 | 503 회복 시점 추적 | S0 D1 follow-up |
| **E1 sprint plan** (오늘) | 사용자 ETA 보고 격상 | master plan §6 외 신규 라인, S2 직교, D2-B 흡수 |
| **E2 1차 ship** (오늘) — 테스트 fixture 인프라 보강 | 공개 자료 3건 git 추적 + assets/public README + test_pymupdf_heading 경로 갱신 + skip 7→5 | mock + 메모리 합성 binary 한계 보강. 다른 컴퓨터·CI 자동 회귀 보호 진입 |
| **E2 2차 ship** (오늘, 사용자 지적 반영) — assets/ 직속 자동 진입 | `_pdf_path`·`_hwpx_path` 우선순위에 `<repo>/assets/` 직속 단계 추가 + `test_hwpx_heading` 마이그 (잘못된 경로 제거) | 사용자 PC 에서 ENV 매뉴얼 없이 private 자료 자동 회귀 (skip 5→0) |
| **E2 3차 ship** (오늘) — 라이센스 5건 마이그 + 다른 컴퓨터 루트 직속 자동 인식 + HWP 테스트 신규 | `assets/public/` 5건 추가 (대법원 판결·결정 3 + 대전시설관리공단 규정 2) + 5단계 우선순위 (`<repo>/<name>` 루트 직속 신설) + `test_hwp_heading.py` 신규 (Hwp5Parser 회귀 4 테스트) | 다른 컴퓨터에서 자료가 repo 루트 직속에 있어도 ENV 0 줄로 자동 인식. public 8건 / 약 11 MB / 모든 컴퓨터·CI 자동 회귀. 단위 테스트 490→**494**, skipped 0 |
| **E2 4차 ship** (오늘, 사용자 점검 반영) — 미진입 자료 5건 (A·B·C) 회귀 진입 | A: `sample-report.pdf` `_PUBLIC_PDF_FILES` 등록 (1줄). B: `test_pptx_parser.py` 5단계 우선순위 + `PptxParserRealAssetTest`. C: `test_docx_parser.py` 5단계 우선순위 + `DocxParserRealAssetTest`. ENV 신규 2개 (`JETRAG_TEST_PPTX_DIR` / `JETRAG_TEST_DOCX_DIR`) | assets/ 14건 중 12건 자동 회귀 진입 (D 2건 = HWP OLE2 아님 처리 불가). PDF KPI 평균 4건→5건 자동 합산. 단위 테스트 494→**496**, skipped 0, 회귀 0 |
| **E2 5차 ship** (오늘, 사용자 점검 반영) — 마지막 2건 (D 그룹) negative path 회귀 + 사용자 안내 메시지 검증·보강 | (1) `Hwp5ParserRejectNonOle2Test` 신규 — 비-OLE2 HWP 2건 거부 동작 + 한국어 안내 메시지 (`OLE2` 또는 `PDF`+`HWPX` 키워드) 검증. (2) backend 메시지 그대로 유지 (이미 한국어 + 변환 옵션 명시). (3) frontend `upload-item.tsx`/`doc/[id]/page.tsx` `error_msg` 카드에 `break-words` 추가 (가독성 보강) | assets/ **14/14 cover** 확정 — 12건 정상 회귀 + 2건 거부 동작 회귀. 단위 테스트 496→**497**, skipped 0, 회귀 0. 다른 컴퓨터·CI: negative fixture 부재 시 자동 skip (시뮬레이션 검증). frontend tsc/lint 0 error |

### 4.5 다음 작업 우선순위 (오늘 결정 + 향후)

**A. 즉시 가능 (현재 컴퓨터, PDF 불필요)**

| 순위 | 작업 | 작업량 | 비고 |
|---|---|---|---|
| 1 | ~~S0 D4 — `/search/eval-precision` 자동 POST 제거~~ | ✅ **ship** | 2026-05-07 완료 (commit 미진입 — 사용자 명시 요청 대기) |
| 2 | ~~S1 D1 잔여 — `auto_goldenset.py` v2 갱신~~ | ✅ **ship** | 2026-05-07 완료 — S1 D2 진입 가능 |
| 3 | ~~E2 1차 ship — 테스트 fixture 인프라 보강~~ | ✅ **ship** | 2026-05-07 완료 — 공개 3건 git 추적, skip 7→5 |
| 3-2 | ~~E2 2차 ship — assets/ 직속 자동 진입~~ | ✅ **ship** | `_pdf_path`·`_hwpx_path` 직속 단계 추가, skip 5→0 |
| 3-3 | ~~E2 3차 ship — 라이센스 5건 + 루트 직속 자동 인식 + HWP 테스트~~ | ✅ **ship** | public 8건 / 5단계 우선순위 / 494 통과 |
| 3-4 | ~~E2 4차 ship — 미진입 자료 5건 (A·B·C) 회귀 진입~~ | ✅ **ship** | sample-report `_PUBLIC_PDF_FILES` 등록 + PPTX/DOCX 5단계 우선순위 + 실 fixture 테스트 클래스 / 496 통과 / assets/ 14건 중 12건 자동 회귀 |
| 3-5 | ~~E2 5차 ship — 마지막 2건 (D 그룹) negative path 회귀 + 사용자 안내 메시지 검증·보강~~ | ✅ **ship** | `Hwp5ParserRejectNonOle2Test` 신규 + frontend `break-words` / 497 통과 / **assets/ 14/14 cover** 확정 |
| 3-6 | ~~**E1 1차 ship 일부 — 인제스트 진행 표시 3대 증상 일괄 fix**~~ | ✅ **ship** | 사용자 명시 "유저에게 큰 혼란" 격상. ETA stage_progress 분해 + progress bar 부분 색칠 + STAGE_ORDER chunk_filter 추가 / 502 통과 / 회귀 0 |
| 4 | E2 follow-up — 기관 규정·법률 샘플 라이센스 검토 | 0.5일 | `직제_규정.hwpx`·`한마음생활체육관_운영_내규.hwpx`·`law_sample` 시리즈 출처 / 공개 가능성 사용자 확인 후 추가 이동 |
| 5 | ~~S1 D2 — 자동 골든셋 100+ 확장 + v1 통합~~ | ✅ **ship** | 2026-05-07 완료 — 128 auto + 32 user → v1 157 row, fallback chain v1 우선, 회귀 0 |
| 5-2 | ~~S1 D3 — 실 query 로그 시각화 대시보드~~ | ✅ **ship** | 2026-05-07 완료 — `GET /admin/queries/stats` + `/admin/queries` page (Server+Client RSC), 8 단위 테스트, 510 통과 회귀 0, tsc/lint 0 error. **1주 누적 (≥2026-05-14) 후 실 query 분포 확인 가능** |
| 6 | ~~S1 D5 — 골든셋 v1 baseline 정량 측정~~ | ✅ **ship** | 2026-05-07 완료 — `evals/run_s1_d5_baseline.py` (~500 LOC) + 단위 테스트 5건 + `evals/results/.gitignore` + 실측정 30 sample seed=42 stratified. 휴리스틱 Δ (B-A) = +6.17pp + lite latency 0.19× + 비용 0.16~0.33×. **scenario A 503 high demand 18/30 실패** → 다음 sprint S1.1 reliable 100+ sample 재측정 후 factory 변경 결정. quota ~$0.16. 528 통과 회귀 0. 별도 work-log: `2026-05-07 S1 D5 골든셋 v1 baseline 정량 측정.md` |
| 7 | ~~S1 D4 — answer_feedback 통합 분석 (👍/👎 분포 + 코멘트 룰 분류)~~ | ✅ **ship** | 2026-05-07 완료 — `GET /admin/feedback/stats` + `/admin/feedback` page (Server+Client RSC), 13 단위 테스트, 523 통과 회귀 0, tsc/lint 0 error. classify_comment 룰 (source>search>answer>other) — 1주 누적 후 D3 처럼 실 데이터로 검증. **1주 누적 (≥2026-05-14) 후 사용자 평가 신호 확인 가능** |
| 8 | E1 1차 ship 잔여 (E1-A1 vision 페이지 sub-ETA 정밀화 + E1-A4 TTL 단축 + E1-A5 fallback 정정·sample<3 미노출) | 1일 | 다른 컴퓨터 진단 후 진입 권고 |

**B. 다른 컴퓨터 (PDF 보유)**

1. E1 진단 (오늘 plan §3 SQL S1~S5, 30분)
2. E1 1차 ship — E1-A1+A4+A5 (정확도 핵심, 1.5~2일)
3. E1 2차 ship = S0 D2 잔여 + 페이지 동시 호출 — D2-B 흡수 (2~3일)

**C. 1주 데이터 누적 후 (S0 마무리)**

1. S0 D3 — budget 초기값 데이터 기반 설정
2. S0 D4 — `budget_guard.py` cost cap 메커니즘
3. S0 D5 — vision 24h cap + 대시보드

**D. S0 마감 후**

S1 D3~D5 → S1.5 (옵션) → S2 → S3 → S4 → S5 master plan 순차 진입.

---

## 5. 남은 이슈 (다른 컴퓨터에서 수행)

### 5.1 즉시 (E1 진단)

1. `git pull` 로 본 work-log + plan 파일 동기화
2. 어제 6~7분 걸린 PDF 1건 재업로드
3. **시작 직전** 화면 ETA + **시작·종료 시각** wall-clock 메모
4. T+1m / T+3m / T+5m 화면 ETA 캡처 (S5)
5. 종료 후 plan 파일 §3 의 SQL S1~S4 paste → plan 파일 §10 에 결과 기록
6. plan 파일 §10.4 의 1차 ship 진입 결정 체크리스트 진행

### 5.2 사용자 결정 필요 (E1 1차 ship 진입 전)

| # | 항목 | senior-planner 권고 default |
|---|---|---|
| 1 | 정확도 vs latency 우선순위 | (a) 정확도 P0 |
| 5 | 첫 인제스트 ETA 미노출 + 카피 | (b) "처음에는 시간 추정이 부정확합니다" |

(2·3·4번은 ship 중 default 로 진행 후 review 시 조정)

### 5.3 후속 sprint (E1 후)

E1 1차 ship 후 권고 순서:
- E1 2차 ship (D2-B 흡수, latency 본진입)
- ~~S1 D5 (2.5-flash vs 2.5-flash-lite 골든셋 회귀)~~ ✅ **2026-05-07 ship 완료** — n=12 신뢰도 낮음 → 후속 **S1.1 reliable 100+** (503 회복 시간대 재측정) 권고
- D4 cost cap

---

## 6. 다음 스코프 (E1 외)

E1 진입 중에도 병렬 가능한 작업:
- master plan §6 의 S1 D2 (자동 골든셋 100+ 확장) — Gemini quota 의존, E1-A2 의 concurrency 검증과 quota 충돌 가능 → **E1 1차 ship 후 진입 권고**
- 기획서 §13 KPI 에 신규 항목 "ETA 표시/실측 ratio" 추가 (E1 1차 ship DoD)

---

## 7. 활성 한계 (sprint 진입 전 점검)

| # | 한계 | 영향 | 회복 절차 |
|---|---|---|---|
| 1 | E1 진단 미완 | 1차 ship 목표 수치 (ETA ratio 0.7~1.3) 가설값 | 다른 컴퓨터에서 PDF 1회 reingest → plan 파일 §10 채움 |
| 2 | 503 fail rate 검증 부족 | vision 인제스트 부분 실패 가능 | E1 진단 S3 (retry_attempt 분포) 동시 검증 |
| 3 | vision_page_cache lookup 미통합 (D2-B) | reingest 시 비용 0 미달성 | E1-A3 = D2-B 흡수 ship 시 해소 |
| 4 | 단가 dict 가격 변경 추적 | estimated_cost 부정확 가능 | 분기별 https://ai.google.dev/pricing 재확인 |

---

## 8. 참고 문서 우선순위

| # | 문서 | 목적 |
|---|---|---|
| 1 | **본 문서** | 2026-05-07 종합 마스터 (계속 업데이트) |
| 2 | [`2026-05-07 S1 D5 골든셋 v1 baseline 정량 측정.md`](./2026-05-07%20S1%20D5%20%EA%B3%A8%EB%93%A0%EC%85%8B%20v1%20baseline%20%EC%A0%95%EB%9F%89%20%EC%B8%A1%EC%A0%95.md) | S1 D5 ship 본문 — 측정 결과·권고·quota 보고 |
| 3 | [`2026-05-07 E1 인제스트 ETA latency sprint plan.md`](./2026-05-07%20E1%20%EC%9D%B8%EC%A0%9C%EC%8A%A4%ED%8A%B8%20ETA%20latency%20sprint%20plan.md) | E1 plan 본문 + 진단 SQL + §10 reingest 결과 칸 |
| 4 | [`2026-05-06 D1~D2 누적 + 다른 컴퓨터 종합 진입 핸드오프.md`](./2026-05-06%20D1~D2%20%EB%88%84%EC%A0%81%20+%20%EB%8B%A4%EB%A5%B8%20%EC%BB%B4%ED%93%A8%ED%84%B0%20%EC%A2%85%ED%95%A9%20%EC%A7%84%EC%9E%85%20%ED%95%B8%EB%93%9C%EC%98%A4%ED%94%84.md) | 어제까지 baseline (D1~D2 ship 누적) |
| 5 | [`2026-05-06 무료유료 모델 전략 통합 plan + 다른 컴퓨터 핸드오프.md`](./2026-05-06%20%EB%AC%B4%EB%A3%8C%EC%9C%A0%EB%A3%8C%20%EB%AA%A8%EB%8D%B8%20%EC%A0%84%EB%9E%B5%20%ED%86%B5%ED%95%A9%20plan%20+%20%EB%8B%A4%EB%A5%B8%20%EC%BB%B4%ED%93%A8%ED%84%B0%20%ED%95%B8%EB%93%9C%EC%98%A4%ED%94%84.md) | Sprint S0~S5 master plan |
| 6 | [`2026-04-22 개인 지식 에이전트 기획서 v0.1.md`](./2026-04-22%20%EA%B0%9C%EC%9D%B8%20%EC%A7%80%EC%8B%9D%20%EC%97%90%EC%9D%B4%EC%A0%84%ED%8A%B8%20%EA%B8%B0%ED%9A%8D%EC%84%9C%20v0.1.md) | 페르소나·KPI·DoD 마스터 — §13 KPI 에 "ETA ratio" 신규 추가 권고 |

---

## 9. 한 문장 요약

> 2026-05-07 D3 진입 — 8 commit push (`a5cfc2a`→`f1c87b4`) + **S1 D3·D4·D5 ship 추가**. baseline 회복 + E1 sprint plan 본문 ship + S0 D4 자동 POST 제거 (+ P2 follow-up 3건) + S1 D1 잔여 (`auto_goldenset.py` v2 + 30 테스트) + **E2 1·2·3·4·5차 ship** (`assets/public/` 8건 git 추적, 5단계 우선순위 5개 형식 자동 인식, `test_hwp_heading.py`/실 fixture 테스트 클래스 신규, **negative path 회귀 + frontend `break-words` 가독성 보강**) + **E1 1차 ship 일부 즉시 진입 — 인제스트 진행 표시 3대 증상 일괄 fix** + **S1 D2 ship — 자동 골든셋 100+ 확장 + v1 통합 + retrieval fallback chain v1 우선** + **S1 D3 ship — 실 query 로그 시각화 대시보드** + **S1 D4 ship — answer_feedback 통합 분석 대시보드** + **S1 D5 ship — v1 골든셋 baseline 정량 측정** (`evals/run_s1_d5_baseline.py` ~500 LOC + sampling 결정성 단위 테스트 5건 + `evals/results/.gitignore` + 실측정 30 sample seed=42 stratified — A 2.5-flash 12/30 성공 503 high demand · B lite 30/30, **휴리스틱 Δ (B-A)=+6.17pp + latency 0.19× + 비용 0.16~0.33×**, n=12 신뢰도 낮음 → S1.1 reliable 100+ 후 결정, quota ~$0.16). 단위 테스트 460→**528** / skipped 0 / 회귀 0. **assets/ 14/14 cover** (12 정상 + 2 거부 동작 회귀). S1 진척률 15→**100%** (D5 ship 완료). E1 잔여 진단·구현은 PDF 보유 다른 컴퓨터에서 진입.

---

## 10. 다른 컴퓨터 진입 안내 — 자동 동작 절차

### 10.1 기본 동기화 (`git pull` 만으로 즉시 동작)

```bash
# 1) 코드 + fixture + 문서 동기화 (assets/public/ 자료 8건 약 11 MB 포함)
git pull origin main

# 2) 의존성 동기화
cd api && uv sync
cd ../web && pnpm install

# 3) 단위 테스트 — public fixture 자동 회귀
cd ../api && uv run python -m unittest discover tests
# 사용자 PC 기대 (E2 5차 ship 후): Ran 497 tests in ~14s, OK  (PDF 5 + HWPX 2 + HWP 1 + sonata + PPTX 1 + DOCX 2 + 비-OLE2 HWP 2 자동 진입, skipped=0)
# 다른 컴퓨터·CI 기대: Ran 497 tests in ~14s, OK (skipped 6~8)  (assets/ 직속 부재 → sonata·PPTX·DOCX·negative HWP 자동 skip)
```

`git pull` 한 시점에 본 컴퓨터의 모든 ship 이 동시에 들어옴:

| 항목 | 자동 동기화 | 비고 |
|---|---|---|
| work-log / plan / 종합 마스터 | ✅ | 본 문서, E1 plan, master plan 등 |
| 코드 변경 | ✅ | `search-precision-card.tsx`, `auto_goldenset.py`, `test_pymupdf_heading.py`, `test_hwpx_heading.py`, `test_hwp_heading.py` (신규), `test_pptx_parser.py` (E2 4차 — 실 fixture 클래스 추가), `test_docx_parser.py` (E2 4차 — 실 fixture 클래스 추가), `.gitignore` 등 |
| **`assets/public/` 공개 자료 8건 (약 11 MB)** | ✅ **별도 scp/cloud 불필요** | PDF 4건 (KOGL 2 + 사용자 명시 1 + 저작권법 §7 2건) + HWPX 2건 (KOGL 1유형 추정) + HWP 1건 (저작권법 §7). E2 4차 ship 으로 `sample-report.pdf` 가 PDF KPI 회귀에 정식 합산 (5건 평균) |
| **`assets/` 직속 자료** (private) | ❌ git ignore | 사용자 PC 에서는 `_pdf_path()` / `_pptx_path()` / `_docx_path()` 등이 자동 회귀 진입 / 다른 컴퓨터 부재 시 자동 skip |
| **`<repo>/` 루트 직속 자료** (다른 컴퓨터 패턴) | ❌ git ignore (`/*.pdf` 등) | 다른 컴퓨터에서 자료를 repo 루트에 두면 3순위 자동 인식 — ENV 0 줄 |
| 단위 테스트 회귀 | ✅ 자동 | 5단계 우선순위 (§10.2) 로 자동 해석. E2 4차 ship 으로 PPTX·DOCX 도 같은 패턴 적용 |

### 10.2 다른 컴퓨터 진입 시 자료 위치별 시나리오

`_pdf_path()` / `_hwpx_path()` / `_hwp_path()` / `_pptx_path()` / `_docx_path()` 의 **5단계 우선순위** 로 컴퓨터별 자료 위치를 자동 인식한다. ENV 변수 매뉴얼 0 줄이 default. (E2 4차 ship 으로 PPTX·DOCX 도 같은 패턴 적용)

**5단계 우선순위 표**

| # | 위치 | 정합 정책 | 자동 인식 |
|---|---|---|---|
| 1 | `<repo>/assets/public/<name>` | git 추적 (모든 컴퓨터·CI) | ✅ |
| 2 | `<repo>/assets/<name>` | `.gitignore` `/assets/*` (사용자 PC raw) | ✅ |
| 3 | `<repo>/<name>` (repo 루트 직속) | `.gitignore` `/*.{pdf,hwp,hwpx,docx,pptx}` (다른 컴퓨터 패턴) | ✅ |
| 4 | `$JETRAG_TEST_*_DIR/<name>` | ENV 폴백 (외장 디스크) | ENV 1줄 |
| 5 | 부재 → skipTest | CI 호환 | — |

**시나리오별 동작** (E2 5차 ship 후)

```text
시나리오 1: 자료가 <repo>/assets/ 직속에 있는 컴퓨터 (사용자 PC 패턴)
  → 2순위 자동 진입, ENV 0
  → 단위 테스트 497 통과 / skipped 0
  → assets/ 14/14 cover (12 정상 회귀 + 2 거부 동작 회귀)
    · PDF 5 + HWPX 2 + HWP 1 + sonata + PPTX 1 + DOCX 2 + alpha (정상 12)
    · 비-OLE2 HWP 2 (negative path: cosmetic_law_sample, law sample2 거부 동작 회귀)

시나리오 2: 자료가 <repo>/ 루트 직속에 있는 컴퓨터 (다른 컴퓨터 패턴)
  → 3순위 자동 진입, ENV 0
  → 단위 테스트 496 통과 / skipped 0 (PDF·HWPX·HWP·PPTX·DOCX 모두 자동)
  → E2 3차 ship + 4차 ship 으로 5개 형식 모두 지원

시나리오 3: 자료가 외장 디스크 / 별 위치 (예: /Volumes/External/...)
  → 4순위 ENV 폴백, 형식별 1줄 설정 필요
     export JETRAG_TEST_PDF_DIR=/Volumes/External/jetrag-data
     export JETRAG_TEST_HWPX_DIR=/Volumes/External/jetrag-data
     export JETRAG_TEST_HWP_DIR=/Volumes/External/jetrag-data
     export JETRAG_TEST_PPTX_DIR=/Volumes/External/jetrag-data   # E2 4차 ship 신규
     export JETRAG_TEST_DOCX_DIR=/Volumes/External/jetrag-data   # E2 4차 ship 신규
  → 단위 테스트 496 통과 / skipped 0

시나리오 4: 자료가 없는 컴퓨터 (CI 포함)
  → public 자료 (PDF 5 + HWPX 2 + HWP 1) 만 회귀
  → private 자료 (sonata·PPTX·DOCX) 자동 skip → 회귀 차단 0
  → 단위 테스트 496 통과 / skipped 4~6
```

> master plan §10.2 의 `JETRAG_PDF_VISION_ENRICH=true`, `JETRAG_GEMINI_RETRY=3` 와는 별개 변수 (테스트 fixture 전용).

### 10.3 비공개 자료 다른 컴퓨터로 옮길 때

git 추적 X 정책 (`.gitignore` 의 `/assets/*` + `/*.{pdf,hwp,hwpx,docx,pptx}`) → 다음 중 하나로 동기화:

1. **USB / 외장 디스크** — 가장 단순, 라이센스 위험 0. 옮긴 후 ENV 1줄 (`JETRAG_TEST_PDF_DIR=<dir>`) 또는 `<repo>/assets/` 또는 `<repo>/` 루트로 복사 → 자동 회귀
2. **iCloud / 사용자 클라우드** (라이센스 안전 디렉토리) — 자동 동기화 가능
3. **인제스트 후 폐기** — Supabase 에 chunks 만 남기고 raw 파일 폐기 (다음 컴퓨터에서 reingest 불필요)

### 10.4 새 자료 추가 정책

| 자료 종류 | 어디에 둘까 | git 추적 | 추가 의무 |
|---|---|---|---|
| 공개 라이센스 (KOGL / CC / 퍼블릭 도메인 / 저작권법 §7) | `assets/public/` | ✅ | README 표 + 출처·라이센스 명시 + senior-developer 가 fixture 변수 갱신 |
| 사용자 raw 자료 (개인정보 포함) | `assets/` 직속 또는 `<repo>/` 루트 | ❌ ignore 유지 | 0 |

E2 3차 ship 이후 public 자료 8건 (PDF 4 + HWPX 2 + HWP 1 + 사용자 명시 sample-report 1). 다음 자료 추가 시:
1. `assets/public/README.md` 표 row 추가
2. 해당 형식의 `_PUBLIC_*_FILES` 변수 갱신 (`test_pymupdf_heading.py` / `test_hwpx_heading.py` / `test_hwp_heading.py`)
3. 회귀 검증 — `cd api && uv run python -m unittest discover tests`

### 10.5 다음 진입 절차

- **E1 진단** (다른 컴퓨터, PDF 보유 시) — §5.1 절차 그대로. PDF 1건 reingest → E1 plan 파일 §10 paste
- **E2 follow-up (남은 항목)**:
  - ~~DOCX/PPTX 형식의 fixture 테스트~~ ✅ **2026-05-07 E2 4차 ship** — 5단계 우선순위 + 실 fixture 테스트 클래스 추가 (사용자 PC 자동 회귀, 다른 컴퓨터 자동 skip). 공개 fixture (`_PUBLIC_PPTX_FILES` / `_PUBLIC_DOCX_FILES`) 는 현재 0건, 라이센스 통과 자료 발굴 시 추가 후보
  - ~~`sample-report.pdf` `_PUBLIC_PDF_FILES` 등록~~ ✅ **2026-05-07 E2 4차 ship** — KPI 평균 4건→5건 자동 합산
  - ~~HWP 의 OLE2 아닌 자료 (`cosmetic_law_sample.hwp`, `law sample2.hwp`) 처리 가이드~~ ✅ **2026-05-07 E2 5차 ship** — D 그룹 close. negative path 회귀 (`Hwp5ParserRejectNonOle2Test`) + frontend `error_msg` 카드 가독성 보강 (`break-words`). assets/ **14/14 cover** 확정 (12 정상 + 2 거부 동작 회귀). 향후 `hwp_parser.py` 가 OLE2 아닌 컨테이너 (HWPML 등) 도 지원하도록 확장 시 positive set 으로 승격 가능
  - ~~기관 규정 2건 / 법률 샘플 5건 라이센스 검토~~ ✅ **2026-05-07 E2 3차 ship** — 5건 마이그, 남은 비공개 자료는 출처 미상 (`브랜딩_스튜디오앤드오어.pptx`, `승인글 템플릿`) 또는 라이센스 부적합 (`sonata-the-edge_catalog.pdf` 현대차 마케팅)
  - ~~`test_hwpx_heading.py` 의 하드코딩 경로 마이그~~ ✅ **2026-05-07 ship** (E2 2차)
  - ~~`test_hwp_heading.py` 신규 작성~~ ✅ **2026-05-07 E2 3차 ship**
- **S1 D2** (자동 골든셋 100+ 확장) — Gemini quota 1회 (~$0.05) 소진. `auto_goldenset.py` v2 로 `--chunks-per-doc 10` 실행
- **RAGAS Context Precision 점수 개선 — 베타 테스트 후 판단** (사용자 결정, 2026-05-07): sonata 카탈로그 + "소나타 휠 종류 뭐있어?" query 에서 47점. judge 가 LLM 아닌 `bge-m3-cosine` 휴리스틱 (`api/app/services/ragas_eval.py:164`, W25 D14 갱신). bge-m3-cosine 은 0.3~0.6 범위가 정상 — 47점이 "낮음" 이라기보다 단순 cosine 의 한계. 답변 품질과 cosine 점수는 별개 메트릭.
  - **베타 사용자 5~10명 데이터 누적 후 진입 결정** — 단일 사례로 결정 X
  - 누적 데이터에서 (a) 점수 vs 사용자 답변 만족도 상관 (b) 점수 분포 (c) judge false negative/positive 빈도 측정
  - 진입 옵션 (우선순위 순):
    1. **LLM judge (Gemini Flash) 옵션 추가** — paid ~$0.005/eval, 의미 평가 정확. ENV 또는 UI 토글
    2. **query expansion 자동차 도메인 사전** — 무료, 휠↔림·인치·알로이 30 entry
    3. **Reranker (BGE-reranker-v2-m3)** — master plan §6 S3 D4
    4. **doc-level RRF 가산** — `JETRAG_DOC_EMBEDDING_RRF=true` ENV 1줄
    5. **paid query decomposition (gated)** — S3 D3
    6. **표/그림 caption 추출** — S4 P1
  - 메모리: `~/.claude/projects/.../memory/project_ragas_score_beta.md`

---

## 11. 다른 컴퓨터 진입 마감 핸드오프 (2026-05-07 D3 종료 시점)

### 11.1 오늘 마감 상태 — 한눈에

| 항목 | 값 |
|---|---|
| **오늘 commit 누적** | **20 commits** (`a5cfc2a`→`74770da` origin/main push 완료) |
| **단위 테스트 baseline** | **528 통과 / skipped 0 / 회귀 0** (시작 시 460) |
| **web tsc + lint** | 0 error |
| **assets/ 회귀 cover** | **14/14** (12 정상 + 2 거부 동작) |
| **Master plan §6 S0 진척률** | ~50% (D1·D2 부분·D3 sweep 곱셈·D4 자동 POST 제거 ship) |
| **Master plan §6 S1 진척률** | **100%** (D1~D5 모두 ship) |
| **신규 sprint** | E1 (인제스트 ETA + latency, 1차 일부 ship) / E2 (테스트 fixture 인프라, 1~5차 cover 완료) |

### 11.2 진입 시 첫 5분 (다른 컴퓨터)

```bash
# 1) 동기화 (assets/public/ 8건 ~11MB 자동 다운로드)
git pull origin main

# 2) 의존성
cd api && uv sync
cd ../web && pnpm install

# 3) 회귀 baseline 확인
cd ../api && uv run python -m unittest discover tests
# 기대: Ran 528 tests, OK (skipped 0~3 — 다른 컴퓨터 시나리오 따라 가변)

cd ../web && pnpm exec tsc --noEmit && pnpm lint
# 기대: 0 error

# 4) 환경 변수 확인 (api/.env)
# 필수: SUPABASE_URL/KEY/SERVICE_ROLE_KEY, GEMINI_API_KEY, HF_API_TOKEN
# 권장: JETRAG_PDF_VISION_ENRICH=true, JETRAG_GEMINI_RETRY=3
# 본 컴퓨터에 .env 가 있다면 그대로 사용. 자세한 매트릭스: 2026-05-06 환경 설정 정리 핸드오프
```

### 11.3 본 문서 + 짝 문서 읽기 우선순위

| # | 문서 | 5분 행동 |
|---|---|---|
| 1 | **본 문서 §0 / §11** | 마감 상태 + 진입 절차 |
| 2 | [`2026-05-07 E1 인제스트 ETA latency sprint plan.md`](./2026-05-07%20E1%20%EC%9D%B8%EC%A0%9C%EC%8A%A4%ED%8A%B8%20ETA%20latency%20sprint%20plan.md) | E1 진단 SQL 5건 + reingest 절차 (PDF 보유 시) |
| 3 | [`2026-05-07 S1 D5 골든셋 v1 baseline 정량 측정.md`](./2026-05-07%20S1%20D5%20%EA%B3%A8%EB%93%A0%EC%85%8B%20v1%20baseline%20%EC%A0%95%EB%9F%89%20%EC%B8%A1%EC%A0%95.md) | 모델 회귀 1차 측정 결과 + S1.1 권고 |
| 4 | [`2026-05-06 무료유료 모델 전략 통합 plan + 다른 컴퓨터 핸드오프.md`](./2026-05-06%20%EB%AC%B4%EB%A3%8C%EC%9C%A0%EB%A3%8C%20%EB%AA%A8%EB%8D%B8%20%EC%A0%84%EB%9E%B5%20%ED%86%B5%ED%95%A9%20plan%20+%20%EB%8B%A4%EB%A5%B8%20%EC%BB%B4%ED%93%A8%ED%84%B0%20%ED%95%B8%EB%93%9C%EC%98%A4%ED%94%84.md) | Sprint S0~S5 master plan |

### 11.4 다음 진입 후보 (우선순위)

| # | 후보 | 작업량 | 의존성 / 진입 신호 |
|---|---|---|---|
| 1 | **E1 진단 + 1차 ship 잔여 (A2/A3/A4/A5)** | 1.5~3일 | PDF 보유 다른 컴퓨터 reingest (E1 plan §3 SQL S1~S5) → §10 채움 → senior-developer 의뢰. **사용자 ETA 보고 직접 해소** (가치 ↑↑) |
| 2 | **S1.1 (가칭) — reliable 100+ sample 모델 회귀 재측정** | 0.5일 | KST 02~05시 503 회복 시간대 재실행 + RAGAS judge timeout 600s 늘림 + sample 100+ (~$0.5~1.0). Δ 신뢰도 확보 후 lite 채택/hybrid/flash 유지 결정 |
| 3 | **S0 D3~D5 — budget 초기값 / cost cap / 24h 대시보드** | 1.5일 | 1주 vision_usage_log 누적 데이터 의존. 진입 신호: ≥2026-05-14 |
| 4 | **S2 — Vision 선별 + budget 본 ship** | 1주 | S0 D3~D5 ship 후 진입 (budget 의존). master plan §6 S2 |
| 5 | **RAGAS 점수 개선 (베타 테스트 후 판단)** | 0.5~1일 | 베타 사용자 5~10명 누적 후 진입. 6 옵션 우선순위 (LLM judge / query expansion / Reranker / doc RRF / decomposition / caption). 메모리 `project_ragas_score_beta.md` |

### 11.5 사용자 결정 보류 항목

| # | 항목 | senior-planner 권고 default |
|---|---|---|
| Q-S0 | budget 초기값 | 1주 데이터 누적 후 자동 산정 |
| Q-S1 | 2.5-flash vs lite | **S1.1 재측정 후 결정** (오늘 1차 측정은 n=12 신뢰도 낮음) |
| Q-S2 | 운영 모드 default | 기본 (5~10p) |
| Q-S3 | cheap router confidence < 0.75 시 | 자동 (사용자 부담 0) |
| Q-S5 | extractive summary 모델 | 무료 BGE-M3 cosine |
| E1-S1 | 정확도 vs latency 우선순위 | (a) 정확도 P0 |
| E1-S5 | 첫 인제스트 ETA 미노출 + 카피 | (b) "처음에는 시간 추정이 부정확합니다" |

### 11.6 활성 한계 (다른 컴퓨터에서 점검 필수)

| # | 한계 | 회복 절차 |
|---|---|---|
| 1 | E1 진단 미완 | 다른 컴퓨터 PDF 1건 reingest → E1 plan §10 채움 |
| 2 | 503 fail rate (실측 vs Gemini paid Tier 1) | E1 진단 S3 (vision_usage_log retry_attempt 분포) 동시 측정 |
| 3 | vision_page_cache lookup 미통합 (D2-B = E1-A3) | E1 2차 ship 시 해소 |
| 4 | S1 D5 모델 회귀 신뢰도 (n=12) | S1.1 100+ sample 재측정 |
| 5 | RAGAS Context Precision 47점 사례 | 베타 사용자 데이터 누적 후 6 옵션 진입 |

### 11.7 한 문장 요약 (마감 시점)

> **2026-05-07 D3 마감** — 20 commit / 단위 테스트 460→528 / S1 100% / E1 1차 일부 / E2 5차 cover (assets 14/14) / 사용자 보고 fix 4건 (drop-zone 이중 열림 / destructive 가독성 / progress bar 1칸 / ETA 정적). 다른 컴퓨터 진입은 본 §11 절차 5분이면 baseline 회복 — 다음 권고는 PDF 보유 시 **E1 진단 + A2~A5 ship**, 그 외 **S1.1 재측정** 또는 **S0 D3~D5 (1주 데이터 누적 후)**.
