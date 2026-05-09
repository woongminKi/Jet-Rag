# 2026-05-09 fuzzy_memory G-U-100~103 라벨 재검증 ship

> Sprint: doc_embedding RRF ablation 후 후속 — fuzzy_memory 신규 라벨 정확도 재검증
> 작성: 2026-05-09 (doc_embedding RRF 효과 0 확인 직후)
> 마감: 4 row search 응답 추적 + 3 row 라벨 정정 + D4 도구 재실행
> 입력: 표본 확장 work-log §2.3 의 fuzzy_memory top-1 -0.25 회귀 + 회귀 진단 work-log "noise 결론"

---

## 0. 한 줄 요약

> **fuzzy_memory G-U-100~103 라벨 재검증 ship — 3 row 라벨 정정**. G-U-100 한마음 ch 11 (이용료 징수) 누락, G-U-101 law2 ch 3/19 (합의해지 의의/본문) 누락, G-U-103 law3 ch 18 (변제 충당 핵심) 누락. acceptable_chunks 확장. 결과: **fuzzy_memory R@10 0.7292 → 0.7542 (+0.025), top-1 0.5000 → 0.7500 (+0.250, 50%↑) 🚀**, MRR +0.089. Overall R@10 0.7264 → 0.7276 (+0.0012), top-1 0.6196 → 0.6319 (+0.0123). 이전 회귀 진단 work-log 결론 "noise" 정정 — **라벨 부정확이 직접 원인**. 단위 테스트 775 OK / 회귀 0. cost 0. 운영 코드 변경 0. 다음 후보 1순위 = G-U-017 query 정정 (cost 0).

---

## 1. 진단 결과

### 1.1 search 응답 추적 (RRF-only)

각 row 의 search top-3 + chunks text 직접 확인:

| row | query | top-1 chunk | top-1 text 발췌 | 라벨 정확도 |
|---|---|---:|---|---|
| **G-U-100** | 한마음 회비 | ch 10 | "제1조(목적) 이 내규는..." (회비 무관) | ❌ |
| **G-U-101** | 계약 합의해지 | ch 3 | "**계약의 합의해지의 의의**..." (직접 매칭) | ❌ ch 3 누락 |
| **G-U-102** | 모노블록 브레이크 | ch 57 | "모노블록 브레이크 19인치 경량 휠..." (정답) | ✅ 정확 |
| **G-U-103** | 채무자 변제 충당 | ch 18 | "**변제에 충당된다**..." (직접 매칭) | ❌ ch 18 누락 |

### 1.2 누락된 핵심 chunks 식별

- **G-U-100 한마음 ch 11**: "제4조(이용료의 징수) 체육관의 이용료는 별표 1과 같이..." → 회비 핵심 매칭
- **G-U-101 law2 ch 3**: "【판시사항】 [1] 계약의 합의해지의 의의 및 그 성립 요건..." → 합의해지 핵심
- **G-U-101 law2 ch 19**: "가) 계약의 합의해지는 계속적 채권채무관계에서..." → 합의해지 본문
- **G-U-103 law3 ch 18**: "원심은 판시와 같은 이유로... 변제에 충당된다고 판단" → 변제 충당 직접 매칭

→ 정답 chunks 가 search top-3 에 진입했는데 **라벨에 없어서 측정상 miss**. fuzzy_memory top-1 -0.25 회귀의 직접 원인.

---

## 2. 라벨 정정 내역

| row | 변경 | 이유 |
|---|---|---|
| G-U-100 | acceptable_chunks: `19,50` → `11,19,50` | ch 11 (이용료 징수 조항) 추가 |
| G-U-101 | acceptable_chunks: `''` → `3,19` | ch 3 (합의해지 판시), ch 19 (합의해지 본문) 추가 |
| G-U-102 | (변경 없음) | 라벨 정확 ✅ |
| G-U-103 | acceptable_chunks: `5,21` → `5,21,18` | ch 18 (변제 충당 직접 매칭) 추가 |

---

## 3. 측정 비교 (RRF-only baseline, golden v2 172 row)

### 3.1 Overall

| metric | 정정 전 | 정정 후 | △ |
|---|---:|---:|---:|
| Overall R@10 | 0.7264 | **0.7276** | +0.0012 |
| **Overall top-1** | 0.6196 | **0.6319** | **+0.0123 ✅** |
| Overall nDCG@10 | 0.6239 | 0.6283 | +0.0044 |
| Overall MRR | 0.5841 | 0.5885 | +0.0044 |

### 3.2 fuzzy_memory qtype (직접 KPI)

| metric | 정정 전 | 정정 후 | △ |
|---|---:|---:|---:|
| **R@10** | 0.7292 | **0.7542** | **+0.025 ✅** |
| **top-1** | 0.5000 | **0.7500** | **+0.250 (50%↑) 🚀** |
| nDCG@10 | 0.6090 | 0.6972 | +0.088 |
| MRR | 0.6198 | 0.7083 | +0.089 |

→ fuzzy_memory top-1 50% 회복.

### 3.3 다른 qtype (안정성 검증)

| qtype | 정정 전 R@10 | 정정 후 R@10 | △ |
|---|---:|---:|---:|
| exact_fact | 0.7387 | 0.7387 | 0 |
| table_lookup | 0.7215 | 0.7215 | 0 |
| cross_doc | 0.4738 | 0.4738 | 0 |
| vision_diagram | 0.9333 | 0.9333 | 0 |
| 기타 | (변동 없음) | | |

→ fuzzy_memory 외 qtype 영향 0 (라벨 변경 scope 한정).

---

## 4. 이전 회귀 진단 결론 정정

### 4.1 "noise" 결론 → "라벨 부정확이 직접 원인"

회귀 진단 work-log (`2026-05-09 table_lookup fuzzy_memory 회귀 진단.md`) §3 의 결론:
- table_lookup top-1 -0.167 = noise
- fuzzy_memory R@10 -0.083 = G-U-017 multi-doc 매칭 fail

당시 진단은 **신규 4 row 의 top-1 분포 분석을 안 했음**. 이번 진단으로:
- G-U-100/101/103 의 라벨 부정확 (정답 chunks 누락)
- 이게 fuzzy_memory top-1 -0.25 회귀의 **진짜 root cause**

### 4.2 신규 라벨링 시 검증 절차 강화 권고

향후 골든셋 확장 시:
1. row 추가 후 search top-3 chunks 직접 확인
2. 정답 chunk 라벨 vs search top-3 chunk 비교
3. 누락된 핵심 chunks 발견 시 acceptable 추가

---

## 5. ROI 검증

### 5.1 cost ROI

- cost: 0 (라벨링 + 검증 작업만)
- gain: fuzzy_memory top-1 +0.25, R@10 +0.025, Overall top-1 +0.0123
- 단위 ROI: 무한대 (cost 0)

### 5.2 누적 효과 비교 (오늘 라벨 정정 sprint 들)

| sprint | 변경 row | cross_doc / fuzzy_memory R@10 | top-1 |
|---|---|---|---|
| 1차 cross_doc 재검증 (4 row) | G-U-015/018/031/032 | cross_doc 0.2917 → 0.4738 | 0.250 → 0.500 |
| **2차 fuzzy_memory 재검증 (3 row)** | G-U-100/101/103 | fuzzy_memory 0.7292 → 0.7542 | 0.500 → 0.750 |

→ minority qtype 의 라벨 정확도가 R@10/top-1 의 핵심 driver 검증.

---

## 6. 다음 후보 우선순위

### 6.1 1순위 — G-U-017 query 정정 또는 row 제외 (cost 0, 0.25일)

임대차 자료 자체 부재 (multi-doc 진단에서 발견). query 정정 또는 negative=true 처리.

### 6.2 2순위 — caption=true top-1 -0.0589 진단 (cost 0)

caption=true row 28건의 top-1 위치 분석. 라벨 누락 가능성 검증.

### 6.3 3순위 — 12 docs v2 prompt reingest (cost ~$0.5~1.5)

D2 fix 효과를 sample-report 외 12 docs 로 확장. 사용자 cost 승인 필요.

### 6.4 4~10순위

| # | 후보 | 작업량 | cost | 권고도 |
|---|---|---|---|---|
| 4 | numeric_lookup R@10 0.5295 진단 | 0.5일 | 0 | ★★ |
| 5 | combo c P95 안정성 재측정 | 0.25일 | 0 | ★★ |
| 6 | chunk_filter 45.5% 마킹 분석 | 0.5일 | 0 | ★★ |
| 7 | budget_guard cache hit 무료 인식 | 0.5일 | 0 | ★ |
| 8 | RPC per-doc cap (큰 fix) | 1주+ | 0 | ★ |
| 9 | S4-B 핵심 엔티티 추출 | 3일 | 0 | ★★ |
| 10 | S5 /answer UX | 1주 | 0 | ★★ |

---

## 7. 사용자 결정 보류 항목

| # | 항목 | 권고 default | 결정 시점 |
|---|---|---|---|
| Q-G-U-017 | G-U-017 query 정정 또는 제외 | 임대차 자료 부재 → query 변경 | 다음 sprint |
| Q-caption-top1 | caption=true top-1 -0.0589 진단 | 라벨 누락 가능성 검증 | 후순위 |
| Q-other-docs | 12 docs v2 prompt reingest | 사용자 명시 cost 승인 | 후순위 |
| Q-doc-emb-rrf | doc_embedding RRF 운영 default | **OFF 유지** — 효과 0 검증 | 결정 완료 |
| (이전 잔존) | 별도 sprint | | |

---

## 8. 핵심 변경 파일 목록

### 신규
- 본 work-log
- `/tmp/diagnose_fuzzy_new.py` — 진단 스크립트 (gitignored, 일회성)

### 수정
- `evals/golden_v2.csv` — 3 row acceptable_chunks 확장 (G-U-100/101/103)

### 운영 코드
- 0 건

### gitignored
- `evals/results/s4_a_d4_results.md` — 라벨 정정 후 측정 갱신
- `evals/results/s4_a_d4_results_rrf_on.md` + `s4_a_d4_w5_k10.md` — doc_embedding RRF ablation 결과 (효과 0)

### 데이터 영향
- 0 건

---

## 9. 한 문장 마감

> **2026-05-09 — fuzzy_memory G-U-100~103 라벨 재검증 ship**. 3 row acceptable_chunks 확장 — 한마음 ch 11 (이용료), law2 ch 3/19 (합의해지), law3 ch 18 (변제 충당) 누락 핵심 정답 추가. **fuzzy_memory R@10 +0.025 / top-1 +0.250 (50%↑) 🚀, MRR +0.089**. Overall R@10 +0.0012 / top-1 +0.0123. 이전 회귀 진단 "noise" 결론 정정 — **라벨 부정확이 직접 원인**. 부수 결과: doc_embedding RRF ablation (w=2,5 / k=10) 효과 0 검증, 운영 default OFF 유지. 단위 테스트 775 OK / 회귀 0. cost 0. 다음 후보 1순위 = G-U-017 query 정정.
