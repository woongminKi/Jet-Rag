# 2026-05-15 HF self-host 검토 — v1.5 sprint 설계

> 작성: senior-planner / HEAD `1ce5465` / KPI #10 P95 29.7s 가 본 검토 트리거 / 제약: 검토·설계만, 코드 변경 0, 임의 커밋 0

---

## 0. 핵심 요약 (~300 word)

KPI #10 (검색 P95 < 3s) 가 29.7s 로 10배 초과. 원인은 HF Inference Providers (router-hf-inference) 의 BGE-M3 에 대한 scale-to-zero 콜드스타트 — warm 170ms, cache hit 115ms 는 정상이지만 idle 후 첫 호출이 1.8~20.6s. 본 검토는 v1.5 sprint 진입을 위한 옵션 비교·권장안·작업 산출이다.

7 옵션 (A~F + DeepInfra) 을 latency · 월비용 · 복잡도 · R@10 회귀 · 한국어 지원 4축으로 평가했다. 페르소나 A 의 일일 검색 ~30 query (한 달 ~900 쿼리, 평균 토큰 ~30) 기준 추산.

**권장 = 2단계 접근**:
1. **즉시 (v1.5 W-1)**: **DeepInfra BGE-M3 pay-per-token API ($0.01/M token)** 로 어댑터 교체. 동일 모델 = R@10 회귀 0 보장. 항시-warm 서버리스 = 콜드스타트 사실상 제거. 월 비용 < $0.01 (페르소나 트래픽). 작업량 4~6h.
2. **fallback (v1.5 W-2 검증 후)**: 만약 DeepInfra latency 측정에서 P95 미달 시 → **HF Dedicated Inference Endpoint CPU x1 ($0.033/h, always-on)** 로 전환. 월 $24, 콜드스타트 0 보장. 작업량 어댑터 URL 토큰 교체만 = 1~2h.

**비권장**:
- self-host GPU (Modal·RunPod·Vast.ai·Fly): 운영 부담 vs 페르소나 트래픽 너무 적음. 비용 효율 X.
- BGE-M3 → e5-small 등 교체: chunks 37,057 재인제스트 비용 (HF API ~$0.10) 은 사소하지만 R@10 회귀 −5%p 위험 (MIRACL 한국어).
- Apple Silicon 로컬 TEI: 본 PC dev 시 좋지만 운영 endpoint 불가 (페르소나 A 환경 = Windows + iPhone + Android, "Windows 항상 켜둠" 보장 X).

**의사결정 필요 5건** (§4): 월 비용 상한 / 회귀 허용 / sprint 시점 / 우선순위 / 인제스트 path 동시 교체 여부.

총 v1.5 작업량: **8~14h** (W-1·W-2·W-3 + 회귀 측정).

---

## 1. 대안 옵션 비교

### 1.1 옵션 카탈로그

| ID | 옵션 | provider/구현 | 형태 | 월 비용 (페르소나) | 콜드스타트 | R@10 회귀 위험 | 한국어 |
|---:|---|---|---|---:|---|---|---|
| **A** | HF Dedicated Endpoint CPU x1 (always-on) | huggingface.co | dedicated | **$24/월** ($0.033/h × 720h) | **0s** | 0 (BGE-M3 동일) | ✅ |
| **A'** | HF Dedicated Endpoint T4 GPU (always-on) | huggingface.co | dedicated | $360/월 ($0.50/h × 720h) | 0s | 0 | ✅ |
| **A''** | HF Dedicated Endpoint scale-to-zero | huggingface.co | dedicated, idle=0 | $0~수$/월 | **수 분 (모델 재로딩)** ❌ | 0 | ✅ |
| **B-1** | Modal serverless A10G | modal.com | serverless | ~$1/월 + idle 0 | **수십초 (컨테이너 + weights)** ❌ | 0 | ✅ |
| **B-2** | RunPod Serverless | runpod.io | serverless | ~$1~3/월 | 수십초 ❌ | 0 | ✅ |
| **B-3** | Vast.ai always-on RTX 3090 | vast.ai | community marketplace | $115/월 ($0.16/h) | 0s | 0 | ✅ (운영 불안정) |
| **B-4** | Fly.io A10 (deprecated 8월) | fly.io | dedicated | $540/월 | 0s | 0 | ⚠️ Aug 1 EOL |
| **C-1** | AWS SageMaker async / serverless inference | aws.amazon.com | managed | ~$20~50/월 | 수십초 ❌ | 0 | ✅ |
| **C-2** | GCP Vertex AI dedicated endpoint | cloud.google.com | managed | $30+ /월 | 0s | 0 | ✅ |
| **D-1** | BGE-M3 → multilingual-e5-small (CPU 친화) | (어댑터 교체) | 모델 변경 | 동일 | warm 가능 | **−3 ~ −5pp 위험** | ⚠️ |
| **D-2** | BGE-M3 → KoSimCSE / dragonkue/BGE-m3-ko | (어댑터 교체) | 모델 변경 | 동일 | 동일 | 한국어 강화·영어 약화 | ✅ |
| **E** | 클라이언트사이드 / Transformers.js / MLX | (브라우저 또는 본 PC) | local | $0 | 0s | 0 (BGE-M3) | ✅ |
| **F-1** | warm-up cron (5분마다 ping) + 캐시 강화 | 현 어댑터 패치 | hack | $0 (요청 horizon 안에서) | **first call 후 0s** | 0 | ✅ |
| **F-2** | persistent embed_query 캐시 hit-rate 끌어올림 | 현 어댑터 | 보조 | $0 | 캐시 hit 만 0s | 0 | ✅ |
| **★★ G** | **DeepInfra BGE-M3 API ($0.01/M token)** | deepinfra.com | serverless, always-warm | **<$0.01/월** (page A 트래픽) | **무시 가능** (warm pool) | 0 | ✅ |

(★★ = 권장)

### 1.2 KPI #10 (P95 < 3s) 달성 가능성

| 옵션 | 추정 P95 | 게이트 달성 |
|---|---:|---|
| A | 150~250ms | ✅ |
| A' (T4) | 50~120ms | ✅ |
| A'' scale-to-zero | 30s+ (cold) | ❌ |
| B-1/B-2 serverless | 15~40s (cold) | ❌ (warm 만 ✅) |
| B-3 Vast.ai always-on | 150~300ms | ✅ (운영 안정성 변수) |
| B-4 Fly | 100ms | ✅ but Aug 1 EOL |
| C-2 Vertex always-on | 100~200ms | ✅ (운영 부담 ↑) |
| D-1/D-2 (warm 동일) | warm 시 OK / cold 동일 | ❌ (provider 동일 시) |
| E 로컬 | 본 PC 만 ✅, mobile 0 | ❌ (페르소나 A) |
| F-1 warm-up cron | first-of-period 만 cold, 나머지 0 | ⚠️ (idle 패턴 의존) |
| F-2 캐시 | hit 만 ✅ (현 16.7%) | ❌ (단독으로는 부족) |
| **G DeepInfra** | **170~400ms 추정 (warm pool)** | **✅** |

### 1.3 비용·복잡도·정확도 4축 정리

| 옵션 | 월 비용 (페르소나 A) | 운영 부담 | 정확도 | 한국어 | 종합 |
|---|---:|---|---|---|---|
| **G DeepInfra** | **<$0.01** | **0** (API 호출만) | **A+** (BGE-M3 동일) | **A** | **★★★** |
| A HF CPU always-on | $24 | low | A+ | A | ★★ |
| B-3 Vast.ai always-on | $115 | high (운영·장애) | A+ | A (불안정) | ★ |
| F-1 warm-up cron + F-2 캐시 | $0 (HF API 호출 ↑) | low | A+ | A | ★★ (보완책) |
| D-1 e5-small | $24 | low | A− (회귀) | B | ✗ |

---

## 2. 권장 옵션 + 근거

### 2.1 권장: **DeepInfra BGE-M3 pay-per-token (옵션 G) → fallback HF CPU always-on (옵션 A)**

**근거 1 — 페르소나·자원 배치 정합**:
- 메모리 `project_persona_kpi_decisions.md` 의 비용 자원 제약 + "개인 지식 관리" 포지셔닝 → **always-on GPU 운영 부담 = 페르소나 외**
- 페르소나 A 트래픽: 일일 ~30 query × 30 token 평균 × 30일 = **27,000 token/월 ≈ $0.00027/월**. 인제스트 (chunks 37k 회복용 재인제스트 발생 시): 37k chunk × 500 token ≈ 18.5M token × $0.01/M = **$0.185 일회성**
- HF Dedicated CPU ($24/월) 와 비교하면 **G 는 거의 무료**, 게다가 **항시-warm 서버리스 = 콜드스타트 사실상 제거**

**근거 2 — R@10 회귀 0 보장**:
- 동일 BGE-M3 모델 → embedding 결정성 (모델 weights 동일 / fp32 vs fp16 의 epsilon 차이만) → **chunks 재인제스트 불요** (기존 dense_vec 그대로 사용 가능)
- embed_query_cache (마이그 016) 의 `model_id=BAAI/bge-m3` 키 유지 = **캐시 hit 그대로 작동**

**근거 3 — 어댑터 layer 영향 최소**:
- `bgem3_hf_embedding.py` 의 `_URL` 한 줄 + auth header 교체만으로 swap 가능
- DeepInfra 는 OpenAI-compatible API 도 제공 → 어댑터 swap 안 해도 됨 (env URL 만 교체)

**근거 4 — fallback 경로 명확**:
- DeepInfra latency 실측 (W-2) 결과 P95 미달 시 → HF Dedicated CPU ($24/월) 로 즉시 전환. URL 토큰만 교체 = 1~2h
- $24/월 은 페르소나 A 의 무료 티어 초과 결정이지만 사용자 명시 승인 가능한 수준 (§4 의사결정 1)

**비권장 근거**:
- **self-host (Modal/RunPod/Vast.ai/Fly)**: 페르소나 A 트래픽 (월 27k token) 에 GPU 운영은 과잉. 비용은 always-on GPU 가 더 비싸고, serverless 는 콜드스타트 동일 문제.
- **BGE-M3 → 다른 모델 교체 (D-1·D-2)**: MIRACL 한국어에서 BGE-M3 가 mE5 대비 +4.6pp nDCG@10 → 회귀 위험. golden_v2 132 row 의 R@10 0.6829 가 회귀하면 PRD M2 결과 ($0.80 사실상 도달) 가 무효화.
- **로컬 / Transformers.js (E)**: 페르소나 A 의 모바일 환경 (Android Chrome / iPhone Safari) 에서 BGE-M3 wasm 로딩 = 1024dim 모델 다운로드 ~1GB · 모바일 RAM 부족.
- **warm-up cron (F-1)**: 보조책으로는 매력. 하지만 단독으로 P95 < 3s 보장 X — first-of-cold-period 가 여전히 cold. 어댑터 변경 없이 추가 적용 가능 → 본 권고에 보조 포함.

### 2.2 BGE-M3 가 아닌 모델 교체 결정의 함정

PRD M0~M2 의 baseline 측정 (golden_v2 182→132, R@10 0.6747~0.6829, top-1 0.7966) 은 모두 BGE-M3 기반이다. 모델 교체 = baseline 재측정 = PRD master 재검증 = **PRD scope 외 추가 sprint**. 비용은 KPI #10 1건 해소를 위해 PRD 전체를 다시 측정해야 함. 비효율.

---

## 3. v1.5 sprint scope

### 3.1 작업 항목 (W-0 ~ W-7 + 측정)

> **2026-05-15 senior-qa 검증 보강**: W-0 신설 — W-1 진입 전 dense_vec 결정성 시험. HF Inference Providers vs DeepInfra 가 fp16/normalize/pooling 차이로 embedding 미세 다를 가능성 → cosine ≥ 0.999 PASS 시 W-1 진입 (캐시·재인제스트 불요), FAIL 시 옵션 A (HF Dedicated CPU) fallback 직행.

| ID | 작업 | 작업량 | DoD |
|---:|---|---:|---|
| **W-0** (신설) | dense_vec 결정성 시험 — HF vs DeepInfra cosine | 1h | sample 100 chunk 대상 cosine ≥ 0.999, FAIL 시 옵션 A fallback. 비용 < $0.01 |
| **W-1** | DeepInfra BGE-M3 어댑터 추가 (또는 ENV URL 토글) | 2h | `JETRAG_EMBED_PROVIDER=deepinfra` ENV 로 swap, 단위 테스트 5건 신규 (인증·차원·retry), 영구 캐시 model_id 동일 |
| **W-2** | DeepInfra warm latency 실측 (12 sample × 3 라운드) | 1h | P95 측정 결과 < 3s 확인. 미달 시 W-2.5 진입 |
| **W-2.5** | (조건부) HF Dedicated CPU always-on endpoint 생성 + 어댑터 URL 교체 | 1~2h + 운영 setup | endpoint URL/token .env 등록, latency 재측정, 월 $24 사용자 승인 |
| **W-3** | KPI #6① R@10 회귀 측정 (golden_v2 132 row) | 1h | R@10 / top-1 / qtype breakdown 비교. ±1pp 안이면 PASS. 회귀 시 dense_vec 결정성 dump (sample 5 chunk embedding 비교) |
| **W-4** | KPI #11 인제스트 throughput 재측정 (1 doc reingest sample) | 1h | embed_batch latency / chunks/s 비교, 회귀 0 |
| **W-5** | warm-up cron (F-1, 보조책) — `embed_query("__warmup__")` 5분 cron 추가 | 1h | `health_warmup.py` script + crontab 예시 문서화. DeepInfra 도 효과 측정 |
| **W-6** | work-log + PRD v1.5 반영 + KPI #10 갱신 | 2h | `검색 latency P95 = X.Xs (게이트 3s vs 결과 ?)`, search_slo 30일 누적 |
| **W-7 (선택)** | dual-provider fallback layer — HF 503 / DeepInfra timeout 시 상호 fallback | 3~4h | 어댑터 chain 구현 (메인=DeepInfra, secondary=HF Inference Providers, ENV order) |

**총 작업량: 8~14h** (W-7 제외 시 8h, 포함 시 14h, W-2.5 발생 시 +1~2h)

### 3.2 DoD (sprint 완료 조건)

1. **KPI #10**: 검색 latency P95 < 3s (12 sample warm / 5 sample cold 평균)
2. **KPI #6①**: golden_v2 132 row R@10 회귀 ≤ **0.5pp** (현 0.6829 → ≥ 0.6779) — 1pp 는 M2 W-4 noise floor ±0.012 영역과 동일하므로 의미있는 게이트가 아님, senior-qa 권고에 따라 0.5pp 로 강화
3. **KPI #11**: 인제스트 throughput 회귀 0 (sample 1 doc reingest 비교)
4. 어댑터 swap 이 ENV 1개 토글로 가능 (Cloud→Local 어댑터 내러티브 §9.4 일관성 유지)
5. embed_query_cache (마이그 016) hit 그대로 작동 (model_id 키 유지)
6. work-log + PRD master v1.5 갱신, 단위 테스트 회귀 0 (현 1202 OK)

### 3.3 측정 protocol

- **latency 측정**: 12 sample × 3 라운드 (cold/warm/cache hit 모두). 첫 호출 = cold, 1분 idle 후 재호출 = warm 검증.
- **회귀 측정**: `evals/run_s4_a_d4_compose_off.py --goldenset v2 --out /tmp/v1_5_r10.md` (golden_v2 132 row). 전후 비교.
- **결정성 비교**: 동일 text 5개에 대해 HF Inference Providers vs DeepInfra dense_vec dump → cosine similarity ≥ 0.999 확인. 0.999 미만 시 dense_vec 재인제스트 필요 (PRD 재측정 트리거).

---

## 4. 의사결정 포인트 (사용자 결정 필요)

### Q1. 월 운영 비용 상한

- 옵션 G (DeepInfra): **< $1/월** (페르소나 트래픽)
- 옵션 A fallback (HF Dedicated CPU): **$24/월** ($288/년)
- 사용자 결정: $1 / $24 / $50 / 무제한 — **무료 티어 한도 §9.9 위반 검토 필요**

권장 안: **$24/월까지 사용자 승인** (옵션 A fallback 가능). 기본은 옵션 G ($1 이하).

### Q2. R@10 회귀 허용 범위

- 옵션 G (BGE-M3 동일): 회귀 0 예상
- 옵션 D-1/D-2 (모델 교체): 회귀 −3 ~ −5pp 가능
- 사용자 결정: 0pp / −1pp / −3pp / −5pp

권장 안: **0pp** (옵션 G 채택). −1pp 이상 회귀는 PRD 결과 무효화 = 별도 sprint.

### Q3. v1.5 sprint 진입 시점

- 현재 잔존: M3 진입 권고 (~1.5주 잡일 + KPI 8개 측정 + W-9 답변 UX). [[project_accuracy_80_prd]]
- 옵션 1: M3 직후 (~1.5주 후)
- 옵션 2: M3 와 병행 (KPI #10 만 v1.5, 나머지 M3 진행)
- 옵션 3: 즉시 v1.5 (M3 보류)

권장 안: **옵션 2 (병행)** — KPI #10 은 발표 카드 (US-06 검색 시연) 직전 차단점. M3 의 KPI 8 측정에 latency 가 들어가므로 v1.5 W-1 만 먼저 ship 하면 M3 KPI 측정이 정상 latency 로 가능.

### Q4. 모델 교체 vs self-host 우선순위

- 권장 = 둘 다 비채택 (옵션 G 가 둘 다 회피)
- 사용자가 self-host 를 학습 가치로 원하면 → 옵션 B-3 (Vast.ai $0.16/h) 또는 옵션 A' (HF T4 $0.50/h) 별도 trial

권장 안: **둘 다 보류. 옵션 G 우선.**

### Q5. 인제스트 path 도 함께 교체할지

- 현 어댑터의 `embed()` / `embed_batch()` 도 HF Inference Providers 호출 — DeepInfra 로 교체 시 인제스트 path 도 자동 교체됨
- 사용자 결정: 검색 path 만 교체 / 인제스트도 함께 교체 (간단·일관성) / 검색만 우선 (보수적)

권장 안: **함께 교체** (어댑터 layer 의 일관성 유지 — Cloud→Local v2 전환 시 깨지지 않음). 단 인제스트는 batch size · timeout 차이가 있을 수 있어 W-4 회귀 측정 필수.

---

## 5. 위험 / 사이드이펙트

### 5.1 dense_vec 결정성 (가장 큰 위험)

- HF Inference Providers BGE-M3 와 DeepInfra BGE-M3 의 **fp16/fp32 precision · normalize 처리 · pooling** 이 다를 수 있음
- **mitigation**: W-2.5 의 결정성 비교 (cosine ≥ 0.999) 가 0.999 미만이면 dense_vec 전체 재인제스트 비용 발생 — chunks 37k × 500 token × $0.01/M = **$0.185** (사소함) + 시간 수 시간 + PRD baseline 재측정 트리거
- **사전 대비**: W-2.5 PASS 시 무사. FAIL 시 의사결정 트리거 — "비용 $0.185 + 재측정 0.5일" 사용자 승인

### 5.2 self-host 운영 부담 (옵션 B 채택 시)

- GPU instance idle 시 비용 지속 (always-on $115/월 ~ $540/월)
- 장애 대응 (instance crash / driver issue / 네트워크) = 페르소나 A 1인 부담
- monitoring stack 구축 (Prometheus / Grafana / alert) 필요 = 페르소나 외 작업
- **본 권장 (옵션 G) 은 이 위험을 0 으로 회피**

### 5.3 검색 path / adapter layer 변경 범위

- 영향 모듈:
  - `api/app/adapters/impl/bgem3_hf_embedding.py` — `_URL` + auth header 교체 (또는 DeepInfra 어댑터 신규 클래스)
  - `api/app/config.py` — `JETRAG_EMBED_PROVIDER` ENV + `deepinfra_api_token` 신규 (또는 HF_API_TOKEN 재사용)
  - `embed_query_cache` — model_id 키 유지 (BGE-M3 동일 → 무영향)
  - `is_transient_hf_error` — DeepInfra 응답 status code 매핑 검증
- **영향 X**:
  - search.py / RRF / intent_router / chunk pipeline = **무영향** (어댑터 layer 가 protocol 차단 §9.4)
  - golden_v2 / 단위 테스트 1202 = **무영향** (mock-based)

### 5.4 캐시 무효화 위험

- 만약 DeepInfra dense_vec 가 HF 와 0.999 미만 cosine → embed_query_cache (마이그 016) 의 기존 entry 가 stale
- **mitigation**: model_id 를 `BAAI/bge-m3@deepinfra` 형태로 분기 → 기존 hit 유지하면서 새 entry 분리 (W-1 의 ENV 분기에 포함)

### 5.5 페르소나 포지셔닝 위반 검토

- DeepInfra account 등록 = 외부 SaaS 1건 추가 = 페르소나 A 의 "혼자 사용하는 개인 도구" 정합
- HF account 와 동일 수준의 의존성 — 포지셔닝 위반 X
- 다만 발표 시 "Cloud-first MVP" 내러티브에 DeepInfra 추가 부담 — 어댑터 layer 의 swap 가능성을 발표 카드로 활용 가능 (§9.4 어댑터 내러티브 강화)

### 5.6 v1.5 sprint 외 영향

- M3 의 KPI 8개 측정 (특히 latency-sensitive: search SLO p50/p95 / answer total) 이 v1.5 후 재측정 필요
- search_slo 30일 누적 = v1.5 ship 이후 재시작 (search_metrics reset W10 Day 3 기 구현 활용)

---

## 6. 인용 / 참조

### 6.1 외부 가격 검증

- HF Inference Endpoints 공식 pricing (smallest CPU $0.033/h, T4 $0.50/h, scale-to-zero idle 무과금 but cold-start 수 분)
- Modal pricing (A10G $0.000306/s ≈ $1.10/h, 콜드스타트 부분 과금)
- RunPod (Serverless per-second), Vast.ai (RTX 3090 $0.16/h)
- DeepInfra BGE-M3 ($0.01/M token, OpenAI-compatible API)
- Fly.io GPU (A10 $0.75/h, **2026-08-01 deprecated** ⚠️)

### 6.2 정확도 검증

- BGE-M3 MIRACL nDCG@10 = 70.0 (vs mE5 65.4, +4.6pp 우위)
- BGE-M3 한국어 강세 검증 (외부 벤치마크)

### 6.3 본 프로젝트 참조

- `api/app/adapters/impl/bgem3_hf_embedding.py` — 어댑터 교체 대상
- `api/app/config.py` (hf_api_token) — ENV 추가 위치
- `work-log/2026-05-15 세션 종합 — 데이터 정리 + audit + robustness + 새 도메인 진단 핸드오프.md` §3.2 — KPI #10 측정 결과
- `work-log/2026-05-12 검색 정확도 80% 달성 PRD.md` v1.4 KPI #10 deferred
- 메모리 `project_persona_kpi_decisions.md` — 비용 자원 결정 컨텍스트

### 6.4 외부 출처

- huggingface.co/pricing (Inference Endpoints)
- huggingface.co/docs/inference-endpoints/en/pricing
- modal.com/pricing
- www.runpod.io/pricing
- deepinfra.com/pricing — BGE-M3 $0.01/M token
- deepinfra.com/BAAI/bge-m3/api
- fly.io/docs/gpus/ (Aug 1 EOL)
- huggingface.co/BAAI/bge-m3 (model card)
- arxiv.org/html/2402.03216v3 (BGE M3 paper)

---

## 7. 다음 액션

1. **사용자 결정**: §4 Q1~Q5 응답 (특히 Q1 비용 상한, Q3 sprint 시점)
2. **W-1 진입 조건 충족 시**: senior-developer 에이전트에 W-1 (DeepInfra 어댑터 swap) 위임. 본 보고서 §3.1 W-1 DoD 첨부
3. **fallback 시나리오**: W-2 latency 측정 결과 DeepInfra 가 게이트 미달이면 W-2.5 분기 — HF Dedicated CPU endpoint 사용자 승인 + setup
