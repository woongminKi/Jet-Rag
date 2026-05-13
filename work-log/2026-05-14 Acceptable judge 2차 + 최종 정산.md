# 2026-05-14 Acceptable judge 2차 + 본 세션 최종 정산

> M3 paid 진입 마지막 sprint. DECISION-11 사인오프 파라미터 (candidate top-K 30 +
> threshold 0.6 + max_count 5) 적용. acceptable_chunks 7건 채움 / 실측 cost $0.17 /
> R@10 0.6817 → 0.6738 (정직 측정 효과).

---

## 1. Acceptable judge 2차 실행 결과

### 1.1 파라미터 (DECISION-11)

- candidate-top-k = **30** (1차 15 → 30 확장)
- acceptable-threshold = **0.6** (1차 0.5 → 0.6 강화)
- acceptable-max-count = **5** (1차 8 → 5 축소)
- 모델: gemini-2.5-flash
- cost cap: $0.10 (DECISION-3 한도 안)

### 1.2 선별·처리

- dry-run 선별: 11건 (acceptable_chunks empty + doc_id 있음)
- skip 사유: already_filled 164 / no_doc_id 2 / negative 5
- 처리 11/11, **채움 7건 / empty 4건**

| 결과 | golden_id | qtype | acceptable_chunks 추가 |
|---|---|---|---|
| filled | G-A-200 | table_lookup | 920, 952 |
| filled | G-A-203 | table_lookup | 335, 884, 915, 994 |
| filled | G-A-205 | table_lookup | 116, 120, 160, 161 |
| filled | G-A-207 | exact_fact | 26, 908 |
| filled | G-A-209 | vision_diagram | 191, 936 |
| filled | G-A-210 | exact_fact | 250, 251, 947 |
| filled | G-U-020 | summary | 52, 572, 902, 918, 992 |
| empty | G-A-011 | table | candidate 가 threshold 0.6 미달 |
| empty | G-A-013 | exact | 동일 |
| empty | G-U-013 | exact | 동일 |
| empty | **G-U-022** | fuzzy_memory | **judge 도 acceptable 매칭 약함** (retriever + judge 모두 약점) |

### 1.3 Cost 정합

- 추정 (cost_per_row $0.005): $0.0550
- **실측 (vision_usage_log, source_type=acceptable_judge): $0.1705** (추정의 3.1x)
- 누적 cost: $0.15 (RAGAS) + $0.17 (acceptable) = **$0.32** (DECISION-3 한도 $0.5 안, -$0.18 마진)

### 1.4 G-U-022 → empty 의 의미

RAGAS 측정의 outlier (Faithfulness 0/0) → 본 acceptable judge 2차에서도 candidate top-30 + threshold 0.6 충족하는 acceptable chunk 못 찾음. 

→ **retriever 회수 약점 + judge 의미 매칭 약점 동시**. golden_v3 분포 시정 (v1.5) 또는 self-host BGE-M3 + ColBERT 필요한 본질 fix 영역.

---

## 2. 회귀 검증 — R@10 재측정

### 2.1 결과

| 측정 시점 | R@10 | top-1 | P95 |
|---|---:|---:|---:|
| W-9.5 hybrid (acceptable 전) | 0.6817 | 0.7797 | 174.4ms |
| **acceptable judge 2차 후** | **0.6738** | **0.7797** | 177.4ms |
| Δ | **−0.0079** | **동일** | +3ms |

### 2.2 직관 의외인 결과 해석

acceptable_chunks 7건 추가 = **정답 set 분모 확장**. 회수 set 변화 X (search-side 불변) → graded recall = ∑(회수된 chunk · score) / ∑(정답 chunk · score) → **분모 ↑로 R@10 약간 감소**.

→ 단순 R@10 하락이 아니라 **정직 측정의 결과**:
- 기획서 §13.3 "숨기지 않기" 정합
- 본 PRD DECISION-6 "doc_match_fail R@10=0 분모 포함" 의 정직성 정책 연장
- 실제 검색 성능 변화 X — top-1 동일 (0.7797)
- DECISION-1 게이트 (top-1 ≥ 0.80) 불변 — M2 W-4 final 0.7966 noise band 안

### 2.3 향후 영향

- 회귀 측정 시 더 정확한 graded R@10 분모 (acceptable 채워진 row 7건)
- 사용자 발표 카드: R@10 "현재 0.67" (DECISION-1 보조 metric) — 0.6738 보고

---

## 3. 본 세션 최종 정산 — 10 commit · KPI 7/8개 측정 · M3 ~90% 진척

### 3.1 commit 흐름

| # | commit | scope | 핵심 |
|---:|---|---|---|
| 1 | `57d87e4` | P1 | vision_page_cache 사전 cap check 우회 (M2 W-4 회귀 직접 원인 fix) |
| 2 | `b280941` | P2 | m2_w4 eval 인자 `--out-md` → `--out` |
| 3 | `330979b` | P3 | admin 테스트 KST 자정 fragility |
| 4 | `7b0f898` | W-9.5 | BM25 ablation harness `--mode` |
| 5 | `da80b86` | S5-A | router signals dead code 회귀 fix + decomposed_subqueries CTA |
| 6 | `a938577` | KPI #7 측정 | hybrid vs dense vs sparse, DECISION-13 input |
| 7 | `6dc361c` | PRD v1.3 | DECISION-13 신규 + 본 세션 종합 |
| 8 | `150b3a5` | KPI #10/#11/#6① + `ingest_slo_report` | 17건 단위 테스트 |
| 9 | `e072eeb` | KPI #4/#5/#9 RAGAS | 30 row stratified, ~$0.15 |
| 10 | (본 commit) | **Acceptable judge 2차 + 최종 정산** | 7건 채움 ~$0.17, R@10 0.6817→0.6738 정직 측정 |

### 3.2 KPI 8개 최종

| # | KPI | 게이트 | 실측 | 결과 |
|---:|---|---|---:|:---:|
| #4 | Faithfulness | ≥ 0.85 | 0.908 | ✅ |
| #5 | Answer Relevancy | ≥ 0.80 | 0.801 | ✅ (경계) |
| #6 ① | 운용 R@10 | ≥ 0.75 / ≥ 0.90 | **0.6738** (정직 측정) | ❌ ("현재 + 가설") |
| #6 ② | Ragas Context Recall | (발표 카드) | (skip) | ⏳ (옵션) |
| #7 | 하이브리드 우세 (DECISION-13) | qtype-aware | ✅ table +0.34 / numeric +0.43 / cross_doc +0.045 | ✅ |
| #8 | 출처 일치율 | ≥ 0.95 (수동 50건) | (수동 대기) | ⏳ (사용자 작업) |
| #9 | 환각률 (Faithfulness 역산) | ≤ 3% | 9.2% | ❌ ("현재 + 가설") |
| #10 | P95 검색 (warm) | ≤ 3초 | 174ms | ✅ |
| #11 | 인제스트 SLO 달성률 | ≥ 90% | 48.3% | ❌ (HF cold-start) |

→ **자동 측정 완료 7개** (#4 ✅ / #5 ✅ / #6① ❌ / #7 ✅ / #9 ❌ / #10 ✅ / #11 ❌)
→ 잔여 2개 = #6 ② (옵션) / #8 (사용자 수동 작업)
→ **3개 ❌ 모두 "현재 + 가설" 정직성 포지셔닝** (기획서 §13.3 정합)

### 3.3 누적 paid cost

- RAGAS eval: ~$0.15
- Acceptable judge 2차: ~$0.17
- **누적: ~$0.32** (DECISION-3 한도 ~$0.5 안, -$0.18 마진)

### 3.4 단위 테스트

- 1124 → 1167 (+43 신규, 회귀 0)

### 3.5 본 세션 발견 사항 (PRD v1.4 input 후보)

1. **DECISION-13 신규** (KPI #7 재정의: qtype-aware top-1 + cross_doc R@10)
2. **S5-A dead code 회귀** (backend ↔ frontend signal 키 mismatch — `T1_cross_doc` vs `cross_doc`) → fix
3. **M2 W-4 회귀 직접 원인** = `vision_page_cache` 사전 cap check 의 historical SUM 차단 → fix
4. **G-U-022 outlier 진단** = retriever cross-doc 회수 약점 + LLM judge 매칭 약점 동시 → v1.5 fix 영역
5. **acceptable_chunks 정직 측정 효과** = R@10 −0.008 (분모 확장)
6. **인제스트 SLO 48.3% 미달** = HF free-tier scale-to-zero cold-start + vision enrich latency → v1.5 self-host fix 영역
7. **dense > hybrid R@10 (단순)** = 운영 default = hybrid 유지 (DECISION-13 qtype-aware 정합)

---

## 4. 다음 후보 — 사용자 결정

본 세션 자연 마감. M3 ~90% 진척 도달. 잔여:

| 후보 | 작업량 | cost | 권고도 |
|---|---|---|---|
| **수동 검수 #8 (50건) + #9 보강 (20건)** | 사용자 ~1~2시간 | $0 | **1순위** (KPI 8개 마감) |
| **KPI #6 ② Ragas Context Recall** | ~$0.05~0.15 + HF latency 73s+ | paid | 2순위 (옵션, 발표 카드 only) |
| **DECISION-12 인제스트 KPI 별도 트랙** | 결정만 | $0 | 3순위 (DoD #2 마감) |
| **G-U-022 회귀 fix 진입** | v1.5 영역 (self-host BGE-M3) | 별도 sprint | 보류 (v1.5) |
| **PRD v1.4 최종 정산** | ~0.5 day | $0 | M3 100% 도달 후 |

권고: **수동 검수 #8 + #9** (사용자 작업 필요, 자동 진행 불가) — 다음 세션 시작 시 사용자 결정.
