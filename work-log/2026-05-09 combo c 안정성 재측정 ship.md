# 2026-05-09 combo c 안정성 재측정 ship — default 채택 부적합 확정

> Sprint: TOC guard ablation 후속 — combo c P95 안정성 재측정
> 작성: 2026-05-09 (TOC ablation ship 직후)
> 마감: 라벨 정정 후 combo c × golden v2 172 row 재측정 + 운영 default 결정 확정
> 입력: TOC guard ablation 핸드오프 §5.1 의 1순위

---

## 0. 한 줄 요약

> **combo c 4차 측정 (라벨 정정 후) ship — 운영 default 채택 부적합 최종 확정**. **R@10 0.6929 / top-1 0.7152 / P95 73036ms (73초)**. RRF-only baseline 0.7082 / 0.7853 대비 R@10 -0.015 / top-1 -0.07 — **reranker net 효과 음성**. P95 73s 비정상 — 어제 71.7s 와 일관 → **HF inference free tier 의 latency 자체 문제** (시간대 변동 아님 확정). **default OFF 유지** 결정. 단위 테스트 775 OK / 회귀 0. 운영 코드 변경 0. 다음 후보 1순위 = 잔존 라벨 stale 정정 (50+ row) 또는 TOC guard 패턴 정밀화.

---

## 1. 4차 측정 결과

### 1.1 metric

| metric | 값 |
|---|---:|
| n / n_eval | 172 / 165 |
| **R@10** | 0.6929 |
| nDCG@10 | 0.6381 |
| MRR | 0.6145 |
| **top-1** | 0.7152 |
| **P95 lat** | **73036.0 ms ⚠** |
| avg lat | 15922.3 ms |
| cache hit rate | 0.000 |
| degrade rate | 0.000 |
| invoked rate | 0.971 |
| err | 0 |

### 1.2 4차 측정 누적 비교

| 측정 | R@10 | top-1 | P95 (ms) | degrade | 비고 |
|---|---:|---:|---:|---:|---|
| 어제 D6 (golden v2 150, 라벨 부정확) | 0.7049 | 0.6667 | **263** | 낮음 | 정상 |
| 오늘 1차 cap=1000 (172, 라벨 부정확) | 0.7315 | 0.6424 | 29568 | 0.145 | cap 임계 도달 |
| 오늘 2차 cap=5000 (172, 라벨 부정확) | 0.7256 | 0.6606 | 71687 | 0.000 | cap 무관 |
| **오늘 4차 cap=5000 (172, 라벨 정정 후)** | **0.6929** | **0.7152** | **73036** | **0.000** | **HF latency 문제 확정** |

→ P95 73s 일관 (71.7s 와 비슷) — 시간대 변동 아닌 **HF inference free tier 의 latency 자체 문제** 확정.

### 1.3 RRF-only vs combo c (라벨 정정 후 동일 시점 비교)

| metric | RRF-only (default) | combo c | △ |
|---|---:|---:|---:|
| Overall R@10 | **0.7082** | 0.6929 | **-0.015 ⚠** |
| Overall top-1 | **0.7853** | 0.7152 | **-0.070 ⚠** |
| nDCG@10 | 0.6496 | 0.6381 | -0.012 |
| MRR | 0.6096 | 0.6145 | +0.005 |
| P95 lat | 1214.8 ms | **73036 ms ⚠⚠** | +71800 ms |

→ **combo c < RRF-only** (라벨 정정 후 reranker net 효과 음성). 라벨 정정으로 RRF-only 가 강화돼 reranker 의 추가 효과가 사라짐.

---

## 2. 운영 default 결정 — OFF 최종 확정

### 2.1 채택 부적합 근거

1. **R@10 -0.015 / top-1 -0.07** — RRF-only 가 combo c 보다 우위
2. **P95 73s 비정상** — HF inference 자체 latency 문제 (시간대 무관)
3. **단위 ROI 음성** — combo c 채택 시 R@10/top-1 모두 떨어짐 + latency 폭증

### 2.2 reranker 효과의 변화 추적

라벨 부정확 시점:
- combo c R@10 0.7256 vs RRF-only 0.7219 → reranker +0.0037 (양성)

라벨 정정 후:
- combo c R@10 0.6929 vs RRF-only 0.7082 → reranker **-0.015 (음성)**

→ **라벨 정확도 향상 시 reranker 의 추가 효과 사라짐**. 의미: reranker 가 라벨 부정확 row 의 일부를 우연히 회복했는데, 라벨 정정 후 그 효과 사라짐.

### 2.3 default OFF 최종 채택

- `_RERANKER_ENABLED_DEFAULT = "false"` 유지
- ENV `JETRAG_RERANKER_ENABLED=true` opt-in 만 가능
- 운영 SLO (P95 < 1s) 충족 + 정확도 우위

---

## 3. ENV opt-in 의 미래 가치

### 3.1 향후 시나리오

- **HF API latency 안정화** (free tier → paid 전환 등) → 재측정 후 default 재논의
- **golden 표본 더 확장** + 더 정확한 라벨 → reranker 효과 변화 확인
- **다른 reranker 모델 연동** (BGE-large-v2 등) → 비교 ablation

### 3.2 ENV opt-in 그대로 유지

- `JETRAG_RERANKER_ENABLED=true` 1회성 set → 디버깅 / 비교 측정 가능
- 운영 default 변경 없음 — 회귀 risk 0

---

## 4. 다음 후보 우선순위

### 4.1 1순위 — 잔존 라벨 stale 정정 (50+ row, cost 0, 0.5~1일)

**왜?**
- DoD R@10 ≥ 0.75 까지 -0.042, 잔존 라벨 stale 정정으로 회복 가능
- DoD top-1 ≥ 0.80 까지 -0.015 (거의 도달)
- cross_doc / synonym_mismatch / fuzzy_memory 일부 잔존

### 4.2 2순위 — TOC guard 패턴 정밀화 (cost 0, 0.5일)

table_lookup 회귀 row 식별 + 정밀 조건 추가. ON 채택 가능성.

### 4.3 3순위 — chunk_filter 마킹 분석 (cost 0, 0.5일)

reingest 시 chunk_filter 37~48% 마킹 — false positive 검토.

### 4.4 4~10순위

| # | 후보 | 작업량 | cost | 권고도 |
|---|---|---|---|---|
| 4 | budget_guard cache hit 무료 인식 | 0.5일 | 0 | ★ |
| 5 | RPC per-doc cap | 1주+ | 0 | ★ |
| 6 | S4-B 핵심 엔티티 추출 | 3일 | 0 | ★★ |
| 7 | S5 /answer UX | 1주 | 0 | ★★ |

---

## 5. 핵심 변경 파일 목록

### 신규
- 본 work-log

### 수정
- 0 건

### 운영 코드
- 0 건 (default OFF 그대로)

### gitignored
- `evals/results/s3_d5_combo_c_v2_relabel.md` — 4차 측정 결과

### 데이터 영향
- 0 건

---

## 6. 한 문장 마감

> **2026-05-09 — combo c 4차 측정 (라벨 정정 후) ship**. R@10 0.6929 / top-1 0.7152 / P95 73036ms — RRF-only (0.7082 / 0.7853) 대비 **reranker net 효과 음성** (R@10 -0.015 / top-1 -0.07). P95 73s 비정상 일관 → **HF inference latency 자체 문제** 확정 (시간대 변동 아님). **운영 default OFF 최종 확정**. 단위 테스트 775 OK / 회귀 0. 운영 코드 변경 0.
