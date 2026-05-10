# 2026-05-10 cost 가드레일 80% 알림 — `_cost_guard` 모듈 ship

> Sprint: cost 가드레일 인프라 — 80% alert + 100% break + 누적 cost 추적
> 작성: 2026-05-10
> 마감: `evals/_cost_guard.py` 신규 + `run_ragas_regression.py` / `generate_golden_rows.py` 통합 + 9 unit tests
> 입력: 직전 sprint 의 RAGAS n=30 재측정 시 누적 cost +0.3% 초과 (~$0.31, 승인 $0.30) 사건

---

## 0. 한 줄 요약

> **cost 가드레일 ship — `_cost_guard.py` 모듈 + 두 cost-incurring 스크립트 통합**. `CostGuard` dataclass 가 누적 cost 추적 + 80% 도달 시 stderr ⚠ alert (1회 발화) + 100% 초과 예측 시 break + partial ship. **opt-in via `--cost-cap-usd` flag** (default None = 비활성, 기존 동작 유지). `run_ragas_regression.py` (cost-per-row 0.005 default) + `generate_golden_rows.py` (cost-per-qtype 0.015 default) 통합. 9 unit tests + 부동소수점 epsilon 보호. **단위 테스트 814 → 823 (+9) / 회귀 0**. 누적 cost 변동 0 (인프라 추가, 호출 0).

---

## 1. 변경 내역

### 1.1 신규 — `evals/_cost_guard.py` (~110 LOC)

**핵심 API**:
```python
from _cost_guard import CostGuard, GuardAction

guard = CostGuard(cap_usd=0.30, est_per_unit=0.005)
for i, item in enumerate(items, start=1):
    action = guard.before_unit(unit_n=1)
    if action == GuardAction.BREAK:
        print(f"[cost-cap] {guard.summary()} → break", file=sys.stderr)
        break
    elif action == GuardAction.ALERT:
        print(f"[cost-cap] ⚠ {guard.summary()}", file=sys.stderr)
    do_unit(item)
    guard.add_actual(0.005)  # 실측 또는 추정
```

**동작**:
- `cap_usd=None / 0` → 항상 PROCEED (비활성)
- `actual_total >= cap × 0.80` → ALERT 1회 발화 (재발 차단)
- `actual_total + est_per_unit > cap` → BREAK
- `summary()` → `"누적 $0.18 / cap $0.30 (60.0%)"`
- 부동소수점 epsilon (1e-9) — 0.1×8=0.79... case 회피

### 1.2 `evals/run_ragas_regression.py` 통합

**신규 CLI flags**:
- `--cost-cap-usd` (default None) — cap 활성화
- `--cost-per-row-usd` (default 0.005) — Gemini 2.5 Flash + Faithfulness/ResponseRelevancy 추정 cost

**main loop 통합**:
- 매 row 측정 전 `guard.before_unit()` 체크
- BREAK → stderr 출력 + break (records 부분 ship)
- ALERT → stderr ⚠ 출력 + 진행
- 측정 성공 (rec.error 없음) 시 `guard.add_actual(cost_per_row_usd)`

### 1.3 `evals/generate_golden_rows.py` 통합

**신규 CLI flags**:
- `--cost-cap-usd` (default None)
- `--cost-per-qtype-usd` (default 0.015) — Gemini 2.5 Flash + few-shot prompt + 5 candidate 생성 추정 cost

**main loop 통합** — qtype 단위로 cap 체크 + 누적.

### 1.4 신규 — `api/tests/test_cost_guard.py` (9 tests)

- `CostGuardDisabledTest` (3): None cap / 0 cap / disabled summary
- `CostGuardAlertTest` (2): 80% 도달 ALERT / ALERT 1회 발화
- `CostGuardBreakTest` (2): projected exceeds → BREAK / unit_n multiplier
- `CostGuardSummaryTest` (2): summary 형식 / 음수 cost graceful

부동소수점 정밀도 검증 (0.1 × 8 = 0.7999... case) — epsilon 1e-9 적용으로 통과.

### 1.5 검증

- **단위 테스트**: 814 → **823 (+9) / OK / skipped=1 / 회귀 0**
- **dry-run smoke**:
  - `run_ragas_regression --dry-run --max-rows 5` → sample 결정 정상
  - `generate_golden_rows --dry-run --qtypes synonym_mismatch --count-per-qtype 1` → prompt 출력 정상

---

## 2. 사용 예시

### 2.1 RAGAS regression — cost cap $0.30

```bash
DEFAULT_USER_ID='...' uv run python ../evals/run_ragas_regression.py \
  --max-rows 30 --seed 42 --skip-context-precision \
  --cost-cap-usd 0.30 --cost-per-row-usd 0.005

# 24/30 row 측정 시 누적 ~$0.12 (40%) → PROCEED
# 48/60 (예시) 측정 시 누적 ~$0.24 (80%) → stderr ⚠ alert
# 60/60 시 누적 ~$0.30 → 다음 row projected $0.305 > $0.30 → BREAK + partial ship
```

### 2.2 generate candidates — cost cap $0.10 (보수)

```bash
uv run python ../evals/generate_golden_rows.py \
  --qtypes synonym_mismatch vision_diagram fuzzy_memory \
  --count-per-qtype 5 \
  --cost-cap-usd 0.10
# 3 qtype × $0.015 = $0.045 → 모두 진행 (45% 도달)
```

### 2.3 비활성 (default, 기존 동작)

```bash
# --cost-cap-usd 미지정 시 가드레일 비활성, 기존 동작 그대로
uv run python ../evals/run_ragas_regression.py --max-rows 30 --seed 42
```

---

## 3. 비판적 한계 (정직 인정)

| # | 한계 | 영향 | 회복 |
|---|---|---|---|
| 1 | **cost 추정 (실측 X)** | Gemini API 가 호출당 cost 미반환 → cost-per-unit 휴리스틱 | Gemini API usage_metadata 가 향후 cost 노출 시 실측 갱신 (별도 sprint) |
| 2 | **per-unit 추정값 보수적** | 실 cost 가 추정보다 작으면 일찍 alert/break (false positive) | 사용 후 실 누적 cost 와 비교하여 추정값 조정 (별도 sprint) |
| 3 | **불연속 단위만 cap 체크** | row 1개 안에서 multiple LLM call 시 누적 추적 X — 큰 row 1개로 cap 1.5× 초과 가능 | per-call granular 추적 (별도 sprint) — RAGAS 의 internal call 분리 추적 |
| 4 | **opt-in default 비활성** | 사용자가 `--cost-cap-usd` 명시 안 하면 가드 X | 향후 가드 default 활성 검토 (큰 안전 vs ergonomic 충돌) |
| 5 | **두 스크립트만 통합** | 다른 cost-incurring 작업 (D5 reingest 등) 미적용 | 점진 통합 (별도 sprint) |

---

## 4. 사용자 결정 보류 항목 변동

| ID | 항목 | 직전 | 갱신 |
|---|---|---|---|
| Q-cost-cap | cost 가드레일 80% 알림 절차 | 신규 | **해소 ✅** — 인프라 ship |
| Q-cost-cap-default | 가드레일 default 활성 여부 | 신규 | **별도 결정** — 현재 opt-in, 안전성 vs ergonomic |
| Q-cost-actual-tracking | Gemini API 실측 cost 통합 | 신규 | 별도 sprint — usage_metadata 노출 시 |

---

## 5. 다음 후보 우선순위

### 5.1 1순위 — TOC default ON 채택 결정 (cost 0, 0.1 day, 사용자 결정)

(이전 sprint 와 동일) net +0.0050 R@10 / +0.006 top-1 / 회귀 0.

### 5.2 2순위 — generate_golden_rows.py prompt 강화 (cost 0, 0.5 day)

expected_summary 자동 chunk-text 회피 명시. 다음 자동 생성 시 정확도 ↑.

### 5.3 3순위 — search vision 표 매칭 정밀화 (cost 0, 1 day)

G-A-204 의 919 (요약표 part 2) 가 search top-10 밖인 문제 — caption 강화 또는 dense embedding 한계 분석.

### 5.4 4~10순위

| # | 후보 | 작업량 | cost | 권고도 |
|---|---|---|---|---|
| 4 | RAGAS n=30 재측정 (정정 효과) | 0.5 day | ~$0.10 (cap=$0.15) | ★★ |
| 5 | uvicorn 좀비 모니터링 자동화 | 0.5 day | 0 | ★ |
| 6 | cross_doc qtype 자동 생성 (B 후속) | 1 day | ~$0.05 (cap=$0.08) | ★★ |
| 7 | visual_grounding metric 신설 | 1 day | ~$0.05 | ★★ |
| 8 | acceptable_chunks LLM-judge 자동 보완 | 1 day | ~$0.10 (cap=$0.15) | ★★ |
| 9 | S4-B 핵심 엔티티 추출 | 3 day | 0 | ★★ |

---

## 6. 핵심 변경 파일 목록

### 신규
- `evals/_cost_guard.py` (~110 LOC) — 가드레일 helper
- `api/tests/test_cost_guard.py` (9 tests)
- 본 work-log

### 수정
- `evals/run_ragas_regression.py` — `--cost-cap-usd` / `--cost-per-row-usd` flags + main loop 통합
- `evals/generate_golden_rows.py` — `--cost-cap-usd` / `--cost-per-qtype-usd` flags + main loop 통합

### 데이터 영향
- 0 건

### 운영 코드 변경 영향
- 0 건 (`api/app/` / `web/src/` 수정 없음)
- 측정 도구 default behavior 변동 X (opt-in via flag)

### 외부 cost
- 0 (인프라 추가, 호출 0)
- 누적 (이번 세션 전체): ~$0.31 (변동 없음)

---

## 7. 한 문장 마감

> **2026-05-10 — cost 가드레일 ship**. `evals/_cost_guard.py` 모듈 + 9 unit tests + 두 cost-incurring 스크립트 (RAGAS regression / generate candidates) opt-in 통합. CostGuard 가 누적 추정 cost 추적 + 80% alert (1회 발화) + 100% break + partial ship. 부동소수점 epsilon 1e-9 보호. 단위 테스트 814 → **823 (+9) / 회귀 0**. 누적 cost 변동 0. 다음 1순위 = **TOC default ON 채택 결정** (사용자) 또는 **generate prompt 강화** (cost 0, 0.5 day).
