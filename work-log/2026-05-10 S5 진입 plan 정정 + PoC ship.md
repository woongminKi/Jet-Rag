# 2026-05-10 S5 진입 plan 정정 + PoC ship (meta schema 확장)

> Sprint: S5 /answer UX 정리 — 격차 분석 + Step 2 (PoC) ship
> 작성: 2026-05-10
> 마감: senior-planner 격차 분석 완료 + S5 plan 정정 + PoC (meta schema + dev console verify)
> 입력: senior-planner 호출 결과 + S5 원 plan (`2026-05-06 무료유료 모델 전략 통합 plan`)

---

## 0. 한 줄 요약

> **S5 진입 plan 정정 + Step 2 PoC ship**. senior-planner 격차 분석으로 S5 plan 의 90%가 이미 W25 D14 sprint 에서 ship 됐음 발견 → **1주 → 2 day 로 축소**. Step 2 PoC: `AnswerResponse` 에 `meta` 필드 (`AnswerMeta` 타입) 추가 + `answer-view.tsx` 에 dev only `useEffect` 로 `console.log`. tsc + lint 0 error. 운영 영향 0 (dev 환경에서만 console). 단위 테스트 영향 없음. 다음 단계 = S5-A (`low_confidence` + cross-doc CTA badge) 진입 (사용자 결정 보류 항목 Q-S5-2 후).

---

## 1. senior-planner 격차 분석 결과

### 1.1 D1~D5 vs 현재 frontend

| Day | S5 plan 작업 | 현재 상태 | 격차 분류 |
|---|---|---|---|
| D1 | extractive summary 자동 표시 | `result-card.tsx` lex 하이라이트 + `answer-view.tsx` LLM 답변 | **부분 구현** (lex-only, dense 매칭 X) |
| D2 | 2 단계 통합 UX | `search/page.tsx` "AI 답변 보기" CTA (W25 D14) | **상당 부분 구현됨** |
| D3 | cross-doc CTA | 백엔드 `meta.low_confidence` + `router_signals` OK / frontend 미사용 | **백엔드 OK, frontend 갭** |
| D4 | RAGAS 회귀 측정 | `RagasEvalCard` + `services/ragas_eval.py` OK / 회귀 자동화 X | **인프라 OK, 자동화 갭** |
| D5 | 매칭 강도 카드 환경 분기 | `relevance-label.tsx` 모든 환경 표시 | **미구현** |

### 1.2 정정된 plan (2 day)

| 단계 | 작업 | 산출물 | 작업량 |
|---|---|---|---|
| **Step 2 (PoC, 본 ship)** | `AnswerResponse` 에 `meta` schema + dev console verify | types.ts + answer-view.tsx | **0.25 day ✅** |
| S5-A (구 D3) | `low_confidence` + cross-doc CTA badge | answer-view.tsx + `RouterSignalsBadge` 신규 | 0.5 day |
| S5-B (구 D4) | RAGAS 회귀 자동화 (golden v2 30 row sample) | `evals/run_ragas_regression.py` + baseline | 1 day |
| S5-C (구 D5) | relevance-label 환경 분기 (`?debug=1`) | relevance-label.tsx env 분기 | 0.25 day |
| ~~S5-D~~ | ~~extractive dense 강조~~ | ~~ROI 낮음~~ | **보류** |

원 1주 → **2 day** 축소.

### 1.3 senior-planner 의 비판적 재검토 (3회)

- **1차**: D1, D2, D4 의 90% 가 W25 D14 에서 이미 ship → 신규 작업 = D3 (meta UI) + D4 (회귀 자동화) + D5 (환경 분기)
- **2차**: low_confidence + cross-doc CTA 는 사실상 D3 1건으로 통합 가능 / extractive (D1) 은 cheap LLM 답변 (`/ask`) 이 더 강하므로 ROI 낮음
- **3차**: RAGAS faithfulness ≥ 0.85 는 industry rule of thumb — 우리 baseline 측정 후 임계 결정

---

## 2. Step 2 PoC 구현

### 2.1 변경 내역

**`web/src/lib/api/types.ts`** — 신규 `AnswerMeta` interface + `AnswerResponse.meta` 필드:

```typescript
export interface AnswerMeta {
  low_confidence?: boolean;
  router_signals?: string[];
  router_confidence?: number;
  decomposed_subqueries?: string[];
  decomposition_cost_usd?: number;
  decomposition_cached?: boolean;
  [key: string]: unknown;  // graceful 통과
}

export interface AnswerResponse {
  // ... 기존 필드 ...
  meta?: AnswerMeta | null;
}
```

backend `api/app/routers/answer.py` line 120 의 `meta: dict | None` 와 정합 (line 462~479 의 `answer_meta` 구조 반영).

**`web/src/components/jet-rag/answer-view.tsx`** — `useEffect` 추가:

```typescript
import { useEffect, useMemo, useRef, useState } from 'react';

// AnswerView 함수 안:
useEffect(() => {
  if (process.env.NODE_ENV !== 'production' && response.meta) {
    console.log('[AnswerView] backend meta:', response.meta);
  }
}, [response.meta]);
```

dev 환경에서만 console.log → 운영 영향 0. S5-A 진입 시 본 useEffect 는 UI 분기로 대체.

### 2.2 검증

- **tsc**: 0 error
- **lint (eslint)**: 0 error / 0 warning
- **단위 테스트 영향**: 없음 (frontend type 추가만)

---

## 3. 사용자 결정 보류 항목

S5-A 진입 전 결정 필요:

| ID | 항목 | 권고 default |
|---|---|---|
| **Q-S5-1** | S5 진입 vs R@10 -0.037 회복 우선순위 | S5 (cost 0, cap 무관) |
| **Q-S5-2** | low_confidence / cross-doc 시 paid 모델 자동 호출 vs 명시 액션 | **명시 액션** (RagasEvalCard / SearchPrecisionCard 패턴 일관) |
| Q-S5-3 | RAGAS 회귀 임계 | baseline 측정 후 결정 |
| Q-S5-4 | relevance-label prod 숨김 정책 | `?debug=1` 시만 표시 |
| Q-S5-5 | RAGAS 회귀 sample 크기 | **30 row** (cost ~$0.30) |
| Q-S5-6 | extractive dense 강조 (S5-D) | 보류 권고 |

---

## 4. 다음 후보 우선순위

### 4.1 1순위 — S5-A 진입 (cost 0, 0.5 day)

**왜?**
- backend meta 가 PoC 로 frontend 까지 도달 검증
- low_confidence + cross-doc CTA badge 가 사용자 가치 큼 (cross_doc 5 row 의 신뢰도 가시화)
- 운영 코드 변경 (frontend) — answer-view.tsx 1 파일 + 신규 badge component

### 4.2 2순위 — R@10 -0.037 회복 (cost 0, 0.5 day)

cross_doc 잔존 라벨 정정 또는 graded R@10 향상.

### 4.3 3순위 — S5-B RAGAS 회귀 자동화

baseline 측정 후 임계 결정.

---

## 5. 핵심 변경 파일 목록

### 신규
- 본 work-log

### 수정 — frontend (2 파일)
- `web/src/lib/api/types.ts` — `AnswerMeta` interface + `AnswerResponse.meta` 추가
- `web/src/components/jet-rag/answer-view.tsx` — `useEffect` 추가 (`useEffect` import + dev console.log)

### 운영 코드 변경 영향
- frontend type 추가만 — runtime 동작 영향 0
- dev 환경에서만 console.log

### 데이터 영향
- 0 건

---

## 6. 한 문장 마감

> **2026-05-10 — S5 진입 plan 정정 + Step 2 PoC ship**. senior-planner 격차 분석으로 S5 plan 90% 이미 W25 D14 ship 발견 → **1주 → 2 day 축소**. PoC: `AnswerMeta` schema + `answer-view.tsx` dev console verify. tsc + lint 0 error, 운영 영향 0. 다음 1순위 = S5-A (`RouterSignalsBadge` 신규 + low_confidence/cross-doc UI 분기) — Q-S5-2 (명시 액션 권고) 결정 후 진입.
