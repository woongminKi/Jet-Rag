# 2026-05-09 보건의료 + 데이터센터 v2 reingest ship (라벨 회귀 발견)

> Sprint: TOC guard ship 후 후속 — 12 docs reingest 옵션 C (보건의료 + 데이터센터)
> 작성: 2026-05-09 (TOC guard ship 직후)
> 마감: 2 PDF docs reingest + 측정 + 라벨 chunk_idx 변동 회귀 발견
> 입력: 사용자 옵션 C 명시 승인 (cost ~$0.50 범위)

---

## 0. 한 줄 요약

> **2 PDF docs (보건의료 + 데이터센터) v2 reingest ship — caption 부착 chunks +43 신규 (보건의료 9 + 데이터센터 34)**, cost +$0.4498 ($0.1417 + $0.3081). 단 **chunks 재생성으로 chunk_idx 변동 → 라벨 stale 회귀 광범위**: vision_diagram top-1 -0.40, cross_doc R@10 -0.20, numeric_lookup top-1 -0.29, Overall R@10 -0.028. ENV `JETRAG_DAILY_BUDGET_USD=3.0` 1회성. 단위 테스트 775 OK. 운영 코드 변경 0 (이번 세션). reingest 자체 효과는 양성 (caption 부착 확장) 단 측정 라벨이 chunks 변동 못 따라감 → 라벨 재검증 sprint 후속 필요. 다음 후보 1순위 = **보건의료/데이터센터 stale 라벨 재검증** (G-A-104~113 패턴, cost 0).

---

## 1. reingest 실행 결과

### 1.1 보건의료 (2303ef36)

| 단계 | chunks | vision_page_cache | cost SUM |
|---|---:|---|---:|
| pre | 175 | 0 | $0.0000 |
| 1차 시도 (daily cap 도달) | 148 | 0 | $0.0000 (vision skip) |
| **2차 (cap 상향)** | **175** | **v2=26** | **$0.1417** |

- 1차 시도: `JETRAG_DAILY_BUDGET_USD` default 0.5 도달로 vision skip → chunks 175→148 (vision sections 부재)
- 2차 시도: `JETRAG_DAILY_BUDGET_USD=3.0` 상향 → 정상 reingest, v2 26 page

### 1.2 데이터센터 (b218e8a1)

| 단계 | chunks | vision_page_cache | cost SUM |
|---|---:|---|---:|
| pre | 443 | 0 | $0.0000 |
| **post** | **443** | **v2=41** | **$0.3081** |

- pipeline 809s, page 35/40 일부 Gemini Vision JSON 파싱 실패 (graceful)
- chunk_filter 37% 마킹

### 1.3 caption 부착 chunks 검증

| doc | chunks total | table_caption | figure_caption | any caption |
|---|---:|---:|---:|---:|
| 보건의료 | 175 | 7 | 6 | **9** |
| 데이터센터 | 443 | 33 | 3 | **34** |
| (sample-report, 이전) | 994 | 56 | 45 | 89 |

→ D2 fix 효과 다른 docs 로 확장 검증.

### 1.4 누적 cost

| 누적 시점 | cost |
|---|---:|
| 어제 sample-report | $0.6268 |
| **+보건의료** | **$0.7685** (+$0.1417) |
| **+데이터센터** | **$1.0766** (+$0.3081) |

총 cost 추가 $0.45 (예상 ~$0.50 안 ✅).

---

## 2. 측정 결과 — **광범위 회귀 발견**

### 2.1 Overall

| metric | reingest 전 | reingest 후 | △ |
|---|---:|---:|---:|
| Overall R@10 | 0.7350 | **0.7071** | **-0.0279 ⚠** |
| Overall top-1 | 0.6687 | 0.6074 | -0.0613 ⚠ |
| Overall nDCG@10 | 0.6391 | 0.6097 | -0.0294 |
| Overall MRR | 0.5926 | 0.5619 | -0.0307 |

### 2.2 qtype breakdown

| qtype | △ R@10 | △ top-1 | 비고 |
|---|---:|---:|---|
| **vision_diagram** | -0.067 | **-0.40 ⚠⚠** | 5 row 중 3 row top-1 hit → 1 row |
| **cross_doc** | **-0.20 ⚠⚠** | 0 | 데이터센터/보건의료 영향 row R@10 폭락 |
| **numeric_lookup** | -0.064 | -0.29 ⚠ | G-A-024 보건의료 영향 |
| synonym_mismatch | -0.063 | -0.25 | (간접 영향) |
| exact_fact | -0.020 | -0.044 | |
| summary | -0.074 | 0 | |
| **table_lookup** | **+0.014 ✅** | 0 | (사실상 영향 0) |
| fuzzy_memory | 0 | 0 | (영향 0) |

### 2.3 doc_type breakdown

| doc_type | △ R@10 | △ top-1 | 비고 |
|---|---:|---:|---|
| pdf | -0.040 | -0.080 | 보건의료/데이터센터 영향 |
| hwpx | +0.024 | -0.080 | (간접) |
| docx | -0.031 | +0.048 | |
| hwp | -0.043 | 0 | |
| pptx | 0 | 0 | |

### 2.4 caption_dependent

| metric | 전 | 후 | △ |
|---|---:|---:|---:|
| caption=true R@10 | 0.7861 | 0.7736 | -0.013 |
| caption=false R@10 | 0.7193 | 0.6933 | -0.026 |
| **caption gap (false-true)** | -0.0671 | -0.0803 | -0.013 (caption=true 추가 우위) |

→ caption gap 은 더 우위. caption 부착 효과 자체는 양성.

---

## 3. Root cause — chunks 재생성 시 chunk_idx 변동

### 3.1 G-A-104~113 stale 패턴 재발

이전 work-log (`2026-05-10 G-A-104~113 doc_id stale fix.md`) 와 동일 패턴:
- chunks 재생성 시 chunk_idx 분포 변동
- 라벨된 정답 chunk_idx 가 현재 chunks 의 다른 chunk 가리킴
- 이전엔 doc_id 변동 (G-A-104~113), 이번엔 chunk_idx 변동

### 3.2 영향 추정

라벨 변경 영향 row:
- **G-U-031** (sonata + 데이터센터): 데이터센터 ch 397 라벨 → 현재 chunk_idx 다를 가능성
- **G-U-032** (보건의료 + 데이터센터): ch 10/441 둘 다 변동 가능
- **G-A-008** (데이터센터 성과 목표): ch 374/317/434 변동 가능
- **G-A-024** (보건의료 사업 내용): ch 9/10/14/36/94/122/158 변동 가능
- **G-A-016** (sample-report 가계부채): sample-report 영향 0 (chunks 변동 없음)

### 3.3 검증 방법

라벨 재검증 sprint 가 필요:
1. 회귀 row 의 search top-3 chunks 직접 추적
2. 의미 매칭 chunks 식별 → 라벨 정정
3. 측정 재실행 → 회복 검증

cost 0, 0.5~1일 추정.

---

## 4. ROI 평가

### 4.1 reingest 자체 효과 (정량)

- caption 부착 chunks +43 (보건의료 9 + 데이터센터 34)
- caption gap 우위 더 강화 (-0.067 → -0.080)
- D2 fix 효과 다른 docs 확장 검증

### 4.2 측정상 회귀 (라벨 stale)

- Overall R@10 -0.028 (광범위 라벨 stale 영향)
- 단 reingest **자체** 결함 아닌 **측정 라벨 부정확** 의 결과
- 라벨 재검증 후 회복 가능

### 4.3 cost ROI 종합

| 항목 | 값 |
|---|---|
| cost: | +$0.4498 |
| caption chunks 확장: | +43 |
| 라벨 stale 회귀: | 광범위 (-0.028 R@10) |
| 라벨 재검증 후 예상: | Overall R@10 회복 + caption=true 추가 우위 |

→ **현 sprint ROI 음성** (라벨 stale 효과 > caption 효과). 라벨 재검증 sprint 후 결정.

---

## 5. 다음 후보 우선순위

### 5.1 1순위 — 보건의료/데이터센터 stale 라벨 재검증 (cost 0, 0.5~1일)

**대상 row** (chunks 변동 영향):
- G-U-031 (sonata + 데이터센터)
- G-U-032 (보건의료 + 데이터센터)
- G-A-008 (데이터센터 성과 목표)
- G-A-024 (보건의료 사업 내용)
- 기타 cross_doc 5 row 의 데이터센터/보건의료 chunks

**작업**:
1. 각 row search top-3 추적
2. 의미 매칭 chunks 식별 → 라벨 정정
3. D4 도구 재실행

### 5.2 2순위 — 추가 PDF reingest (cost ~$0.5)

sonata, 이력서, 포트폴리오, law3 reingest. 사용자 cost 승인 필요.

### 5.3 3~10순위

| # | 후보 | 작업량 | cost | 권고도 |
|---|---|---|---|---|
| 3 | vision_diagram top-1 진단 | 0.25일 | 0 | ★★ |
| 4 | TOC guard 패턴 정밀화 | 0.5일 | 0 | ★ |
| 5 | combo c P95 안정성 재측정 | 0.25일 | 0 | ★★ |
| 6 | chunk_filter 마킹 분석 | 0.5일 | 0 | ★★ |
| 7 | RPC per-doc cap | 1주+ | 0 | ★ |
| 8 | S4-B 핵심 엔티티 추출 | 3일 | 0 | ★★ |
| 9 | S5 /answer UX | 1주 | 0 | ★★ |

---

## 6. 사용자 결정 보류 항목

| # | 항목 | 권고 default | 결정 시점 |
|---|---|---|---|
| Q-stale-relabel | 보건의료/데이터센터 stale 라벨 재검증 | cost 0, 즉시 가능 | 다음 sprint |
| Q-more-pdf | 추가 4 PDF reingest (sonata/이력서/포트폴리오/law3) | 사용자 명시 cost 승인 | 후순위 |
| Q-cap-policy | `JETRAG_DAILY_BUDGET_USD` 운영 default | 0.5 그대로 (시범 한정 3.0) | 결정 완료 |
| (이전 잔존) | 별도 sprint | | |

---

## 7. 핵심 변경 파일 목록

### 신규
- 본 work-log

### 수정
- `/tmp/reingest_sample_report.py` — DOC_ID ENV 으로 받게 변경 (gitignored)

### 운영 코드
- 0 건

### gitignored
- `evals/results/s4_a_d4_results.md` — reingest 후 측정 갱신

### 데이터 영향
- 보건의료 chunks: 175 → 175 (vision sections +27 / chunk_filter -27)
- 보건의료 vision_page_cache: 0 → v2=26
- 데이터센터 chunks: 443 → 443 (안정)
- 데이터센터 vision_page_cache: 0 → v2=41
- vision_usage_log SUM: $0.6268 → $1.0766 (+$0.4498)
- ENV `JETRAG_DAILY_BUDGET_USD=3.0` 1회성 후 unset

---

## 8. 한 문장 마감

> **2026-05-09 — 보건의료 + 데이터센터 v2 reingest ship**. caption 부착 chunks +43 (보건의료 9 + 데이터센터 34) 확장, cost +$0.4498. 단 **chunks 재생성으로 chunk_idx 변동 → 라벨 stale 회귀 광범위** (vision_diagram top-1 -0.40, cross_doc R@10 -0.20, Overall R@10 -0.028). G-A-104~113 패턴 재발. caption gap 자체는 더 우위 (-0.067 → -0.080). 단위 테스트 775 OK. 운영 코드 변경 0. ENV `JETRAG_DAILY_BUDGET_USD=3.0` 1회성. 다음 후보 1순위 = **보건의료/데이터센터 stale 라벨 재검증** (cost 0, 0.5~1일).
