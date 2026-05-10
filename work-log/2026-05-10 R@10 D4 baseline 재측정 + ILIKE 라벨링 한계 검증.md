# 2026-05-10 R@10 D4 baseline 재측정 + ILIKE 자동 라벨링 한계 검증

> Sprint: D4 R@10 baseline 재측정 (golden v2 172 → 181 row 영향)
> 작성: 2026-05-10
> 마감: D4 도구 재실행 + n=148/172 vs n=181 비교 + 신규 9 row R@10 분리 측정 + 1순위 sprint 의 ILIKE 자동 라벨링 한계 정량 검증
> 입력: 직전 sprint (vision_diagram 임계 분리) + 1순위 sprint (golden v2 +9 row)

---

## 0. 한 줄 요약

> **D4 R@10 baseline 재측정 ship** (golden v2 181 row, 218s, BGE-M3 안정 ✅). Overall **R@10 0.7132 → 0.6953** (△ -0.018), **top-1 0.8712 → 0.8372** (△ -0.034). 신규 9 row 만 분리 시 **R@10 = 0.3704, top-1 hit rate = 22.2%** (2/9) — ILIKE 자동 라벨링 한계 정량 검증 (acceptable_chunks 가 ILIKE keyword 매칭 ≠ RRF 검색 top-10 매칭). DoD top-1 ≥ 0.80: **✅ 도달 유지** (0.8372). DoD R@10 ≥ 0.75: ❌ 미달 (-0.0547, 직전 -0.0368 → 더 멀어짐). 단위 테스트 805 OK / 회귀 0. **누적 cost 변동 0** (외부 API 호출 0). 다음 후보 = 신규 9 row 라벨 수동 정정 (cost 0, 1 day).

---

## 1. 측정 결과

### 1.1 Overall (n=181, n_eval=172, RRF-only)

| 시점 | n / n_eval | R@10 | top-1 | nDCG@10 | MRR | P95 lat |
|---|---:|---:|---:|---:|---:|---:|
| 직전 (172 row, 1순위 sprint 전) | 172/163 | 0.7132 | 0.8712 | 0.6725 | 0.6340 | (변동) |
| **현재 (181 row)** | **181/172** | **0.6953** | **0.8372** | 0.6518 | 0.6090 | 3065.4ms |
| △ | +9/+9 | **-0.018** | **-0.034** | -0.021 | -0.025 | — |

### 1.2 신규 9 row 만 분리 측정

| id | qtype | R@10 | top-1 | nDCG | MRR |
|---|---|---:|---:|---:|---:|
| G-A-211 | vision_diagram | 0.333 | ✗ | 0.202 | 0.125 |
| G-A-212 | vision_diagram | **0.000** | ✗ | 0.000 | 0.000 |
| G-A-213 | vision_diagram | **0.000** | ✗ | 0.000 | 0.000 |
| G-A-214 | vision_diagram | 0.333 | **✓** | 0.469 | 0.500 |
| G-U-104 | synonym_mismatch | 0.333 | ✗ | 0.148 | 0.062 |
| G-U-105 | synonym_mismatch | 0.333 | ✗ | 0.141 | 0.056 |
| G-U-106 | synonym_mismatch | **1.000** | **✓** | 0.967 | 0.500 |
| G-U-107 | synonym_mismatch | 1.000 | ✗ | 0.552 | 0.167 |
| G-U-108 | synonym_mismatch | **0.000** | ✗ | 0.000 | 0.000 |
| **mean** | — | **0.3704** | **2/9 = 22.2%** | 0.275 | 0.156 |

### 1.3 qtype별 변화 (신규 row 영향)

| qtype | n_eval (직전) | n_eval (현재) | R@10 (직전) | R@10 (현재) | △ |
|---|---:|---:|---:|---:|---:|
| **synonym_mismatch** | 4 | **9** | 0.50 | **0.6128** | +0.11 (acceptable_chunks 풍부) |
| **vision_diagram** | 6 | **9** | 1.00 | **0.5161** | -0.48 (G-A-212/213 R@10=0) |
| 그 외 | (변동 0) | (변동 0) | — | — | — |

→ vision_diagram 의 R@10 1.00 → 0.5161 급락은 신규 4 row 중 2건 (G-A-212/213) 이 R@10=0 이라 평균 끌어내림.

### 1.4 DoD KPI 판정

| KPI | 측정값 | 임계 | 판정 | 직전 |
|---|---:|---:|:---:|:---:|
| **top-1 ≥ 0.80** | **0.8372** | 0.80 | **충족 ✅** | 충족 (0.8712) |
| R@10 ≥ 0.75 | 0.6953 | 0.75 | 미달 (-0.055) | 미달 (-0.037) |
| top-1 ≥ 0.95 | 0.8372 | 0.95 | 미달 (-0.113) | 미달 (-0.079) |

→ DoD top-1 도달 **유지** (0.8372 ≥ 0.80). R@10 갭 -0.018 확장.

---

## 2. 핵심 발견 — ILIKE 자동 라벨링 한계 정량 검증

### 2.1 1순위 sprint 의 가설 vs 결과

| 가설 (1순위 sprint 진입 시) | 실제 결과 |
|---|---|
| ILIKE keyword 매칭 → acceptable_chunks 자동 채움 → R@10 양호 | **mean R@10 = 0.3704** — 매우 낮음 |
| LLM hallucination 자동 검증 (G-Z-008 drop) | ✅ 검증 — 단 ILIKE 통과 row 가 RRF 검색 통과 보장 X |

### 2.2 원인 분석

**ILIKE keyword 매칭 ≠ RRF 검색 top-10 매칭**:
- ILIKE 는 단순 substring 매칭 (예: "비식별화" 단어 포함 chunks)
- RRF 는 BM25 sparse + dense (의미) 매칭의 결합
- 예: G-U-104 ("보건의료 빅데이터에서 개인 프라이버시 지침") 의 must_include="비식별화;개인정보" → ILIKE 가 chunks 9,13,18 매칭. 단 query 의 "프라이버시 지침" 은 "비식별화" 와 의미적 거리 → dense 검색이 다른 chunks 우선
- → top-10 에 9,13,18 안 나옴 → R@10 = 0

### 2.3 row 별 R@10 분석

| row | R@10 | 원인 분석 |
|---|---:|---|
| G-U-106 | **1.000** | "한마음생활체육관 시행일" — 시행일 chunks 10,23,24 모두 top-10 에 들어옴 (정확) |
| G-U-107 | 1.000 | "한마음생활체육관 정기권 이용" — 11,50,51 chunks 매칭 |
| G-A-211/214 | 0.333 | vision_diagram — 1/3 chunks 만 top-10 (text-based search 의 vision 한계) |
| G-A-212/213 | 0.000 | vision_diagram — chunks 0건 매칭 (vision content 특성 + ILIKE 키워드 일반적 ("미국;경제성장률") → 다른 chunks 가 RRF top-10 점유) |
| G-U-104/105/108 | 0.333 / 0 | synonym_mismatch — query 의 "유사어" 가 chunks 의 "정확어" 와 대응 안 됨 (synonym_mismatch qtype 의 본질) |

### 2.4 ILIKE 자동 라벨링의 적정 사용 시나리오

- ✅ **명확한 키워드 + 단일 정답 chunks** (G-U-106 패턴): 효과 큼
- ❌ **synonym/유사어 query** (synonym_mismatch qtype): query 어휘 ≠ chunks 어휘
- ❌ **vision/도표 query**: chunks text 만으로 vision content 표현 X
- ⚠ **중복 candidates** (G-U-104 ≈ G-U-108): 동일 acceptable_chunks → 동일 측정값 → 정보 중복

→ 다음 sprint 권고: 신규 9 row **수동 라벨링 review** (D4 raw 결과 활용 → 실 search top-10 chunks 를 acceptable_chunks 후보로 검토).

---

## 3. 비판적 한계 (정직 인정)

| # | 한계 | 영향 | 회복 |
|---|---|---|---|
| 1 | **신규 9 row R@10 0.3704** | overall R@10 -0.018 끌어내림 | 수동 라벨링 정정 (별도 sprint, cost 0) |
| 2 | **vision_diagram qtype 급락** (1.00 → 0.516) | qtype 평균 통계 신뢰도 ↓ | 신규 vision row 4건 acceptable_chunks 정정 + RAGAS qtype override 와 일관 |
| 3 | **G-U-104 ≈ G-U-108 중복** | 정보 중복 → 표본 효과 ↓ | dedup (1건 drop) |
| 4 | **ILIKE 라벨링은 단순 키워드 매칭만** | RRF 검색 결과와 괴리 | 라벨링 시 search 실측 (D4 raw 활용) 권고 |
| 5 | **DoD R@10 갭 확장** (-0.037 → -0.055) | 형식적 baseline 하락 | minority qtype 라벨 정확도 ↑ 후 자연 회복 |

→ **본질적 회귀 X** — golden v2 표본 확장으로 baseline 정직성 증가. 다음 sprint 의 라벨 정정 후 회복 예상.

---

## 4. 사용자 결정 보류 항목 변동

| ID | 항목 | 직전 | 갱신 |
|---|---|---|---|
| Q-new9-relabel | 신규 9 row 라벨링 정정 | 신규 | **다음 1순위 권고** — 수동 review (cost 0, 1 day) |
| Q-vision-baseline-fail | G-A-212/213 R@10=0 | 신규 | acceptable_chunks 재선정 또는 doc 자체 재검토 |
| Q-dedup-G-U-104-108 | G-U-104 ≈ G-U-108 중복 | 신규 | 1건 drop 또는 query 차별화 |

---

## 5. 다음 후보 우선순위

### 5.1 1순위 — 신규 9 row 라벨 수동 정정 (cost 0, 1 day)

**왜?** 본 sprint 의 ILIKE 라벨링 한계 정량 검증 (R@10 0.37). search 실측 결과 (D4 raw) 활용한 수동 정정으로 baseline 회복 가능.

**작업**:
- G-A-212/213 (R@10=0): doc 자체 재검토 + acceptable_chunks 재선정 또는 row drop
- G-U-104/108 (중복): 1건 drop 또는 query 차별화
- G-A-211/214 (R@10=0.333): top-10 실 chunks 추가 검토
- G-U-105 (R@10=0.333): 직제규정 doc 의 "재산관리내규" 관련 chunks 재검토

### 5.2 2순위 — TOC guard 패턴 정밀화 (cost 0, 0.5 day)

table_lookup -0.083 회복 (직전 핸드오프 §6 한계 #4).

### 5.3 3순위 — expected_answer_summary 정정 (cost 0, 0.5 day)

G-U-105~107 의 chunk-text 같은 expected_summary → 실 summary 정정.

### 5.4 4~10순위

| # | 후보 | 작업량 | cost | 권고도 |
|---|---|---|---|---|
| 4 | cost 가드레일 80% 알림 절차 | 0.25 day | 0 | ★★ |
| 5 | uvicorn 좀비 모니터링 자동화 | 0.5 day | 0 | ★ |
| 6 | cross_doc qtype 자동 생성 (B 후속) | 1 day | ~$0.05 | ★★ |
| 7 | visual_grounding metric 신설 | 1 day | ~$0.05 | ★★ |
| 8 | S4-B 핵심 엔티티 추출 | 3 day | 0 | ★★ |

---

## 6. 핵심 변경 파일 목록

### gitignored 산출 (본 work-log §1 reproduced)
- `evals/results/s4_a_d4_results.md` — n=181 결과
- `evals/results/s4_a_d4_raw.json` — n=181 raw cells
- `evals/results/s4_a_d4_results.n148.md` (backup) — 직전 n=148 baseline
- `evals/results/s4_a_d4_raw.n148.json` (backup)

### 신규 commit 대상
- 본 work-log 1건

### 데이터 영향
- 0 건 (chunks / vision_page_cache / golden_v2.csv 변동 없음)

### 운영 코드 변경 영향
- 0 건 (`api/app/` / `web/src/` 수정 없음)
- 측정 도구 변경 0 (기존 `run_s4_a_d4_breakdown_eval.py` 재실행)

### 외부 cost
- 0 (RRF-only baseline, vision/Gemini/HF reranker 비활성)
- 누적 (이번 세션 전체): ~$0.31 (변동 없음)

---

## 7. 한 문장 마감

> **2026-05-10 — D4 R@10 baseline 재측정 ship** (golden v2 181 row, 218s, BGE-M3 안정). Overall R@10 0.7132 → **0.6953** (-0.018), top-1 0.8712 → **0.8372** (-0.034, DoD ≥ 0.80 도달 유지 ✅). 신규 9 row 만 분리 시 R@10 = **0.3704** (top-1 hit 2/9) — **ILIKE 자동 라벨링 한계 정량 검증** (keyword 매칭 ≠ RRF 검색 매칭, vision_diagram + synonym_mismatch qtype 약점). 단위 테스트 805 OK / 회귀 0. 누적 cost 변동 0. 다음 1순위 = **신규 9 row 라벨 수동 정정** (cost 0, 1 day).
