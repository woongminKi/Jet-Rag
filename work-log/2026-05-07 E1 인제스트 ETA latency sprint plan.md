# 2026-05-07 E1 인제스트 ETA latency sprint plan

> 프로젝트: Jet-Rag
> 작성일: 2026-05-07
> 작성자: Claude (senior-planner + Explore + senior-developer 협업 — 본 문서는 plan 단계)
> 목적: 다른 컴퓨터(문제 PDF 보유)에서 본 문서 1개만 보고 (1) E1 sprint 가 왜 신설됐는지 (2) 어떤 진단을 먼저 돌릴지 (3) 진단 후 어떤 개선을 어떤 순서로 ship 할지 5분 안 파악
>
> **이 문서는 사용자 명시 요청 항목** — 어제(2026-05-05) PDF 업로드 시 ETA "3분" 표시였으나 실측 6~7분 소요. 다른 컴퓨터 진입 작업 문서에 E1 항목을 항상 포함하라는 지시.

---

## 0. 한 줄 요약

> **신규 sprint E1 — 인제스트 ETA 정확도(0.7~1.3) + 50p PDF latency p50 ≤ 3분**. 진단 SQL 5건 (S1~S5) 을 reingest 직후 즉시 실행 → 가설 확정 → 1차 ship (E1-A1 ETA 공식 분해 + E1-A5 fallback 정정 + E1-A4 TTL 단축, 1.5~2일) → 2차 ship (E1-A3 vision_page_cache lookup + E1-A2 페이지 동시 호출, D2-B 흡수, 2~3일). 본 컴퓨터에선 plan + SQL 만 준비, 측정·구현은 다른 컴퓨터에서 PDF 재업로드 후 진입.

---

## 1. 배경 + 사용자 보고

- **2026-05-05 사용자 PDF 업로드** 시 화면에 "남은 시간 3분" 표시였으나 실측 6~7분 소요. ETA / 실측 ratio ≈ 0.4 — 약속한 시간의 2배 이상 어긋남
- latency 자체도 50p PDF 6~7분이면 일상 사용 부담. 페르소나 A (개인 기억 보조) 의 즉시성 가치 손상
- **문제 PDF 는 다른 컴퓨터에 있음** — 본 컴퓨터에선 측정 불가. 이 문서를 다른 컴퓨터에서 열어 §3 의 진단 SQL 을 실행해야 데이터 확보
- 메모리 (`project_e1_eta_latency.md`) 에 영구 저장 — 본 항목은 다른 컴퓨터 진입 작업 문서마다 누락 없이 포함

---

## 2. Explore 가 정리한 코드 위치 + 가설

### 2.1 ETA 계산 (백엔드 측, web 아님)

| 위치 | 역할 | 핵심 |
|---|---|---|
| `api/app/ingest/eta.py:133-162` `compute_remaining_ms()` | 메인 공식 | `ingest_logs` 최근 500건 stage 별 median 5분 TTL 캐시 → 현재 stage 부터 합산 (단순 선형 외삽) |
| `api/app/ingest/eta.py:105-123` `_get_stage_medians_ms()` | 캐시 갱신 | 5분 TTL — 503 wave 직후 갱신 지연 위험 |
| `api/app/ingest/eta.py` `_FALLBACK_STAGE_MS` | cold start | extract = 5000ms 고정 → 50p PDF 실측 분단위와 큰 괴리 |
| `web/src/lib/hooks/use-docs-batch-polling.ts:6` | 폴링 | 1.5s 주기, 5분 timeout |
| `web/src/lib/format.ts:61-71` `formatRemainingMs()` | 표시 | ms ≤ 0 → null 반환 → 라벨 미노출. 오버런 케이스 미처리 |
| `web/src/components/jet-rag/stage-progress.tsx:29-31` | 라벨 분기 | done/failed 가 아닐 때만 ETA 표시 |

### 2.2 인제스트 파이프라인 (모두 직렬)

| 위치 | 동작 |
|---|---|
| `api/app/ingest/pipeline.py:3-4` | 9단계 직렬 — extract → chunk → chunk_filter → content_gate → tag_summarize → load → embed → doc_embed → dedup. **비동기/concurrent 0** |
| `api/app/ingest/stages/extract.py:379-442` | vision_enrich sweep × per-page 순차. 동시 호출 0 |
| `api/app/adapters/impl/_gemini_common.py:29,45-71` | 503 시 지수 백오프 (delay = 1.0 × 2^(n-1) + jitter, default retry 1회) |
| `api/migrations/001_init.sql:76-107` | `ingest_logs` 에 stage / status / duration_ms / started_at / finished_at 존재. **vision 페이지별 breakdown 없음** |

### 2.3 부정확 3대 원인 가설

1. **vision 페이지 수 변동성 미반영** — extract median 에 50p PDF 같은 outlier 의 vision 시간이 가중 안 됨
2. **cold start fallback 부족** — `ingest_logs` 적을 때 extract = 5000ms 고정 → 분단위 실측과 괴리
3. **5분 TTL 캐시 진부화** — 503 wave 직후 median 갱신이 늦어 ETA 가 낮은 옛 baseline 으로 표시

### 2.4 latency 자체 가설

- vision_enrich 페이지 순차 호출 (concurrency=0) → N 페이지 × per-call latency 직선 누적
- 503 retry 의 지수 백오프 가 sweep 간 누적
- vision_page_cache lookup 미통합 (D2-B 미진입) → reingest 시 매 페이지 재호출

---

## 3. 진단 단계 (다른 컴퓨터에서 PDF 1회 reingest 직후 즉시 실행)

진단 대상은 어제 6~7분 걸린 50p PDF 1건. **reingest 시작 직전 ETA 표시값 + reingest 시작·종료 wall-clock 기록부터 시작**. 그 다음 아래 SQL 5건을 Supabase Studio → SQL Editor 에서 paste→Run.

### S1 — 해당 reingest job 의 stage 별 duration

```sql
SELECT stage, status, duration_ms, started_at, finished_at
FROM ingest_logs
WHERE job_id = :job_id
ORDER BY started_at;
```

해석 기준:
- `extract.duration_ms > 60_000` (60초) → vision 직렬 호출 latency 누적 의심 → E1-A2 (페이지 동시 호출) 우선
- extract 짧고 embed/load 가 느리면 E1-A2 효과 제한 → 다른 stage 손봄

### S2 — 최근 500건 stage median (eta.py 가 실제 사용하는 값)

```sql
SELECT stage,
       COUNT(*) AS n,
       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY duration_ms) AS p50_ms,
       PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95_ms
FROM (
  SELECT stage, duration_ms FROM ingest_logs
  WHERE status = 'succeeded'
  ORDER BY id DESC
  LIMIT 500
) t
GROUP BY stage;
```

해석 기준:
- extract p50 vs 50p reingest extract.duration_ms 비율이 ETA 오차 절반 설명
- `p95_ms / p50_ms > 5` → sample 분산 폭주 → E1-A1 (sub-stage 분해) 강한 근거

### S3 — vision_usage_log retry_attempt 분포 (P2 활성화 후)

```sql
SELECT retry_attempt, COUNT(*) AS n,
       AVG(EXTRACT(EPOCH FROM (called_at - LAG(called_at) OVER (PARTITION BY doc_id, page ORDER BY called_at)))) * 1000 AS avg_gap_ms
FROM vision_usage_log
WHERE doc_id = :doc_id
GROUP BY retry_attempt
ORDER BY retry_attempt;
```

해석 기준:
- `retry_attempt > 1` row 가 페이지 수의 5% 초과 → 503 wave 누적 → E1-A6 (백오프 cap) 가치 상승
- avg_gap > 4000ms → vision API 자체 latency 큼 → E1-A2 효과 측정 가능

### S4 — vision_page_cache hit rate (D2-B 통합 전 baseline)

```sql
SELECT
  COUNT(DISTINCT (doc_id, page)) AS unique_pages,
  COUNT(*) AS total_calls
FROM vision_usage_log
WHERE doc_id = :doc_id;
```

해석 기준:
- `total_calls > unique_pages` → 동일 페이지 중복 호출 (sweep 재시도 또는 reingest 누적). cache 통합 시 hit 가능 영역 정확화

### S5 — ETA 표시 vs 실측 (수동, SQL 아님)

```text
- reingest 시작 직후 (T+0) 화면 ETA: "약 N분 남음"
- T+1m, T+3m, T+5m 시점 화면 ETA 캡처 3장
- 실제 finish wall-clock 시각
- ETA/실측 비율 = 표시값 / (finish - start)
```

### 진단 통과 기준

- S1+S2 완료 → extract median 과 실 measurement ratio 산출
- extract stage 가 ETA 오차의 80% 이상 설명 → §4 1차 ship 진입
- embed/load 가 동일 비중으로 어긋나면 추가 측정 1라운드 (job 2건 더) 필요

---

## 4. 개선 후보 (E1-A1 ~ A7)

| ID | 변경 위치 | 효과 | 위험 | 작업량 | 권고 |
|---|---|---|---|---|---|
| **E1-A1** | `eta.py` STAGE_ORDER + FALLBACK_STAGE_MS — extract 를 `extract_text` + `extract_vision` (페이지 수 가중) sub-stage 분해 (in-memory, schema 무변경) | ETA ratio 0.7~1.3 진입 | vision_usage_log join 1쿼리 추가 (5분 cache 흡수) | M | **1순위 — 정확도 직격** |
| **E1-A2** | `extract.py:378-442` page loop → ThreadPoolExecutor + `JETRAG_VISION_CONCURRENCY` (default 3, max 5) | 50p × 3s 직렬 150s → concurrency 3 시 약 50s | Gemini RPM/RPD 한도, sweep burst | M~L | **2순위 — latency 직격, quota 검증 필수** |
| **E1-A3** | `extract.py` page 호출 직전 cache lookup (마이그 015 `vision_page_cache`). key = (sha256(png_bytes), page, model_version). upsert ON CONFLICT DO NOTHING | reingest 시 vision 호출 0 | cache key 가 doc_id 만이면 다른 사용자 동일 PDF 미공유 | M | **3순위 — D2-B 본진입 흡수** |
| **E1-A4** | `eta.py` `_CACHE_TTL_SECONDS` 5분 → 90초 (또는 ingest_logs insert hook) | 503 wave 직후 baseline 갱신 빠름 | DB read 1쿼리 / 90초 (무시) | S | **5순위 — 작지만 안전, 1차 ship 옵션** |
| **E1-A5** | `eta.py` extract fallback 5000 → 진단 baseline (예: 50p 기준 120000) 상향 + stage sample < 3 시 ETA None 반환 | 첫 사용자 misleading ETA 제거 | UX 후퇴로 보일 수 있음 → "처음에는 시간 추정이 부정확합니다" 카피 권고 | S | **4순위 — 1차 ship 같이** |
| **E1-A6** | `_gemini_common.py:60` `delay = min(..., 30.0)` cap | retry 회복 시 worst case 누적 줄임 | 거의 없음 | S | **6순위 — 진단 S3 결과로 가변** |
| **E1-A7** | FastAPI SSE endpoint + `use-docs-batch-polling.ts` 교체 | 폴링 1.5s → 즉시 갱신 (체감만) | SSE 인프라 신규, MVP §9.2 스택 변경 위험 | L | **deferred — E1 범위 밖, 별도 sprint 후보** |

---

## 5. 권고 ship 순서

### 5.1 1차 ship — E1-Diag + 정확도 핵심 (1.5~2일)

1. §3 진단 데이터 수집 (50p PDF 1회 reingest)
2. **E1-A1** (ETA 공식 분해) + **E1-A5** (fallback 정정 + sample 부족 시 미노출) + **E1-A4** (TTL 단축)
3. DoD: 50p PDF 2회 추가 reingest 로 표시/실측 ratio 측정 → 0.7~1.3 진입 확인

### 5.2 2차 ship — latency 본진입 (D2-B 흡수, 2~3일)

1. **E1-A3** (vision_page_cache lookup 통합) + **E1-A2** (페이지 동시 호출 concurrency=3)
2. D2-B 라벨도 동시 close — work-log 에 양쪽 명시
3. DoD: 동일 50p PDF p50 ≤ 3분, reingest cache hit > 90%

### 5.3 3차 ship — 옵션·deferred

- **E1-A6** (백오프 cap) — 진단 S3 가 retry_attempt > 1 비율 큰 경우만
- **E1-A7** (SSE) — master plan §6 별도 sprint 후보로 등록만

---

## 6. 회귀 위험 + 사이드이펙트

- **동시 호출 quota** (E1-A2): Gemini 무료 티어 RPM 한도. semaphore default 3 + sweep 진입 전 sleep 1s 보호. 진단 S3 의 retry_attempt 분포가 1차 ship 직후 회귀 모니터링 핵심 KPI
- **ETA 공식 변경 초기 사용자 영향** (E1-A1, A5): ingest_logs 비어있을 때 ETA None → "처음에는 시간 추정이 부정확합니다" 한 줄 안내 권고. 기획서 §11.5 포지셔닝 정합 (과한 약속 회피)
- **ingest_logs schema 영향**: E1-A1 in-memory 분해 → schema 무변경. vision_usage_log join 1쿼리 추가는 5분 cache 안에서 흡수
- **vision_page_cache write race** (E1-A3): 동시 페이지 호출 + cache write 충돌 → `INSERT ... ON CONFLICT DO NOTHING`
- **ThreadPool/asyncio** : FastAPI BackgroundTasks 는 sync 함수 OK. extract.py 동시 호출은 ThreadPoolExecutor 권고 (asyncio 변환은 pipeline.py 전체 chain 영향 → 범위 폭발)
- **TTL 단축** (E1-A4) DB read: 5분→90초 = 1쿼리/90초, 무시 가능
- **format.ts null 처리**: 이미 null → 라벨 미노출 → 변경 없음. 단 ETA 오버런 (실측 > 표시) UX 는 별도 결정 필요 (§8)

---

## 7. master plan 정합성 점검

- **S2 (vision_need_score) 와 직교**: S2 는 "어느 페이지를 vision 할지" (scope 축소), E1 은 "vision 한 페이지를 얼마나 빨리·정확히" (throughput + ETA). 같이 ship 시 conflict 적음
- **D2-B 본진입**: E1-A3 가 D2-B 와 동일 → sprint 라벨 **"E1" 단일** 로 D2-B 흡수, work-log 본문에 D2-B close 명시 (권고)
- **D2-C (retry 3→1) 영향**: 적용됨 — E1-A6 권고 6순위로 떨어짐
- **S5 (답변 UX) 와 무관**: search 흐름 영향 0
- **§13 KPI 매핑**: E1 은 KPI "P95 응답" 의 인제스트 측 변형. 신규 KPI **"ETA 표시/실측 ratio"** 추가 권고 → 기획서 §13.1 추가 항목

---

## 8. 결정 보류 / 사용자 확인 필요 항목

다음은 1차 ship 진입 전 또는 ship 중 사용자 결정 필요:

| # | 항목 | 옵션 | senior-planner 권고 (default) |
|---|---|---|---|
| 1 | 정확도 vs latency 우선순위 | (a) 정확도 P0 / (b) latency P0 | (a) 정확도 P0 — latency 변동성 줄어든 후 측정 가능 |
| 2 | ETA 오버런 UX (실측 > 표시 200%) | (a) 0 후 자연 미노출 / (b) "예상보다 오래 걸리고 있어요" 폴백 / (c) "약 3~7분 남음" 범위 | **(b)** — 페르소나 A 정합, 약속 회피 + 안심 |
| 3 | sprint 라벨링 | (a) E1 단일로 D2-B 흡수 / (b) D2-E 신규 + E1 분리 / (c) E1 + D2-B 동시 진행 별도 ship | **(a)** — 추적성 |
| 4 | vision concurrency default | 1 / 3 / 5 | **3** — 진단 S3 결과 후 조정 가능 |
| 5 | 첫 인제스트 ETA 미노출 + 카피 | (a) 표시 / (b) "처음에는 시간 추정이 부정확합니다" 안내 | **(b)** |

**1·5번은 1차 ship 진입 전 명시 답변 필요.** 2·3·4번은 default 로 진행하고 review 시 조정 가능.

---

## 9. 다른 컴퓨터에서 본 plan 진입 절차 (5분 안)

### 9.1 사전 확인

```bash
git pull
cd api && uv sync
# 단위 테스트 baseline (현재 460 OK, skipped 7)
uv run python -m unittest discover tests
```

### 9.2 Supabase 마이그 정합성

`2026-05-06 D1~D2 누적 핸드오프.md` §2.1 의 검증 SQL 으로 6개 테이블/15컬럼 확인. 누락 시 마이그 005~008/014/015 적용.

### 9.3 환경 변수 (이미 추가 완료 상태 가정)

```bash
JETRAG_PDF_VISION_ENRICH=true
JETRAG_GEMINI_RETRY=3   # 503 회복용 (D2-C 후 회복)
```

### 9.4 reingest + 진단

1. 어제 6~7분 걸린 PDF 1건 재업로드
2. **시작 직전** 화면 ETA 표시값 메모 (예: "3분")
3. **시작 시각·종료 시각** wall-clock 메모
4. T+1m, T+3m, T+5m 화면 ETA 캡처 (S5)
5. 종료 후 본 문서 §3 의 SQL S1~S4 를 Supabase Studio 에서 paste→Run
6. 결과를 본 문서 끝 §10 (진단 결과) 섹션에 추가 — 실측·SQL 결과 그대로

### 9.5 진단 후 진입

§3 통과 기준 충족 → senior-developer 에 1차 ship (E1-A1+A5+A4) 의뢰.

```text
컨텍스트:
- work-log/2026-05-07 E1 인제스트 ETA latency sprint plan.md §10 의 진단 결과
- 1차 ship 범위: E1-A1 (ETA 공식 분해) + E1-A5 (fallback 정정 + sample 부족 시 미노출) + E1-A4 (TTL 단축)
- DoD: §1 의 ETA ratio 0.7~1.3 (50p PDF reingest 2회 측정 평균)
- 회귀 보호: 단위 테스트 460 통과 유지 + eta.py 신규 테스트 추가
```

---

## 10. 진단 결과 (2026-05-07 PDF 보유 컴퓨터에서 측정)

> 측정 PDF: `포트폴리오_이한주 - na Lu.pdf` (15p, 33MB) — plan §3 가정 "50p 6~7분"과 다르게 실제 15p 인데 **15분 58초** 소요. doc_id `fa24fabf-76b0-4a46-92eb-b6235c104796` / job_id `8654d260-5b6a-4425-97d2-9490c5d51db9`.
>
> 자동 측정: `/tmp/e1_measure.py` (POST /documents → batch-status 5s 폴링). 결과 raw: `/tmp/e1_measure_result.json`, `/tmp/e1_diag_result.json`.

### 10.1 wall-clock + ETA 표시

| 항목 | 값 |
|---|---|
| reingest 시작 시각 | 2026-05-07 10:35:55.007 UTC |
| reingest 종료 시각 | 2026-05-07 10:51:52.931 UTC |
| **실측 소요 (분)** | **15분 58초 (957.9s)** |
| T+0 화면 ETA | **224.5s (3분 44초)** ← 사용자 어제 보고 "3분"과 일치 |
| T+1m (60s) ETA | 213.1s (3분 33초) |
| T+3m (180s) ETA | 99.1s (1분 39초) |
| T+5m (300s) ETA | 87.7s (1분 27초) |
| T+10m (600s) ETA | 87.7s (1분 27초) ← page 12 stuck, ETA 거의 정지 |
| **표시/실측 ratio** | **0.23** (T+0 224.5 / 실측 957.9) — plan §1 가정 "0.4"보다 심각 |
| stage 전환 | T+905s tag_summarize → T+910 embed → T+925 doc_embed → T+930 completed |

### 10.2 SQL 결과

#### S1 — job stage duration (해당 job)

| stage | duration_ms | 점유율 |
|---|---:|---:|
| **extract** | **923143** | **96.4%** |
| tag_summarize | 5742 | 0.6% |
| embed | 17660 | 1.8% |
| chunk | 2 | 0.0% |
| chunk_filter | 6 | 0.0% |
| content_gate | 69 | 0.0% |
| load | 472 | 0.0% |
| doc_embed | 476 | 0.0% |
| dedup | 85 | 0.0% |
| **합** | 947655 | 100% |

→ **§3 통과 기준 충족** ("extract stage 가 ETA 오차의 80% 이상 설명" → 96.4%)

#### S2 — 최근 500건 stage 별 median

| stage | n | p50_ms | p95_ms | p95/p50 |
|---|---:|---:|---:|---:|
| **extract** | 16 | **244756** | 923143 | **3.77** |
| tag_summarize | 12 | 14681 | 28894 | 1.97 |
| embed | 17 | 34746 | 83788 | 2.41 |
| doc_embed | 16 | 590.5 | 1644 | 2.78 |
| load | 17 | 394 | 815 | 2.07 |
| content_gate | 16 | 72.5 | 107 | 1.48 |
| dedup | 16 | 53.0 | 95 | 1.79 |
| chunk_filter | 16 | 8.0 | 28 | 3.50 |
| chunk | 16 | 2.5 | 8 | 3.20 |

→ extract p50 **244756ms (4분 5초)**, p95 **923143ms (15분 23초)** — 본 reingest 가 p95 와 일치.  
→ ETA 공식이 p50 (244756ms ≈ 4분) 으로 추정해서 실측 16분 (≈ p95) 과 큰 괴리.  
→ p95/p50 = 3.77 (plan 기준 "5 초과" 까지는 아니지만 여전히 큰 분산. **vision-heavy PDF / 503 wave 구간 sample 이 적어 p95 underrepresented** 가능성)

#### S3 — vision_usage_log retry_attempt 분포 (본 doc)

| retry_attempt | n | 비율 |
|---|---:|---:|
| 0 | 1 | 5.9% |
| 1 | 12 | 70.6% |
| 2 | 1 | 5.9% |
| **3** | **3** | **17.6%** ← 503 fail 임계 |

→ vlog_total = 17 (15 unique pages + sweep 2 의 page 12, 14 = 17).  
→ **page 12 sweep 1: attempt 3 fail (503 UNAVAILABLE), sweep 2: attempt 1 success.**  
→ **page 14 sweep 1: attempt 3 fail (503), sweep 2: attempt 3 fail (503)** — 결국 vision section 없이 진행.  
→ **retry attempt ≥1 비율 94% (16/17)** — Gemini 503 wave 가 1회 시도로 거의 안 통과 시점. avg_gap 안정 측정 불가 (sweep boundary 가 LAG window 깸).

#### S4 — vision_page_cache hit baseline

| 항목 | 값 |
|---|---:|
| unique_pages_in_vlog | 15 |
| total_calls | 17 |
| **vision_page_cache rows for sha** | **0** ← D2-B (E1-A3) 미진입, 예상치 |

→ cache 통합 후 reingest 시 vision 호출 0 가능. 본 reingest 는 baseline (cache miss 100%).

### 10.3 가설 검증 결과

- **가설 1 (vision 페이지 수 미반영) — 확정.** extract median 244756ms 인데 본 reingest extract = 923143ms (3.77배). 페이지별 vision latency 변동성 + 503 retry 변동성을 medians 가 못 잡음.
- **가설 2 (cold start fallback 부족) — 부분 확정.** sample n=16 있어 fallback 5000 ms 안 쓰임 (medians 244756 사용). cold start 보다 **medians 자체의 분산 미반영** 이 핵심.
- **가설 3 (5분 TTL 진부화) — 확정.** 측정 16분 동안 ETA 224 → 213 → 99 → 88 → 88 (page 12 stuck 시 정지) → 53 → 38 → 0.6 으로 줄지만, 실측 시간과 매번 큰 괴리. **TTL 안에서 본 job 의 extract 진행이 medians 갱신 안 됨** — 본 job 의 extract 가 끝나야 (T+15:23) 반영. 그 전엔 옛 baseline 사용.
- **추가 가설 (G4) — sweep retry latency 미반영.** Gemini 503 wave + sweep 2/3 재시도 가 ETA 공식에 무시. plan §3 의 "extract.duration_ms > 60_000 → vision 직렬 latency 누적" 기준 → 본 job 923143 ms 로 압도적 초과.

### 10.4 1차 ship 진입 결정

- [x] §3 진단 통과 기준 충족 (extract = 오차의 96.4%, ≥80% 임계)
- [x] §8-1 (정확도 P0) — plan default 채택
- [x] §8-5 (sample <3 ETA 미노출 + "처음에는 시간 추정이 부정확합니다" 카피) — plan default 채택
- [x] senior-developer 1차 ship 의뢰 진행 (별도 §11 참조)

### 10.5 측정에서 얻은 추가 인사이트

1. **15p PDF가 16분 걸렸다** — plan §1 의 "50p 6~7분" 가정이 사용자 보고와 다름. 즉 어제 사용자 보고의 PDF 도 15p (사용자 메모리 재확인 필요). 또는 어제는 503 wave 가 약했고 오늘 더 심한 상태.
2. **page 12 sweep 1 fail → sweep 2 success** = sweep 재시도 전략은 효과 있음 (incremental.py 정상 동작).
3. **page 14 sweep 1, 2 모두 503 fail** → vision 정보 없이 chunk 진행. 검색 품질 영향 가능 (별도 회귀 검증 필요).
4. **extract 만 96.4% 점유** → E1 sprint 1차 ship 의 가치 명확. extract sub-stage 분해 + retry 누적 반영이 ETA 정확도 직격.
5. **stage_progress(12)에서 stuck 4분 이상** = sweep 1 retry 누적 동안 progress 미갱신. plan E1-A1 (sub-stage 분해) 가 sweep 카운터까지 노출하면 사용자 안심 효과 (1/3 sweep 진행 중 같은 표시).



---

## 11. 1차 ship 결과 (2026-05-07)

> 범위: E1-A1 (ETA 공식 분해) + E1-A4 (TTL 5분→90초) + E1-A5 (fallback 정정 + sample <3 None + web 카피)
> 진행 시간: 약 2시간 (단일 sprint 안에서 ship)

### 11.1 변경 파일

| 파일 | 변경 |
|---|---|
| `api/app/ingest/eta.py` | 전면 개편 — vision_usage_log p95 fetch 추가, sub-stage 분해 (extract + unit='pages'), TTL 90s, sample <3 → None, fallback 상향 (extract 5000→120000, embed 10000→35000, tag_summarize 3000→5000) |
| `api/tests/test_ingest_eta.py` | 신규 case 4건 + 기존 정책 변경 반영 (cold start → None) — 총 15 tests, 모두 OK. 진단 §10 ratio 시뮬 case 포함 (vision p95=50000 → ratio ≈ 1.0) |
| `web/src/components/jet-rag/stage-progress.tsx` | `estimatedRemainingMs == null` + 진행 중 → "처음에는 시간 추정이 부정확합니다" 카피 노출 (plan §8-5 default) |

### 11.2 검증 결과

```text
[api]   uv run python -m unittest discover tests
        Ran 531 tests in 16.549s — OK (skipped=1)
[api]   uv run python -m unittest tests.test_ingest_eta -v
        Ran 15 tests in 0.950s — OK (E1 case 4건 신규 포함)
[web]   pnpm exec tsc --noEmit
        0 error
[web]   pnpm lint
        0 error
```

### 11.3 핵심 설계 (구현 후 회고)

#### E1-A1 — extract sub-stage in-memory 분해

- `STAGE_ORDER` schema 무변경 (web 정합 보호)
- `compute_remaining_ms` 안에서 `current_stage="extract"` + `stage_progress.unit="pages"` + `total>0` 시 **vision sub-stage 분해 활성**
- 추정식: `remaining = (total - current) × vision_p95_ms × 1.2` (sweep retry buffer)
- vision_p95: `vision_usage_log` 최근 200건 succeeded 의 latency p95 (90s TTL cache)
- 미가용 시 fallback `30000ms/page`
- `unit ≠ 'pages'` (chunk row 등) → 기존 비율 분해 유지 (호환)

**왜 p50 + 보정 1.5x 대신 p95 채택**: 진단 §10.2 에서 본 reingest 가 p95 (923143ms) 와 일치. p50 + 1.5x = ~370000 < 실측 절반. p95 가 503 wave + sweep retry burst 자연 흡수.

**왜 sub-stage 분해를 schema 변경 없이 in-memory 로**: schema 변경 시 web 측 STAGE_ORDER + 마이그 + indicator panel 동시 변경 → 범위 폭발. 동일 효과를 현재 stage_progress.unit 만으로 분기 가능.

#### E1-A4 — 90s TTL

- `_CACHE_TTL_SECONDS = 300 → 90`
- DB read 부담 미미 (1쿼리/90초 × table 2 = 미미)
- 503 wave 직후 baseline 갱신 빠름

#### E1-A5 — sample <3 None + 카피

- `_FALLBACK_STAGE_MS["extract"] = 5000 → 120000` (15p PDF vision 보수적)
- `embed = 10000 → 35000`, `tag_summarize = 3000 → 5000` (진단 §10.2 p50 반영)
- `_has_any_baseline` — medians 가 빈 dict 면 ETA None (cold start 만 차단). 한 stage 라도 sample >=3 이면 ETA 노출 (다른 stage 는 fallback)
- web `stage-progress.tsx`: `estimatedRemainingMs == null` + 진행 중 → "처음에는 시간 추정이 부정확합니다" 카피

### 11.4 Δ ratio (진단 §10 case 시뮬레이션)

진단 §10 PDF (15p, 실측 957.9s) 에 대해 **단위 테스트 mock 시뮬**으로 ratio 검증:

| 조건 | 표시 ETA | 실측 | ratio |
|---|---:|---:|---:|
| **이전** (T+0, plan §10) | 224.5s | 957.9s | **0.23** |
| **이후 (E1-A1+A5)** vision p95=50000ms 가정 | ≈ 950s | 957.9s | **≈ 0.99** |
| 이후 (E1-A1+A5) vision p95=8000ms (정상시) | ≈ 144s | 60s | **≈ 2.4** ← over-estimate but 빠르게 끝나면 사용자 만족 |

DoD 0.7~1.3 진입은 **vision p95 가 503 wave 반영된 sample** 일 때 충족. 단위 테스트 `test_diagnostic_case_ratio_within_target` 가 회귀 보호.

### 11.5 회귀 위험 + 검증

| 위험 | 대응 | 검증 |
|---|---|---|
| stage_progress unit≠'pages' (chunk 등) 비율 분해 깨짐 | 기존 ratio 분해 보존 | `test_compute_remaining_ms_with_stage_progress_non_pages` |
| vision_usage_log 미가용 (D2 미진입 환경) | fallback 30000 ms/page | `test_extract_pages_substage_uses_fallback_when_no_vision_log` |
| cold start (sample 0) → None | web 카피 분기 | `test_cold_start_returns_none_for_first_ingest` + stage-progress.tsx |
| ingest_logs DB error | None 반환 (graceful) | `test_db_error_returns_none` |
| TTL 단축으로 DB 부하 증가 | 1쿼리/90초 × 2 table = 미미 | (실측 없음, 가정) |

### 11.6 over-estimate 분석 (정직)

- 정상 PDF (vision p95 = 5~10s) 에 대해선 ETA 가 실측의 1.5~2배 over-estimate 가능. 이유는 sweep buffer 1.2 + p95 사용. plan §8-2 default (b) "예상보다 오래 걸리고 있어요" 안내가 over-estimate 보호 (실측이 ETA 보다 짧게 끝남).
- under-estimate (실측 > 표시) 보다 over-estimate 가 사용자 신뢰 측면 우월 — "약속보다 빨리 끝남" UX.
- 다만 **너무 길게 표시 (예: 10분 이상)** 시 abandon 위험. 추가 측정에서 ratio > 1.5 발생 시 sweep_buffer 1.2 → 1.0 로 조정 검토.

### 11.7 다음 후보 (E1 2차 ship)

plan §5.2 권고 — E1-A3 (vision_page_cache lookup, D2-B 흡수) + E1-A2 (페이지 동시 호출 concurrency=3). 다만 사용자 결정 필요:

1. E1 1차 ship 의 실 reingest 검증 (PDF 보유 컴퓨터에서 다시 측정) → ratio 0.7~1.3 확인 후 2차 진입
2. 또는 1차 결과 만족하면 다른 sprint (S2 search 등) 우선

---

## 12. 참고 문서 우선순위

| # | 문서 | 목적 |
|---|---|---|
| 1 | **본 문서** | E1 plan + 진단 절차 (master) |
| 2 | [`2026-05-06 D1~D2 누적 + 다른 컴퓨터 종합 진입 핸드오프.md`](./2026-05-06%20D1~D2%20누적%20+%20다른%20컴퓨터%20종합%20진입%20핸드오프.md) | 어제까지 baseline (D1~D2 ship 누적, 마이그 6개, factory.py, 단가 정정) |
| 3 | [`2026-05-06 무료유료 모델 전략 통합 plan + 다른 컴퓨터 핸드오프.md`](./2026-05-06%20무료유료%20모델%20전략%20통합%20plan%20+%20다른%20컴퓨터%20핸드오프.md) | Sprint S0~S5 master plan + E1 정합성 점검 (§7) 근거 |
| 4 | [`2026-05-06 푸시 안 된 환경 설정 정리 다른 컴퓨터 동기화용.md`](./2026-05-06%20푸시%20안%20된%20환경%20설정%20정리%20다른%20컴퓨터%20동기화용.md) | `.env` 변수 매트릭스 |
| 5 | [`2026-04-22 개인 지식 에이전트 기획서 v0.1.md`](./2026-04-22%20개인%20지식%20에이전트%20기획서%20v0.1.md) | 페르소나·KPI·DoD 마스터 — §13 KPI 에 "ETA ratio" 신규 추가 권고 |

---

## 13. 한 문장 요약

> E1 sprint = ETA "3분→실측 6~7분" 사용자 보고 해소 위한 신규 라인. 진단 SQL 5건 후 1차 ship (정확도 핵심 3종) → 2차 ship (D2-B 흡수 + 페이지 동시 호출). 본 컴퓨터엔 plan + SQL 만 있고 검증·구현은 PDF 보유 다른 컴퓨터에서 진입.
