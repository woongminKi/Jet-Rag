# 2026-05-14 S5-A — router signals 정합 fix + decomposed_subqueries CTA

> M3 잔여 frontend 작업. 핸드오프 §6.1 W-9 D3 "cross-doc CTA UI 분기" 의 실질 완성.
> 본 세션 4번째 ship (P1 → P2 → P3 → W-9.5 → 본 S5-A 마무리).

---

## 1. 한 줄 요약

`RouterSignalsBadge` 가 **dead code 상태였던 회귀 발견 + fix**. backend 의 actual signal label
(`T1_cross_doc` 등) ↔ frontend SIGNAL_META 키 (`cross_doc` 등) mismatch → badge 0회 발화.
정합 fix + `decomposed_subqueries` CTA 추가로 사용자 명시 액션 가시화.

---

## 2. 회귀 진단

### 2.1 발견
- backend `intent_router.IntentRouterDecision.triggered_signals`:
  `T1_cross_doc`, `T2_compare`, `T3_causal`, `T4_change`, `T5_long_query`, `T6_low_confidence`, `T7_multi_target` (7종)
- frontend `SIGNAL_META`:
  `cross_doc`, `temporal`, `ambiguous` (3종, backend 와 키 mismatch)
- `RouterSignalsBadge` 의 filter: `(sig): sig is keyof typeof SIGNAL_META => sig in SIGNAL_META`
  → backend signal 이 SIGNAL_META 와 정확히 매칭 안 되어 **모두 graceful filter 로 skip**
- 결과: signal 안내문 **0회 발화** (low_confidence 안내문만 노출 가능).

### 2.2 비판적 재검토 (3회)
1. **1차**: 단순 라벨 매핑 fix — backend label 그대로 SIGNAL_META 키로 사용.
2. **2차**: 무엇이 진짜 가치 있는 signal? T1 (cross_doc) 와 T7 (multi_target) 이 사용자 노출 가치 큼. T2~T5 는 정보성이지만 noise 발생 위험 — 보수적으로 미노출.
3. **3차**: T6 (low_confidence) 는 `meta.low_confidence` boolean 으로 별도 안내문 노출됨 → signal badge 에 추가 시 중복. 미포함.

### 2.3 결정
- SIGNAL_META 키를 backend label 정합 (`T1_cross_doc`, `T7_multi_target`) 으로 변경.
- T2~T6 는 SIGNAL_META 에 추가 X → 기존 graceful filter 로 자연 skip.
- T6 의 low_confidence 안내는 별도 `meta.low_confidence` boolean 분기로 유지.

---

## 3. 변경 파일

### 3.1 `web/src/components/jet-rag/router-signals-badge.tsx`

**A. SIGNAL_META 키 정합 fix (회귀 복구)**:
```typescript
// before — backend 와 mismatch, 실제 발화 0
const SIGNAL_META = {
  cross_doc: { ... },
  temporal: { ... },
  ambiguous: { ... },
};

// after — backend triggered_signals 와 정합
const SIGNAL_META = {
  T1_cross_doc:   { label: '여러 문서를 비교하는 의도로 인식했어요', icon: Compass },
  T7_multi_target:{ label: '여러 대상을 동시에 묻는 질문이에요',   icon: Layers },
};
```

**B. `decomposed_subqueries` CTA 추가 (핸드오프 D3 의 실질)**:
```tsx
{subqueries.length > 0 && (
  <div className="space-y-1.5 border-t border-border/40 pt-1.5">
    <p className="flex items-center gap-1.5 text-[11px] ...">
      <Search /> 분해된 sub-query 로 검색했어요 — 직접 재검색해 보세요:
    </p>
    <ul className="flex flex-wrap gap-1.5">
      {subqueries.map((sq) => (
        <li key={sq}>
          <Link href={`/search?q=${encodeURIComponent(sq)}`}
                className="inline-flex items-center rounded bg-primary/10 ...">
            {sq}
          </Link>
        </li>
      ))}
    </ul>
  </div>
)}
```

**C. 표시 조건 확장** — `subqueries.length > 0` 도 트리거에 포함:
```typescript
if (!lowConfidence && signals.length === 0 && subqueries.length === 0) {
  return null;
}
```

**D. docstring 정정** — backend `T1`~`T7` 매핑 + 노출 정책 명시.

---

## 4. 회귀 검증

- `npx tsc --noEmit`: 0 error
- `pnpm lint`: 0 error / 0 warning
- `pnpm build`: 성공 (10 routes prerender)
- backend 변경 0 → backend 단위 테스트 영향 없음 (1150건 유지).

---

## 5. 운영 영향

- **회귀 복구**: T1_cross_doc / T7_multi_target signal 발화 시 frontend 에 안내문 정상 노출 (이전 0회 → 의도된 발화).
- **신규 가치**: paid query decomposition (ENV `JETRAG_PAID_DECOMPOSITION_ENABLED=true`) 발화 시 sub-query 가 클릭 가능 `/search?q=...` 링크로 노출 → 사용자 명시 액션 완성.
- **사이드 이펙트**: 없음 (graceful filter 패턴 유지, unknown signal 은 자연 skip).

---

## 6. 본 세션 잡일·M3 진입 마감

| commit | scope | 작업 |
|---|---|---|
| `57d87e4` | P1 fix | vision_page_cache hit 시 사전 cap check 우회 |
| `b280941` | P2 fix | m2_w4_full_reingest `--out-md` → `--out` |
| `330979b` | P3 fix | admin 테스트 timezone fragility (KST 정오) |
| `7b0f898` | W-9.5 | BM25 ablation harness (KPI #7 측정 인프라) |
| (본 commit) | S5-A 마무리 | router signals 정합 fix + decomposed_subqueries CTA |

핸드오프 §6.3 의 M3 진입 순서:
1. ✓ P1·P2 잡일 정리
2. ✓ W-9.5 BM25 ablation harness
3. ✓ S5-A frontend 정합 fix + CTA
4. 다음 후보: **Acceptable judge 2차** ($0.05) / **KPI 측정 실행** (ablation 3회 run, ~1.5h) / **S5-C 추가 정리 검토**

---

## 7. 다음 후보 (사용자 결정)

1. **D. KPI #7 실제 측정** (W-9.5 ablation 3 run, ~1.5h, $0) — 가장 즉시
2. **B. Acceptable judge 2차** (DECISION-11, ~$0.05, ~1일) — golden 라벨 강화
3. **PRD v1.3 갱신** — 본 세션 변경 종합, 본격 KPI 측정 plan
