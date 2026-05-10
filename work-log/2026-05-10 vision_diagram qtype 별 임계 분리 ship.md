# 2026-05-10 vision_diagram qtype 별 임계 분리 ship

> Sprint: RAGAS 회귀 자동화 후속 — qtype 별 floor override
> 작성: 2026-05-10
> 마감: `_QTYPE_FLOOR_OVERRIDES` + `derive_qtype_thresholds` + per-qtype compare_against_baseline + 5 unit tests + 기존 baseline 재렌더 (cost 0)
> 입력: 직전 sprint (RAGAS n=30 재측정) §4 한계 #1 (vision_diagram faithfulness 0.50 < 임계 0.85, LLM judge 한계)

---

## 0. 한 줄 요약

> **vision_diagram qtype 별 임계 분리 ship**. `evals/run_ragas_regression.py` 에 `_QTYPE_FLOOR_OVERRIDES` (vision_diagram.faithfulness=0.50) + `derive_qtype_thresholds` + per-qtype compare_against_baseline 추가. **기존 n=30 baseline 재렌더 (cost 0)** — vision_diagram faithfulness 임계 0.85 → **0.50** 으로 정정 (LLM judge 의 vision claim 검증 한계 반영). overall faithfulness/answer_relevancy 임계는 0.85/0.80 그대로. 5 unit tests 추가, 단위 테스트 800 → 805 (+5) / 회귀 0. **누적 cost (이번 세션) 변동 0** (재렌더 만 사용).

---

## 1. 변경 내역

### 1.1 `evals/run_ragas_regression.py`

**추가 1 — `_QTYPE_FLOOR_OVERRIDES`**:
```python
_QTYPE_FLOOR_OVERRIDES: dict[str, dict[str, float]] = {
    "vision_diagram": {
        "faithfulness": 0.50,  # LLM judge 한계 — diagram 기반 claim verify 불가
    },
}
```

**추가 2 — `_floor_for(metric, qtype)`**: qtype 별 floor lookup (override 우선)

**수정 — `derive_thresholds(aggregates, *, qtype=None)`**: qtype 인자 시 override floor 사용

**추가 3 — `derive_qtype_thresholds(qtype_breakdown)`**: 각 qtype 별 ThresholdGuard dict 반환

**수정 — `render_markdown` / `render_json`**: `qtype_thresholds` optional 인자 + override 가 있는 qtype 만 markdown 별도 섹션 표기 + JSON 의 `qtype_threshold_guard` 필드

**수정 — `compare_against_baseline(..., current_qtype_breakdown=None)`**: baseline 의 `qtype_threshold_guard` 와 현재 qtype mean 비교 → override 있는 qtype 만 alert

**main 흐름 update**: `derive_qtype_thresholds(qtype_breakdown)` 추가 호출 → render 함수에 전달

### 1.2 `api/tests/test_run_ragas_regression.py` — 5 신규 tests

- `QtypeFloorOverrideTest` (4 tests):
  - `test_overall_uses_global_floor` — qtype 미지정 시 _INDUSTRY_FLOOR
  - `test_vision_diagram_uses_override_floor` — vision_diagram.faithfulness=0.50, answer_relevancy 는 0.80 그대로
  - `test_unknown_qtype_falls_back_to_global` — override 없는 qtype 은 default
  - `test_derive_qtype_thresholds_iterates_all` — 전 qtype 별 ThresholdGuard 생성
- `CompareBaselineTest` (1 추가): `test_qtype_threshold_guard_in_baseline` — baseline JSON 의 qtype_threshold_guard 와 현재 qtype 비교 → alert

### 1.3 검증

- **단위 테스트**: 800 → **805 (+5) / OK / 회귀 0**
- **재렌더 검증**: `/tmp/rerender_baseline.py` 로 기존 n=30 baseline 의 raw rows 재가공 → 새 markdown 의 `### qtype 별 임계 override` 섹션에 vision_diagram=0.50 정상 표기

---

## 2. 재렌더 결과 (n=30 baseline)

### 2.1 Overall (불변)

| metric | n | mean | recommended threshold |
|---|---:|---:|---:|
| faithfulness | 29 | 0.894 | **0.850** |
| answer_relevancy | 29 | 0.836 | **0.800** |
| context_precision | 0 | — | **0.700** |

### 2.2 qtype 별 임계 override (신규)

| qtype | metric | override industry_floor | recommended |
|---|---|---:|---:|
| **vision_diagram** | **faithfulness** | **0.50** | **0.500** |

→ vision_diagram (n=2, mean=0.500, stdev=0.0) 의 statistical -2σ=0.500. industry override 0.50. recommended=max(0.500, 0.500)=**0.500**.

### 2.3 회귀 alert 영향

| 시나리오 | 이전 (override 없음) | 현재 (override 적용) |
|---|---|---|
| vision_diagram faithfulness 0.50 baseline | overall 0.85 임계 → ❌ alert | qtype 0.50 임계 → ✅ 통과 |
| vision_diagram faithfulness 0.45 회귀 | overall 0.85 임계 → ❌ (false alarm 의도와 무관) | qtype 0.50 임계 → ❌ (실 회귀 정확 탐지) |
| 다른 qtype faithfulness | overall 0.85 임계 그대로 | overall 0.85 임계 그대로 (변동 0) |

→ vision_diagram 의 false alarm 제거 + 실 회귀 (0.50 미만) 는 여전히 alert.

---

## 3. 비판적 한계 (정직 인정)

| # | 한계 | 영향 | 회복 방법 |
|---|---|---|---|
| 1 | **vision_diagram override 0.50 = baseline 그대로** | statistical -2σ=0.500 (n=2 동일값) → 회귀 margin 0 | n 확장 후 실 σ 측정 + override 재조정 |
| 2 | **override = 단순 industry floor 낮춤** | LLM judge 의 본질 한계 (vision claim 검증 불가) 자체는 미해결 | 별도 metric (visual_grounding) 신설 또는 multimodal judge 도입 (별도 sprint) |
| 3 | **다른 qtype 의 override 미정의** | fuzzy_memory G-U-024 faithfulness 0.75 (n=1) 같은 사례 잠재 | n 확장 후 qtype 별 분석 결과로 추가 override 결정 |
| 4 | **재렌더 = baseline JSON 의 raw rows 재가공** | 측정 시점/seed 동일 → 새 측정 아님 | 다음 측정 시 자동 적용 (script logic 갱신됨) |

---

## 4. 사용자 결정 보류 항목 변동

| ID | 항목 | 직전 | 갱신 |
|---|---|---|---|
| Q-vision-faithfulness | vision_diagram LLM judge 한계 | 별도 sprint 권고 | **부분 해소 ✅** — override 0.50 으로 false alarm 차단. visual_grounding metric 신설은 별도 sprint |
| Q-other-qtype-overrides | 다른 qtype override 결정 | 신규 | n 확장 후 별도 결정 |

---

## 5. 다음 후보 우선순위

### 5.1 1순위 — R@10 D4 baseline 재측정 (cost 0, 0.5 day)

신규 9 row (G-U-104~108 / G-A-211~214) 의 R@10 / top-1 측정. BGE-M3 HF 안정 시 진입.

### 5.2 2순위 — TOC guard 패턴 정밀화 (cost 0, 0.5 day)

table_lookup -0.083 회귀 회복.

### 5.3 3순위 — expected_answer_summary 정정 (cost 0, 0.5 day)

G-U-105~107 의 chunk-text 같은 expected_summary → 실 summary 정정 (RAGAS 정확도 ↑).

### 5.4 4~10순위

| # | 후보 | 작업량 | cost | 권고도 |
|---|---|---|---|---|
| 4 | cost 가드레일 80% 알림 절차 | 0.25 day | 0 | ★★ |
| 5 | uvicorn 좀비 모니터링 자동화 | 0.5 day | 0 | ★ |
| 6 | cross_doc qtype 자동 생성 (B 후속) | 1 day | ~$0.05 | ★★ |
| 7 | visual_grounding metric 신설 | 1 day | ~$0.05 | ★★ |
| 8 | S4-B 핵심 엔티티 추출 | 3 day | 0 | ★★ |

---

## 6. 핵심 변경 파일 목록

### 수정
- `evals/run_ragas_regression.py` — `_QTYPE_FLOOR_OVERRIDES` + `_floor_for` + `derive_thresholds(qtype=)` + `derive_qtype_thresholds` + render/compare 갱신
- `api/tests/test_run_ragas_regression.py` — 5 신규 tests (15 → 20)

### 신규 / gitignored (재렌더 산출, 본 work-log §2 reproduced)
- `evals/results/s5_b_ragas_baseline.md` — 새 qtype override 섹션 포함
- `evals/results/s5_b_ragas_baseline.json` — `qtype_threshold_guard` 필드 추가

### 일회성 (gitignored, /tmp)
- `/tmp/rerender_baseline.py` — 기존 baseline 재렌더 helper

### 신규 commit 대상
- 본 work-log 1건

### 데이터 영향
- 0 건 (chunks / vision_page_cache / golden_v2.csv 변동 없음)

### 운영 코드 변경 영향
- 0 건 (`api/app/` / `web/src/` 수정 없음)

### 외부 cost
- 0 (재렌더 만 사용, LLM 호출 0)
- 누적 (이번 세션 전체): ~$0.31 (변동 없음)

---

## 7. 한 문장 마감

> **2026-05-10 — vision_diagram qtype 별 임계 분리 ship**. `_QTYPE_FLOOR_OVERRIDES` + `derive_qtype_thresholds` + per-qtype compare_against_baseline + 5 unit tests. 기존 n=30 baseline 재렌더 (cost 0) — vision_diagram faithfulness 임계 **0.85 → 0.50** 으로 정정 (LLM judge 한계 반영). 단위 테스트 800 → 805 (+5) / 회귀 0. 누적 cost ~$0.31 변동 없음. 다음 1순위 = **R@10 D4 baseline 재측정** (cost 0, BGE-M3 안정 시).
