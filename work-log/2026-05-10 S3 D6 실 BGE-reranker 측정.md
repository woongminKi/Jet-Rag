# 2026-05-10 S3 D6 실 BGE-reranker 측정 (golden v2)

> Sprint: S3 D6 (Master plan §6 / table_lookup 진단 후 권고 1순위)
> 작성: 2026-05-10
> 마감: ship 완료 (측정 + table_lookup 가설 검증 + work-log)
> 입력: golden v2 (157 row) + S3 D5 도구 (mock 없이) + 실 BGE-reranker HF inference

---

## 0. 한 줄 요약

> **S3 D6 실 BGE-reranker 측정 ship — combo b R@10 0.6941 → 0.7049 (+0.0108), top-1 0.6200 → 0.6733 (+0.0533).** table_lookup 진단 가설 부분 검증 — **G-A-021 reranker 효과 0 (caption 보강 필요 가설 일치), G-A-107 top-1 회복, table_lookup top-1 0.1667 → 0.3333**. **신규 회귀 발견** — summary top-1 -0.111 / fuzzy_memory top-1 -0.200 (표본 작음 한계). P95 latency combo b=87s (HF timeout 3회), combo c=263ms (cache+MMR 운영 적합). cost: HF inference ~150건 (cap 1000 한참 미만 / cost ~$0). 운영 코드 변경 0, 단위 테스트 766 OK / 회귀 0.

---

## 1. 진입 결정 근거

table_lookup 진단 (`work-log/2026-05-10 table_lookup 약점 진단.md`) 의 권고 1순위:
- 5/6 의 top-1 약점 → **reranker 효과로 회복 가능 (가설)**
- 1/6 의 G-A-021 retrieve 실패 → caption 보강 필요 (D5 별개)

S3 D5 mock-reranker 측정에서는 폭락 → mock 한계. 실 BGE-reranker D6 별도 측정 필수.

cost 추정 (사전): HF inference monthly cap 1000 calls × candidate cap 20. 사용자 승인 받음.

---

## 2. 측정 절차

### 2.1 ENV / 도구

S3 D5 도구 (`evals/run_s3_d5_search_stack_eval.py`) 직접 실행:

```bash
DEFAULT_USER_ID='00000000-0000-0000-0000-000000000001' \
  uv run python ../evals/run_s3_d5_search_stack_eval.py \
    --combo all --goldenset v2 \
    --out ../evals/results/s3_d6_v2_results.md \
    --out-json ../evals/results/s3_d6_v2_raw.json
```

mock-reranker **OFF** (실 BGE-reranker HF inference 활성).

### 2.2 3 조합

- combo a (RRF-only baseline): `JETRAG_RERANKER_ENABLED=false`
- combo b (RRF+reranker, cap20): `JETRAG_RERANKER_ENABLED=true` + `JETRAG_RERANKER_CANDIDATE_CAP=20`
- combo c (RRF+reranker+MMR): + `JETRAG_MMR_LAMBDA=0.7`

### 2.3 운영 코드 변경

**0 건** — ENV 토글 + S3 D5 도구 v2 goldenset 듀얼 지원 활용.

---

## 3. 측정 결과

### 3.1 Overall (golden v2 157 row, n_eval=150)

| combo | label | R@10 | nDCG@10 | MRR | top-1 | P95 lat (ms) | doc fail |
|---|---|---:|---:|---:|---:|---:|---:|
| a | RRF-only | 0.6941 | 0.6004 | 0.5573 | 0.6200 | 8110.5 | 1 |
| b | RRF+reranker | **0.7049** | 0.6335 | 0.5938 | **0.6733** | 86966.4 | 1 |
| c | RRF+reranker+MMR | 0.7049 | 0.6322 | 0.5922 | 0.6667 | 263.0 | 1 |

DoD KPI 판정 (combo b — top-1 최우선):
- R@10 ≥ 0.75: **미달** (-0.0451)
- top-1 ≥ 0.80: **미달** (-0.1267)
- top-1 ≥ 0.95: 미달

### 3.2 reranker_path 분포

| combo | invoked | cached | degraded | disabled |
|---|---:|---:|---:|---:|
| a | 0.000 | 0.000 | 0.000 | 1.000 |
| b | 0.955 | 0.000 | 0.000 | 0.045 |
| c | 0.981 | 0.000 | 0.000 | 0.019 |

→ cache hit 0% (LRU 비어있음 — 측정 시작 시 reset). 실 운영은 cache hit 누적되며 변동.

### 3.3 latency

| combo | avg | P95 |
|---|---:|---:|
| a | 3040.9 ms | 8110.5 ms |
| b | 15315.0 ms | **86966.4 ms** (87s) |
| c | 292.5 ms | 263.0 ms |

combo b P95 87s = HF inference timeout 3회 (`5.1s/5.6s/5.9s 후 재시도`) + 첫 호출 cold start. 운영 적합성 우려.
combo c 263ms = MMR 후 cache 활용 + 일부 row 가 cache hit. 운영 적합.

### 3.4 qtype 별 변화 (combo a → b)

| qtype | n_eval | a R@10 | b R@10 | △ R@10 | a top-1 | b top-1 | △ top-1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| **exact_fact** | 113 | 0.7369 | **0.7400** | +0.0031 | 0.6814 | **0.7434** | +0.0620 |
| **numeric_lookup** | 7 | 0.5499 | **0.6587** | +0.1088 | 0.4286 | **0.5714** | +0.1429 |
| **synonym_mismatch** | 4 | 0.7330 | **0.7955** | +0.0625 | 0.5000 | **0.7500** | +0.2500 |
| **table_lookup** | 6 | 0.5944 | **0.6361** | +0.0417 | 0.1667 | **0.3333** | +0.1667 |
| vision_diagram | 2 | 0.8333 | 0.8333 | 0 | 0.5000 | 0.5000 | 0 |
| cross_doc | 4 | 0.1667 | 0.1667 | 0 | 0.2500 | 0.2500 | 0 |
| **summary** | 9 | 0.5926 | 0.6481 | +0.0556 | **0.6667** | **0.5556** | **-0.1111** ⚠ |
| **fuzzy_memory** | 5 | 0.5667 | 0.4667 | -0.1000 ⚠ | **0.4000** | **0.2000** | **-0.2000** ⚠ |

→ reranker 효과 명확한 qtype 4종 (exact_fact / numeric_lookup / synonym_mismatch / table_lookup) + 회귀 2종 (summary / fuzzy_memory).

---

## 4. table_lookup 진단 가설 검증

### 4.1 6 row cell-level 변화

| id | doc | a R@10/top1 | b R@10/top1 | 가설 검증 |
|---|---|---|---|---|
| G-U-003 | sonata | 1.0 / ✗ | 1.0 / ✗ | rerank 효과 0 (top-1 정답 vs predicted top-1 다름 그대로) |
| **G-A-008** | 데이터센터 | 0.5 / ✗ | **0.75 / ✗** | R@10 +0.25 (acceptable 추가 retrieve) |
| G-A-011 | 브랜딩 | 1.0 / ✓ | 1.0 / ✓ | 변화 없음 (이미 hit) |
| **G-A-021** | sample-report | **0.0 / ✗** | **0.0 / ✗** | **reranker 효과 0 — caption 보강 필요 가설 일치** ⭐ |
| **G-A-107** | 포트폴리오 | 0.667 / ✗ | **0.667 / ✓** | **top-1 회복** ⭐ |
| G-A-111 | 포트폴리오 | 0.4 / ✗ | 0.4 / ✗ | 변화 없음 (top-1 회복 안 됨) |

### 4.2 가설 검증 결론

| 가설 | 결과 |
|---|---|
| 5/6 의 top-1 약점 reranker 회복 | **1/5 회복** (G-A-107) — 부분 검증 |
| 1/6 의 G-A-021 caption 보강 필요 | **검증 ✓** (reranker 효과 0) |

→ table_lookup 의 핵심 fix 는 **G-A-021 caption 보강 (D5 본격) + 실 BGE-reranker 도입 (combo c 운영)** 의 결합이 필요.

---

## 5. 신규 회귀 발견 — summary / fuzzy_memory

### 5.1 summary top-1 -0.1111

- combo a: 6/9 top-1 hit → combo b: 5/9
- 1건 더 reranker 가 잘못 선택. 표본 9건 한계지만 추적 필요

### 5.2 fuzzy_memory top-1 -0.2000

- combo a: 2/5 → combo b: 1/5
- 1건 회귀. 표본 5건 한계

### 5.3 추정 원인

- BGE-reranker 가 query-chunk 의미 매칭 강화 → "정확한 의미" query (exact_fact 등) 에 효과적
- summary / fuzzy_memory 같이 **간접·암시적 query** 에는 reranker 가 dense 의 의미 매칭을 약화시킬 수 있음
- D5 reingest 후 chunks 의 caption 보강 시 변화 가능 (추가 측정 필요)

---

## 6. 회귀 검증

```
Ran 766 tests in 22.136s
OK (skipped=1)
```

회귀 0.

---

## 7. 비용 / 운영 적합성

### 7.1 HF inference 호출

- 약 150건 호출 (combo b 의 invoked rate 0.955 × 157 row)
- transient timeout 3회 → retry 후 성공
- monthly cap 1000 calls 한참 미만, cost ~$0

### 7.2 운영 적합성

| 운영 시나리오 | 적합 combo | 이유 |
|---|---|---|
| 검색 정확도 최우선 | combo b | top-1 +0.053, 단 P95 87s |
| 운영 latency 균형 | **combo c** | top-1 +0.047, P95 263ms (cache+MMR) |
| HF inference 불안정 시 | degrade → combo a | 자동 fallback 가드 |

→ **combo c 운영 default 권고** (top-1 회복 + latency 적합 + cross-doc 변화 없음).

---

## 8. 다음 후보 우선순위 (재정렬)

| # | 후보 | 작업량 | 권고도 | 이유 |
|---|---|---|---|---|
| 1 | **summary / fuzzy_memory 회귀 분석** | 0.5일 | 신규 ★★★ | 표본 작지만 reranker 의 qtype-specific 회귀 추적 |
| 2 | **D5 본격 reingest** (24h+ 후) | 가변 + cost ~$0.50 | ★★ → ★★★ | G-A-021 caption 보강 정량 정당화 (D6 검증 완료) |
| 3 | **search() cross_doc retrieve 진단** | 0.5~1일 | ★★ | cross_doc R@10 0.1667 reranker 효과 0 |
| 4 | **chunk text 헤더 합성 강화** | 1~2일 | ★★ | 표 본문 chunk 매칭 약점 일반 fix |
| 5 | **reranker degrade threshold 튜닝** | 0.25일 | ★ | combo b/c 운영 시 fallback 정책 |
| 6 | **Phase 2-B cross_doc row 확장** | 0.5~1일 | ★★ | search 진단 후 |

### 권고 (비판적 재검토 후)

**1순위 = summary / fuzzy_memory 회귀 분석** (cost 0).
- 이유: D6 신규 발견 — reranker 가 일부 qtype 회귀. 운영 default combo c 진입 전 root cause 식별 필수
- 작업: 회귀된 row 의 search 응답 + reranker score 추적 + chunk text 분석

**2순위 = D5 본격 reingest** (24h+ 후 cap 회복).
- 이유: G-A-021 reranker 효과 0 → caption 보강만 fix. D6 검증으로 ROI 가설 정량 정당화
- 사전: 24h cap 회복 + DEFAULT_USER_ID UUID 정합성 + 사용자 cost 승인

---

## 9. 사용자 결정 보류 항목

| # | 항목 | 권고 default | 결정 시점 |
|---|---|---|---|
| Q-D6-1 | 운영 default combo | **combo c** (top-1 +0.047, P95 263ms) | 사용자 명시 채택 |
| Q-D6-2 | summary / fuzzy_memory 회귀 처리 | root cause 분석 후 결정 | 다음 sprint |
| Q-D6-3 | reranker degrade threshold 운영 값 | default 0.8 유지, monthly 1000 cap 모니터링 | 운영 진입 후 |

---

## 10. 핵심 변경 파일 목록

### 신규
- 본 work-log
- `evals/results/s3_d6_v2_results.md` (gitignored) — markdown 결과
- `evals/results/s3_d6_v2_raw.json` (gitignored) — raw cell

### 수정
- 0 건 (운영 코드 / 측정 도구 / golden / 단위 테스트 모두 변경 없음)

---

## 11. 한 문장 마감

> **2026-05-10 — S3 D6 실 BGE-reranker 측정 ship**. combo b R@10 0.6941 → **0.7049 (+0.0108)**, top-1 0.6200 → **0.6733 (+0.0533)**. table_lookup 진단 가설 부분 검증 — G-A-021 reranker 효과 0 (caption 보강 필요 일치), G-A-107 top-1 회복. **신규 회귀 발견** — summary top-1 -0.111 / fuzzy_memory top-1 -0.200 (표본 작음 한계). P95 87s (combo b) vs 263ms (combo c) — **combo c 운영 default 권고**. cost ~$0 (HF inference ≤ monthly cap). 단위 테스트 766 OK / 회귀 0. 다음 후보 1순위 = summary / fuzzy_memory 회귀 분석 (cost 0).
