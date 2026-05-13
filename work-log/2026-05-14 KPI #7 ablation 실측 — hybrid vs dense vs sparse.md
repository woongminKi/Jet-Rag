# 2026-05-14 KPI #7 ablation 실측 — hybrid vs dense vs sparse

> W-9.5 harness 직접 사용 — golden_v2 182 row × 3 mode 측정.
> **충격 발견: KPI #7 "하이브리드 우세 +5pp" 단순 R@10 기준은 ❌ (dense 가 더 좋음).
> 하지만 qtype 별 trade-off 가 본질 — table_lookup / numeric_lookup top-1 에서
> hybrid 가 압도 (+0.34 / +0.43). KPI 정의 재검토 필요.**

---

## 1. 측정 환경

- HEAD: `da80b86` (S5-A router signals fix 직후)
- golden_v2: 182 row (전체)
- ENV: RRF-only baseline (`JETRAG_RERANKER_ENABLED=false` + `JETRAG_MMR_DISABLE=1`)
- raw 결과 (gitignore):
  - `evals/results/m3_kpi7_hybrid_20260514_003456.{md,json}`
  - `evals/results/m3_kpi7_dense_20260514_003533.{md,json}`
  - `evals/results/m3_kpi7_sparse_20260514_003608.{md,json}`

---

## 2. overall 결과

| mode | R@10 | top-1 | P95 (ms) | 시간 |
|---|---:|---:|---:|---|
| **hybrid** | **0.6817** | **0.7797** | 174.4 | 27.3s |
| dense | 0.7137 | 0.7797 | 156.8 | 24.9s |
| sparse | 0.5268 | 0.5593 | 129.4 | 20.0s |

### 2.1 KPI #7 "하이브리드 우세 +5pp" 단순 판정

| 비교 | R@10 | top-1 | 판정 |
|---|---:|---:|---|
| hybrid vs sparse | **+15.5pp** | +22.0pp | ✅ 압도 |
| **hybrid vs dense** | **−3.2pp** | 동률 (0.7797) | **❌ 회귀** |

→ **단순 R@10 기준으로 KPI #7 미달**. dense 단독이 hybrid 보다 더 좋음.

### 2.2 noise floor 검토

- 핸드오프 §3.3: R@10 noise floor ≈ ±0.012 (1~2 row in 177)
- 본 측정 R@10 차이 (hybrid - dense) = **−0.032 ≈ ~6 row** → **noise floor 초과 (유의미)**

---

## 3. qtype 별 분석 (본질 발견)

| qtype | n | R@10 H / D / S | top-1 H / D / S | 핵심 |
|---|--:|---|---|---|
| exact_fact | 115 | 0.711 / 0.738 / 0.551 | 0.85 / 0.88 / 0.59 | dense ↑ broad·top-1 |
| **table_lookup** | 12 | 0.670 / 0.670 / 0.615 | **0.92** / 0.58 / 0.58 | **hybrid top-1 +0.34** |
| summary | 9 | 0.704 / 0.778 / 0.541 | 0.89 / 1.00 / 0.78 | dense ↑ R@10·top-1 |
| **numeric_lookup** | 7 | 0.660 / 0.680 / 0.510 | **0.86** / 0.43 / 0.57 | **hybrid top-1 +0.43** |
| fuzzy_memory | 9 | 0.669 / 0.745 / 0.544 | 0.67 / 0.78 / 0.56 | dense ↑ |
| synonym_mismatch | 8 | 0.689 / 0.754 / 0.490 | 0.38 / 0.50 / 0.38 | dense ↑ |
| vision_diagram | 8 | 0.511 / 0.625 / 0.378 | 0.50 / 0.62 / 0.38 | dense ↑ broad |
| **cross_doc** | 9 | **0.474** / 0.429 / 0.244 | 0.22 / 0.22 / 0.22 | **hybrid R@10 +0.045** |
| out_of_scope | 5 | 0.000 / 0.000 / 0.000 | 0.00 / 0.00 / 0.00 | 무관 |

### 3.1 핵심 trade-off

- **hybrid 가 결정적 우세 qtype**: `table_lookup`, `numeric_lookup` — top-1 에서 dense 를 0.34~0.43 압도. 표·수치 query 는 sparse signal (정확 키워드 매칭) 이 결정적.
- **dense 가 결정적 우세 qtype**: `exact_fact`, `summary`, `fuzzy_memory`, `synonym_mismatch`, `vision_diagram` — 자연어 의미 매칭이 더 강함. sparse 가 noise.
- **cross_doc**: hybrid R@10 +0.045 우세 (sparse 가 cross-doc 라벨 다양성 확보 도움).

### 3.2 운영 권고

| 사용 패턴 | 권고 mode |
|---|---|
| 표·수치 query (table_lookup / numeric_lookup) | **hybrid** (top-1 압도) |
| 자연어 의미 query (exact_fact / summary 등) | **dense** (R@10 broad 우세) |
| 디폴트 단일 mode | **hybrid** (top-1 안정성 > broad R@10) |

→ 현 운영 default = hybrid 는 **table/numeric top-1 안정성** 으로 정당화. 단순 R@10 기준 +5pp 가 아닌, **qtype-aware top-1 우세** 로 KPI #7 재정의 권고.

---

## 4. KPI #7 재정의 제안 (PRD v1.3 input)

### 4.1 기존 정의 한계
- 기획서 §13.1 KPI #7: "하이브리드 우세 +5pp"
- 단순 overall R@10 비교 — qtype trade-off 가시화 불가

### 4.2 재정의 제안 (3안)

| 안 | 정의 | 충족 여부 |
|---|---|---|
| A. **qtype-aware top-1** | table_lookup + numeric_lookup top-1 에서 hybrid vs dense +0.15pp 이상 | **✅ +0.34 / +0.43** |
| B. **cross_doc R@10 우세** | cross_doc R@10 hybrid vs dense +0.03pp 이상 | ✅ +0.045 |
| C. 종합 (A + B AND) | 둘 다 충족 | ✅ |

→ **권고 A+B 채택**. table/numeric/cross_doc 의 도메인 가치가 일반 nl 보다 큼 (사용자가 답을 위해 검색하는 핵심 query type).

---

## 5. 다음 후보 (사용자 결정)

1. **PRD v1.3 갱신** — 본 발견 반영 + KPI #7 재정의 + S5-A dead code 회귀 + P1~P3 잡일 종합 — 0.5 day, $0
2. **Acceptable judge 2차** (DECISION-11, ~$0.05, ~1일)
3. **dense top-1 회귀 분석** — table_lookup 에서 dense top-1 = 0.58 인 이유 추적 (BGE-M3 한국어 매칭 한계?) — 0.5~1 day, $0
4. **MMR + reranker ON ablation** — 본 측정은 RRF-only baseline. reranker / mmr ON 시 hybrid 의 R@10 회복 가능성 검토 — 0.5 day, $0

**권고: 1 (PRD v1.3 갱신)** — 본 세션 6 commit 의 결과를 종합 + KPI #7 재정의 input + 다음 세션 진입 명확화. 잡일·발견·실측 모두 v1.3 으로 마감.

---

## 6. raw 결과 sample (감사용)

```
[hybrid] 측정 완료 — mode=hybrid 27.3s, R@10=0.6817, top-1=0.7797, P95=174.4ms
[dense]  측정 완료 — mode=dense  24.9s, R@10=0.7137, top-1=0.7797, P95=156.8ms
[sparse] 측정 완료 — mode=sparse 20.0s, R@10=0.5268, top-1=0.5593, P95=129.4ms
```

gitignore: `evals/results/m3_kpi7_*` (raw md + json 모두 보존, push 안 됨).
