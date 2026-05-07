# 2026-05-07 D3 진입 — E1 sprint 신설 + plan 작성

> 프로젝트: Jet-Rag
> 작성일: 2026-05-07 (계속 업데이트되는 마스터)
> 작성자: Claude (Explore + senior-planner 협업, senior-developer 미진입)
> 목적: 오늘(2026-05-07) Jet-Rag 작업의 종합 마스터. 추가 작업 발생 시 새 파일이 아니라 본 문서에 계속 업데이트.

---

## 0. 한 줄 요약

> **다른 컴퓨터 진입 baseline 회복 완료 (단위 테스트 460/0 OK, Supabase 마이그 3개 테이블·15컬럼 정합)** + **사용자 ETA 보고 (어제 PDF "3분" 표시 vs 실측 6~7분) 를 신규 sprint E1 으로 격상** — Explore 로 코드 정독 → senior-planner 로 plan 작성 → 별도 파일 ship. 문제 PDF 가 다른 컴퓨터에 있어 진단·구현은 다른 컴퓨터에서 진입. 본 컴퓨터에선 plan + SQL 준비까지 ship.

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

### 4.2 S1 — 골든셋 v1 + 실 query 로그 (목표 1주, 약 **15% 진척**)

| Day | 작업 | 상태 | 근거 |
|---|---|---|---|
| D1 | 사용자 draft 골든셋 33 entry | ✅ | commit `0cdcea4` — `evals/golden_v0.6_user.csv` |
| D1 | `auto_goldenset.py` v2 갱신 | ✅ **2026-05-07 ship** | 12 컬럼 schema + 9 query_type + must_include/source_hint + negative 5건 + 30 단위 테스트 |
| D2 | 자동 100+ 확장 (`golden_v0.7_auto.csv`) | ❌ | 파일 없음 |
| D2 | 통합 `golden_v1.csv` | ❌ | 파일 없음 |
| D3 | 실 query 로그 대시보드 | ❌ | `web/src/app/admin/queries/` 미존재 추정 |
| D4 | answer_feedback 통합 분석 | ❌ | 미진입 |
| D5 | 골든셋 v1 baseline 정량 측정 | ❌ | 미진입 |

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

### 4.5 다음 작업 우선순위 (오늘 결정 + 향후)

**A. 즉시 가능 (현재 컴퓨터, PDF 불필요)**

| 순위 | 작업 | 작업량 | 비고 |
|---|---|---|---|
| 1 | ~~S0 D4 — `/search/eval-precision` 자동 POST 제거~~ | ✅ **ship** | 2026-05-07 완료 (commit 미진입 — 사용자 명시 요청 대기) |
| 2 | ~~S1 D1 잔여 — `auto_goldenset.py` v2 갱신~~ | ✅ **ship** | 2026-05-07 완료 — S1 D2 진입 가능 |
| 3 | ~~E2 1차 ship — 테스트 fixture 인프라 보강~~ | ✅ **ship** | 2026-05-07 완료 — 공개 3건 git 추적, skip 7→5 |
| 4 | E2 follow-up — 기관 규정·법률 샘플 라이센스 검토 | 0.5일 | `직제_규정.hwpx`·`한마음생활체육관_운영_내규.hwpx`·`law_sample` 시리즈 출처 / 공개 가능성 사용자 확인 후 추가 이동 |
| 5 | S1 D2 — 자동 골든셋 100+ 확장 + v1 통합 | 1일 | Gemini quota 의존 |
| 6 | E1 1차 ship 일부 (E1-A1 + E1-A5) | 1일 | 진단 없이도 진입 가능 (덜 정확) |

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

### 4.1 즉시 (E1 진단)

1. `git pull` 로 본 work-log + plan 파일 동기화
2. 어제 6~7분 걸린 PDF 1건 재업로드
3. **시작 직전** 화면 ETA + **시작·종료 시각** wall-clock 메모
4. T+1m / T+3m / T+5m 화면 ETA 캡처 (S5)
5. 종료 후 plan 파일 §3 의 SQL S1~S4 paste → plan 파일 §10 에 결과 기록
6. plan 파일 §10.4 의 1차 ship 진입 결정 체크리스트 진행

### 4.2 사용자 결정 필요 (1차 ship 진입 전)

| # | 항목 | senior-planner 권고 default |
|---|---|---|
| 1 | 정확도 vs latency 우선순위 | (a) 정확도 P0 |
| 5 | 첫 인제스트 ETA 미노출 + 카피 | (b) "처음에는 시간 추정이 부정확합니다" |

(2·3·4번은 ship 중 default 로 진행 후 review 시 조정)

### 4.3 후속 sprint (E1 후)

E1 1차 ship 후 권고 순서:
- E1 2차 ship (D2-B 흡수, latency 본진입)
- S1 D5 (2.5-flash vs 2.5-flash-lite 골든셋 회귀)
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
| 2 | [`2026-05-07 E1 인제스트 ETA latency sprint plan.md`](./2026-05-07%20E1%20%EC%9D%B8%EC%A0%9C%EC%8A%A4%ED%8A%B8%20ETA%20latency%20sprint%20plan.md) | E1 plan 본문 + 진단 SQL + §10 reingest 결과 칸 |
| 3 | [`2026-05-06 D1~D2 누적 + 다른 컴퓨터 종합 진입 핸드오프.md`](./2026-05-06%20D1~D2%20%EB%88%84%EC%A0%81%20+%20%EB%8B%A4%EB%A5%B8%20%EC%BB%B4%ED%93%A8%ED%84%B0%20%EC%A2%85%ED%95%A9%20%EC%A7%84%EC%9E%85%20%ED%95%B8%EB%93%9C%EC%98%A4%ED%94%84.md) | 어제까지 baseline (D1~D2 ship 누적) |
| 4 | [`2026-05-06 무료유료 모델 전략 통합 plan + 다른 컴퓨터 핸드오프.md`](./2026-05-06%20%EB%AC%B4%EB%A3%8C%EC%9C%A0%EB%A3%8C%20%EB%AA%A8%EB%8D%B8%20%EC%A0%84%EB%9E%B5%20%ED%86%B5%ED%95%A9%20plan%20+%20%EB%8B%A4%EB%A5%B8%20%EC%BB%B4%ED%93%A8%ED%84%B0%20%ED%95%B8%EB%93%9C%EC%98%A4%ED%94%84.md) | Sprint S0~S5 master plan |
| 5 | [`2026-04-22 개인 지식 에이전트 기획서 v0.1.md`](./2026-04-22%20%EA%B0%9C%EC%9D%B8%20%EC%A7%80%EC%8B%9D%20%EC%97%90%EC%9D%B4%EC%A0%84%ED%8A%B8%20%EA%B8%B0%ED%9A%8D%EC%84%9C%20v0.1.md) | 페르소나·KPI·DoD 마스터 — §13 KPI 에 "ETA ratio" 신규 추가 권고 |

---

## 9. 한 문장 요약

> 2026-05-07 D3 진입 — baseline 회복 + ETA 보고를 E1 sprint 로 격상해 plan 본문 ship + S0 D4 자동 POST 제거 + S1 D1 잔여 (`auto_goldenset.py` v2) + **E2 1차 ship (assets/public/ 신설 + 공개 PDF 3건 git 추적, skip 7→5)**. E1 진단·구현은 PDF 보유 다른 컴퓨터에서 진입, 본 컴퓨터에선 plan + 측정 SQL 준비까지.

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
# 사용자 PC 기대: Ran 494 tests in ~14s, OK  (PDF 4 + HWPX 2 + HWP 1 + private sonata 모두 자동 진입, skipped=0)
# 다른 컴퓨터·CI 기대: Ran 494 tests in ~14s, OK (skipped 1~3)  (assets/ 직속 부재 → private sonata + alpha 자동 skip)
```

`git pull` 한 시점에 본 컴퓨터의 모든 ship 이 동시에 들어옴:

| 항목 | 자동 동기화 | 비고 |
|---|---|---|
| work-log / plan / 종합 마스터 | ✅ | 본 문서, E1 plan, master plan 등 |
| 코드 변경 | ✅ | `search-precision-card.tsx`, `auto_goldenset.py`, `test_pymupdf_heading.py`, `test_hwpx_heading.py`, `test_hwp_heading.py` (신규), `.gitignore` 등 |
| **`assets/public/` 공개 자료 8건 (약 11 MB)** | ✅ **별도 scp/cloud 불필요** | PDF 4건 (KOGL 2 + 사용자 명시 1 + 저작권법 §7 2건) + HWPX 2건 (KOGL 1유형 추정) + HWP 1건 (저작권법 §7) |
| **`assets/` 직속 자료** (private) | ❌ git ignore | 사용자 PC 에서는 `_pdf_path()` 등이 자동 회귀 진입 / 다른 컴퓨터 부재 시 자동 skip |
| **`<repo>/` 루트 직속 자료** (다른 컴퓨터 패턴) | ❌ git ignore (`/*.pdf` 등) | 다른 컴퓨터에서 자료를 repo 루트에 두면 3순위 자동 인식 — ENV 0 줄 |
| 단위 테스트 회귀 | ✅ 자동 | 5단계 우선순위 (§10.2) 로 자동 해석 |

### 10.2 다른 컴퓨터 진입 시 자료 위치별 시나리오

`_pdf_path()` / `_hwpx_path()` / `_hwp_path()` 의 **5단계 우선순위** 로 컴퓨터별 자료 위치를 자동 인식한다. ENV 변수 매뉴얼 0 줄이 default.

**5단계 우선순위 표**

| # | 위치 | 정합 정책 | 자동 인식 |
|---|---|---|---|
| 1 | `<repo>/assets/public/<name>` | git 추적 (모든 컴퓨터·CI) | ✅ |
| 2 | `<repo>/assets/<name>` | `.gitignore` `/assets/*` (사용자 PC raw) | ✅ |
| 3 | `<repo>/<name>` (repo 루트 직속) | `.gitignore` `/*.{pdf,hwp,hwpx,docx,pptx}` (다른 컴퓨터 패턴) | ✅ |
| 4 | `$JETRAG_TEST_*_DIR/<name>` | ENV 폴백 (외장 디스크) | ENV 1줄 |
| 5 | 부재 → skipTest | CI 호환 | — |

**시나리오별 동작**

```text
시나리오 1: 자료가 <repo>/assets/ 직속에 있는 컴퓨터 (사용자 PC 패턴)
  → 2순위 자동 진입, ENV 0
  → 단위 테스트 494 통과 / skipped 0

시나리오 2: 자료가 <repo>/ 루트 직속에 있는 컴퓨터 (다른 컴퓨터 패턴)
  → 3순위 자동 진입, ENV 0
  → 단위 테스트 494 통과 / skipped 0
  → E2 3차 ship 으로 신규 지원

시나리오 3: 자료가 외장 디스크 / 별 위치 (예: /Volumes/External/...)
  → 4순위 ENV 폴백, 1줄 설정 필요
     export JETRAG_TEST_PDF_DIR=/Volumes/External/jetrag-data
     export JETRAG_TEST_HWPX_DIR=/Volumes/External/jetrag-data
     export JETRAG_TEST_HWP_DIR=/Volumes/External/jetrag-data
  → 단위 테스트 494 통과 / skipped 0

시나리오 4: 자료가 없는 컴퓨터 (CI 포함)
  → public 자료 (PDF 4 + HWPX 2 + HWP 1 + alpha) 만 회귀
  → private 자료 자동 skip → 회귀 차단 0
  → 단위 테스트 494 통과 / skipped 1~3
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
  - DOCX/PPTX 형식의 fixture 테스트 (현재 자료 0) — 자료 출처 미상이므로 신규 공개 자료 발굴 필요
  - HWP 의 OLE2 아닌 자료 (`cosmetic_law_sample.hwp`, `law sample2.hwp`) 처리 가이드 — Hwp5Parser 가 인제스트 단계에서 거부하므로 fixture 자체 부적합 (private 유지). 향후 hwp_parser.py 가 OLE2 아닌 컨테이너도 지원하도록 확장 시 fixture 추가 가능
  - ~~기관 규정 2건 / 법률 샘플 5건 라이센스 검토~~ ✅ **2026-05-07 E2 3차 ship** — 5건 마이그, 남은 비공개 자료는 출처 미상 (`브랜딩_스튜디오앤드오어.pptx`, `승인글 템플릿`) 또는 라이센스 부적합 (`sonata-the-edge_catalog.pdf` 현대차 마케팅)
  - ~~`test_hwpx_heading.py` 의 하드코딩 경로 마이그~~ ✅ **2026-05-07 ship** (E2 2차)
  - ~~`test_hwp_heading.py` 신규 작성~~ ✅ **2026-05-07 E2 3차 ship**
- **S1 D2** (자동 골든셋 100+ 확장) — Gemini quota 1회 (~$0.05) 소진. `auto_goldenset.py` v2 로 `--chunks-per-doc 10` 실행
