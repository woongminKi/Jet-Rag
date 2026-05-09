# 2026-05-09 추가 4 PDF reingest ship (law3/포트폴리오/이력서/sonata)

> Sprint: 보건의료+데이터센터 reingest 후속 — 추가 4 PDF reingest 옵션 진입
> 작성: 2026-05-09 (보건의료+데이터센터 ship 직후)
> 마감: 4 PDF 순차 reingest + caption 부착 검증 + 측정 + 광범위 라벨 stale 회귀 확인
> 입력: 사용자 옵션 2 명시 승인 (cost ~$0.5)

---

## 0. 한 줄 요약

> **추가 4 PDF reingest ship — caption 부착 +36 chunks (포트폴리오 11 + sonata 25)**, cost +$0.4223 (law3 0 + 포트폴리오 0.147 + 이력서 0.006 + sonata 0.269). law3/이력서는 needs_vision page 부재로 vision call 거의 안 됨. **vision_diagram top-1 +0.20 (0.2 → 0.4) 회복** (sonata caption 효과). 단 **chunk_idx 변동 광범위 라벨 stale 회귀 추가**: cross_doc R@10 0.3419 → 0.2286 (-0.113), fuzzy_memory R@10 -0.048, Overall R@10 0.7071 → 0.6897. ENV `JETRAG_DAILY_BUDGET_USD=3.0` 1회성. 단위 테스트 775 OK. 운영 코드 변경 0. **누적 13 docs caption 부착 chunks 168** (sample-report 89 + 보건의료 9 + 데이터센터 34 + 포트폴리오 11 + sonata 25). 다음 후보 1순위 = **광범위 라벨 stale 재검증** (cost 0, 1~2일).

---

## 1. reingest 실행 결과 (4 PDF 순차)

| doc | chunks (전→후) | v2 cache | cost | caption chunks |
|---|---:|---:|---:|---:|
| **law3** | 28 → 26 | 0 | $0 | 0 (vision call 0) |
| **포트폴리오** | 77 → 79 | 15 | $0.1468 | **11** |
| **이력서** | 120 → 103 | 2 | $0.0063 | 0 (caption 추출 0) |
| **sonata** | 134 → 129 | 27 | $0.2692 | **25** |
| **합계** | — | 44 | **$0.4223** | **+36 chunks** |

→ 작은 doc (law3/이력서) 은 needs_vision page 부재로 vision call 거의 안 함. 큰 doc (sonata) 만 의미 있는 caption 부착.

---

## 2. 누적 13 docs caption 부착 chunks 분포

| doc | chunks total | caption 부착 | 비율 |
|---|---:|---:|---:|
| sample-report | 994 | 89 | 9.0% |
| 데이터센터 | 443 | 34 | 7.7% |
| sonata | 129 | 25 | 19.4% |
| 포트폴리오 | 79 | 11 | 13.9% |
| 보건의료 | 175 | 9 | 5.1% |
| 이력서 | 103 | 0 | 0% |
| law3 | 26 | 0 | 0% |
| 기타 6 docs (hwpx/hwp/docx/pptx) | — | 0 | (vision 미적용) |
| **합계** | — | **168** | — |

---

## 3. 측정 결과 (RRF-only baseline)

### 3.1 Overall

| metric | 보건의료+데이터센터 직후 | +4 PDF 후 | △ |
|---|---:|---:|---:|
| Overall R@10 | 0.7071 | **0.6897** | **-0.0174 ⚠** |
| Overall top-1 | 0.6074 | **0.6258** | **+0.0184 ✅** |
| Overall nDCG@10 | 0.6097 | 0.6056 | -0.004 |
| Overall MRR | 0.5619 | 0.5581 | -0.004 |

### 3.2 qtype breakdown

| qtype | △ R@10 | △ top-1 | 비고 |
|---|---:|---:|---|
| **vision_diagram** | -0.067 | **+0.20 ✅** | sonata caption chunks 부착 효과 |
| **cross_doc** | **-0.113 ⚠⚠** | 0 | sonata 영향 (G-U-031) |
| **fuzzy_memory** | -0.048 | 0 | sonata+이력서 영향 (G-U-001/102/024) |
| **exact_fact** | -0.020 | -0.009 | 광범위 chunk_idx 변동 |
| table_lookup | -0.014 | 0 | (작음) |
| **summary** | +0.056 | +0.111 | (의외) |
| 기타 | (변동) | | |

### 3.3 caption_dependent gap

| metric | 전 | 후 | △ |
|---|---:|---:|---:|
| caption=true R@10 | 0.7736 | 0.7384 | -0.035 |
| caption=false R@10 | 0.6933 | 0.6796 | -0.014 |
| **gap (false-true)** | **-0.0803** | **-0.0589** | gap 축소 (caption=true 우위 약화) |

→ caption=true R@10 도 라벨 stale 회귀로 떨어짐. caption gap 축소.

### 3.4 doc_type breakdown

| doc_type | △ R@10 | 비고 |
|---|---:|---|
| **pdf** | **-0.036 ⚠** | sonata + 이력서 + 포트폴리오 + 보건의료 + 데이터센터 광범위 영향 |
| hwpx | +0.005 | (간접) |
| docx | +0.005 | (간접) |
| hwp | -0.006 | |
| pptx | 0 | |

---

## 4. 누적 효과 (오늘 reingest 2 sprint)

### 4.1 caption gap (직접 KPI)

| 시점 | caption=true R@10 | caption=false R@10 | gap (false-true) |
|---|---:|---:|---:|
| reingest 전 (numeric fix 후) | 0.7795 | 0.7099 | -0.0696 |
| 보건의료+데이터센터 후 | 0.7736 | 0.6933 | -0.0803 |
| **+4 PDF 후 (현재)** | **0.7384** | 0.6796 | -0.0589 |

→ caption=true 우위 자체는 유지. 단 라벨 stale 효과로 caption=true R@10 절대값 떨어짐.

### 4.2 cost ROI 종합

| 항목 | 값 |
|---|---|
| 누적 cost | +$0.8721 (오늘 2 sprint) |
| caption 부착 chunks | +79 (보건의료 9 + 데이터센터 34 + 포트폴리오 11 + sonata 25) |
| Overall R@10 | 0.7350 → 0.6897 (-0.045) |
| cross_doc R@10 | 0.5457 → 0.2286 (-0.32) |
| vision_diagram top-1 | 0.6 → 0.4 (-0.20, sonata 회복으로 부분 만회) |

→ **현재 측정 ROI 음성**. 라벨 재검증 후 회복 가능.

---

## 5. Root cause 동일 — chunks 재생성 시 chunk_idx 변동

이전 sprint (보건의료+데이터센터) work-log §3 와 동일:
- chunks 재생성 시 chunk_idx 분포 변동
- 라벨된 정답 chunk_idx 가 현재 chunks 의 다른 chunk 가리킴
- G-A-104~113 stale 패턴 재발

**확장 영향**:
- sonata 변동 → G-U-001 (시트 가죽), G-U-102 (모노블록), G-U-031 (sonata+데이터센터) 영향
- 포트폴리오 변동 → G-A-107/111 (SAP) 영향
- 이력서 변동 → G-U-024 (이력서 프로젝트 경력) 영향
- 보건의료 변동 → G-U-032, G-A-024 영향
- 데이터센터 변동 → G-A-008, G-U-031, G-U-032 영향

---

## 6. 다음 후보 우선순위

### 6.1 1순위 — 광범위 라벨 stale 재검증 (cost 0, 1~2일)

**대상 row** (chunks 변동 영향, ~10+ row):
- cross_doc 5 row 모두 (sonata/데이터센터/보건의료 영향)
- fuzzy_memory 일부 (G-U-001/102/024)
- exact_fact 일부 (G-A-107/111 등 SAP 매칭)
- numeric_lookup G-A-024 (보건의료)
- vision_diagram 일부 (sample-report 외)

**작업**:
1. 각 row search top-3 추적
2. 의미 매칭 chunks 식별 → 라벨 정정
3. D4 도구 재실행 → 회복 검증

### 6.2 2순위 — vision_diagram top-1 +0.20 회복 분석

sonata caption 부착이 vision_diagram top-1 회복시킴 → 원인 분석으로 어떤 chunks 가 의미 매칭됐는지 확인.

### 6.3 3~10순위

| # | 후보 | 작업량 | cost | 권고도 |
|---|---|---|---|---|
| 3 | TOC guard 패턴 정밀화 | 0.5일 | 0 | ★ |
| 4 | combo c P95 안정성 재측정 | 0.25일 | 0 | ★★ |
| 5 | chunk_filter 마킹 분석 | 0.5일 | 0 | ★★ |
| 6 | budget_guard cache hit 무료 인식 | 0.5일 | 0 | ★ |
| 7 | RPC per-doc cap | 1주+ | 0 | ★ |
| 8 | S4-B 핵심 엔티티 추출 | 3일 | 0 | ★★ |
| 9 | S5 /answer UX | 1주 | 0 | ★★ |

---

## 7. 사용자 결정 보류 항목

| # | 항목 | 권고 default | 결정 시점 |
|---|---|---|---|
| Q-stale-relabel | 광범위 라벨 stale 재검증 | cost 0, 1~2일 | 다음 sprint |
| Q-cap-policy | `JETRAG_DAILY_BUDGET_USD` 운영 default | 0.5 그대로 (시범 한정 3.0) | 결정 완료 |
| (이전 잔존) | 별도 sprint | | |

---

## 8. 핵심 변경 파일 목록

### 신규
- 본 work-log

### 수정
- 0 건 (운영 코드 / 골든셋 / 단위 테스트 모두 변경 없음)

### gitignored
- `evals/results/s4_a_d4_results.md` — 4 PDF reingest 후 측정 갱신

### 데이터 영향
- law3 chunks: 28 → 26
- 포트폴리오 chunks: 77 → 79
- 이력서 chunks: 120 → 103
- sonata chunks: 134 → 129
- vision_page_cache 추가: v2=44 (포트폴리오 15 + 이력서 2 + sonata 27, law3 0)
- vision_usage_log SUM 누적: ~$1.50 (오늘 모든 sprint)

---

## 9. 한 문장 마감

> **2026-05-09 — 추가 4 PDF reingest ship**. caption 부착 +36 chunks (포트폴리오 11 + sonata 25), cost +$0.4223. law3/이력서 needs_vision page 부재로 vision call 거의 안 됨. **vision_diagram top-1 +0.20 (sonata 효과) 회복**. 단 **chunk_idx 변동 광범위 라벨 stale 회귀**: cross_doc R@10 -0.113, fuzzy_memory -0.048, Overall R@10 -0.0174. **누적 13 docs caption 부착 chunks 168**. 단위 테스트 775 OK. 운영 코드 변경 0. 다음 후보 1순위 = **광범위 라벨 stale 재검증** (cost 0, 1~2일).
