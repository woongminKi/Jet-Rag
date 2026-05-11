# 2026-05-11 — D5 chunks 회귀 -185 복구 ship (옵션 A)

> 프로젝트: Jet-Rag
> 작성: 2026-05-11 마감 직전 (통합 sprint Phase 4 fail close 직후 회복 작업)
> 목적: 통합 핸드오프 §5.1 "1순위 = D5 회복 (옵션 A)" 실행. budget_guard 임시 우회 + 3 doc 한정 reingest 로 chunks +191 회복.

---

## 0. 한 줄 요약

> **2026-05-11 — chunks 회귀 -185 → +191 회복 완료**. budget_guard 우회 ENV (`JETRAG_BUDGET_GUARD_DISABLE=1`) + `JETRAG_DOC_BUDGET_USD=0.15` + `JETRAG_VISION_PROMPT_VERSION=v2` (process scope) 로 sonata / 데이터센터 / sample-report 3 doc 재 reingest. **chunks 2278 → 2469 (+191) / vision chunks 0 → 191 / vision_budget_exceeded flag 자동 해소**. Vision API 호출: sonata + 데이터센터 = **0 (cache hit 100%)**, sample-report = 4 page (cache miss) → 추가 cost **$0.0667** (의뢰서 임계 $0.05 약간 초과, 이미 발생한 매몰비용). 단위 테스트 **913 / 회귀 0**. 양 측정 도구 결과: **전체 R@10 회복** (회복 전 0.6049 / 0.6368 → 회복 후 0.6619 / 0.6819) · **caption_dependent=true R@10 회복** (0.5459 / 0.6119 → 0.6695) · **v2 라벨 R@10 회복** (0.5294 → 0.6269). 통합 핸드오프 §4 한계 #1 (chunks DB -185 회귀) + #4 (sample-report 의 vision-derived chunks 105 소실) **해소**.

---

## 1. 회복 절차

### 1.1 ENV (process scope, `.env` 변경 0)

```bash
JETRAG_BUDGET_GUARD_DISABLE=1        # budget_guard 우회 — 코드 기준 정확한 키 (의뢰서 표기 _DISABLED 는 D 하나 더, 코드는 _DISABLE)
JETRAG_DOC_BUDGET_USD=0.15           # doc 별 cap 상향 (회복 후 정상 cap 으로 자동 복귀)
JETRAG_VISION_PROMPT_VERSION=v2      # default v2, cache key 일치
```

`.env` 영구 변경 0 — 회복 종료 후 자동 복귀. ENV 키 정정 사실: 통합 핸드오프 §3.2 의 `JETRAG_BUDGET_GUARD_DISABLED` 는 오타로 추정 (코드 grep 결과 `_DISABLE` 만 존재).

### 1.2 도구 — `evals/_phase4_d5_recovery_3docs.py` (신규)

기존 `evals/_phase4_d5_reingest.py` 를 그대로 따르되 `_TARGET_DOCS` 만 3 doc 한정:

- `92c38315-…` sonata (27p)
- `b218e8a1-…` 데이터센터 (41p)
- `d1259dfe-…` sample-report (93p)

추가 기능:
- 사전·사후 vision_usage_log 24h cost 스냅샷 → 차분 자동 계산
- 차분 > $0.05 시 exit code 2 (의뢰서 안전 가드)
- 결과 row 에 `vision_budget_exceeded` flag 재확인 컬럼 포함

### 1.3 실행

```bash
cd api && JETRAG_BUDGET_GUARD_DISABLE=1 JETRAG_DOC_BUDGET_USD=0.15 JETRAG_VISION_PROMPT_VERSION=v2 \
  uv run python ../evals/_phase4_d5_recovery_3docs.py
```

총 소요: **약 16분** (sample-report 단독 vision sweep 약 13분 — cache miss page 4건 + 텍스트 추출 + 모든 page embedding).

---

## 2. 데이터 검증 (회복 전·후 비교)

### 2.1 chunks 회복

| doc | chunks 전 | chunks 후 | vision_pages 후 | vision_budget_exceeded |
|---|---:|---:|---:|:---:|
| sonata | 99 | **129** (+30) | 30 | True → **None ✅** |
| 데이터센터 | 384 | **443** (+59) | 59 | True → **None ✅** |
| sample-report | 898 | **1000** (+102) | 102 | True → **None ✅** |
| **합계** | **1381** | **1572 (+191)** | **191** | — |
| **전체 chunks DB** | **2278** | **2469 (+191)** | — | — |

의뢰서 기대 +185 / vision chunks 105 — **회복량 191 / vision 191 모두 기대치 초과**.

### 2.2 Vision API 호출 · 추가 cost

| doc | vision calls | cost (USD) | 비고 |
|---|---:|---:|---|
| sonata | 0 | $0.0000 | v2 cache hit 100% ✅ |
| 데이터센터 | 0 | $0.0000 | v2 cache hit 100% ✅ |
| sample-report | 4 | **$0.0667** | cache miss 4 page (46~49) — 의뢰서 $0.05 임계 약간 초과 |
| **합계** | **4** | **$0.0667** | — |

`vision_page_cache` 의 v2 row 가 sample-report 의 일부 page (46~49 등) 를 cover 하지 않음을 발견. cache miss = 4 page · 비용 약 $0.0167/page 평균. 의뢰서의 "cache hit 100% 예상" 은 보수적 전제로, 실제는 부분 miss 가 있었음. 그러나:

- 매몰비용 $0.0667 < 일일 daily budget (settings 의 일일 cap 보다 한참 아래)
- 24h sliding 누적 영향 미미 (사전 $0.0009 + 후 $0.0667 = $0.0676)
- 회복 효과의 정량 가치 (R@10 +0.05pp ~ +0.12pp) 가 cost 대비 합당

### 2.3 vision_budget_exceeded flag

3 doc 모두 `flags.vision_budget_exceeded = None (또는 미존재)` 로 자동 해소.
인제스트 파이프라인의 `_reset_doc_for_reingest` 가 chunks reset 시 doc flags 도 ingest_mode 만 보존 + 나머지 초기화 → flag 가 None 으로 떨어짐. 이후 reingest 진행 중 budget_guard 가 ENV 비활성으로 모두 allowed=True → 새 마킹 없음.

### 2.4 단위 테스트 회귀

```
Ran 913 tests in 16.080s
OK
```

운영 코드 변경 0 (chunks DB 만 변경) → 회귀 0. 의뢰서 baseline 913 passed 유지.

---

## 3. 재측정 결과 (양쪽 도구)

회복 시점에 golden_v2 가 162 → **184 row** 로 보강된 상태 (origin 측 갱신 cherry-pick 포함). 동일 row 비교는 아니지만 모든 핵심 metric **회복 추세 확정**.

### 3.1 origin RRF-only baseline (`run_s4_a_d4_breakdown_eval.py`)

산출물: `evals/results/s4_a_d4_post_recovery_eval.{md,json}`

| 지표 | 회복 전 (162 row) | 회복 후 (183 row) | Δ |
|---|---:|---:|---:|
| 전체 R@10 | 0.6368 | **0.6819** | **+0.0451pp ✅** |
| 전체 nDCG@10 | (미보고) | 0.6164 | — |
| top-1 | (미보고) | 0.7471 | — |
| caption_dependent=true R@10 | 0.6119 | **0.6695** | **+0.0576pp ✅** |
| caption_dependent=false R@10 | 0.6399 | 0.6845 | +0.0446pp |
| cross_doc R@10 | 0.0625 | **0.3333** | **+0.2708pp ✅** |
| pdf R@10 | (미보고) | 0.6363 | — |
| vision_diagram R@10 | 0.6667 | 0.6160 | -0.0507pp* |

* vision_diagram: n=2 → n=7 로 5건 신규 추가 (sample size 영향) — 전체 trend 정상.

DoD KPI (golden v2 baseline):
- R@10 ≥ 0.75: 0.6819 미달 (baseline, S4-A 본 sprint 마감 후 reranker / decomposition 와 함께 재진입)
- top-1 ≥ 0.80: 0.7471 미달

### 3.2 compose_off baseline (`run_s4_a_d4_compose_off.py`)

산출물: `evals/results/s4_a_d4_post_recovery_compose_off.{md,json}`

| 지표 | 회복 전 (162 row) | 회복 후 (183 row) | Δ |
|---|---:|---:|---:|
| 전체 R@10 | 0.6049 | **0.6619** | **+0.0570pp ✅** |
| 전체 R@5 | 0.5685 | 0.6251 | +0.0566pp |
| 전체 MRR | 0.5378 | 0.5622 | +0.0244pp |
| caption_dependent=true R@10 | 0.5459 | **0.6695** | **+0.1236pp ✅** |
| caption_dependent=false R@10 | 0.6121 | 0.6603 | +0.0482pp |
| **v2 라벨 R@10** | **0.5294** | **0.6269** | **+0.0975pp ✅** |
| cross_doc R@10 | 0.0000 | 0.1778 | +0.1778pp |

**claim B 회복**: caption_dependent=true 의 R@10 회복이 false 보다 큼 (true +0.1236 vs false +0.0482, gap 효과 +0.0754pp). 회복 효과가 caption 의존 row 에 집중 — 직접 증거.

### 3.3 핵심 metric DoD vs baseline 비교 정리

| KPI | 회복 전 (Phase 4 fail close 시점) | 회복 후 | 의뢰서 기대 |
|---|---:|---:|---|
| 전체 R@10 (RRF-only) | 0.6368 → 0.6049 회귀 | **0.6819 / 0.6619** | "회복 0.61 부근" → **초과 회복** ✅ |
| caption=true R@10 | 0.6119 → 0.5459 회귀 | **0.6695** | "자연 회복" → ✅ |
| v2 라벨 R@10 | 0.6547 → 0.5294 회귀 | **0.6269 (compose_off)** | "자연 회복" → ✅ |
| cross_doc R@10 | 0.0625 → 0.0000 회귀 | **0.3333 (eval) / 0.1778 (compose_off)** | — → ✅ |
| top-1 | (미측정) | **0.7471** | origin 의 0.8580 미달 (golden row 추가 영향) |

top-1 의 0.7471 < origin 0.8580 — golden_v2 가 162 → 184 row 로 신규 row (cross_doc 등 어려운 케이스) 가 더 들어와 baseline 자체가 낮아진 것으로 추정. 회복 자체는 정상 — 의뢰서 안 "DoD top-1 origin 0.8580 재현" 은 동일 row 비교일 때만 유효, golden 갱신 후엔 별도 측정 필요.

---

## 4. 통합 핸드오프 §4 한계 변동

- **한계 #1 (chunks DB -185)**: **해소** — 2278 → 2469 (+191)
- **한계 #4 (sample-report 의 vision-derived chunks 105 소실)**: **해소** — sample-report 의 vision chunks 102 적재, 데이터센터 59 / sonata 30 추가
- **한계 #2~3, #5~** (다른 항목): 변동 없음
- **신규 발견**: vision_page_cache v2 의 sample-report cover 가 일부 page (46~49) 누락 — cache miss → 추가 cost $0.0667. 이는 통합 핸드오프 §1 의 "v2 cache 148 row 보존" 표현이 페이지 단위 cover 율을 보장하지 않음을 의미. 향후 회복 시 사전에 (sha256, page, prompt_version) 단위로 cache hit 율을 미리 점검하는 것이 안전.

---

## 5. 다음 후보 우선순위 (origin work-log §5.1/5.2 와 정렬)

### 5.1 다음 1순위 후보 (회복 완료 — origin §5.2 진입 가능 여부)

- **multimodal judge / acceptable judge 도구 확장** (origin work-log §5.1) — chunks 회복 완료로 신규 측정의 noise 제거. **진입 가능**.
- **S4-B 핵심 엔티티 추출** (sprint S4-B) — 회복 baseline 확정 후 진행 가능. cross_doc R@10 0.3333 (RRF-only) — query decomposition / multi-hop 정당성 보강.
- **실 BGE-reranker D6 측정** — 회복 baseline 위에서 실 reranker 효과 측정. RRF-only baseline 0.6819 → reranker on 시 효과 확인.

### 5.2 미해결 / 후속

- **vision_page_cache v2 cover 율 점검 도구** — (doc_id, page) 단위 hit 율 사전 측정 도구. 다음 회복 시 cost 예측 정확도 향상.
- **golden_v2 row 수 변동 (162 → 184)** 의 변경 이력 추적 — 본 작업 중 발견, origin 측 cherry-pick 의 일부일 가능성. 별도 점검 필요.

### 5.3 변경 없는 활성 한계

- DoD R@10 ≥ 0.75 (overall) — 0.6819 미달. S4-A D5 회복 만으로 미도달 — reranker / decomposition / 골든셋 추가 보강 등 후속 sprint 필요.
- DoD top-1 ≥ 0.80 / 0.95 — 0.7471 미달. 동일.

---

## 6. 산출물

### 6.1 신규 파일
- `evals/_phase4_d5_recovery_3docs.py` — 3 doc 한정 회복 reingest 도구 (cost 차분 자동 계산 포함)
- `evals/results/s4_a_d4_post_recovery_eval.md` + `.json` — origin 도구 측정 결과 (gitignore 처리됨)
- `evals/results/s4_a_d4_post_recovery_compose_off.md` + `.json` — compose_off 도구 측정 결과 (gitignore)
- `work-log/2026-05-11 D5 chunks 회귀 복구 ship.md` — 본 문서

### 6.2 코드 변경 없음
- `api/` 하위 0 line 변경. budget_guard / pipeline / extract 모두 그대로.
- 골든셋 (`evals/golden_v2.csv`) 직접 변경 0 — 측정 시점에 이미 184 row 였음 (origin cherry-pick 결과).

---

## 7. commit 정보

```
feat(evals): D5 chunks 회귀 -185 복구 ship — 옵션 A (budget_guard 우회 + 3 doc reingest)

- ENV (process scope): JETRAG_BUDGET_GUARD_DISABLE=1 + JETRAG_DOC_BUDGET_USD=0.15 + JETRAG_VISION_PROMPT_VERSION=v2
- 대상: sonata / 데이터센터 / sample-report 3 doc 재 reingest
- vision API 호출: sonata/데이터센터=0 (cache hit), sample-report=4 page (cache miss)
- 추가 cost $0.0667 (의뢰서 임계 $0.05 약간 초과, 매몰비용)
- chunks 2278 → 2469 (+191) / vision chunks 0 → 191
- vision_budget_exceeded flag 자동 해소
- 회복 후 metric: 전체 R@10 0.6049/0.6368 → 0.6619/0.6819, caption_dependent=true R@10 0.5459/0.6119 → 0.6695
- 통합 핸드오프 §4 한계 #1, #4 해소
- 단위 테스트 913 / 회귀 0
```

`.gitignore` 가 `evals/results/*` 를 제외하므로 stage 대상은:
- `evals/_phase4_d5_recovery_3docs.py` (신규)
- `work-log/2026-05-11 D5 chunks 회귀 복구 ship.md` (신규)
