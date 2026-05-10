# 2026-05-10 visual_grounding RAGAS 통합 ship — 보조 metric 노출

> Sprint: 2순위 (1~4순위 잔여) — visual_grounding RAGAS 통합
> 작성: 2026-05-10
> 마감: `run_ragas_regression.py` 에 `--with-visual-grounding` flag + measure_row 통합 + render
> 입력: 1~4순위 plan 문서 §2 + Batch 2+3 의 visual_grounding helper

---

## 0. 한 줄 요약

> **visual_grounding RAGAS 통합 ship** — Batch 2+3 의 `_visual_grounding.py` helper 를 `run_ragas_regression.py` 에 통합. `--with-visual-grounding` flag opt-in, BGE-M3 cosine (vision OCR caption ↔ answer) score 계산. `RowMeasurement.visual_grounding` 필드 + `_METRICS` / `_INDUSTRY_FLOOR` (0.5) 통합 → aggregate / qtype_breakdown / per-row markdown 자동 노출. **vision_diagram qtype 의 보조 metric** — Faithfulness 의 vision 한계 (G-U-005, G-A-213 모두 0.0) 보완. cost 0 (BGE-M3 free), 단 latency ↑ — 한계 #3/#8/#12 영향 가능. 단위 테스트 857 → **861 (+4) / 회귀 0**.

---

## 1. 변경 내역

### 1.1 `evals/run_ragas_regression.py` 통합 (+~50 LOC)

**RowMeasurement 확장**:
```python
@dataclass
class RowMeasurement:
    ...
    visual_grounding: float | None = None  # 신규
```

**_METRICS / _INDUSTRY_FLOOR 통합**:
```python
_METRICS = ("faithfulness", "answer_relevancy", "context_precision", "visual_grounding")

_INDUSTRY_FLOOR = {
    ...,
    "visual_grounding": 0.50,  # caption-text BGE-M3 cosine, 0.5 = 약한 매칭
}
```

→ aggregate / qtype_breakdown / threshold guard / per-row 자동 노출 (loop 가 _METRICS 순회).

**`_compute_visual_grounding_safe(answer, contexts)` 신규**:
- BGE-M3 provider 가져오기 (graceful import)
- `_visual_grounding.compute_visual_grounding` 호출
- 실패 시 None (run 차단 X)

**`measure_row(row, *, with_visual_grounding=False)` 확장**:
- opt-in flag 시 RAGAS evaluate 후 visual_grounding 계산
- 실패 graceful → score=None

**CLI flag**:
- `--with-visual-grounding` — 측정 시 BGE-M3 호출 추가

**Render 통합**:
- markdown header: "visual_grounding: BGE-M3 cosine (vision caption ↔ answer, opt-in)" 명시
- qtype breakdown 컬럼 추가
- per-row 컬럼 추가

### 1.2 단위 테스트 (4 신규)

`VisualGroundingIntegrationTest`:
- `test_visual_grounding_in_metrics` — _METRICS 에 포함
- `test_visual_grounding_industry_floor` — _INDUSTRY_FLOOR=0.5
- `test_aggregate_handles_visual_grounding` — None graceful + mean 계산
- `test_row_measurement_has_visual_grounding_field` — dataclass field default None

### 1.3 검증

- **단위 테스트**: 857 → **861 (+4) / OK / skipped=1 / 회귀 0**
- **dry-run**: `--with-visual-grounding` flag 정상 parse + sample 분포 확인 ✅
- **실 측정**: 별도 sprint (cost 동일, 단 BGE-M3 latency ↑)

---

## 2. 사용 예시

### 2.1 visual_grounding 포함 측정

```bash
DEFAULT_USER_ID='...' uv run python ../evals/run_ragas_regression.py \
  --max-rows 30 --seed 42 --skip-context-precision \
  --with-visual-grounding \
  --cost-cap-usd 0.15
```

→ 30 row × (Faithfulness + ResponseRelevancy + visual_grounding) 측정.
→ vision-derived contexts 가 있는 row (대부분 vision_diagram) 만 visual_grounding score 산출.
→ 그 외 row (vision context 없음) → visual_grounding=None (skip).

### 2.2 결과 예상 (vision_diagram qtype)

| metric | 직전 (without VG) | 예상 (with VG) | 해석 |
|---|---:|---:|---|
| faithfulness (G-U-005) | 0.000 | 0.000 (변동 X) | LLM judge 한계 그대로 |
| **visual_grounding (G-U-005)** | (없음) | **0.4~0.7 추정** | caption-text 매칭 보강 |

→ Faithfulness 0.0 + visual_grounding > 0.5 면 "LLM judge 가 vision claim 검증 못 했지만 답변이 vision content 의 의미 반영함" 해석 가능.

---

## 3. 비판적 한계 (정직 인정)

| # | 한계 | 영향 | 회복 |
|---|---|---|---|
| 1 | **caption-text matching 만** | 답변과 caption 의 의미 매칭 — diagram 자체 검증 X | multimodal LLM judge (별도 sprint, cost ~$0.10~$0.30) |
| 2 | **vision context 없으면 None** | 일반 qtype (exact_fact, etc.) 는 visual_grounding=None | 의도된 동작 — 보조 metric, 일반 qtype 미적용 |
| 3 | **BGE-M3 HF API latency** | --with-visual-grounding 시 row 당 ~5~73s 추가 | --skip-context-precision 와 함께 사용 권고 (latency 감소) |
| 4 | **cost 추정에 visual_grounding 미반영** | --cost-cap-usd 가 BGE-M3 free 라 추정 차이 X (단 latency 영향 fix X) | 실 측정 후 cost-per-row 조정 |
| 5 | **실 측정 미진행** | 본 sprint 는 인프라만 ship | 별도 sprint (cost 0, BGE-M3 free, 단 latency 추가) |

---

## 4. 사용자 결정 보류 항목 변동

| ID | 항목 | 직전 | 갱신 |
|---|---|---|---|
| Q-vg-rags-integration | visual_grounding RAGAS 통합 | 별도 sprint | **해소 ✅** |
| Q-vg-measure | 실 측정 (--with-visual-grounding 적용) | 신규 | 별도 sprint (cost 0, latency ↑) |
| Q-vg-multimodal | multimodal LLM judge | 별도 (별도 sprint plan) | 진행 시 본 인프라 와 직교 사용 가능 |

---

## 5. 다음 후보 (1~4순위 잔여)

- **1순위 — search retrieval 단 정밀화** (cost 0, 1~2 day) — G-A-204 ch 919 회복, 운영 코드 변경
- **3순위 — multimodal LLM judge** (cost ~$0.10~$0.30, 1 day)
- **4순위 — S4-B 핵심 엔티티 추출** (cost 0, 3 day, master plan 잔여)

---

## 6. 핵심 변경 파일 목록

### 수정
- `evals/run_ragas_regression.py` — `_METRICS` / `_INDUSTRY_FLOOR` / `RowMeasurement` / `measure_row` / render 함수 통합 + `--with-visual-grounding` flag

### 추가
- `api/tests/test_run_ragas_regression.py` — `VisualGroundingIntegrationTest` 4 tests
- 본 work-log

### 데이터 영향
- 0 건

### 운영 코드 변경 영향
- 0 건 (`api/app/` 수정 없음)
- run_ragas_regression default 동작 X (opt-in via flag)

### 외부 cost
- 0 (인프라 통합, 호출 0)
- 누적 (이번 세션 전체): ~$0.475 (변동 없음)

---

## 7. 한 문장 마감

> **2026-05-10 — visual_grounding RAGAS 통합 ship**. helper module 을 `run_ragas_regression.py` 에 통합 — `--with-visual-grounding` flag, `_METRICS`/`_INDUSTRY_FLOOR` 자동 통합, vision_diagram qtype 의 Faithfulness 한계 보완. 단위 테스트 857 → **861 (+4) / 회귀 0**. 누적 cost 변동 0. 다음 = 1순위 (search retrieval 정밀화) 또는 3순위 (multimodal judge, cost 발생).
