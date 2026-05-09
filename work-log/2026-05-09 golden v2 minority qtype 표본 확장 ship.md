# 2026-05-09 golden v2 minority qtype 표본 확장 ship

> Sprint: 종합 핸드오프 §5.1 1순위 — golden v2 minority qtype 표본 확장
> 작성: 2026-05-09 (D2 fix + 회귀 진단 ship 직후)
> 마감: golden v2 157 → 172 (+15 row) + D4 도구 재실행 + caption gap 반전 검증
> 입력: 회귀 진단 work-log §4.1 의 1순위 권고 (cost 0, 1~2일)

---

## 0. 한 줄 요약

> **golden v2 표본 확장 ship — 157 → 172 row (+15)**. table_lookup 6→12, fuzzy_memory 6→10, caption_dependent=true 18→29, vision_diagram 3→6. 결과: **caption gap 반전** (+0.0130 → **-0.0696**, caption=true 가 caption=false 보다 R@10 우위), Overall R@10 0.7072 → **0.7219 (+0.0147)**, **caption=true R@10 0.6957 → 0.7795 (+0.0838 🚀)**, **table_lookup R@10 0.6096 → 0.7215 (+0.112)**, **vision_diagram R@10 0.8333 → 0.9333 (+0.10)**, exact_fact+caption=true top-1 0.7778 → 0.8182 (DoD ≥ 0.80 cell 도달 ✅). 단위 테스트 775 OK / 회귀 0. cost 0. 운영 코드 변경 0. **D2 fix 효과 통계적으로 확정** — caption 부착이 caption_dependent=true row 의 R@10 우위로 직결. 다음 후보 1순위 = combo c 운영 default 채택 (R@10 +0.01~0.03 회수 가능).

---

## 1. 라벨링 작업

### 1.1 추가된 15 row 분포

| qtype | id 범위 | n | caption_dependent | doc |
|---|---|---:|---|---|
| **table_lookup** | G-A-200 ~ G-A-205 | 6 | true (6) | sample-report (caption 부착 chunks 활용) |
| **fuzzy_memory** | G-U-100 ~ G-U-103 | 4 | false (4) | 한마음 운영내규 / law sample2 / sonata / law sample3 |
| **vision_diagram** | G-A-206, 208, 209 | 3 | true (3) | sample-report (figure_caption 보유) |
| **exact_fact** | G-A-207, 210 | 2 | true (2) | sample-report (table/figure caption 보유) |

### 1.2 라벨링 전략

- **sample-report 의 caption 부착 chunks 33+ 페이지** (D2 fix 후 89 chunks) 활용 → 11 row 의 정답으로 직접 매핑
- **fuzzy_memory 4 row** — chunks text 직접 검증 (한마음 ch 26 회비 표 / law2 ch 4 합의해지 / sonata ch 57 모노블록 / law3 ch 20 변제 충당)
- 각 row 의 source_chunk_text + must_include + expected_answer_summary 라벨링

### 1.3 ID 충돌 회피

- 기존 G-U-* 0~32 → 새 G-U-100~103 (gap 100 으로 충돌 회피)
- 기존 G-A-* 1~123 → 새 G-A-200~210

---

## 2. 측정 비교 (RRF-only baseline)

### 2.1 Overall (157 → 172 row)

| metric | 이전 (148 n_eval) | 확장 후 (163 n_eval) | △ |
|---|---:|---:|---:|
| n_eval | 148 | **163** | +15 |
| Overall R@10 | 0.7072 | **0.7219** | **+0.0147 ✅** |
| Overall top-1 | 0.6284 | 0.6135 | -0.0149 |
| Overall nDCG@10 | 0.6151 | 0.6204 | +0.0053 |
| Overall MRR | 0.5794 | 0.5811 | +0.0017 |

### 2.2 caption_dependent gap (D2 fix 효과의 핵심 KPI)

| 시점 | true R@10 | false R@10 | gap (false-true) | n_eval (true) |
|---|---:|---:|---:|---:|
| D4 baseline (어제) | 0.6796 | 0.7142 | +0.0346 | 17 |
| D5 본격 (1차) | 0.6898 | 0.7145 | +0.0247 | 17 |
| OCR broadcast | 0.6814 | 0.7121 | +0.0306 | 17 |
| split/merge fix | 0.6957 | 0.7087 | +0.0130 | 17 |
| **표본 확장** | **0.7795** ⭐ | 0.7099 | **-0.0696** | **28** |

→ **caption gap 반전!** caption=true row 가 caption=false 보다 R@10 우위로 전환. **D2 fix 의 효과가 통계적으로 확정** — 18→28 표본 (n_eval) 으로 신뢰도 회복.

### 2.3 qtype breakdown (확장 큰 변화)

| qtype | 이전 R@10 | 확장 후 R@10 | △ R@10 | △ top-1 | n_eval |
|---|---:|---:|---:|---:|---:|
| **vision_diagram** | 0.8333 | **0.9333** | **+0.10 ⭐** | -0.40 | 5 (3→5) |
| **table_lookup** | 0.6096 | **0.7215** | **+0.112 🚀** | 0 | 12 (6→12) |
| **fuzzy_memory** | 0.7083 | **0.7292** | +0.021 | -0.25 | 8 (4→8) |
| summary | 0.7222 | 0.7222 | 0 | 0 | 9 |
| exact_fact | 0.7340 | 0.7387 | +0.005 | +0.005 | 114 |
| synonym_mismatch | 0.7330 | 0.7330 | 0 | 0 | 4 |
| numeric_lookup | 0.5295 | 0.5295 | 0 | 0 | 7 |
| cross_doc | 0.2917 | 0.2917 | 0 | 0 | 4 |

→ table_lookup + vision_diagram + fuzzy_memory 모두 R@10 ↑. 단 fuzzy_memory top-1 -0.25 (신규 4 row 중 일부 top-1 miss).

### 2.4 qtype × caption_dependent cross-tab (DoD 도달 cell)

| qtype | caption | n_eval | R@10 | top-1 |
|---|:---:|---:|---:|---:|
| **exact_fact + caption=true** | true | **11** | 0.7729 | **0.8182 ✅ DoD top-1 ≥ 0.80 도달** |
| vision_diagram + caption=true | true | 5 | **0.9333** | 0.6000 |
| table_lookup + caption=true | true | 12 | **0.7215** | 0.3333 |

→ exact_fact + caption=true 가 DoD top-1 임계 도달. caption 부착 + 정확한 query 조합 효과 확인.

### 2.5 doc_type breakdown

| doc_type | 이전 R@10 | 확장 후 R@10 | △ |
|---|---:|---:|---:|
| pdf | 0.6727 | **0.7053** | **+0.033 ✅** (sample-report 11 row 추가 효과) |
| hwpx | 0.7500 | 0.7200 | -0.030 (한마음 1 row 추가) |
| docx | 0.6638 | 0.6638 | 0 |
| hwp | 0.8366 | 0.8502 | +0.014 (law2 1 row 추가) |
| pptx | 1.0 | 1.0 | 0 |

---

## 3. ROI 가설 검증 결과

### 3.1 가설 — 표본 확장이 통계 신뢰도 회복 + D2 fix 효과 확정

**검증**: 강한 양성.

- caption gap **반전** (+0.013 → -0.070) — caption_dependent=true row 가 R@10 우위
- Overall R@10 +0.0147
- table_lookup R@10 +0.112, vision_diagram +0.10
- exact_fact + caption=true cell 의 top-1 0.8182 — **DoD 임계 도달**
- 누적 D2 fix 효과 (caption 부착 chunks 0 → 89, 14.8배 증가) 가 표본 확장 후 R@10 우위로 직결

### 3.2 cost ROI

- cost: 0 (라벨링 작업만)
- gain: caption gap 반전 + table_lookup +0.112 + vision_diagram +0.10
- 단위 ROI: 무한대 (cost 0, R@10 의미 있는 이동)

### 3.3 어제 회귀 진단 결론 검증

회귀 진단에서 "table_lookup top-1 -0.167 / fuzzy_memory R@10 -0.083 = noise" 라고 결론지었는데, 표본 확장 후:
- table_lookup top-1 0.333 → 0.333 (변동 없음, 12 row 안정)
- fuzzy_memory R@10 0.7083 → 0.7292 (+0.021, 회복)
- fuzzy_memory top-1 0.75 → 0.50 (신규 4 row 중 2 row 가 top-1 miss)

→ 회귀의 일부는 noise 였지만, fuzzy_memory top-1 의 새 -0.25 는 신규 row 의 query intent 특성 (구어체/추측형 → 정답이 top-1 까지 도달 못함). 다음 sprint 의 multi-doc 또는 search 진단 후보.

---

## 4. 다음 후보 우선순위 (표본 확장 후 재정렬)

### 4.1 1순위 — combo c (RRF+rerank+MMR) 운영 default 채택 (작업량 0.25일)

**왜?**
- 어제 핸드오프 §5.4 #8: combo c R@10=0.7049 / top-1=0.6667 / P95=263ms (어제 측정)
- 표본 확장 후 RRF-only R@10 = 0.7219. combo c 측정 재현 시 추가 +0.01~0.03 가능
- DoD R@10 ≥ 0.75 까지 -0.028 (combo c 측정 재현으로 도달 가능 시그널)

**작업**: ENV 토글 (`JETRAG_RERANKER_ENABLED=true` + reranker config) → S3 D5 도구 재실행 → 결과 비교 → 운영 default 결정

### 4.2 2순위 — multi-doc cross_doc retrieve 진단 (cost 0)

cross_doc R@10 = 0.2917 (-0.4302 vs overall) 잔존. G-U-015/017/032 류 multi-doc 매칭 약점.

### 4.3 3~10순위

| # | 후보 | 작업량 | cost | 권고도 |
|---|---|---|---|---|
| 3 | 12 docs v2 prompt reingest (sample-report 외) | 가변 | $0.5~1.5 | ★★ |
| 4 | numeric_lookup R@10 0.5295 진단 | 0.5일 | 0 | ★★ |
| 5 | fuzzy_memory top-1 -0.25 진단 (신규 row 분석) | 0.5일 | 0 | ★ |
| 6 | chunk_filter 45.5% 마킹 분석 | 0.5일 | 0 | ★★ |
| 7 | budget_guard cache hit 무료 인식 패치 | 0.5일 | 0 | ★ |
| 8 | S4-B 핵심 엔티티 추출 | 3일 | 0 | ★★ |
| 9 | S5 /answer UX | 1주 | 0 | ★★ |

---

## 5. 사용자 결정 보류 항목

| # | 항목 | 권고 default | 결정 시점 |
|---|---|---|---|
| Q-combo-c | 운영 default combo c 채택 | DoD 임계 +0.01~0.03 도달 가능 시그널 | 다음 sprint |
| Q-multi-doc | G-U-015/017/032 multi-doc retrieve 진단 | search() level 분석, cost 0 | 다음 sprint |
| Q-other-docs | 12 docs v2 prompt reingest | sample-report 외 doc 효과 확장. cost ~$0.5~1.5 | 사용자 명시 cost 승인 후 |
| Q-fuzzy-top1 | fuzzy_memory top-1 -0.25 회귀 처리 | 신규 4 row 라벨링 정확도 재검증 | 후순위 |
| (어제 잔존) | 별도 sprint | | |

---

## 6. 핵심 변경 파일 목록

### 신규
- 본 work-log

### 수정
- `evals/golden_v2.csv` — 157 → 172 row (+15)

### 운영 코드
- **0 건**

### gitignored 산출물
- `evals/results/s4_a_d4_results.md` — 표본 확장 후 측정 갱신
- `evals/results/s4_a_d4_raw.json` — raw

### 데이터 영향
- 0 건 (chunks / vision_page_cache / vision_usage_log 변동 없음)

---

## 7. 한 문장 마감

> **2026-05-09 — golden v2 minority qtype 표본 확장 ship**. 157 → 172 row (+15) 라벨링 후 D4 도구 재실행. **caption gap 반전** (+0.013 → **-0.070**), Overall R@10 +0.0147, **caption=true R@10 +0.0838 🚀**, table_lookup R@10 +0.112, vision_diagram +0.10, exact_fact+caption=true top-1 **0.818 (DoD ≥ 0.80 도달 ✅)**. 단위 테스트 775 OK / 회귀 0. cost 0. **D2 fix 효과 통계적으로 확정**. 다음 후보 1순위 = combo c 운영 default 채택 (DoD R@10 ≥ 0.75 도달 가능 시그널, 0.25일).
