# 2026-05-09 combo c 운영 default 측정 재현 (P95 폭증 → 채택 보류)

> Sprint: 표본 확장 핸드오프 §4.1 1순위 — combo c 운영 default 채택 + 측정 재현
> 작성: 2026-05-09 (golden v2 표본 확장 ship 직후)
> 마감: combo c × golden v2 172 row 재측정 + DoD 판정 + 운영 default 결정
> 입력: D6 어제 측정 (combo c P95 263ms / R@10 0.7049) + 표본 확장 후 RRF-only 0.7219

---

## 0. 한 줄 요약

> **combo c (RRF+reranker+MMR) 측정 재현 ship — R@10 0.7315 (+0.0096 vs RRF-only) / top-1 0.6424 (+0.0289)** 의미 있는 개선. 단 **P95 latency 29568ms (29.5s) — 어제 D6 측정 263ms 대비 112배 폭증**. 원인 추정: HF inference cold start (인터넷 끊김 직후 측정) + degrade rate 0.145 (월간 cap 80% 임계 도달). DoD R@10 ≥ 0.75 미달 (-0.0185). **운영 default 채택은 보류 권고** — P95 안정성 검증 (측정 시점 차이 / cap 회복 후 재측정) 후 결정. 단위 테스트 775 OK / 회귀 0. 운영 코드 변경 0. 다음 후보 1순위 = **multi-doc cross_doc retrieve 진단** (cost 0, cross-doc R@10 0.0833 잔존).

---

## 1. 측정 결과

### 1.1 combo c × golden v2 172 row

| metric | 값 |
|---|---:|
| n / n_eval | 172 / 165 |
| **R@10** | **0.7315** |
| nDCG@10 | 0.6427 |
| MRR | 0.5880 |
| **top-1** | **0.6424** |
| **P95 lat** | **29567.9 ms ⚠** |
| avg lat | 8840.9 ms |
| cache hit rate | 0.000 |
| degrade rate | 0.145 |
| err | 0 |

### 1.2 reranker_path 분포

| path | 비율 |
|---|---:|
| invoked | 0.831 |
| cached | 0.000 |
| **degraded** | **0.145** ⚠ |
| disabled | 0.023 |
| doc 매칭 fail | 1 / 172 |

→ **degrade path 발동** — reranker 월간 cap 80% 임계 도달. 일부 row 가 RRF-only 로 fallback 하면서 latency 변동 + 효과 일부 약화.

---

## 2. 비교 분석

### 2.1 RRF-only (D4) vs combo c (오늘 재현)

| metric | RRF-only (D4) | combo c | △ |
|---|---:|---:|---:|
| R@10 | 0.7219 | **0.7315** | **+0.0096 ✅** |
| top-1 | 0.6135 | **0.6424** | **+0.0289 ✅** |
| nDCG@10 | 0.6204 | 0.6427 | +0.0223 |
| MRR | 0.5811 | 0.5880 | +0.0069 |
| P95 lat | 1030 ms | **29568 ms ⚠⚠** | **+28538 ms** |

→ R@10 + top-1 의미 있는 개선. 단 P95 폭증.

### 2.2 어제 D6 측정 (golden v2 150) vs 오늘 (golden v2 172)

| metric | D6 어제 | 오늘 | △ |
|---|---:|---:|---:|
| R@10 | 0.7049 | 0.7315 | +0.0266 (표본 확장 effect 누적) |
| top-1 | 0.6667 | 0.6424 | -0.0243 (확장 row 의 top-1 분포 영향) |
| **P95 lat** | **263 ms** | **29568 ms** | **+29305 ms (112배 폭증)** ⚠⚠ |
| degrade rate | (미측정 / 낮음) | 0.145 | 신규 발동 |

→ **P95 latency 가 비정상적으로 폭증**. 어제 측정 시점에 비해:
1. **인터넷 끊김 직후 재시작** — HF inference cold start
2. **reranker 월간 cap 80% 임계** 도달 (degrade rate 0.145) — 일부 호출이 cap 카운팅 차단되며 latency spike

### 2.3 cross-doc sub-report

| metric | 값 |
|---|---:|
| n_subset | 10 (top-5 distinct doc_id ≥ 3 row) |
| n_eval | 6 |
| R@10 | 0.0833 |
| top-1 | 0.0000 |

→ cross-doc 약점 그대로 (이전 측정과 일관). multi-doc retrieve 진단 후속 sprint 필요.

---

## 3. DoD KPI 판정

| KPI | 측정값 | 임계 | 판정 |
|---|---:|---:|:---:|
| R@10 ≥ 0.75 | 0.7315 | 0.75 | **미달** (-0.0185) |
| top-1 ≥ 0.80 | 0.6424 | 0.80 | 미달 |
| top-1 ≥ 0.95 | 0.6424 | 0.95 | 미달 |
| cross-doc top-5 distinct ≥ 3 row | 10 / 172 (5.8%) | — | informational |

→ R@10 임계까지 -0.0185 — combo c 만으로는 도달 못함. 추가 회복 후보 필요.

---

## 4. 운영 default 결정

### 4.1 채택 보류 권고

**이유**:
1. **P95 latency 29.5s 비현실적** — 운영 SLO 부적합. 단일 측정 결과로 default 결정 위험
2. **degrade rate 0.145** — 월간 cap 임계 도달 상태에서 측정. cap 회복 후 재측정 필요
3. **R@10 +0.0096 / top-1 +0.0289** 효과는 있지만 DoD 도달 부족 (-0.0185)
4. **인터넷 끊김 직후 측정** — HF cold start 가능성

### 4.2 채택 조건 (다음 sprint)

다음 조건 모두 충족 시 ENV default 변경 고려:
- combo c P95 ≤ 1500ms 안정 측정 (3회 이상 평균)
- degrade rate ≤ 0.05 (월간 cap 회복 후)
- R@10 ≥ 0.74 (임계 -0.01 이내)

### 4.3 ENV default 변경 plan (보류 중)

`api/app/routers/search.py` 의:
- `_RERANKER_ENABLED_DEFAULT = "false"` → `"true"`
- MMR default 는 ON (`JETRAG_MMR_DISABLE` 안 set) — 변경 불필요

---

## 5. 다음 후보 우선순위 (재정렬)

### 5.1 1순위 — multi-doc cross_doc retrieve 진단 (cost 0)

**왜?**
- cross-doc sub-report R@10 0.0833 / top-1 0.0000 — 가장 큰 약점
- G-U-015/017/032 + 신규 row 까지 multi-doc 매칭 fail 잔존
- cost 0, 0.5~1일

### 5.2 2순위 — combo c P95 안정성 재측정

월간 cap 회복 후 (24h+ 또는 다음 달) 3회 측정 평균. 안정 시 운영 default 채택.

### 5.3 3~10순위

| # | 후보 | cost | 작업량 | 권고도 |
|---|---|---|---|---|
| 3 | 12 docs v2 prompt reingest | $0.5~1.5 | 가변 | ★★ |
| 4 | numeric_lookup R@10 0.5295 진단 | 0 | 0.5일 | ★★ |
| 5 | fuzzy_memory top-1 -0.25 진단 | 0 | 0.5일 | ★ |
| 6 | chunk_filter 45.5% 마킹 분석 | 0 | 0.5일 | ★★ |
| 7 | budget_guard cache hit 무료 인식 | 0 | 0.5일 | ★ |
| 8 | reranker monthly cap 회복 + 재측정 | 0 | 0.25일 (대기) | ★★ |
| 9 | S4-B 핵심 엔티티 추출 | 0 | 3일 | ★★ |
| 10 | S5 /answer UX | 0 | 1주 | ★★ |

---

## 6. 사용자 결정 보류 항목

| # | 항목 | 권고 default | 결정 시점 |
|---|---|---|---|
| Q-combo-c-default | combo c 운영 default 채택 | **보류** — P95 안정성 검증 후 | 다음 sprint |
| Q-cap-recovery | reranker monthly cap 회복 대기 | 24h+ 또는 다음 달 자동 회복 | 자동 |
| Q-multi-doc-priority | multi-doc cross_doc 진단 진입 | cross-doc R@10 0.0833 → 회복 시도 | 다음 sprint |
| Q-other-docs | 12 docs v2 prompt reingest | sample-report 외 doc 효과 확장. cost ~$0.5~1.5 | 사용자 명시 cost 승인 후 |
| (어제 잔존) | 별도 sprint | | |

---

## 7. 핵심 변경 파일 목록

### 신규
- 본 work-log
- `evals/results/s3_d5_combo_c_v2_172.md` + `.json` (gitignored)

### 수정
- 0 건

### 운영 코드
- **0 건**

### 데이터 영향
- 0 건

---

## 8. 한 문장 마감

> **2026-05-09 — combo c 측정 재현 ship**. R@10 0.7315 (+0.0096 vs RRF-only), top-1 0.6424 (+0.0289). DoD R@10 ≥ 0.75 미달 (-0.0185). 단 **P95 latency 29568ms (29.5s) 비정상 폭증** (어제 D6 263ms 대비 112배). 원인 추정: HF cold start + degrade rate 0.145 (월간 cap 80% 도달). **운영 default 채택 보류 권고** — P95 안정성 재측정 후 결정. 단위 테스트 775 OK / 회귀 0. 운영 코드 변경 0. 다음 후보 1순위 = **multi-doc cross_doc retrieve 진단** (cost 0, cross-doc R@10 0.0833 잔존).
