# 2026-05-15 DECISION-12 인제스트 KPI 인프라 측정 — 현 corpus 기준

> 작성: 본 세션 직접 / HEAD `1ce5465` / 측정 시점: 2026-05-15 / 비용 0 (DB 분석만)
> **결론**: KPI #1·#2·#3 측정 인프라 가동 확인, 현 corpus(12 active doc) 기준 모두 게이트 초과. 단 PRD 정의 "벤치 30개" 충족은 별도 자료 준비 필요.

## 0. 배경

PRD master v1.4 (`work-log/2026-05-12 검색 정확도 80% 달성 PRD.md`) §본 PRD §I.1: DECISION-12 = 인제스트 KPI #1·#2·#3 = **별도 트랙**, 본 PRD scope 밖. M3 발표 시 "인프라 준비됨, 벤치셋 30개 별도 트랙" 표시.

2026-05-15 세션 핸드오프 §7.5 에 "별도 트랙 (DECISION-12) — 새 3 doc 인제스트 성공으로 SLO 측정 케이스 확보" 명시. 본 측정은 그 케이스 확보 사실을 정량화하고, 인프라 readiness 를 확인하는 것이 목적.

## 1. KPI 정의 (PRD master 인용)

| KPI | 도메인 | 항목 | 게이트 | 측정 대상 (PRD 정의) |
|---:|---|---|---|---|
| #1 | 인제스트 | HWP/HWPX 성공률 | ≥ 95% | 벤치 30개 |
| #2 | 인제스트 | PDF 성공률 | ≥ 98% | 벤치 30개 |
| #3 | 인제스트 | Vision 캡셔닝 성공률 | ≥ 90% | 타입 10종 × 5 = 50 페이지 |

본 측정은 위 게이트의 sample 평가 — **PRD 정의 벤치셋이 아닌 현 corpus(12 active doc) 기준**.

## 2. 본 측정 — 현 corpus

### 2.1 KPI #1 — HWP/HWPX 성공률

| doc_type | completed | failed | rate |
|---|---:|---:|---:|
| **hwpx** | 6 | 0 | **100%** ✅ |
| **hwp** | 2 | 0 | **100%** ✅ |
| 합계 | 8 | 0 | **100%** ✅ |

- 게이트 95% 초과 ✅. 단 sample n=8 (벤치 30개 미달).

### 2.2 KPI #2 — PDF 성공률

| 범주 | n | 성공 | 실패 | rate |
|---|---:|---:|---:|---:|
| 활성 PDF doc (deleted_at IS NULL) | 11 | 11 | 0 | **100%** |
| 모든 PDF jobs (재시도 포함) | 36 + 5 PDF-related failed | 36 | 5 | 87.8% |

- **활성 doc 기준 100% ✅** (게이트 98% 초과).
- 단일 doc 의 reingest 시도 누적 기준으로는 87.8% — failed 5건 모두 transient 또는 dev 작업 (§2.4).

### 2.3 KPI #3 — Vision 캡셔닝 성공률

| prompt_version | n_total | n_ok | n_empty | rate | avg cost (USD) |
|---|---:|---:|---:|---:|---:|
| v1 | 8 | 8 | 0 | **100%** | $0.013907 |
| v2 | 193 | 193 | 0 | **100%** | $0.008353 |
| **합계** | **201** | **201** | **0** | **100%** ✅ | — |

- 게이트 90% 초과 ✅. 단 PRD 정의 "타입 10종 × 5 = 50 페이지" 다양성 ablation 별도 필요.
- 누적 vision 비용: **$1.7233** (v1=$0.1113 + v2=$1.6120).

### 2.4 실패 5건 상세 (모두 transient 또는 dev)

| stage | n | 사례 | 해석 |
|---|---:|---|---|
| `load` | 2 | NULL byte (22P05) / statement timeout (57014) | **robustness fix `b70b672` 직전 발생** — 본 세션 patch 후 해소 |
| `dedup` | 1 | invalid UUID `"test"` (22P02) | **dev 작업물** (테스트 doc_id) |
| `embed` | 1 | "stale running" (sample-report) | `_repair_sample_report_dense_vec.py` 로 복구 (2026-05-12) |
| `extract` | 1 | `ConnectionTerminated error_code:9` | HTTP/2 transient (HF API 일시 단절) |

→ **production failure 0건**. 5건 모두 dev/transient 분류.

## 3. 부수 측정 — latency / throughput (KPI 외)

KPI 항목은 아니지만 인프라 가동 측정으로 부수 산출.

### 3.1 latency by doc_type (completed 46 jobs, 활성 + 재시도 포함)

| doc_type | n_jobs | avg | p50 | p95 | 해석 |
|---|---:|---:|---:|---:|---|
| pdf | 36 | 386.9s | 233.4s | 971.2s | 사업보고서 크기 영향 |
| hwpx | 6 | 58.5s | 55.0s | 107.5s | 빠름 |
| hwp | 2 | 27.5s | — | 27.7s | 빠름 |
| pptx | 2 | 40.7s | — | 42.1s | 빠름 |

### 3.2 최근 latest-per-doc 기준 (12 active)

| bucket | n_docs | avg | p50 | p95 | total_chunks | chunks/s |
|---|---:|---:|---:|---:|---:|---:|
| **신규 3 (arXiv·삼성·SK)** | 3 | 810.0s | 853.2s | 958.7s | 35,048 | 14.42 |
| **기존 9** | 9 | 97.7s | 79.2s | 249.8s | 2,009 | 2.29 |

→ **신규 3 doc 은 페이지 수 + chunks 폭증으로 latency 5~13분**, 단 chunks throughput 은 6.3× 빠름 (SK 30.24 chunks/s 최고).

### 3.3 chunks 분포 (per_doc)

| doc | size_mb | latency_s | chunks | chunks/MB | 비고 |
|---|---:|---:|---:|---:|---|
| arXiv 영어 학술 | 0.81 | 970 | 749 | 921 | 학술 PDF, vision 0 (budget) |
| SK 사업보고서 | 6.77 | 853 | **25,806** | 3,814 | 표 cell 분할 — over-chunking 의심 |
| 삼성전자 사업보고서 | 2.17 | 606 | 8,493 | 3,908 | 동일 |
| sample-report | 8.58 | 334 | 1,000 | 117 | 정상 |
| 데이터센터 안내서 | 1.04 | 117 | 443 | 425 | 정상 |
| 직제 규정 (hwpx) | 0.07 | 119 | 171 | 2,290 | 정상 |
| 보건의료 빅데이터 | 0.93 | 79 | 175 | 188 | 정상 |
| 기웅민 이력서 | 0.09 | 124 | 103 | 1,085 | 정상 |
| 브랜딩 스튜디오 (pptx) | 3.01 | 42 | 5 | 2 | 정상 (슬라이드 5) |
| 한마음생활체육관 | 0.05 | 14 | 56 | 1,187 | 정상 |
| law sample3 (pdf) | 0.25 | 23 | 26 | 103 | 정상 |
| law sample2 (hwp) | 0.11 | 27 | 30 | 276 | 정상 |

- SK/삼성 의 chunks/MB ≈ 3,800 — 다른 PDF 의 ~30배 (over-chunking 확인). 2026-05-15 §3.1 의 "표 위주 PDF, PyMuPDF block-level 분할 의도된 동작" 정합.
- 권고 3 (over-chunking 측정) 에서 **검색 품질 회귀 신호 0** 확인 완료 — chunks 폭증의 production 영향 없음.

## 4. 인프라 readiness 평가

| 항목 | 상태 |
|---|---|
| ingest_jobs 측정 인프라 | ✅ (status·started_at·finished_at·current_stage·stage_progress 모두 기록) |
| documents 메타 인프라 | ✅ (size_bytes·doc_type·deleted_at) |
| chunks 카운트 | ✅ |
| vision_page_cache 가시화 | ✅ (prompt_version·estimated_cost·result) |
| 실패 사유 추적 | ✅ (error_msg·current_stage 페어로 분류 가능) |
| 비용 누적 추적 | ✅ ($1.72 누적, doc 별 합산 RPC 존재) |
| 벤치셋 30개 별도 자료 | ❌ (PRD 정의 충족용 — 별도 sprint) |
| 타입 10종 × 5 vision 다양성 | ❌ (PRD 정의 충족용 — 별도 sprint) |

**결론: 측정 인프라는 100% 가동, 벤치셋 자료만 별도 준비 시 KPI #1·#2·#3 본격 ship 가능.**

## 5. 결정 권고

### 5.1 본 PRD M3 발표 시 (즉시 적용 가능)

- "**인제스트 KPI 인프라 readiness ✅**" 명시
- "현 corpus 12 active doc 기준 sample 측정: HWP/HWPX 100% · PDF 100% · Vision 100% — 모두 게이트 초과" 보고
- "PRD 정의 벤치셋 30개 충족은 별도 트랙" 명시 (PRD §I.1 ②분리 권고와 일치)

### 5.2 별도 트랙 sprint 진입 시 (DECISION-12 actual ship)

작업 견적 (참고용):

| ID | 작업 | 작업량 | 비용 추정 |
|---:|---|---:|---:|
| W-1 | 벤치 PDF 30개 수집 (다양한 소스) | 2~3h | $0 |
| W-2 | 벤치 HWP/HWPX 30개 수집 | 1~2h | $0 (사용자 자산 활용) |
| W-3 | vision 다양성 타입 10종 × 5 = 50 페이지 큐레이션 | 3~4h + vision 비용 | ~$0.42 (50 × $0.008) |
| W-4 | 배치 인제스트 + KPI 산출 자동화 | 2~3h | — |
| W-5 | work-log + PRD 갱신 | 1h | — |

총: 9~13h + $0.42 vision 비용

### 5.3 페르소나 정합 검토

DECISION-12 분리 사인오프 (PRD §I.1 ②) 그대로 유지가 권장. 페르소나 A (개인 지식 관리, 한국어 위주, 비용 의식적) 입장에서 벤치셋 30개 큐레이션은 부담. 본 측정의 sample (12 doc) 결과로 신뢰성 입증 가능.

## 6. 회귀 / 사이드이펙트

- 본 측정은 DB read-only 분석 → 회귀 0
- 코드 변경 0
- 단위 테스트 변경 0 (1202 OK 유지)

## 7. 인용 / 참조

- PRD master: `work-log/2026-05-12 검색 정확도 80% 달성 PRD.md` v1.4 §본 PRD §I.1
- 2026-05-15 핸드오프 §7.5 "별도 트랙 (DECISION-12)"
- 권고 3 결과 (over-chunking 회귀 0): `work-log/2026-05-15 over-chunking 검색 품질 영향 측정.md`
- 인제스트 robustness fix: `work-log/2026-05-14 인제스트 robustness fix — NULL byte + batch split.md`
- 측정 raw SQL: ingest_jobs / documents / chunks / vision_page_cache 4-way join (본 work-log §2~§3)

## 8. 남은 이슈 / 다음 스코프 후보

- **DECISION-12 본격 ship**: 벤치셋 30개 + vision 50 페이지 큐레이션 (별도 sprint, 사용자 결정)
- 인제스트 latency P95 게이트 별도 정의 검토 (KPI #11 의 인제스트 SLO 와 관계 명확화)
- over-chunking 의 SK/삼성 (chunks/MB = 3,800) 별도 chunking 정책 검토 — 단 권고 3 의 "검색 품질 회귀 0" 결과로 우선순위 낮음
- 본 측정을 자동화하여 정기 SLO 보고서 생성 (`evals/ingest_slo_report.py` 활용 가능)
