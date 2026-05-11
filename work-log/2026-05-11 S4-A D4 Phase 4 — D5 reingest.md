# 2026-05-11 S4-A D4 Phase 4 — D5 reingest (회귀 -0.0319pp 임계 초과)

통합 sprint Phase 4 의 vision-enrich PDF 6 doc full reingest 실행 결과 — **회귀 임계 -0.03pp 초과** (post R@10 = 0.6049 vs 임계 통과 기준 >= 0.6068). 단위 테스트 회귀 0 (778). chunks DB 변경만, 코드 변경 0. Phase 5 진입 **보류**.

핵심 원인: vision_usage_log 의 doc 별 누적 cost (전체 기간) 가 per-doc cap $0.15 를 초과한 3 doc (sonata $0.2692 / 데이터센터 $0.3081 / sample-report $0.6268) 의 vision_enrich 단계가 budget_guard 에 의해 **진입 차단** → vision_page_cache v2 cache hit 의 기회도 없이 chunks 재구성 → vision-derived chunks 105건 소실.

> **(close)** 회귀 fail close — 사용자 옵션 C 선택. 복구 절차는 종합 핸드오프 (`2026-05-11 종합 + 2026-05-12 진입 핸드오프.md`) §5.1 (옵션 A) 참조. chunks DB 회귀 -185 상태로 sprint close 이지 운영 ship 아님 — 다음 진입 시 1순위 = 옵션 A 회복.

## 1. 작업 요약

| 단계 | 결과 |
|---|---|
| Step 1 사전 점검 | PK 3-tuple 확인 / 24h cost $0.0006 (0.1%) / per-doc cache v2 100% / 예상 cost $0 |
| Step 2 ENV 설정 | `JETRAG_DOC_BUDGET_USD=0.15` / `JETRAG_VISION_PROMPT_VERSION=v2` / `JETRAG_PDF_VISION_ENRICH_MAX_SWEEPS=2` (process scope) |
| Step 3 reingest | 6 doc 모두 OK / 총 8m 18s / vision API 실 호출 0 |
| Step 4 데이터 검증 | 3 doc 정상 (cache hit) / 3 doc vision_pages 소실 (budget cap skip) |
| Step 5 재측정 | 전체 R@10 = **0.6049** → 임계 0.0019pp 초과 (Fail) |
| 단위 테스트 | 778 / 회귀 0 |

## 2. Step 1 사전 점검 결과

### 2.1 vision_page_cache PK
- migration 015 + `vision_cache.py` 코드 양쪽에서 `(sha256, page, prompt_version)` 3-tuple UNIQUE 확인
- v1 row 8건 (sample-report sha `b35f...` page [51, 52, 53, 55, 56, 57, 59, 60]) **보존 가능**
- v2 row 148건 분포: 6 doc 의 prior_vision_pages 와 100% 일치

### 2.2 사전 점검 표

| doc | sha (16) | total cache | v1 | v2 | prior_vision_pages | likely_new_calls |
|---|---|---:|---:|---:|---:|---:|
| 포트폴리오 | 5789295b... | 15 | 0 | 15 | 15 | 0 |
| 기웅민 이력서 | 469ed80d... | 2 | 0 | 2 | 2 | 0 |
| sonata | 330fb897... | 27 | 0 | 27 | 27 | 0 |
| 보건의료 | 93ea53ee... | 26 | 0 | 26 | 26 | 0 |
| 데이터센터 | 918a2cf1... | 41 | 0 | 41 | 41 | 0 |
| sample-report | b35f5b19... | 45 | 8 | 37 | 37 | 0 |

**TOTAL estimated cost = $0** — cache 100% hit 가정.

### 2.3 누적 historical cost (vision_usage_log SUM 전체 기간)

| doc | calls | distinct pages | sum_cost | avg/call |
|---|---:|---:|---:|---:|
| sample-report | 45 | 45 | **$0.6268** | $0.0139 |
| 데이터센터 | 43 | 41 | **$0.3081** | $0.0072 |
| sonata | 58 | 29 | **$0.2692** | $0.0046 |
| 보건의료 | 26 | 26 | $0.1417 | $0.0055 |
| 포트폴리오 | 32 | 15 | $0.1468 | $0.0046 |
| 기웅민 이력서 | 2 | 2 | $0.0063 | $0.0032 |

**핵심 누락**: 3 doc 의 누적 cost (`$0.2692 ~ $0.6268`) 가 per-doc cap `$0.15` (의뢰서 상향값) 를 모두 초과. 사전 점검 §6 의 "vision_page_cache hit → 호출 0 → cost 0" 추정은 옳지만, **budget_guard 가 cache lookup 이전 단계에서 차단**한다는 점을 고려하지 못했음.

### 2.4 24h sliding cost

- 24h total = $0.0006 (0.1% / cap $0.50) → 잔여 $0.4994
- 의뢰서 §Step 1 임계 70% 안전 — 통과

## 3. Step 3 reingest 실측 cost / 결과

reingest 진행 (작은 doc → 큰 doc 순서):

| # | doc | chunks pre→post | vision_pages pre→post | elapsed | budget cap skip |
|---|---|---|---|---:|:---:|
| 1 | 포트폴리오 | 79 → 79 | 15 → 15 | 63.9s | — |
| 2 | 기웅민 이력서 | 103 → 103 | 2 → 2 | 25.8s | — |
| 3 | sonata | 129 → **99** | 27 → **0** | 45.0s | **YES** (used=$0.2692, cap=$0.15) |
| 4 | 보건의료 | 175 → 175 | 26 → 26 | 61.6s | — |
| 5 | 데이터센터 | 443 → **384** | 41 → **0** | 69.4s | **YES** (used=$0.3081, cap=$0.15) |
| 6 | sample-report | 994 → **898** | 37 → **0** | 224.7s | **YES** (used=$0.6268, cap=$0.15) |

**total elapsed**: 498.8s (8.3m).
**total cost (vision_usage_log delta)**: $0 (모든 doc 의 vision API 호출 0 — cap skip 또는 cache hit).
**소실 vision_pages**: 27 + 41 + 37 = **총 105 vision chunks 소실**.

### 3.1 budget cap skip 메시지 (extract.py:228 로그)

```
PDF vision enrich skip — budget cap (scope=doc, used=$0.2692, cap=$0.1500) doc_id=92c38315-...
PDF vision enrich skip — budget cap (scope=doc, used=$0.3081, cap=$0.1500) doc_id=b218e8a1-...
PDF vision enrich skip — budget cap (scope=doc, used=$0.6268, cap=$0.1500) doc_id=d1259dfe-...
```

`budget_guard.check_doc_budget` 가 vision_usage_log 의 doc_id 별 전체 기간 SUM 을 cap 과 비교 — reingest 이전 누적이 cap 을 넘으면 vision_enrich 진입 자체가 차단되고 vision_page_cache lookup 도 수행되지 않음.

## 4. Step 4 데이터 변화

### 4.1 vision_page_cache row count
- pre: 156 (v1=8, v2=148)
- post: **156 (변동 0)** — vision API 호출 0 이므로 새 row 추가 0, v1 row 8건 그대로 보존
- (3-tuple PK 의 v1 보존성 검증 완료 — 추가 검증 가능했으나 v2 cache hit 자체가 발생하지 않아 v1↔v2 추이 비교는 의뢰서 의도와 별개로 무의미)

### 4.2 chunks 변화

| doc | pre chunks | post chunks | delta |
|---|---:|---:|---:|
| 포트폴리오 | 79 | 79 | 0 |
| 기웅민 이력서 | 103 | 103 | 0 |
| sonata | 129 | 99 | **-30** |
| 보건의료 | 175 | 175 | 0 |
| 데이터센터 | 443 | 384 | **-59** |
| sample-report | 994 | 898 | **-96** |
| 합계 | 1923 | 1738 | **-185** |

185 chunks 감소 — vision chunks 105 + 추가로 base text 측 chunk 재합성에서의 변동.

### 4.3 vision_usage_log SUM (오늘)
- pre/post 동일 — 추가 row 0, cost 추가 0.

## 5. Step 5 회귀 측정 결과

도구: `evals/run_s4_a_d4_breakdown.py --goldenset v2`
산출물: `evals/results/s4_a_d4_post_d5.md` / `evals/results/s4_a_d4_post_d5.json`

| metric | Phase 3 baseline | post-D5 | delta | 회귀 |
|---|---:|---:|---:|:---:|
| 전체 R@10 | 0.6368 | **0.6049** | **-0.0319** | **YES — 임계 초과** |
| 전체 R@5 | (미기록) | 0.5685 | — | — |
| cross_doc R@10 | 0.0625 | **0.0000** | -0.0625 | YES |
| caption_dependent=true R@10 | 0.6119 | **0.5459** | -0.0660 | YES (claim B 후퇴) |
| caption_dependent=false R@10 | 0.6399 | 0.6121 | -0.0278 | 임계 안쪽 |
| v2 라벨 R@10 (n=77) | 0.6547 | **0.5294** | -0.1253 | YES |
| qtype=exact_fact R@10 | 0.6892 | 0.6681 | -0.0211 | 임계 안쪽 |

**Pass 여부**: **Fail** (임계 0.0019pp 초과).

### 5.1 prompt_version 라벨 출현
- v1 라벨: **0건** (도구가 chunks majority 로 판정 — chunks 측 v1 신호 부재)
- v2 라벨: 77건 / unlabeled 85건
- vision_page_cache 측 v1 row 8건 보존 확인이지만 chunks 측 surfacing 도구 한계로 직접 비교 불가 (의뢰서 §Step 5 의 "prompt_version breakdown — v1 라벨 출현 여부" 결과 = 출현 X)

## 6. 회복 옵션 (사용자 결정 필요)

별도 진단 문서 `evals/results/s4_a_d4_post_d5_REGRESSION.md` 에 상세 옵션 4건.

권고: **옵션 A** — `JETRAG_BUDGET_GUARD_DISABLED=1` 임시 ENV 로 sonata / 데이터센터 / sample-report 3 doc 재 reingest. v2 cache hit 100% 예상 → 추가 cost 0. 예상 회복 후 R@10 = 0.63~0.66.

사용자 명시 승인 필요한 사유:
- budget_guard 비활성은 운영 안전장치 우회 — destructive 변경
- 추가 reingest 는 3 doc 의 chunks 또 1회 재구성

## 7. 사이드 이펙트 점검

| 항목 | 결과 |
|---|---|
| documents.flags.vision_budget_exceeded | 3 doc 자동 마킹 (sonata / 데이터센터 / sample-report) |
| documents.flags.ingest_mode | default 유지 (의도) |
| chunks (doc_id, chunk_idx) UNIQUE | OK — 재구성 시 0부터 다시 시작 |
| vision_page_cache row count | 156 변동 0 |
| 다른 7 doc (vision-enrich 대상 외) 영향 | 0 (의뢰서 §금지 사항 준수) |
| 단위 테스트 회귀 | 0 (778) |
| 코드 변경 | 0 (chunks DB 변경만) |

## 8. 남은 이슈

### 8.1 [P0] vision_enrich 진입 차단 정책 재검토
- 현재: budget_guard.check_doc_budget 가 전체 기간 SUM 기준 → reingest 시 누적 cost 가 한 번이라도 cap 을 넘으면 vision_enrich 영구 차단
- 대안 A: vision_page_cache hit 인 경우 cost 추가 0 이므로 cap 검사 우회 (cache hit 확인 후 cap 차감)
- 대안 B: reingest 시점 별 윈도우 (e.g., 직전 7일) 만 cap 계산
- 대안 C: cap 자체를 reingest 흐름에서만 우회 ENV 추가 (`JETRAG_REINGEST_BYPASS_BUDGET=1`)

### 8.2 [P1] prompt_version chunks-level surfacing
- 의뢰서 §Step 5 의 "v1 라벨 출현 여부" 측정은 도구 측 한계로 불가
- vision_page_cache 측은 v1 보존 (3-tuple PK) 이지만 chunks 측 metadata 에 page-level prompt_version 라벨 없음
- 개선안: chunks.metadata 에 `vision_prompt_version` 키 추가 (S4-A D5 또는 후속 sprint)

### 8.3 [P2] 사전 점검 도구 cap modeling
- `_phase4_d5_precheck.py` §6 는 cache hit 기반 cost 추정만 — budget_guard 진입 차단 모델링 추가 필요
- 개선안: 각 doc 의 누적 cost vs 예상 cap 차이를 §3 자리에 명시 + 차단 위험 doc 사전 경고

## 9. 다음 스코프

### 9.1 사용자 결정 후 진행 가능
- 옵션 A 승인 시: 3 doc 회복 reingest (~5분) + 재측정 (~5분) → ship 또는 추가 진단
- 옵션 B 승인 시: cap 영구 상향 후 동일 흐름
- 옵션 C 선택 시: Phase 4 결과 그대로 보류, P0 이슈 해결 sprint 별도 신설

### 9.2 Phase 5 진입 보류
- 회귀 임계 통과 후에만 진입 가능 (의뢰서 §Step 5 정책)

## 10. 산출물

| 파일 | 역할 |
|---|---|
| `evals/_phase4_d5_precheck.py` | Step 1 사전 점검 (PK / cost / cache 분포) |
| `evals/_phase4_d5_reingest.py` | Step 3 reingest 실행 (6 doc 순차) |
| `evals/results/s4_a_d4_post_d5.md` | Step 5 재측정 markdown |
| `evals/results/s4_a_d4_post_d5.json` | Step 5 raw json |
| `evals/results/s4_a_d4_post_d5_REGRESSION.md` | 회귀 진단 + 회복 옵션 4건 |
| `work-log/2026-05-11 S4-A D4 Phase 4 — D5 reingest.md` | 본 문서 |

## 11. ENV diff (process scope, 영구 변경 0)

```
JETRAG_DOC_BUDGET_USD=0.15
JETRAG_VISION_PROMPT_VERSION=v2
JETRAG_PDF_VISION_ENRICH_MAX_SWEEPS=2
```

`.env` 파일은 변경 0 — reingest process 종료 후 default 로 자동 복귀.

## 12. commit
- 미커밋 — 의뢰서 §금지 사항 "임의 커밋 금지" 준수.
- 코드 변경 0. 신규 산출물은 `evals/` 와 `work-log/` 만.

## 13. 핵심 결론

1. **chunks 재구성 자체는 OK** — 3 doc (포트폴리오 / 기웅민 이력서 / 보건의료) 의 chunks DB 가 v2 prompt + S4-A D2 chunk.text 합성 통합 반영 완료.
2. **3 doc 회귀** — sonata / 데이터센터 / sample-report 의 vision chunks 105건이 budget cap 진입 차단으로 소실. caption-dependent / v2 라벨 / cross_doc subset 모두 큰 폭 회귀.
3. **회복 가능** — `JETRAG_BUDGET_GUARD_DISABLED=1` 임시 ENV 로 3 doc 재 reingest 시 vision_page_cache v2 hit 100% 예상 → 추가 cost 0 으로 회복.
4. **단위 테스트 회귀 0** (778 / OK) — 코드 변경 0, chunks DB 변경만.
5. **Phase 5 진입 보류** — 사용자 결정 (옵션 A/B/C) 후 진행.
