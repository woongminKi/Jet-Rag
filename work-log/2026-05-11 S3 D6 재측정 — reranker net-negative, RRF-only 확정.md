# 2026-05-11 S3 D6 재측정 — reranker net-negative, production default = RRF-only 확정

> 프로젝트: Jet-Rag
> 작성: 2026-05-11 (`ed48e0b` HEAD 기준 후속 — 핸드오프 §5 deferred 6순위 = D6 재측정)
> 범위: 회복된 chunks DB(2469) + golden_v2 183 row 기준 실 BGE-reranker 3 combo 재측정 → Q-D6-1 production default combo 확정 → S3 종결
> 흐름: senior-planner 명세 → 사전 체크(degrade 카운터 우회) → 측정 실행(메인 세션, ~87분 background) → senior-qa 검증(ship GO) → work-log
> 비용: paid $0 (BGE-reranker HF 무료, vision API 호출 0, paid_decomposition OFF). 운영 코드 변경 0. read-only.

---

## 0. 한 줄 요약

> **D6 재측정 결과 — 회복 DB(+191 vision_compose chunks)에서 reranker 가 net-negative.** combo a(RRF-only) 가 최선: R@10 **0.6539** vs b/c 0.6363/0.6325 (−0.018/−0.021), top-1 **0.7386** vs b/c 0.7216 (−0.017). nDCG/MRR 만 b/c 미세 ↑ (잡은 것 랭킹은 좋아지나 recall·top-1 은 낮아짐 — cross-encoder 알려진 trade-off). + HF free-tier reranker endpoint 운영 불가 (combo b P95 **196s**, read timeout 빈발). → **Q-D6-1 = production default RRF-only 확정** (`JETRAG_RERANKER_ENABLED=false` default 유지 — 코드 변경 0). reranker 코드(cap/cache/degrade guard/MMR)는 옵션 ENV 토글로 보존. **S3 종결.** 단위 테스트 951 / 회귀 0. 신규 발견: RRF-only 도 P95 67.6s (HF embed query cold-start) — 후속 1순위 후보.

---

## 1. 왜 재측정인가

- D6 는 2026-05-10 에 한 번 측정됨 (`work-log/2026-05-10 S3 D6 실 BGE-reranker 측정.md`) — 하지만 그건 **Phase 4 회귀(-185) 이전 chunks DB(1738) + golden_v2 157 row** 기준이라 baseline 오염.
- 그 후 Phase 9 옵션 A 회복(chunks 1738→2469, +191 vision_compose chunks — 정확히 표/그림 caption chunk) + golden_v2 157→183 row(cross_doc +26) + Phase 11 acceptable_chunks LLM-judge(158 filled / 25 empty) 로 측정 baseline 이 크게 달라짐.
- 핸드오프 §5 가 "Phase 5 실 BGE-reranker 측정 (D6) — Phase 9 회복으로 chunks DB 정상 → 측정 baseline 회복" 을 deferred 6순위로 명시. **Q-D6-1 (production default combo) 이 2026-05-10 부터 "combo c 권고, 사용자 명시 채택 대기" 로 미결** — 재측정으로 확정 + S3 종결이 목적.

---

## 2. 측정 절차

### 2.1 사전 체크

| 체크 | 결과 |
|---|---|
| HF_API_TOKEN | ✅ 존재 |
| chunks DB | ✅ total 2469 / vision_compose 191 / vision_budget_exceeded 0 |
| golden_v2.csv | ✅ 183 데이터 행 / acceptable 158 filled / 25 empty / relevant_chunks 전부 채워짐 |
| 단위 테스트 baseline | ✅ 951 OK / skipped 0 (핸드오프 "930" 표기는 Phase 11 직전 — 951 이 최신) |
| **reranker degrade 카운터** | ⚠ `vision_usage_log` `source_type='reranker_invoke'` 30일 누적 = **1136건** (2026-05-08~09 테스트 흔적) — 이미 monthly cap(1000)·degrade 임계(800) 초과. 우회 안 하면 측정이 전부 `degraded` 경로(HF skip, RRF score 유지)로 fallback → reranker 품질 측정 불가 |

### 2.2 측정 한정 우회 (운영값 변경 아님 — process scope export)

```bash
JETRAG_RERANKER_MONTHLY_CAP_CALLS=10000000   # degrade 발동 방지 — 측정 한정
JETRAG_RERANKER_DEGRADE_THRESHOLD=1.0        # 동상
```
→ senior-qa 검증: 이 우회는 "실 reranker 가 실제로 돌게" 만 할 뿐 score 계산엔 무개입. `degrade_rate=0.000` 이 우회 덕인 게 맞고 측정 결과 왜곡 없음.

### 2.3 명령

```bash
cd api && \
DEFAULT_USER_ID='00000000-0000-0000-0000-000000000001' \
JETRAG_PAID_DECOMPOSITION_ENABLED=false \
JETRAG_RERANKER_MONTHLY_CAP_CALLS=10000000 \
JETRAG_RERANKER_DEGRADE_THRESHOLD=1.0 \
  uv run python ../evals/run_s3_d5_search_stack_eval.py \
    --combo all --goldenset v2 \
    --out ../evals/results/s3_d6_v2_recovery_results.md \
    --out-json ../evals/results/s3_d6_v2_recovery_raw.json
```
- mock-reranker **OFF** (실 BGE-reranker HF inference `hf-inference/models/BAAI/bge-reranker-v2-m3/.../text-classification`)
- combo b 가 ~87분 (HF cold start + read timeout retry 빈발) → background 실행. combo a → b → c 순차 (단일 프로세스)
- 산출물 (전부 `evals/results/` 하위, gitignored): `s3_d6_v2_recovery_results.md` / `_raw.json` / `_s3_d6_run.log`

---

## 3. 측정 결과 (golden_v2 183 row, n_eval=176)

### 3.1 3 combo × 7 metric

| combo | label | n/n_eval | R@10 | nDCG@10 | MRR | top-1 | P95 lat(ms) | cache hit | degrade | err |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **a** | **RRF-only** (`RERANKER_ENABLED=false`, `MMR_DISABLE=1`) | 183/176 | **0.6539** | 0.5961 | 0.5622 | **0.7386** | 67644 | 0.000 | 0.000 | 0 |
| b | RRF+reranker cap20 (`RERANKER_ENABLED=true`, cap=20, `MMR_DISABLE=1`) | 183/176 | 0.6363 | **0.6001** | 0.5792 | 0.7216 | **196435** | 0.000 | 0.000 | 0 |
| c | RRF+reranker+MMR λ=0.7 (`RERANKER_ENABLED=true`, cap=20, `MMR_DISABLE=0`, `MMR_LAMBDA=0.7`) | 183/176 | 0.6325 | 0.6007 | **0.5839** | 0.7216 | 2842 | 0.000 | 0.000 | 0 |

- latency avg: a 7734ms / b 28595ms / c 707ms. runtime: combo b 5233s(87분) / combo c 130s.
- reranker_path: a disabled 1.000 / b invoked 0.923 (=169 row) · disabled 0.077 (=14 row: rpc row≤1 또는 candidate 0 진입 못함 + transient timeout fallback — search.py 가 transient 시 path 미갱신 → 초기값 disabled 유지) / c invoked 0.984. **`disabled` 라벨이 두 종류(block 미진입 + transient fallback)를 conflate** — 해석 주의.
- **cache hit 0.000 인데 combo c 가 avg 707ms / P95 2.8s 로 빠른 건 cache 가 아니라** combo b 가 앞서 87분 돌면서 HF reranker·embed 모델이 warm 된 상태에서 c 가 측정됐기 때문 (b 의 avg 28.6s 와 모순 → warm-up 효과). 즉 **c 의 P95 2.8s 는 운영 cold P95 가 아님 — 운영 cold reranker P95 ≈ combo b 의 ~196s**.
- combo b 로그: "bge-reranker.rerank transient 실패 (attempt 1/3, 5.x s 후 재시도) ... attempt 2/3 ... → RRF fallback (reason=transient): The read operation timed out" 다수. transient ~44건 / retry 진입 18건 / RRF fallback 11건. hard error 0.
- doc 매칭 fail 1건 (expected_doc_title partial match 실패 — golden CSV title 정정 필요, 알려진 minor, D6 범위 밖).

### 3.2 DoD KPI (combo a baseline — 셋 중 최선)

| KPI | 측정값 | 임계 | 판정 |
|---|---:|---:|:---:|
| top-1 ≥ 0.95 | 0.7386 | 0.95 | 미달 |
| R@10 ≥ 0.75 | 0.6539 | 0.75 | 미달 |
| 정확도 (top-1) ≥ 0.80 | 0.7386 | 0.80 | 미달 |

→ S3 의 "top-1 ≥ 0.95 / R@10 ≥ 0.75" 목표는 어느 combo 도 미달. 단 combo a 가 셋 중 최선 → reranker 가 이 갭을 메우지 못함이 확인됨.

### 3.3 cross_doc only sub-report (n_subset 15, n_eval 11)

| combo | R@10 | nDCG@10 | MRR | top-1 |
|---|---:|---:|---:|---:|
| a | 0.1273 | 0.0874 | 0.1136 | 0.0000 |
| b | 0.0455 | 0.0279 | 0.0303 | 0.0000 |
| c | 0.0455 | 0.0279 | 0.0303 | 0.0000 |

→ reranker 가 cross_doc 를 더 악화 (truncation 이 multi-doc 쿼리의 분산 relevant chunk 를 더 잘 떨굼). 표본 작아(신호 있는 row 4건) 절대값은 noise 수준이나 방향 일관. cross_doc 개선은 S4-B(entity) 영역 — 본 작업 범위 밖.

### 3.4 query_type 별 R@10 Δ(b−a) — 어디서 손해 나는가 (senior-qa 분석)

| query_type | n | mean Δ R@10 | 합계 Δ |
|---|---:|---:|---:|
| fuzzy_memory | 8 | **−0.2115** | −1.69 |
| synonym_mismatch | 8 | −0.0824 | −0.66 |
| cross_doc | 10 | −0.0389 | −0.39 |
| vision_diagram | 7 | −0.0317 | −0.22 |
| exact_fact | 105 | −0.0186 | **−1.96** (분모 최대 → 미세 손실 누적) |
| table_lookup | 10 | **+0.0500** | +0.50 |
| summary | 9 | +0.0556 | +0.50 |
| numeric_lookup | 7 | **+0.1173** | +0.82 |

개별 최대 회귀: G-A-101 (1.0→0.286), G-A-068 (1.0→0.333), G-U-102 (1.0→0.333, fuzzy), G-U-107 (1.0→0.333), G-U-001 (0.875→0.25, fuzzy), G-U-016 (0.778→0.222, vision_diagram) — "이미 RRF 로 완벽히 잡던 것을 reranker 가 망친" 케이스 다수.

---

## 4. 해석 — 분기 3 (reranker net-negative on 회복 DB)

### 4.1 결론

planner 명세 §4 결정 트리의 **분기 3** — combo b/c 가 combo a 의 R@10 도 top-1 도 못 넘음 → **production default = RRF-only (combo a)**. reranker 코드는 제거하지 않고 옵션 ENV 토글로 남김 (cap/cache/degrade guard/MMR 전부 보존 — 운영 ENV `JETRAG_RERANKER_ENABLED=true` 로 언제든 켤 수 있음).

### 4.2 왜 net-negative인가 (원인 — *추정*, 데이터로 부분 지지)

- reranker 는 nDCG/MRR 은 미세 ↑ (cap=20 candidate 안에서 잡은 relevant chunk 의 순위 품질 개선) — recall/top-1 은 ↓ (RRF top-10 안에 있던 relevant chunk 를 cross-encoder 가 demote 해서 누락 / 비정답을 #1 로 올림). 도메인 미튜닝 cross-encoder + 짧은 한국어 chunk 에서 흔한 trade-off.
- 데이터가 가리키는 손해 지점: **fuzzy_memory (평균 −0.21, 가장 큼) + synonym_mismatch (−0.08) + 분모 최대인 exact_fact 의 미세 누적 (−0.02 × 105 = −1.96)**. → "fuzzy/synonym 같은 간접·암시 query 에서 cross-encoder 가 RRF 의미 매칭보다 약함" + "회복 DB(+191 vision chunks)에서 RRF 자체가 이미 충분해 reranker 의 추가 가치 없음".
- (당초 가설 "vision_compose chunk 가 RRF 로 잘 잡혀서 reranker 가 그걸 demote 해 손해" 는 데이터로 약지지/부분반증 — vision-ish qtype(vision_diagram/table_lookup/numeric_lookup) 묶음은 오히려 +0.046 개선. 원인은 미확정으로 둠. 어느 가설이든 reranker net-negative 결론은 불변.)
- **+ 운영 불가**: HF free-tier reranker P95 196s, read timeout 빈발 → 품질 무관하게 운영 default 부적합. 05-10 측정에도 timeout 3건 있었음 — 추세 일관. free-tier 에 SLA 없으므로 재측정해도 위험 동일 → 재측정 불요.

### 4.3 2026-05-10 ↔ 재측정 부호 역전

| | golden_v2 row | chunks DB | combo a R@10 | combo b R@10 | combo b top-1 vs a |
|---|---|---|---:|---:|---:|
| 05-10 D6 | 157 | 1738 (Phase 4 회귀 후) | 0.6941 | 0.7049 (+0.011) | **+0.053 (도움)** |
| 재측정 (본 문서) | 183 | 2469 (Phase 9 회복) | 0.6539 | 0.6363 (−0.018) | **−0.017 (해)** |

- **절대값(0.6941 → 0.6539)을 직접 비교하지 말 것** — golden 분모(157→183, +26 cross_doc R@10~0.13 drag) + acceptable Phase 11 +12 filled (graded recall 분모 inflation, 커밋 `ed48e0b` 에 "0.7088→0.6999 −0.0089" 기록됨) + chunks DB 회복으로 매칭 chunk 분포 변동. → combo a R@10 하락은 회귀가 아니라 측정 조건 변화.
- **핵심 신호 = 부호 역전**: broken DB(vision chunks 빠짐)에선 RRF 약했고 reranker 가 메꿀 여지 있었음 → 회복 DB(vision chunks 복구)에선 RRF 이미 충분 → reranker net-negative. **교훈: reranker 가치는 base retrieval 품질에 반비례.**

---

## 5. 검증 (senior-qa, ship GO)

| 항목 | 결과 |
|---|---|
| 측정 방법론 정합 | ✅ golden v2 183 (`_raw.json n_golden=183, mock_reranker=false`), n_eval 176 = 183 − 6(정답 chunks 둘 다 empty) − 1(doc fail), 3 combo ENV 명세대로, degrade-bypass 필수·무왜곡 |
| combo b timeout-fallback 오염 | ✅ 오염 아님 — search.py 가 transient 시 path 미갱신 → fallback row 는 `disabled` 로 집계 (invoked 169 = "진짜 reranked only" subset). evaluable ∩ b=invoked (n=164) 재집계: R@10 0.6519→0.6330(−0.019), top-1 0.7378→0.7195(−0.018) — 전체와 동일 부호. 분기-3 불변 |
| 회귀 0 | ✅ `cd api && uv run python -m unittest discover tests` → 951 OK / skipped 0 / failures 0. git tree clean (D6 산출물 3개 전부 gitignored) |
| paid cost $0 | ✅ vision API 0, paid_decomposition OFF, HF free-tier |
| drift 해석 | ✅ "절대값 비교 부적절, combo 상대 Δ + 부호 역전이 핵심" 타당 |
| production default 권고 | ✅ "RRF-only 확정, reranker 옵션 유지, S3 종결" 타당. 코드 변경 0 (`_RERANKER_ENABLED_DEFAULT` 이미 false). **사용자 사인오프 1건 권고** (sprint-level 결정) |
| 등급 | 치명 0 / 중요: 원인 가설 표현 완화(반영함 §4.2), HF embed cold-start 후속 1순위(§6), Q-D6-1 사인오프 / 사소: 도구 헤더 "golden_v1.csv" 오표기(05-10 동일), `disabled` 라벨 conflate, golden CSV title 1건, degrade cap 30일 윈도우 dev 부적합 |

---

## 6. 남은 이슈 / 한계

- **#A HF embed query cold-start (RRF-only 도 P95 67.6s)** — reranker 무관, search 전반 영향. combo a p50 495ms / p90 24.4s / p95 67.6s / max 137.9s — long tail 전부 `bge-m3.embed_query transient`(read timeout / 500) 14건의 5~11s+ 재시도 누적. KPI "검색 P95 ≤ 3초"(기획서 §13.1) 대비 압도적 미달, reranker 보다 시급할 수 있음. → **후속 1순위 후보**: HF embed query warmup ping / timeout·retry 정책 조정 / self-host embed 검토.
- **#B reranker 가 fuzzy_memory·synonym_mismatch 에 약함** — 운영 default 는 OFF 라 당장 영향 없으나, 향후 reranker 를 켜는 시나리오(특정 qtype 한정 라우팅 등) 검토 시 fuzzy/synonym 은 RRF-only 유지가 나음.
- **#C cross_doc R@10 0.1273 (RRF-only)** — 모든 qtype 중 최약(top-1 0.0). reranker 도 못 고침(오히려 0.0455 로 악화). entity-aware ranking (S4-B) + query decomposition 영역 — S4-B 본 PC 재검증 + entity_boost ablation 으로 다뤄야 함.
- **#D `reranker_invoke` degrade 카운터 1136 → ~1474** — 본 측정이 ~338건 추가 (combo b 169 + c 169). monthly cap 1000 / 30일 윈도우가 dev 테스트기에 너무 작음 → 운영엔 무영향(reranker off 라 호출 0)이나 향후 측정·테스트 재현 시 또 우회 필요. → 윈도우 확대 or env-tunable or 주기적 test-run record cleanup 절차 문서화 검토 (별도 백로그).
- **#E `run_s3_d5_search_stack_eval.py` 도구 버그** — 출력 markdown 헤더가 `--goldenset` 무관하게 "golden_v1.csv" 하드코딩 (line 597-600), `disabled` path 라벨이 두 종류 conflate. 측정값 영향 0, 도구 정정 후보.
- **#F golden_v2.csv doc 매칭 fail 1건** — expected_doc_title partial match 실패. golden CSV title 정정 후보.

---

## 7. 다음 스코프 (2026-05-12+ 진입 시)

핸드오프 §5 우선순위 갱신:
1. ~~Phase 5 실 BGE-reranker 측정 (D6)~~ → **본 문서로 완료. production default = RRF-only 확정, S3 종결.**
2. **(승격, 신규 1순위 후보) HF embed query cold-start 대응** — #A. RRF-only P95 67.6s 가 검색 KPI(P95 ≤ 3초) 압도적 미달. warmup ping / timeout·retry 튜닝 / self-host 검토. 0.5~1일
3. **multimodal judge 17 row 확대** (~$0.03) — Phase 10 효과 일반화, paired t-test
4. **S4-B 본 PC 재검증** + entity_boost factor ablation — #C cross_doc 약점 대응. origin 80% ship 의 chunks DB 정합성 확인 후 entity_boost 1.0/1.5/2.0
5. acceptable judge 2차 라운드 (~$0.05) — caption_dependent huge-doc 7건 `[empty]` 재시도
6. chunk text augmentation (S4-A D6 후보, 5~7일)
7. G-A-208 multimodal judge timeout 진단

새 미결 / 결정:
- ~~Q-D6-1 production default combo~~ → **RRF-only 확정** (코드 default 이미 false, 변경 0). sprint-level 결정이라 **사용자 사인오프 권고** — 이견 있으면 회신.
- ~~Q-D6-1b 적용 방식~~ → moot (코드 변경 0)
- ~~Q-D6-cold-start reranker warmup~~ → moot (reranker off). 단 embed cold-start(#A)는 별개 1순위
- **Q-D6-3** reranker degrade cap 1000 / 30일 윈도우 운영값 — 윈도우 확대 vs env-tunable vs test-record cleanup. 별도 백로그
- **Q-embed-coldstart** #A 대응 진입 시점 + 방식 (warmup ping / retry 튜닝 / self-host)

---

## 8. ship 메모

- 운영 코드 변경 **0** (`JETRAG_RERANKER_ENABLED` default 이미 `false` = 권고 상태). golden 변경 0. 도구 변경 0. → 커밋 대상 = 본 work-log + 메모리 갱신뿐.
- D6 산출물 (`evals/results/s3_d6_v2_recovery_results.md` / `_raw.json` / `_s3_d6_run.log`) 은 `evals/results/.gitignore` (`*`) 로 무시됨 — 커밋 자동 제외.
- master plan §6 의 S3 항목 "D6 실 reranker 측정 deferred" → "완료" 갱신은 기획 문서 수정이라 사용자 확인 후 (Q-D6-master).
- push 는 사용자 요청 시.
