# 2026-05-10 S5-C — relevance-label + rrf_score `?debug=1` 환경 분기 ship

> Sprint: S5 /answer UX 정리 — Step 5 (S5-C) 환경 분기
> 작성: 2026-05-10
> 마감: result-card.tsx 의 RelevanceLabel + rrf_score badge 운영 노출 차단 + `?debug=1` 시 표시
> 입력: Q-S5-4 권고 default (`?debug=1` 시만 표시) + 한계 #12 (relevance-label prod 노출, 사용자 멘탈 모델 혼란 가능)

---

## 0. 한 줄 요약

> **S5-C ship — `result-card.tsx` 에서 `RelevanceLabel` + `rrf_score` badge 를 `debug=true` (`?debug=1` URL param) 시만 표시**. 사용자 멘탈 모델 정리 (정답 신뢰도와 혼동 회피). 운영 노이즈 제거. tsc + lint 0 error / unit tests 775 OK / 회귀 0. **S5 진척률 ~40% → ~60% 도달**. 운영 코드 변경 1 파일 (`result-card.tsx`, +4/-2). doc 페이지의 `매칭 강도` 표시는 자체 tooltip + chunk 단위 컨텍스트 (다른 의미) — scope 제외.

---

## 1. 변경 내역

### 1.1 `web/src/components/jet-rag/result-card.tsx`

**수정 1** (line ~51): `<RelevanceLabel ... />` → `{debug && <RelevanceLabel ... />}`

```tsx
// Before
<RelevanceLabel relevancePct={relevancePct} />

// After
{debug && <RelevanceLabel relevancePct={relevancePct} />}
```

**수정 2** (line ~88): `rrf_score` badge → `{debug && ...}`

```tsx
// Before
{typeof chunk.rrf_score === 'number' && (
  <span ...>rrf {chunk.rrf_score.toFixed(4)}</span>
)}

// After (debug 가드 추가)
{debug && typeof chunk.rrf_score === 'number' && (
  <span ...>rrf {chunk.rrf_score.toFixed(4)}</span>
)}
```

`debug` prop 은 `result-card.tsx` 가 이미 받음 (line 13). `app/search/page.tsx` 는 `searchParams.debug === '1'` 로 분기 (line 36). 운영 (`?debug=1` 미설정) 시 두 영역 모두 hidden, debug 시 모두 표시 — 멘탈 모델 일관.

### 1.2 분기 scope 결정 (비판적 재검토)

S5-C 권고 scope 는 핸드오프 §5.3: "relevance-label.tsx + result-card rrf score `?debug=1` 환경 분기".

**doc 페이지 `매칭 강도` 표시 (`web/src/app/doc/[id]/page.tsx`)** — scope 제외 결정:
- 자체 tooltip 으로 "이 문서 안 청크들 중 가장 강한 매칭 대비 상대 강도예요. 정답 신뢰도와는 다릅니다." 명시 (line ~419)
- chunk 단위 컨텍스트 (search 결과 카드의 doc 단위와 다른 의미)
- `RelevanceLabel` 컴포넌트 미사용 (자체 inline 구현)
- 별도 sprint 로 분리 (Q-S5-4 의 권고 범위 밖)

### 1.3 검증

- **tsc** (`pnpm tsc --noEmit`): 0 error
- **lint** (`pnpm lint`): 0 error / 0 warning
- **unit tests** (`uv run python -m unittest discover tests`): **775 OK / skipped 1 / 회귀 0**
- **frontend test 영향**: 0 (result-card.tsx 단위 테스트 부재)

---

## 2. 효과

### 2.1 운영 영향

- **운영 (default, `?debug=1` 없음)**: result-card 헤더 우측 32px 컨테이너 (RelevanceLabel) hidden + chunk 행 우측 rrf badge hidden
  - 헤더는 title block 이 full width 차지 → 시각적 cleanup
  - chunk 행은 left section (page/section_title) + overlap badge (있을 시) 만 표시
- **debug (`?debug=1`)**: 모든 영역 표시 (이전 운영 동작과 동일)

### 2.2 사용자 멘탈 모델 영향

- "매칭 강도 100%" 가 정답 신뢰도로 오해되는 risk 차단 (Q-S5-4 의 핵심 동기)
- 운영 사용자에게는 `검색 결과 → AI 답변` 흐름 (S5-A `RouterSignalsBadge` + `AnswerView`) 만 노출 → 의도 일관

### 2.3 측정 KPI 영향

- 0 (frontend UI cleanup, retrieval / scoring 로직 변경 0)

---

## 3. 사용자 결정 보류 항목 변동

| ID | 항목 | 직전 | 갱신 |
|---|---|---|---|
| Q-S5-4 | relevance-label prod 숨김 정책 | `?debug=1` 시만 표시 (권고 default) | **채택 ✅** (해소) |

다른 항목 변동 0.

---

## 4. S5 진척률 추이

| 시점 | 진척률 | 잔여 |
|---|---:|---|
| S5 진입 plan 정정 + PoC ship (586c01d) | ~25% | S5-A + S5-B + S5-C |
| S5-A — RouterSignalsBadge ship (908e6a6) | ~40% | S5-B + S5-C |
| **S5-C ship (현재)** | **~60%** | **S5-B (RAGAS 회귀 자동화) 만 잔여** |

S5-B (1 day, ~$0.30) 마감 시 S5 100% 도달.

---

## 5. 다음 후보 우선순위 (C → A → B 의 A 단계)

### 5.1 1순위 — S5-B RAGAS 회귀 자동화 (cost ~$0.30, 1 day)

**진입 조건** (사용자 명시 승인 필수):
- Q-RAGAS-cost: ~$0.30 cost 승인 (30 row × ~$0.01 추정, 1회성 baseline)
- Q-S5-5: sample 30 row 채택 확인

**산출물**:
- `evals/run_ragas_regression.py` (신규)
- baseline 측정 결과 (faithfulness / answer_relevancy / context_precision)
- 임계 가드 결정 (Q-S5-3, baseline 측정 후)

**효과**: S5 100% 마감 + 회귀 자동 탐지 인프라 확보 (장기 ROI 큼)

### 5.2 2순위 — golden v2 표본 확장 (cost 0, 0.5~1 day)

cross_doc 5 / synonym 4 / fuzzy_memory 5 row 표본 작음 → 통계 신뢰도 향상.

### 5.3 3순위 — R@10 -0.037 회복 (cost 0, 0.5 day)

cross_doc 잔존 라벨 정정. 단 G-U-018 doc-size bias 구조적 한계.

---

## 6. 핵심 변경 파일 목록

### 신규
- 본 work-log

### 수정
- `web/src/components/jet-rag/result-card.tsx` (+4/-2) — RelevanceLabel + rrf badge `{debug && ...}` 분기

### 데이터 영향
- 0 건

### 운영 코드 변경 영향
- frontend UI cleanup — retrieval / scoring / API 동작 영향 0
- `?debug=1` URL param 시 이전 동작 그대로 (회귀 0)

---

## 7. 한 문장 마감

> **2026-05-10 — S5-C ship**. `result-card.tsx` 에서 `RelevanceLabel` + `rrf_score` badge 를 `debug=true` 시만 표시 (`?debug=1` URL param). 사용자 멘탈 모델 정리 (정답 신뢰도 혼동 회피) + 운영 노이즈 제거. tsc + lint 0 error / unit tests 775 OK / 회귀 0. **S5 진척률 ~40% → ~60% 도달**. 다음 1순위 = **S5-B RAGAS 회귀 자동화** (cost ~$0.30, 1 day, 사용자 cost 승인 필요).
