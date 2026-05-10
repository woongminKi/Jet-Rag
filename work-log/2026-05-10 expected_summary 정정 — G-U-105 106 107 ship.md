# 2026-05-10 expected_summary 정정 ship — G-U-105/106/107

> Sprint: B 단계 후속 — LLM 자동 생성 row 의 expected_answer_summary 정정
> 작성: 2026-05-10
> 마감: 3 row 의 chunk-text 형태 summary → 의미적 요약 형태 정정 (RAGAS 정확도 향상 input)
> 입력: B 단계 work-log §3 (G-U-105/106/107 chunk-text 형태 summary 한계)

---

## 0. 한 줄 요약

> **expected_summary 정정 ship — G-U-105/106/107 3 row**. B 단계의 LLM 자동 생성 시 expected_answer_summary 자리에 chunk-text 가 채워진 문제 fix. 3 row 모두 의미적 요약 (목적/내용/시점/조건 명시) 으로 정정. 다른 신규 row (G-U-104 / G-A-211 / G-A-214) 는 이미 적절한 summary 형태 → KEEP. **CSV 14 컬럼 무결성 ✅, row 수 178 변동 X**. RAGAS judge (faithfulness, answer_relevancy) 가 더 의미있는 ground truth 와 비교 가능 → 다음 RAGAS 재측정 시 정확도 향상 기대 (직접 측정은 별도 sprint, cost ~$0.10). 단위 테스트 814 OK / 회귀 0. 누적 cost 변동 0.

---

## 1. 변경 내역

### 1.1 `evals/golden_v2.csv` — 3 row expected_answer_summary 정정

#### G-U-105 (synonym_mismatch, 직제 규정)
```
query: "직제 규정에서 자산 관리 지침의 수정 사항은 무엇인가요?"

Before (chunk-text 형태):
  ⑨재산관리내규 중 다음과 같이 개정한다. 제6조 제1항 중 "경영본부장"을
  "경영전략처장", 제6조 제2항

After (의미 요약):
  재산관리내규 개정 사항 — 경영본부장이 경영전략처장으로 직제 변경 등
  관련 조항 정정 내용.
```

#### G-U-106 (synonym_mismatch, 한마음 시행일)
```
query: "한마음생활체육관 운영 내규의 적용 시작일과 바뀐 점을 알려주세요."

Before (chunk-text 형태):
  이 내규는 2022년 7월 1일부터 시행한다. 부칙 (내규 제709호) 이 내규는
  발령한 날부터 시행한다.

After (의미 요약):
  한마음생활체육관 운영 내규 시행일 — 본 내규는 2022년 7월 1일부터 시행,
  이후 2024년 4월 30일 개정안은 발령일부터 시행 (부칙 제709호).
```

#### G-U-107 (synonym_mismatch, 한마음 정기권)
```
query: "한마음생활체육관 정기권 이용 안내에 대해 알려주세요."

Before (chunk-text 형태):
  ※ 이용료는 1일 1회 입장 기준임. (일일입장: 2시간, 월 회원: 일 1회)
  [별표 2] <개정 2022

After (의미 요약):
  한마음생활체육관 정기권 이용 안내 — 이용료는 1일 1회 입장 기준
  (일일입장 2시간, 월 회원 일 1회 입장 가능). 회원카드 사용 [별표 2] 참조.
```

### 1.2 KEEP 결정 (이미 적절한 summary 형태)

| id | qtype | summary | 판정 |
|---|---|---|---|
| G-U-104 | synonym_mismatch | "개인정보 비식별화 방안" | 짧지만 의미 명확 — KEEP |
| G-A-211 | vision_diagram | "쏘나타 디 엣지의 파라메트릭 다이내믹스 테마가 적용된 외관 디자인 이미지." | 정상 — KEEP |
| G-A-214 | vision_diagram | "주요 국가들의 인플레이션 흐름과 전망을 보여주는 도표." | 정상 — KEEP |

### 1.3 검증

- **CSV schema**: 14 컬럼 무결성 ✅ (Python csv 로드 + set 비교)
- **row 수**: 178 (변동 X)
- **단위 테스트**: 814 / OK / skipped=1 / 회귀 0

---

## 2. 효과 (간접)

### 2.1 RAGAS judge 정확도 향상

기존 chunk-text 형태 summary 는 RAGAS judge (LLM) 가 ground truth 로 사용하기에 부적합:
- **Faithfulness**: 답변의 claim 이 ground truth 와 일치하는지 — chunk text 와 비교 시 형식 차이로 점수 변동
- **AnswerCorrectness** (사용 시): 답변의 정답 일치도 — chunk-text 는 "정답 형태" 가 아니라 "원문 발췌" 라 정확도 낮음

정정 후 의미 요약 형태 → judge 가 답변과 의미적 비교 가능 → RAGAS 점수 신뢰도 ↑.

### 2.2 measurement 영향

본 sprint 는 라벨 정확도만 향상. 실측 효과는 다음 RAGAS 재측정 시 확인 가능:
- 다음 RAGAS n=30 재측정 (cost ~$0.10) 시 G-U-105/107 의 점수 변동 확인 가능
- 별도 sprint (사용자 cost 승인 필요)

---

## 3. 비판적 한계 (정직 인정)

| # | 한계 | 영향 | 회복 |
|---|---|---|---|
| 1 | **의미 요약은 사람 작성** | 자동 일관성 보장 X (다음 자동 생성 row 도 같은 문제 잠재) | `generate_golden_rows.py` 의 prompt 강화 (별도 sprint) — "summary 자리에 chunk text 넣지 말 것" 명시 |
| 2 | **RAGAS 직접 재측정 미진행** | 정정 효과 정량 검증 X | 다음 RAGAS n=30 재측정 (cost ~$0.10) 시 자동 검증 |
| 3 | **G-U-104 짧은 summary** | "개인정보 비식별화 방안" 6 단어 — RAGAS judge 가 너무 sparse 로 평가 가능 | 필요 시 확장 (별도 sprint) |
| 4 | **다른 골든셋 row 의 summary 정확도 미점검** | 50+ row 의 summary 가 모두 적절한지 미검증 | acceptable_chunks LLM-judge 자동 보완 sprint 와 함께 진행 |

---

## 4. 사용자 결정 보류 항목 변동

| ID | 항목 | 직전 | 갱신 |
|---|---|---|---|
| Q-summary-fix | G-U-105~107 expected_summary 정정 | 별도 sprint | **해소 ✅** |
| Q-generate-prompt-strengthen | generate_golden_rows.py prompt 강화 | 신규 | 별도 sprint — "summary 자리 chunk text 금지" 명시 |
| Q-ragas-remeasure | 정정 후 RAGAS 재측정 | 신규 | 별도 sprint (사용자 cost ~$0.10 승인 필요) |

---

## 5. 다음 후보 우선순위

### 5.1 1순위 — TOC default ON 채택 결정 (cost 0, 0.1 day, 사용자 결정)

이전 sprint 와 동일. net +0.0050 R@10 / +0.006 top-1 / 회귀 0.

### 5.2 2순위 — cost 가드레일 80% 알림 절차 (cost 0, 0.25 day)

이번 세션 cost +0.3% 초과 학습 반영.

### 5.3 3순위 — generate_golden_rows.py prompt 강화 (cost 0, 0.5 day)

본 sprint 의 chunk-text 문제 자동화. 다음 자동 생성 시 같은 문제 재발 방지.

### 5.4 4~10순위

| # | 후보 | 작업량 | cost | 권고도 |
|---|---|---|---|---|
| 4 | search vision 표 매칭 정밀화 (G-A-204 919 회복) | 1 day | 0 | ★★ |
| 5 | RAGAS n=30 재측정 (정정 효과 검증) | 0.5 day | ~$0.10 | ★★ |
| 6 | uvicorn 좀비 모니터링 자동화 | 0.5 day | 0 | ★ |
| 7 | cross_doc qtype 자동 생성 (B 후속) | 1 day | ~$0.05 | ★★ |
| 8 | visual_grounding metric 신설 | 1 day | ~$0.05 | ★★ |
| 9 | acceptable_chunks LLM-judge 자동 보완 | 1 day | ~$0.10 | ★★ |
| 10 | S4-B 핵심 엔티티 추출 | 3 day | 0 | ★★ |

---

## 6. 핵심 변경 파일 목록

### 수정
- `evals/golden_v2.csv` — G-U-105/106/107 expected_answer_summary 정정 (3 cells)

### 신규
- 본 work-log

### 일회성 (gitignored, /tmp)
- `/tmp/fix_summaries.py` — 정정 helper

### 데이터 영향
- 0 건 (CSV 3 cell 변경, row 수 / schema 변동 X)

### 운영 코드 변경 영향
- 0 건 (`api/app/` / `web/src/` 수정 없음)

### 외부 cost
- 0
- 누적 (이번 세션 전체): ~$0.31 (변동 없음)

---

## 7. 한 문장 마감

> **2026-05-10 — expected_summary 정정 ship**. G-U-105/106/107 3 row 의 chunk-text 형태 expected_answer_summary → 의미 요약 형태 (목적/내용/시점/조건 명시) 정정. RAGAS judge 정확도 향상 input 확보. CSV 178 row 무결성 ✅, 단위 테스트 814 OK / 회귀 0. 누적 cost 변동 0. 다음 1순위 = **TOC default ON 채택 결정** (사용자 결정) 또는 **generate_golden_rows.py prompt 강화** (cost 0, 0.5 day).
