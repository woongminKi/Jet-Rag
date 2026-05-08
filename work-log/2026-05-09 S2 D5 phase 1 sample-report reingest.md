# 2026-05-09 S2 D5 phase 1 — sample-report reingest-missing 실 호출

S2 master plan §6 D5 phase 1 진입 — 사전 점검에서 데이터센터 PDF 누락 0p (가설 깨짐) 확인 후, sample-report (43p 누락) 로 대상 변경하여 incremental vision reingest 실 호출. 외부 vision API ~$0.19 사용 사용자 사전 승인 (실측 $0.111). doc 단위 cost cap $0.10 발동으로 8p 만 vision 처리 + 35p 잔여 누락. 단위 테스트 회귀 0 (684/skip 1).

## 1. 작업 내용

### 1.1 대상 변경 근거
- 1차 후보 (`데이터센터 활성화 지원방안`): 사전 점검 §3 시점 누락 0p — 가설 깨짐
- 2차 후보 (`sample-report`): 누락 43p (51~93), 골든셋 G-A-021 매칭 — 사용자 승인 후 변경

### 1.2 실 호출
- `POST /documents/d1259dfe-c402-4cd0-bb04-3e67d88a2773/reingest-missing?mode=default`
- HTTP 202 Accepted
- job_id: `6c4cccdf-2335-47fd-8b5f-88cf9eafb7e9`
- 응답 `missing_pages_before`: [51, 52, 53, …, 93] = 43p
- queued: 2026-05-08T03:11:55.992Z
- started: 2026-05-08T03:11:56.007Z
- finished: 2026-05-08T03:15:58.227Z
- 총 소요: **3분 42초 (222s)**

### 1.3 stage 별 timing (logs 기록)
| stage | status | duration_ms | 비고 |
|---|---|---:|---|
| extract (vision) | succeeded | ~215,000 | 8 vision 호출 + 35p budget skip |
| load | succeeded | 263 | chunks insert |
| embed | succeeded | 25,714 | 16 chunks 임베딩 |

## 2. Phase 1 measurement table (8 metric)

phase1_start_ts (job started_at) 기준 차분 측정.

| # | metric | 실측값 | 비고 |
|---|---|---:|---|
| M1 | vision_call_count | **8** | sample-report doc, source_type=pdf_vision_enrich |
| M2 | vision_cost_usd | **$0.111256** | per-doc cap $0.10 초과 → 35p 보강 생략 |
| M3 | vision_latency_ms_p50 / p95 | N/A | vision_usage_log 에 latency_ms 컬럼 부재 |
| M4 | chunks_inserted | **16** | 1045 → 1061 (chunk_idx > 1044) |
| M5 | newly_processed_pages_via_vision | **8** | [51, 52, 53, 55, 56, 57, 59, 60] |
| M6 | sliding_24h_remaining | **$0.195913** | $0.50 - $0.304087 (window 54 rows) |
| M7 | total_tokens (prompt+image+output+thinking) | **50,038** | 8 호출 평균 6,255 tok |
| M8 | R@10 (G-A-021) | 0.000 (도구 한계 — §6 참조) | predicted top10 에 acceptable hit (904, 908) 있으나 도구 acceptable 미전달 |

### 2.1 vision call 실 발생 page list (v3 + cost cap 효과 실증)
호출 8p: **51, 52, 53, 55, 56, 57, 59, 60**
- 잔여 35p (54, 58, 61~93): per-doc cost cap $0.10 초과로 skip
- vision page 누적 (post): **58 / 93** (사전 50 → +8)
- 평균 retry_attempt = 1.000 (재시도 없음)

### 2.2 chunks 분포 변화
| | total | vision_pages | pages_with_chunks | missing_vision |
|---|---:|---:|---:|---:|
| 사전 점검 | 1045 | 50 | 93 | 43 |
| post-reingest | **1061** | **58** | 93 | **35** |
| diff | +16 | +8 | 0 | -8 |

## 3. 사이드 이펙트 점검 (master plan §7)

| 항목 | 결과 | 비고 |
|---|---|---|
| documents.flags 정합 | OK | `vision_budget_exceeded=true` (정상 발동), `vision_page_cap_exceeded` 부재 |
| ingest_jobs / ingest_logs 상태 | OK | status=completed, attempts=1, 모든 stage succeeded |
| chunks (doc_id, chunk_idx) UNIQUE | OK | chunk_idx 1045~1060 신규, 기존 0~1044 보존 |
| section_title prefix 컨벤션 | OK | 16개 모두 `(vision) p.{n} {분류 / OCR}` |
| metadata.vision_incremental marker | NG (이슈) | 신규 16 chunks 모두 `vision_incremental` key 미설정 → §5 이슈 |
| 다른 6 PDF 영향 0 | OK | §4 회귀 가드 표 |
| 운영 모드 (mode=default) 동작 | OK | S2 D3 ship 토글 정상 |
| per-doc cost cap 발동 | OK (예상) | $0.1113 > $0.10 → 35p skip + flags marking |

### 3.1 documents.flags (post-reingest)
```json
{
  "has_pii": true,
  "ingest_mode": "default",
  "third_party": false,
  "document_type": "보고서",
  "has_watermark": false,
  "vision_budget": {
    "scope": "doc",
    "reason": "문서당 비용 한도 초과 ($0.1113 > $0.1000) — vision 보강 일부 생략",
    "cap_usd": 0.1,
    "used_usd": 0.111256
  },
  "time_reference": "2026-02",
  "vision_budget_exceeded": true
}
```

## 4. 회귀 가드 결과 (master plan §9)

### 4.1 다른 6 PDF chunks 영향 0 (사전 점검 §3.5 비교)
| doc_id | title | total | vision | 변동 |
|---|---|---:|---:|:---:|
| 9878d7bd-…-768 | 포트폴리오_이한주 | 77 | 15 | 0 |
| 92c38315-…-23a | sonata-the-edge_catalog | 134 | 35 | 0 |
| 51dd2cc0-…-9cc | 기웅민 이력서 | 120 | 21 | 0 |
| 0ed5a024-…-372 | law sample3 | 28 | 2 | 0 |
| 2303ef36-…-464 | 보건의료 빅데이터 플랫폼 | 175 | 27 | 0 |
| **d1259dfe-…-773** | **sample-report** | **1061** (+16) | **163** (+16) | **변동 (의도)** |
| b218e8a1-…-7fa | 데이터센터 활성화 지원방안 | 443 | 59 | 0 |

→ sample-report 외 6 PDF 변동 0 — **회귀 가드 통과**.

### 4.2 단위 테스트 회귀 0
```
cd api && uv run python -m unittest discover tests
Ran 684 tests in 15.312s OK (skipped 1 / 회귀 0)
```
- 사전 점검 baseline: 684 / skipped 1 — **동일**

## 5. 남은 이슈

### 5.1 [P2] metadata.vision_incremental marker 미설정
- 신규 16 chunks 모두 `metadata->>'vision_incremental'` 가 None — section_title prefix `(vision) p.{n}` 만으로 식별 가능하나 incremental 식별용 metadata 마커가 의도된 코드 경로에서 셋팅되지 않음
- 명세 §6 측정 SQL `metadata->>'vision_incremental' = 'true'` 기준이면 0건으로 잡혀 잘못된 측정 위험
- 추후 incremental reingest 트레이서빌리티 강화 필요 (예: chunks 인서트 시 `metadata.vision_incremental=true` 셋팅)

### 5.2 [P1] G-A-021 R@10 측정 도구 한계
- predicted top10: `[904, 963, 810, 1018, 902, 937, 1028, 935, 908, 772]`
- relevant_chunks (CSV): **{868}** (1개)
- acceptable_chunks (CSV): {904, 908, 909, 916, 759, …} (29개) — predicted 의 904/908 이 hit
- `evals/run_s2_d4_pre_regression.py:_measure_baseline_retrieval` 가 `acceptable_chunks` 를 search 측에 전달하지 않아 hit 0 으로 측정 → R@10 = 0.000
- **D4-pre baseline 도 동일 도구로 측정** — 명세상 1.000 표기는 가공·재해석 결과일 가능성. baseline 재측정 필요
- 실 의미: pre/post 모두 acceptable 기준이면 R@10 ≥ 0.2 (904, 908 hit) — 회귀 0 가능성 높음, 도구 보강 후 재판정

### 5.3 [P2] vision_usage_log latency_ms 컬럼 부재
- 측정 metric M3 (vision_latency_ms_p50/p95) 산출 불가
- 컬럼 추가 또는 기록 시점 t0/t1 추가 필요 (마이그레이션 신설)

### 5.4 [P2] per-doc cost cap $0.10 적정성 재검토
- 90+ page 문서 (sample-report 93p) 의 경우 page당 평균 ~$0.0042 → 24p 호출 가능 → cap 발동 시 nearly half 누락
- 대용량 PDF cap 동적 산정 (e.g., page 수 비례) 또는 per-doc cap 상향 고려

## 6. 다음 스코프

### 6.1 Phase 2 (다음 PDF reingest) — 보류 권고
- sample-report 의 잔여 35p (54, 58, 61~93) 는 per-doc cap 으로 skip — 같은 doc 재호출은 cap 재발동
- 후보:
  - (a) per-doc cap 상향 후 sample-report 동일 doc 재시도 (예: cap $0.20 → 잔여 vision $0.07 추가 필요, sliding 24h 잔여 $0.196 안에 가능)
  - (b) 다른 PDF (보건의료 빅데이터 플랫폼 / 포트폴리오) 누락 페이지 측정 후 phase 2
- **사용자 결정 필요** — 동일 doc 추가 reingest 는 vision_page_cache 무효화 정책에 따라 skip 가능성

### 6.2 S3 진입 권고
- vision 보강 ROI 의 정량 검증은 R@10 도구 보강 (acceptable_chunks 전달) 선행 필요
- D5 phase 1 결과만으로는 retrieval 영향 판정 불가 — S2 D5 종료 + S3 (보조 인덱스) 진입 시 도구 보강 함께 진행

### 6.3 도구 보강 항목 (S3 함께)
- [ ] `_measure_baseline_retrieval` 에 acceptable_chunks 전달 (graded R@10 측정)
- [ ] `chunks.metadata.vision_incremental=true` 셋팅 (incremental 트레이서빌리티)
- [ ] `vision_usage_log.latency_ms` 컬럼 추가

## §측정 도구 fix — R@10 acceptable_chunks 전달 (2026-05-09 추가 ship)

### F.1 fix 내용 (planner v0.1 명세 그대로)

§5.2 [P1] / §6.3 첫 항목 (acceptable_chunks 전달 누락) 해결.

| 변경 위치 | LOC | 내용 |
|---|---:|---|
| `evals/run_s2_d4_pre_regression.py` `GoldenRow` (L77~) | +4 | `acceptable_chunks: tuple[int, ...] = ()` 필드 추가 |
| `evals/run_s2_d4_pre_regression.py` `_load_golden_targets` (L130 부근) | +5 | `acceptable_chunks` 컬럼 파싱 — `relevant_chunks` 와 동일 로직 |
| `evals/run_s2_d4_pre_regression.py` `_measure_baseline_retrieval` (L501~) | +3 | `recall_at_k(..., acceptable_chunks=accept_set)` 호출 + 결과 dict 에 `acceptable_used` 추가 (3 분기 일관) |
| `evals/run_s2_d4_pre_regression.py` retrieval CSV writer (L900) | +1 | `acceptable_used` 컬럼 출력 (트레이서빌리티) |
| `api/tests/test_retrieval_metrics.py` `GradedRecallFourCaseTest` | +66 | acceptable hit only / relevant hit only / both hit / both miss 4 케이스 |
| `api/tests/test_s2_d4_pre_regression_acceptable.py` (신규) | +138 | 통합 테스트 1건 — golden CSV → GoldenRow → recall_at_k 호출 인자 캡처 |

명세 외 리팩토링 0 — `_check_chunk_pages` 등 다른 함수의 `g.relevant_chunks` 사용 미변경.

### F.2 단위 테스트 회귀 0

```bash
cd api && uv run python -m unittest discover tests
# 결과: Ran 689 tests in 14.583s, OK (skipped=0 표기 / baseline 684 → +5)
```

baseline (684 / skipped 1) → fix 후 689 / 회귀 0. 4 케이스 + 통합 1건 = +5 추가.

### F.3 D4-pre baseline v1 ↔ v2 delta (per-row R@10)

`evals/results/s2_d4_pre_baseline_v2.md` 산출. v1 = 어제 명세 §3.3 baseline, v2 = 본 fix 후 graded recall.

| id | acc | predicted top10 hit | v1 R@10 (binary) | v2 R@10 (graded) | delta | 해석 |
|---|---:|---|---:|---:|---:|---|
| G-A-008 | 2 | relv 374 hit, acc miss | 1.000 | **0.500** | -0.500 | acceptable 2개 추가로 max_score 분모 inflation (1.0 + 0.5×2 = 2.0). hit 동일 (1.0/2.0). 품질 변화 0. |
| G-A-011 | 0 | relv 1 hit | 1.000 | **1.000** | 0.000 | acceptable 0건 → binary 동일. |
| G-A-021 | 29 | relv 868 miss, acc 904+908 hit | 0.000 | **0.182** | **+0.182** | **실 회복** — predicted 의 904/908 이 golden acceptable 에 포함, hit_score=0.5+0.5=1.0, max_score = 1.0+0.5×9 (cap k=10) = 5.5 → 1.0/5.5. **§5.2 [P1] 핵심 수정.** |
| G-A-107 | 1 | empty (search 미포함) | 0.000 | 0.000 | 0.000 | search 응답에 doc 미포함 — 변화 없음. |
| G-A-111 | 3 | empty (search 미포함) | 0.000 | 0.000 | 0.000 | 동일. |
| **평균** | — | — | **0.4000** | **0.3364** | **-0.0636** | 표면 평균 하락은 G-A-008 분모 inflation 영향. **수치 하락 ≠ 품질 저하**. |

### F.4 S2 D5 phase 1 post 재측정 (G-A-021)

기존 §2 M8 / §5.2 의 한계가 본 fix 로 회복:

| 측정 시점 | predicted top10 (head) | relevant hit | acceptable hit | R@10 (graded) | 비고 |
|---|---|---|---|---:|---|
| §2 M8 (도구 fix 전) | 904, 963, 810, 1018, 902 | 868 miss | (미전달) | 0.000 | 도구 한계로 false negative |
| F.4 (도구 fix 후) | 904, 963, 810, 1018, 902, 937, 1028, 935, 908, 772 | 868 miss | 904, 908 hit | **0.182** | 회복 — acceptable 2 hit, max_score 5.5, hit_score 1.0 |

post-reingest doc (sample-report d1259dfe-…) 의 retrieval 동작 자체는 본 fix 로 변경되지 않음 — 도구 측의 graded 평가만 추가. **신규 vision-derived chunk (1045~1060) 가 G-A-021 search 결과에 진입했는지 별개 검증은 §6.1 phase 2 (per-doc cap 상향 후 sample-report 재시도) 로 이연.**

### F.5 "수치 하락 ≠ 품질 저하" 메모

| 지표 | v1 (binary) | v2 (graded) | 의미 |
|---|---:|---:|---|
| 평균 R@10 | 0.4000 | 0.3364 | 분모(max_score) 가 acceptable 포함으로 커지면서 표면 하락 |
| G-A-021 R@10 | 0.000 | 0.182 | **실 회복** — false negative 제거 |
| G-A-008 R@10 | 1.000 | 0.500 | 분모 inflation, hit 자체는 동일 |

graded recall 의 정의상:
- 분자 = relevant hit × 1.0 + acceptable hit × 0.5
- 분모 = ideal sort (relevant 1.0×n_r + acceptable 0.5×n_a) 후 cap K 합

acceptable 이 많은 row 일수록 분모가 커져 표면 R@10 이 낮아진다. 따라서 v1 (binary) 와 v2 (graded) 직접 비교는 부적절 — **v2 vs (앞으로의) post-reingest v2 측정** 만이 회귀 판정 기준이다. 본 fix 의 효과는 G-A-021 같이 acceptable hit 만 잡히는 row 의 false negative 제거이며, 회귀 가드 도구로서의 신뢰도가 높아졌다.

### F.6 신규 산출물

| 파일 | 역할 |
|---|---|
| `evals/results/s2_d4_pre_baseline_v2.md` | D4-pre baseline 재실행 markdown |
| `evals/results/s2_d4_pre_baseline_v2_hints.csv` | source_hint cross-check per-row |
| `evals/results/s2_d4_pre_baseline_v2_chunks.csv` | DB chunk page cross-check per-row |
| `evals/results/s2_d4_pre_baseline_v2_retrieval.csv` | graded R@10 + acceptable_used per-row |
| `api/tests/test_s2_d4_pre_regression_acceptable.py` | 통합 테스트 1건 (golden CSV → recall_at_k 인자 캡처) |

### F.7 사이드 이펙트 점검

- 운영 코드 변경 0 — `evals/` + `api/tests/` 만 변경
- DB 마이그 0 / 외부 API 0 / 새 패키지 0
- 본 fix 의 결과 dict `acceptable_used` 키 추가 — retrieval CSV writer (L890~) 도 일관 추가하여 다운스트림 파이프라인 신규 키 처리 0
- vision API 호출 0 (`--measure-retrieval` 만 사용, search 는 BGE-M3 무료 티어)
- `_check_chunk_pages` 의 `g.relevant_chunks` 사용은 의도적으로 미변경 — chunk-level 회귀 위험 분석은 relevant 한정이 정합 (acceptable 은 사용자 의도 여유분으로 회귀 위험 분모 오염 방지)

### F.8 후속 (S3 함께)

§6.3 도구 보강 항목 갱신:
- [x] `_measure_baseline_retrieval` 에 acceptable_chunks 전달 (graded R@10 측정) ← **본 fix 로 완료**
- [ ] `chunks.metadata.vision_incremental=true` 셋팅 (incremental 트레이서빌리티)
- [ ] `vision_usage_log.latency_ms` 컬럼 추가

phase 2 진입 시 sample-report 재 reingest 후 **v2 baseline ↔ v2 post** 비교로 vision 보강 ROI 정량 검증 가능.

---

## 7. ENV diff
변경 0. (기본값 그대로)
- `JET_VISION_PER_DOC_CAP_USD=0.10` (default)
- `JET_VISION_PER_DOC_PAGE_CAP=NN` (default)
- `JET_VISION_24H_BUDGET_USD=0.50` (default)

## 8. commit hash
- baseline: 514c368 (S1.5 v3 ship + P1 fix 4건 + QA 검증 통합)
- D5 phase 1 commit: **미커밋** — work-log 만 신규, 코드 변경 0
- 측정 도구 fix commit: **미커밋** — `evals/run_s2_d4_pre_regression.py` + `api/tests/` 보강
- 신규 산출물:
  - `evals/results/s2_d5_phase1_post_retrieval.md`
  - `evals/results/s2_d5_phase1_post_retrieval_hints.csv`
  - `evals/results/s2_d5_phase1_post_retrieval_chunks.csv`
  - `evals/results/s2_d5_phase1_post_retrieval_retrieval.csv`
  - `work-log/2026-05-09 S2 D5 phase 1 sample-report reingest.md`

## 9. 핵심 결론

1. **incremental vision reingest 정상 동작 확인** — 누락 43p 중 8p 처리, chunks 보존 + 신규 16 chunks 추가, 단위 테스트 회귀 0
2. **per-doc cost cap $0.10 발동 + flags marking 확인** — S2 D2 ship 메커니즘 production 환경 실증
3. **v3 vision_need_score 의 효과는 본 케이스에서 미검증** — cost cap 이 먼저 발동하여 vision_need_score 임계 통과 vs 임계 미달 페이지 분리 측정 불가
4. **R@10 회귀 측정 도구 한계 발견** — acceptable_chunks 전달 누락으로 false negative 위험, S3 진입 시 도구 보강 우선
5. **Phase 2 진입은 사용자 판단 필요** — per-doc cap 상향 후 동일 doc 재시도 vs 다른 PDF 신규 측정
