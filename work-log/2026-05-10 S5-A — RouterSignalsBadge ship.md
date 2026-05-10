# 2026-05-10 S5-A — RouterSignalsBadge ship (low_confidence + router_signals UI)

> Sprint: S5 정정 plan §S5-A — low_confidence + cross-doc CTA badge
> 작성: 2026-05-10
> 마감: RouterSignalsBadge 신규 컴포넌트 + answer-view.tsx 통합 + tsc/lint 검증
> 입력: S5 PoC ship 후속 (Q-S5-2 default 명시 액션 권고 채택)

---

## 0. 한 줄 요약

> **S5-A ship — `RouterSignalsBadge` 신규 컴포넌트 + answer-view.tsx 통합**. backend `meta.low_confidence` + `meta.router_signals` (cross_doc / temporal / ambiguous) 시각화. 명시 액션 패턴 (Q-S5-2) — autoplay X, 안내만 노출. tsc + lint 0 error. 운영 코드 변경 = web 2 파일 (answer-view.tsx + router-signals-badge.tsx 신규). PoC `useEffect` 제거 (UI 분기로 대체). 다음 단계 = S5-B (RAGAS 회귀 자동화, 1 day) 또는 R@10 회복.

---

## 1. 신규 컴포넌트 — RouterSignalsBadge

### 1.1 파일

**`web/src/components/jet-rag/router-signals-badge.tsx`** (신규)

### 1.2 표시 조건

`meta.low_confidence === true` 또는 `meta.router_signals` 가 알려진 신호 1개 이상 포함 시 표시. 그 외 `null` 반환.

### 1.3 신호 매핑

| signal | 안내 메시지 | 아이콘 |
|---|---|---|
| `cross_doc` | 여러 문서를 비교하는 의도로 인식했어요 | Compass |
| `temporal` | 시간 기준 질문으로 인식했어요 | Clock |
| `ambiguous` | 질문 의도가 명확하지 않을 수 있어요 | HelpCircle |
| (low_confidence) | 이 질문 의도를 명확히 파악하지 못했어요 — 출처를 직접 확인 권장 | AlertCircle |

알 수 없는 signal 은 graceful skip (백엔드 신규 추가 시 안전).

### 1.4 명시 액션 (Q-S5-2)

- autoplay 안 함 — paid 모델 자동 호출 0
- CTA 없이 안내만 표시 (사용자가 명시적으로 "다른 키워드로 검색" 또는 출처 클릭 등 결정)
- master plan §3 원칙 5 (RagasEvalCard / SearchPrecisionCard 패턴 일관)

---

## 2. answer-view.tsx 통합

### 2.1 변경 내역

```typescript
// import 변경
import { useMemo, useRef, useState } from 'react';  // useEffect 제거
import { RouterSignalsBadge } from './router-signals-badge';  // 신규

// useEffect (PoC console.log) 제거 → 주석으로 대체
// S5-A — RouterSignalsBadge 가 신뢰도 배지 아래에 추가 안내 표시

// 신뢰도 배지 아래 신규 통합
<RouterSignalsBadge meta={response.meta} />
```

### 2.2 위치

신뢰도 배지 (`meta.tone` 박스) 와 답변 본문 article 사이.
사용자 시선 자연스러운 위치 — 답변 읽기 전 의도 안내.

---

## 3. 검증

| 항목 | 결과 |
|---|---|
| **tsc** | 0 error ✅ |
| **eslint** | 0 error / 0 warning ✅ |
| **단위 테스트 영향** | 없음 (frontend UI 추가만) |
| **운영 영향** | meta 부재 시 `null` 반환 — 기존 UX 유지 |

---

## 4. 사용자 결정 보류 항목 (이번 sprint 후 잔존)

| ID | 항목 | 권고 default | 결정 시점 |
|---|---|---|---|
| Q-S5-3 | RAGAS 회귀 임계 | baseline 측정 후 결정 | S5-B 측정 완료 후 |
| Q-S5-4 | relevance-label prod 숨김 정책 | `?debug=1` 시만 표시 | S5-C 진입 전 |
| Q-S5-5 | RAGAS 회귀 sample 크기 | 30 row | S5-B 진입 전 |
| Q-S5-6 | extractive dense 강조 (S5-D) | 보류 (ROI 낮음) | 별도 |

**해소된 항목**:
- ~~Q-S5-1: S5 vs R@10 회복 우선순위~~ → S5 진입 채택 ✅
- ~~Q-S5-2: autoplay vs 명시 액션~~ → **명시 액션 채택** ✅

---

## 5. 다음 후보 우선순위

### 5.1 1순위 — S5-B RAGAS 회귀 자동화 (cost ~$0.30, 1 day)

**왜?**
- golden v2 30 row sample × RAGAS 측정 → faithfulness baseline 결정
- 사용자 결정 (Q-S5-3) 후 임계 가드 추가 → 회귀 자동 탐지
- 사용자 cost 명시 승인 필요

### 5.2 2순위 — R@10 -0.037 회복 (cost 0, 0.5 day)

cross_doc 잔존 라벨 정정 또는 graded R@10 향상.

### 5.3 3순위 — S5-C 환경 분기 (cost 0, 0.25 day)

`relevance-label` / rrf score `?debug=1` 환경 분기.

### 5.4 4~10순위

| # | 후보 | 작업량 | cost | 권고도 |
|---|---|---|---|---|
| 4 | TOC guard 패턴 정밀화 | 0.5일 | 0 | ★★ |
| 5 | chunk_filter 마킹 분석 | 0.5일 | 0 | ★ |
| 6 | budget_guard cache hit 무료 인식 | 0.5일 | 0 | ★ |
| 7 | RPC per-doc cap | 1주+ | 0 | ★ |
| 8 | S4-B 핵심 엔티티 추출 | 3일 | 0 | ★★ |

---

## 6. 핵심 변경 파일 목록

### 신규
- `web/src/components/jet-rag/router-signals-badge.tsx` (신규 컴포넌트)
- 본 work-log

### 수정
- `web/src/components/jet-rag/answer-view.tsx` — `RouterSignalsBadge` import + 통합, `useEffect` PoC 제거

### 운영 코드 변경 영향
- frontend UI 추가만 — runtime 동작 영향 0 (meta 부재 시 null 반환)
- backend 변경 0

### 데이터 영향
- 0 건

---

## 7. 한 문장 마감

> **2026-05-10 — S5-A ship**. `RouterSignalsBadge` 신규 컴포넌트 (low_confidence + cross_doc/temporal/ambiguous 안내) + `answer-view.tsx` 통합. **명시 액션 패턴** (Q-S5-2 default) — autoplay X. tsc + lint 0 error. 운영 코드 변경 = web 2 파일. PoC `useEffect` 제거. 다음 후보 1순위 = S5-B RAGAS 회귀 자동화 (cost ~$0.30) 또는 R@10 회복.
