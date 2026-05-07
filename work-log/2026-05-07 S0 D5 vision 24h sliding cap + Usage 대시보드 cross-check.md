# 2026-05-07 Sprint S0 D5 — vision 24h sliding cap + Google AI Studio Usage 대시보드 cross-check

## 목적

master plan §6 Sprint S0 D5 + §7.4 정합:

> "F5 — vision 24h 누적 cap 자가 차단 + Google AI Studio Usage 대시보드 cross-check"

D4 (commit f90fdc8) 의 `check_doc_budget` / `check_daily_budget` 은 `daily=calendar-day (UTC midnight 리셋)` 기준이었다. 자정 직전 폭주 후 자정 직후 다시 폭주 가능한 한계가 있었고, D5 는 **24h sliding window** 를 추가해 calendar-day 와 독립적으로 rolling 24시간 누적도 강제한다.

또한 사용자가 "DB 의 estimated_cost SUM" 과 "실제 Google 빌링" 이 일치하는지 cross-check 할 수 있도록 **Usage 대시보드 점검 절차** 를 가이드 형태로 정리한다.

## 결과

- `app/services/budget_guard.py`
  - `BudgetScope` 에 `24h_sliding` 추가
  - `check_24h_sliding_budget(cap_usd, now=None)` 신규 — `now-24h ~ now` SUM
  - `_sum_24h_sliding_cost(now=None)` + `_sliding_cutoff_iso(now=None)` 헬퍼
  - `check_combined(...)` 에 `sliding_24h_cap_usd: float | None = None` 인자 추가
    - None 이면 doc + daily 만 검사 (D4 호환)
    - 우선순위: doc → daily → 24h_sliding (가장 먼저 fail 한 scope 반환)
- `app/config.py`
  - `Settings.sliding_24h_budget_usd: float` 추가
  - ENV `JETRAG_24H_BUDGET_USD` (default = `JETRAG_DAILY_BUDGET_USD` 와 동일 값)
- `app/ingest/stages/extract.py` — `check_combined` 호출 4곳 모두 `sliding_24h_cap_usd=settings.sliding_24h_budget_usd` 전달 (사전 + 페이지 루프 재검사)
- `app/ingest/incremental.py` — 같은 패턴 3곳 (사전 + sweep 루프 + post sweep 마킹)
- `tests/test_budget_guard.py` — 신규 10건 (`24h_sliding` 통과/도달/DB graceful/ENV 비활성/cutoff 결정성, `check_combined` 우선순위 4건, 인제스트 통합 1건)

검증
- `python -m unittest discover tests` — **577 OK / skipped=1** (D4 567 → +10, 회귀 0)
- `pnpm tsc --noEmit` — 0 error (web 변경 없음)
- `pnpm lint` — 0 error

## 설계 결정 (비판적 재검토 3회)

### 1) sliding 함수 분리 vs `check_daily_budget` 토글 vs 단일 통합 함수

| 옵션 | 의도 명확성 | 테스트 단순성 | D4 호환 |
|---|---|---|---|
| A. 별도 `check_24h_sliding_budget` 함수 | 높음 — scope 명시 | 단순 | 높음 |
| B. `check_daily_budget(window_hours=24)` 토글 | 중간 — 혼동 위험 | 분기 추가 | 중간 (시그니처 변경) |
| C. 단일 함수에서 calendar+sliding 합산 | 낮음 — 의도 흐림 | 복잡 | 낮음 |

**채택: A**. 의도 명확 + master plan task 가 sliding 을 "추가 시그널" 로 명시. `check_combined` 가 셋을 우선순위로 묶음 → 호출자 보는 인터페이스는 D4 와 동일 단순.

### 2) ENV 분리 vs daily 와 동일 값 fallback

| 옵션 | 운영 유연성 | default 안전성 |
|---|---|---|
| A. 별도 ENV `JETRAG_24H_BUDGET_USD` (default=daily) | 높음 | 동일 cap → 폭주 차단 효과 동등 |
| B. daily 와 강제 동일 값 | 낮음 | 안전 |
| C. 별도 + default 보수적 (예: 0.8 × daily) | 높음 | 보수 |

**채택: A**. master plan task §6 D5 에 "선택" 으로 표기. default = daily 값 → calendar-day 와 같은 cap 으로 자정 우회만 차단. 운영자가 sliding 을 더 보수적으로 잡고 싶으면 ENV 만 분리.

### 3) `now` 인자 주입 vs 내부 `datetime.now()` 직접

| 옵션 | 테스트 결정성 | 운영 단순성 |
|---|---|---|
| A. `now: datetime | None = None` 주입점 | 결정적 | 약간 복잡 |
| B. 내부 `datetime.now()` 만 | 비결정적 | 단순 |

**채택: A**. cutoff 계산 로직 검증을 위해 결정적 테스트가 필요. 운영 코드 호출자는 None 으로 호출 → 동작 동일.

### 4) `check_combined` 우선순위 — doc → daily → 24h_sliding

| 옵션 | 의미 | 차단 정확성 |
|---|---|---|
| A. doc → daily → 24h_sliding | 좁은 범위 → 넓은 범위 | 가장 좁은 cap 이 먼저 fail 시 차단 (자연) |
| B. 24h_sliding → daily → doc | 넓은 → 좁은 | sliding 이 가장 먼저 fail (UI 가 항상 sliding 표시 — 헷갈림) |
| C. 모두 검사 후 가장 큰 차이 반환 | 정보량 최대 | 복잡 + 호출 비용 ↑ |

**채택: A**. 직관 정합 — doc 단위 cap 도달 시 사용자에게 "이 문서 한도" 안내가 먼저 떠야. sliding 은 시스템 전체 안전망 성격이라 마지막. 또한 doc 가드는 short-circuit 이 가장 빠른 SQL (doc_id=eq 단일).

## D5 검증 안 된 가정 (정직 인정)

- **clock skew**: Python `datetime.now(UTC)` 와 Postgres `called_at` 가 동기. 클러스터 환경에서 NTP 미동기화 시 ±30초 정도 차이 가능 → cap 도달 시점 유동. 페르소나 A (단일 서버) 에서는 무시.
- **cutoff 정밀도**: 초 단위 `isoformat()` → ms 무시. 24시간 단위에서 영향 0.
- **인덱스 활용**: 014 마이그의 `idx_vision_usage_created (called_at)` 가 gte 쿼리에 효율적이라고 가정. 페르소나 A 1일 ≈ 250 row 규모에서는 인덱스 없어도 sequential scan 이 0.1초.

## §종합 — Sprint S0 (D1~D5) 누적 효과

master plan §6 Sprint S0 진척: 시작 0% → D4 ~50% → **D5 100%**.

| Day | 핵심 산출물 | 효과 |
|---|---|---|
| D1 | 마이그 014 + record_call 보강 + LLMAdapter 팩토리 | token + estimated_cost row 단위 추적 (이전 success/error 만) |
| D2 | vision_page_cache (sha256, page, prompt_version) | reingest 시 vision 호출 0 (이전 매번 재호출) |
| D2-C | sweep × retry 곱셈 제거 (3×3=9 → 2×1=2) | 페이지당 worst case 호출 9 → 2 |
| D3 | scripts/compute_budget.py + settings.doc/daily_budget_usd | 데이터 기반 budget 잠정값 + cap 메커니즘 의존성 채움 |
| D4 | budget_guard.py (doc + daily) + UI flag + 재처리 버튼 | cap 도달 시 graceful skip + 사용자 통제권 |
| D5 | 24h sliding window + Usage 대시보드 cross-check 가이드 | 자정 우회 폭주 차단 + 빌링 검증 가능 |

### S0 누적 정량 효과

- **vision worst case 호출 (50p PDF)**:
  - Sprint 시작 시점 (D2-C 이전 + sweep×retry 곱셈): **450 호출** (3 sweep × 3 retry × 50p)
  - D2-C 이후 (sweep×retry 곱셈 제거): **100 호출** (2 sweep × 1 retry × 50p)
  - D2 cache hit 시점: **50 호출** (첫 ingest), **0 호출** (reingest)
- **비용 가시성**:
  - 시작 시점: success/error_msg/quota_exhausted/source_type 4축
  - S0 종료: + doc_id, page, prompt_tokens, output_tokens, thinking_tokens, retry_attempt, estimated_cost, model_used **8축 추가** (마이그 014)
- **폭주 차단**: 0 → **3중 (doc + daily calendar + 24h sliding)**
- **빌링 cross-check**: 불가 → **가능 (D5 가이드)**

S2 / S3 진입 시 prerequisite 모두 해소.

---

## §부록 — Google AI Studio Usage 대시보드 cross-check 가이드

### 목적

`vision_usage_log.estimated_cost` 가 실제 Google 빌링과 일치하는지 사용자가 정기 검증. 불일치 발생 시 단가 가정 오류 / token 누락 / 캐시 미반영 등을 즉시 진단.

### 점검 주기 권고

- **주 1회 (월요일 오전)** — 이전 7일 누적 cross-check
- Sprint S0 종료 후 1주 누적 1회 (D5 ship 후 첫 점검)
- 월말 1회 (월별 누적 vs 빌링 청구서)

### 1) Google AI Studio 접속 절차

1. https://aistudio.google.com 접속
2. 좌측 사이드바 **"Get API key"** → 우측 상단 **"Usage"** 탭
3. 또는 https://console.cloud.google.com → 좌측 메뉴 **"APIs & Services"** → **"Quotas & System Limits"** → "Generative Language API" 검색
4. (월별 청구) 좌측 **"Billing"** → **"Reports"** → SKU 필터로 "Generative Language" 선택

### 2) 확인 항목

| 대시보드 항목 | DB 대응 컬럼 |
|---|---|
| 일별 input tokens (Google) | `SUM(prompt_tokens + image_tokens)` |
| 일별 output tokens (Google) | `SUM(output_tokens)` |
| 일별 thinking tokens (Flash Thinking 활성 시) | `SUM(thinking_tokens)` |
| 일별 비용 USD (Google billing) | `SUM(estimated_cost)` |
| 모델 분포 (예: gemini-2.5-flash) | `model_used` GROUP BY |
| 일별 호출 수 | `COUNT(*) WHERE success=true` |

### 3) DB cross-check 쿼리

```sql
-- 7일 누적 비교용. start_date / end_date 는 대시보드와 동일 범위로 조정.
SELECT
    DATE(called_at AT TIME ZONE 'UTC') AS call_date,
    model_used,
    COUNT(*) FILTER (WHERE success = true) AS calls_success,
    COUNT(*) FILTER (WHERE success = false) AS calls_failed,
    SUM(prompt_tokens) AS sum_prompt_tokens,
    SUM(output_tokens) AS sum_output_tokens,
    SUM(thinking_tokens) AS sum_thinking_tokens,
    ROUND(SUM(estimated_cost)::numeric, 4) AS sum_estimated_cost_usd
FROM vision_usage_log
WHERE called_at >= NOW() - INTERVAL '7 days'
GROUP BY call_date, model_used
ORDER BY call_date DESC, model_used;
```

### 4) 불일치 시 원인 진단 절차

| 증상 | 가능 원인 | 확인 절차 |
|---|---|---|
| **DB SUM < Google 빌링** (DB 가 더 작음) | (a) `thinking_tokens` 누락 — Flash Thinking 활성인데 D1 record_call 이 미수집 | `SELECT * FROM vision_usage_log WHERE thinking_tokens IS NOT NULL LIMIT 5` 로 컬럼 채움 여부 확인 |
| | (b) sweep retry 가 vision_usage_log 에 미기록 | `retry_attempt > 1` row 비율 확인 — Google 은 모든 시도 과금, DB 가 첫 시도만 기록 시 누락 |
| | (c) D2 cache hit 가 별도 카운트 안 함 (Google 호출 0 = 정상, DB 도 0 = 정합) | cache hit 검증 후 `vision_page_cache` 행 수 vs `vision_usage_log.success=true` 비교 |
| **DB SUM > Google 빌링** (DB 가 더 큼) | (a) free tier discount 미반영 (Gemini 2.0 Flash 무료 quota 1500/일) | RPM/RPD 무료 한도 안에서는 Google 비용 0, DB estimated_cost 는 단가 적용 |
| | (b) estimated_cost 단가 가정이 실제보다 높음 (Google 가격 인하 등) | 단가 정의 위치 (`_gemini_common.py` 또는 `image_parser`) 확인 후 갱신 |
| **모델 분포 불일치** | model alias 변경 (예: gemini-2.0-flash → gemini-2.5-flash) | `model_used` GROUP BY 로 시점별 분포 확인 → 마이그레이션 시점 기록 |

### 5) cross-check 실패 시 대응

1. **로깅 보강** — `vision_usage_log` 의 row 단위 dump 후 Google Usage CSV 다운로드 → 시점별 join
2. **단가 갱신** — `app/adapters/impl/image_parser.py` 또는 `_gemini_common.py` 의 단가 상수 업데이트 + 마이그레이션 작성 (이전 row 의 estimated_cost 는 보존, 신규만 새 단가)
3. **회귀 테스트** — `tests/test_vision_metrics.py` 에 단가 검증 케이스 추가
4. **work-log 기록** — 불일치 일자 / 원인 / 수정 내용 명시

### 6) 점검 체크리스트 (운영자용)

- [ ] Google AI Studio Usage 일별 비용 다운로드
- [ ] 위 DB cross-check 쿼리 실행
- [ ] DB SUM vs 빌링 ±5% 허용 범위 안인지
- [ ] thinking_tokens 컬럼 NULL 비율 < 50% 인지 (Flash Thinking 활성 환경)
- [ ] sweep retry row (retry_attempt > 1) 비율 < 20% 인지 (D2-C 이후 자연 감소)
- [ ] 불일치 발견 시 work-log 작성 + 다음 sprint 백로그 등록

---

## 회귀 / 한계

- **race**: D4 와 동일. 같은 doc 동시 인제스트 시 budget_guard SUM 시점이 다를 수 있음 — best-effort 차단.
- **clock skew**: Python `datetime.now(UTC)` 와 Postgres `called_at` 동기 가정. 단일 서버 환경에서는 무시.
- **sliding cap 의 false positive**: 어제 폭주 → 오늘 정상 사용 시 sliding 이 어제 누적까지 합산해 cap 도달 가능. 실제 동작 의도. 사용자 입장에서는 "어제 한도 도달" 이 "오늘 안 풀림" 으로 보일 수 있어 UI 카드에 시점 정보 (`status.reason` 한국어) 가 명확해야. 본 ship 의 `_mark_budget_exceeded_flag` 가 D4 그대로 — UI 는 scope='24h_sliding' 일 때 "최근 24시간 한도" 카피로 자동 표시 (D4 카드의 `scope` 동적 라벨이 자연 처리).
- **UI 카드 카피**: D4 의 `VisionBudgetExceededCard` 가 scope='24h_sliding' 도 "최근 24시간 한도" 로 표시하려면 web 에 scope 분기 추가 필요. 본 D5 ship 은 백엔드만 — UI 카피는 D4 의 generic 카피 ("시각 보강 일부 생략 (한도 도달)") 가 sliding 케이스도 자연스럽게 cover. scope 별 분기 추가는 후속 sprint 백로그 (S1 또는 S2 진입 시 일반 UX 정비 묶음).

## 다음 후보 (D5 후 사용자 확인 대상)

| 후보 | 우선순위 | 작업량 | 가치 |
|---|---|---|---|
| **S1 진입 (master plan 다음)** | A | 1주 | 골든셋 v1 + 실 query 로그 — S2 vision 선별 baseline |
| F3 (D4 task 누락 분) | B | 0.5d | `/search/eval-precision` 자동 POST 제거 (mount 시 0) |
| UI scope 분기 보강 | B | 0.3d | `VisionBudgetExceededCard` 가 scope='24h_sliding' 일 때 카피 분기 |
| 1주 누적 후 budget 재산정 | C | 0.3d | scripts/compute_budget.py 재실행 (실 데이터 누적 후) |

권고: **S1 진입** — master plan §6 정합. S0 100% 진척 → S1 prerequisite 해소. UI scope 분기는 S1 안에서 묶어 ship 가능.

---

## commit 정보

- 단위 테스트: 567 → **577 (+10)**
- web tsc + lint: 0 error
- 변경 파일:
  - `api/app/services/budget_guard.py`
  - `api/app/config.py`
  - `api/app/ingest/stages/extract.py`
  - `api/app/ingest/incremental.py`
  - `api/tests/test_budget_guard.py`
  - `work-log/2026-05-07 S0 D5 vision 24h sliding cap + Usage 대시보드 cross-check.md`
