# 2026-05-03 W14 Day 1 — ablation mode 토글 frontend

> W13 Day 2 backend `/search?mode=…` ablation 인프라의 frontend 회수.

---

## 0. 한 줄 요약

W14 Day 1 — SearchSubheader 에 hybrid/dense/sparse 3-state 토글 추가 + URL 보존 (q · debug · doc_id · mode 모두 일관). 사용자가 검색 결과 페이지에서 직접 ablation 비교 가능. tsc·lint 0 error, backend 회귀 0.

---

## 1. 비판적 재검토

### 1.1 후보

| 옵션 | 비용 | 결정 |
|---|---|---|
| OpenAI 어댑터 (DoD ④) | ~3h | ❌ 토큰 부담 |
| **frontend ablation 토글** | ~30분 | ✅ 채택 — Day 2 backend 자산 회수 |
| monitor CI yaml | ~30분 | ⚠ 사용자 환경 의존 |

### 1.2 토글 디자인

| 옵션 | 결정 |
|---|---|
| dropdown select | ❌ 클릭 2회 (열기 + 선택) |
| **3-state segmented** | ✅ 채택 — 1-click 전환 |
| 3 별도 button | ⚠ 시각적 그룹 약함 |

mobile 노출은 좁은 폭 보호로 md+ 만 (W7 Day 1 queryParsed badges 정책 일관).

---

## 2. 구현

### 2.1 변경 파일

| 파일 | 변경 |
|---|---|
| `web/src/lib/api/index.ts` | `SearchMode` 타입 + `searchDocuments(..., mode?)` 시그니처 확장. `mode==='hybrid'` 면 URL 생략 (default 보존) |
| `web/src/app/search/page.tsx` | `searchParams.mode` 파싱 + `parseMode` 화이트리스트 + searchDocuments 전달 + SearchSubheader prop |
| `web/src/components/jet-rag/search-subheader.tsx` | `mode` prop + `buildUrl` helper (q·debug·doc_id·mode 모두 보존) + `switchMode` + 3-state segmented 토글 (md+) |

### 2.2 URL 보존 정책

`buildUrl({ q?, mode? })` helper — 한 곳에서 모든 상태 조합:
- q: 검색어 (override 가능)
- debug: 1 시 보존
- doc_id: 있으면 보존 (US-08)
- mode: hybrid 면 생략 (default), dense/sparse 면 명시

→ 검색어 변경 / debug 토글 / mode 전환 시 다른 상태 손실 0.

### 2.3 토글 UI

```tsx
<div className="hidden h-7 items-center rounded-md border border-border bg-card md:inline-flex">
  {(['hybrid', 'dense', 'sparse'] as const).map((m) => (
    <button
      key={m}
      onClick={() => switchMode(m)}
      className={mode === m ? 'bg-primary text-primary-foreground' : 'text-muted-foreground'}
      aria-pressed={mode === m}
    >
      {m}
    </button>
  ))}
</div>
```

→ 활성 mode 만 primary 배경, 비활성은 muted-foreground. aria-pressed 로 접근성↑.

---

## 3. 검증

```bash
cd web && pnpm exec tsc --noEmit && pnpm lint  # 0 error
cd ../api && uv run python -m unittest discover tests  # 236 ran 회귀 0
```

라이브 smoke (사용자 환경):
- `/search?q=계약` → SearchSubheader 에 [hybrid|dense|sparse] 토글 표시 (md+)
- "dense" 클릭 → URL `?q=계약&mode=dense` → 응답 dense_rank 만 통과
- "sparse" 클릭 → URL `?q=계약&mode=sparse` → 응답 sparse_rank 만 통과
- "hybrid" 클릭 → URL `?q=계약` (default 생략)

---

## 4. 누적 KPI (W14 Day 1 마감)

| KPI | W13 Day 4 | W14 Day 1 |
|---|---|---|
| 단위 테스트 | 236 | 236 (frontend 변경) |
| 한계 회수 | 20 | 20 |
| ablation 활용도 | backend 만 | **+ frontend 토글** |
| 마지막 commit | 7ffccfd | (Day 1 commit 예정) |

---

## 5. 알려진 한계 (Day 1 신규)

| # | 한계 | 회수 시점 |
|---|---|---|
| 78 | 토글 mobile 미노출 (md+ 만) — sm 사용자는 URL 직접 편집 필요 | 사용자 피드백 후 |
| 79 | 토글 전환 시 새 fetch (SSR re-render) — 빠른 클릭 시 race | acceptable trade-off (Next.js 16 RSC) |

---

## 6. 다음 작업 — W14 Day 2 (자동 진입)

| 우선 | 항목 | 사유 |
|---|---|---|
| 1 | **OpenAI 어댑터 스왑 시연** | DoD ④ (~3h) |
| 2 | **monitor CI yaml + 가이드** | 사용자 환경 |
| 3 | **augment 본 검증** | quota 회복 |
| 4 | **mode=dense/sparse 별 SLO 분리** (한계 #77) | 정확도 |

**Day 2 자동 진입**: monitor CI yaml — 30분 작은 sprint, 운영 인프라 마무리.

---

## 7. 한 문장 요약

W14 Day 1 — SearchSubheader 에 hybrid/dense/sparse 3-state 토글 ship + URL 보존 정책 정착. 사용자가 검색 결과 페이지에서 직접 ablation 비교 가능. tsc·lint 0 error.
