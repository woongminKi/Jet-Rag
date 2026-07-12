<!-- BEGIN:nextjs-agent-rules -->
# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.
<!-- END:nextjs-agent-rules -->

---

## Jet-Rag 프로젝트별 패턴 (W17~W19 누적)

본 프로젝트가 Next.js 16 + React 19 환경에서 정착시킨 자율 진행 패턴.

### 1. Server Component 첫 fetch + Client Component refetch

**패턴 도입**: W17 Day 1 (`metrics-trend-card.tsx`)

```tsx
// page.tsx (Server Component)
export default async function HomePage() {
  const [stats, searchTrend] = await Promise.all([
    getStats(),
    getStatsTrend('7d', 'search', 'all').catch(() => null),  // graceful fallback
  ]);
  return <HomeGrid searchTrend={searchTrend} ... />;
}

// metrics-trend-card.tsx ('use client')
'use client';
export function MetricsTrendCard({ initialTrend }: { initialTrend: TrendResponse }) {
  const [range, setRange] = useState(initialTrend.range);
  const [fetchedTrend, setFetchedTrend] = useState<TrendResponse | null>(null);
  // useEffect 가 fetch — initialTrend.range 와 동일 시 fetch 생략
}
```

**핵심**:
- SSR HTML 즉시 표시 (initial range 데이터 그대로)
- 토글 시 client refetch — useEffect + cancelled flag (race 방지)
- `.catch(() => null)` graceful fallback — 백엔드 미기동 환경 대응

### 2. React 19 `react-hooks/set-state-in-effect` 회피

**패턴 도입**: W17 Day 1 / W19 Day 1 (lint rule 도입 후)

useEffect 안의 동기 setState 가 lint 에러 발생. 해결:

```tsx
// ❌ 잘못된 패턴 — useEffect 안 동기 setState
useEffect(() => {
  if (someCondition) {
    setIsLoading(true);  // lint error
    setError(null);
  }
  // fetch ...
}, [deps]);

// ✅ 올바른 패턴 — handler 가 동기 setState 처리
const handleToggle = (next: TrendRange) => {
  setRange(next);
  setIsLoading(true);   // handler 안 동기 — OK
  setError(null);
};

useEffect(() => {
  // handler 가 set 한 state 후 fetch 만 실행
  // .then/.finally 안 setState 는 비동기라 OK
  fetchData().then(setFetched).finally(() => setIsLoading(false));
}, [deps]);
```

**핵심**:
- handler 분리 — 동기 setState 는 user interaction handler 에서 처리
- useEffect 본문은 fetch / subscribe 같은 외부 system sync 만
- `.then/.finally` 콜백 안 setState 는 비동기라 lint rule 영향 없음

### 3. useTransition + interruptible navigation (race 방지)

**패턴 도입**: W19 Day 1 (`search-subheader.tsx`)

router.push() 직접 호출 시 빠른 클릭 race 가능. useTransition 으로 감싸 React 가 stale transition 자동 cancel.

```tsx
const [isPending, startTransition] = useTransition();

const switchMode = (next: 'hybrid' | 'dense' | 'sparse') => {
  if (next === mode) return;
  startTransition(() => {
    router.push(buildUrl({ mode: next }));
  });
};

// button 에 disabled={isPending} + aria-busy 표시
<button disabled={isPending} aria-pressed={mode === m}>{m}</button>
```

**핵심**:
- React 19 표준 — useSWR / 외부 라이브러리 의존 X
- isPending 자동 관리 — 수동 state 불필요
- startTransition 안 router.push 가 interruptible — 빠른 두 번째 클릭이 이전 transition 자동 cancel

### 4. SVG 직접 그리기 (시각화 의존성 0)

**패턴 도입**: W16 Day 3 (sparkline) / W20 Day 4 (CSS bar)

recharts (~150KB) / visx 같은 차트 라이브러리 회피. 단순 시각화는 SVG `<polyline>` + viewBox 또는 CSS `<div>` width%.

```tsx
// SVG sparkline
<svg viewBox={`0 0 ${SVG_WIDTH} ${SVG_HEIGHT}`} preserveAspectRatio="none" className="h-12 w-full">
  <polyline points={polylinePoints} fill="none" stroke="currentColor" strokeWidth="1.5" />
</svg>

// CSS bar (max 정규화)
<div className="h-1 overflow-hidden rounded-sm bg-border" role="progressbar" aria-valuenow={p50}>
  <div className="h-full bg-foreground/60" style={{ width: `${widthPct}%` }} />
</div>
```

**핵심**:
- 외부 의존성 0 정책 일관 (운영 정책 §6.32 / §6.51)
- viewBox + preserveAspectRatio="none" — 카드 폭에 자동 fit
- min 4% 보장 — 정성적 비교 가능 (0% 보이지 않음 회피)

### 5. mobile-first responsive grid

**패턴 도입**: W16 Day 4 (#40 회수) / W19 Day 4 (mobile 폰트)

```tsx
// 작은 폰 (<640px) 1 컬럼, sm+ (≥640px) 2 컬럼
<div className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">

// mobile 폰트 미세 조정 (W14 9px → W19 10.5px)
<button className="px-2 text-[10.5px] md:text-[11px]">
```

---

## 자율 진행 시 주의사항

1. **'use client' 도입 전 RSC 가능 여부 검토** — Server Component fetch 가 SSR HTML 에 데이터 즉시 포함 → SEO/UX 우위. interactivity 필요할 때만 'use client'.
2. **useEffect 안 동기 setState 금지** — React 19 lint 자동 검출. handler 로 분리.
3. **외부 의존성 추가는 사용자 묵시 승인 케이스만** — 예: python-pptx (W8 사용자 자료 자연 도입). recharts / playwright 같은 신규 의존성은 사용자 명시 승인.
