'use client';

import { useEffect, useState } from 'react';
import {
  getStatsTrend,
  type TrendMetric,
  type TrendMode,
  type TrendRange,
  type TrendResponse,
} from '@/lib/api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

interface MetricsTrendCardProps {
  /** Server Component 가 초기 fetch 한 trend (default range='7d', mode='all').
   *  토글 시 client refetch — initialTrend 의 (range, mode) 와 동일 시 fetch 생략. */
  initialTrend: TrendResponse;
  metric?: TrendMetric;
  /** initialTrend.mode 와 동기화된 default mode. metric=vision 시 무시 (UI 비활성). */
  mode?: TrendMode;
}

const RANGE_OPTIONS: TrendRange[] = ['24h', '7d', '30d'];
const MODE_OPTIONS: TrendMode[] = ['all', 'hybrid', 'dense', 'sparse'];

// SVG sparkline canvas — 의존성 회피 (recharts/visx 미도입).
const SVG_WIDTH = 280;
const SVG_HEIGHT = 48;
const SVG_PADDING = 4;

export function MetricsTrendCard({
  initialTrend,
  metric = 'search',
  mode: initialMode = 'all',
}: MetricsTrendCardProps) {
  const [range, setRange] = useState<TrendRange>(initialTrend.range);
  // metric=search 만 mode 토글 의미. metric=vision 시 'all' 고정 (백엔드도 무시).
  const [mode, setMode] = useState<TrendMode>(initialMode);
  const [fetchedTrend, setFetchedTrend] = useState<TrendResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);

  // (range, mode) 가 initialTrend 와 일치 하는지 — fetch 생략 조건.
  const isInitialQuery =
    range === initialTrend.range &&
    (metric !== 'search' || mode === (initialTrend.mode ?? 'all'));

  useEffect(() => {
    if (isInitialQuery) return;
    let cancelled = false;
    getStatsTrend(range, metric, mode)
      .then((next) => {
        if (cancelled) return;
        setFetchedTrend(next);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setFetchError(
          err instanceof Error
            ? err.message
            : '추세 데이터를 불러오지 못했습니다.',
        );
      })
      .finally(() => {
        if (cancelled) return;
        setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [range, metric, mode, isInitialQuery]);

  const handleRangeChange = (next: TrendRange) => {
    if (next === range) return;
    setRange(next);
    triggerLoadingForToggle({
      isInitialNext:
        next === initialTrend.range &&
        (metric !== 'search' || mode === (initialTrend.mode ?? 'all')),
      setIsLoading,
      setFetchError,
    });
  };

  const handleModeChange = (next: TrendMode) => {
    if (next === mode) return;
    setMode(next);
    triggerLoadingForToggle({
      isInitialNext:
        range === initialTrend.range &&
        next === (initialTrend.mode ?? 'all'),
      setIsLoading,
      setFetchError,
    });
  };

  // (range, mode) 일치 시에만 fetched 사용. metric=vision 시 mode 비교 생략.
  const matchesQuery = (t: TrendResponse | null): boolean =>
    t != null &&
    t.range === range &&
    (metric !== 'search' || (t.mode ?? 'all') === mode);

  const trend: TrendResponse | null = matchesQuery(initialTrend)
    ? initialTrend
    : matchesQuery(fetchedTrend)
      ? fetchedTrend
      : null;

  const buckets = trend?.buckets ?? [];
  const errorCode = trend?.error_code ?? null;
  const totalSamples = buckets.reduce((sum, b) => sum + b.sample_count, 0);
  const hasSamples = totalSamples > 0;

  // metric 별 표시값 — search: p95_ms 시계열, vision: sample_count 시계열
  const series: number[] =
    metric === 'search'
      ? buckets.map((b) => b.p95_ms ?? 0)
      : buckets.map((b) => b.sample_count);
  const seriesMax = Math.max(...series, 1);

  const points = series.map((v, i) => {
    const x =
      SVG_PADDING +
      ((SVG_WIDTH - 2 * SVG_PADDING) * i) /
        Math.max(series.length - 1, 1);
    const y =
      SVG_HEIGHT -
      SVG_PADDING -
      ((SVG_HEIGHT - 2 * SVG_PADDING) * v) / seriesMax;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const polylinePoints = points.join(' ');

  const titleText =
    metric === 'search' ? '검색 응답 추세' : 'Vision API 호출 추세';
  const seriesLabel = metric === 'search' ? 'p95 (ms)' : '호출 수';

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center justify-between text-base font-semibold">
          <span>{titleText}</span>
          <RangeToggle
            value={range}
            onChange={handleRangeChange}
            disabled={isLoading}
          />
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {fetchError ? (
          <p className="text-sm text-destructive">{fetchError}</p>
        ) : trend === null ? (
          <p className="text-sm text-muted-foreground">데이터 불러오는 중…</p>
        ) : errorCode === 'migrations_pending' ? (
          <p className="text-sm text-muted-foreground">
            마이그레이션 005·006·007 적용 후 자동 활성됩니다.
          </p>
        ) : !hasSamples ? (
          <p className="text-sm text-muted-foreground">
            측정 데이터가 없습니다. 잠시 후 다시 확인해주세요.
          </p>
        ) : (
          <>
            <div
              className="rounded border border-border bg-background/40 p-2"
              aria-busy={isLoading}
            >
              <svg
                viewBox={`0 0 ${SVG_WIDTH} ${SVG_HEIGHT}`}
                className={`h-12 w-full transition-opacity ${
                  isLoading ? 'opacity-50' : 'opacity-100'
                }`}
                preserveAspectRatio="none"
                role="img"
                aria-label={`${seriesLabel} 시계열 sparkline`}
              >
                <polyline
                  points={polylinePoints}
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  className="text-foreground"
                />
              </svg>
            </div>

            {/* W18 Day 1 — search 카드만 mode 토글 (ablation 비교) */}
            {metric === 'search' && (
              <ModeToggle
                value={mode}
                onChange={handleModeChange}
                disabled={isLoading}
              />
            )}

            <div className="grid grid-cols-1 gap-2 text-xs sm:grid-cols-2">
              <Stat label="총 샘플" value={`${totalSamples.toLocaleString()}건`} />
              <Stat
                label={metric === 'search' ? '최근 p95' : '최근 quota 초과'}
                value={
                  metric === 'search'
                    ? `${recentNonZero(buckets.map((b) => b.p95_ms ?? 0))}ms`
                    : `${buckets.reduce((s, b) => s + (b.quota_exhausted_count ?? 0), 0)}건`
                }
              />
            </div>

            <div className="border-t border-border pt-2 text-[10px] text-muted-foreground">
              {buckets.length} 개 bucket · 시리즈: {seriesLabel}
              {metric === 'search' && ` · mode: ${mode}`}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

function triggerLoadingForToggle({
  isInitialNext,
  setIsLoading,
  setFetchError,
}: {
  isInitialNext: boolean;
  setIsLoading: (v: boolean) => void;
  setFetchError: (v: string | null) => void;
}) {
  if (isInitialNext) {
    setIsLoading(false);
    setFetchError(null);
  } else {
    setIsLoading(true);
    setFetchError(null);
  }
}

function RangeToggle({
  value,
  onChange,
  disabled,
}: {
  value: TrendRange;
  onChange: (next: TrendRange) => void;
  disabled?: boolean;
}) {
  return (
    <div
      role="radiogroup"
      aria-label="시간 범위"
      className="inline-flex rounded border border-border bg-background/40 text-[10px] font-normal"
    >
      {RANGE_OPTIONS.map((opt) => {
        const active = opt === value;
        return (
          <button
            key={opt}
            type="button"
            role="radio"
            aria-checked={active}
            disabled={disabled}
            onClick={() => onChange(opt)}
            className={`px-2 py-1 transition-colors disabled:opacity-50 ${
              active
                ? 'bg-foreground/10 font-semibold text-foreground'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            {opt}
          </button>
        );
      })}
    </div>
  );
}

function ModeToggle({
  value,
  onChange,
  disabled,
}: {
  value: TrendMode;
  onChange: (next: TrendMode) => void;
  disabled?: boolean;
}) {
  return (
    <div
      role="radiogroup"
      aria-label="검색 mode"
      className="inline-flex flex-wrap gap-1 text-[10px] font-normal"
    >
      {MODE_OPTIONS.map((opt) => {
        const active = opt === value;
        return (
          <button
            key={opt}
            type="button"
            role="radio"
            aria-checked={active}
            disabled={disabled}
            onClick={() => onChange(opt)}
            className={`rounded border border-border px-2 py-0.5 transition-colors disabled:opacity-50 ${
              active
                ? 'border-foreground/40 bg-foreground/10 font-semibold text-foreground'
                : 'bg-background/40 text-muted-foreground hover:text-foreground'
            }`}
          >
            {opt}
          </button>
        );
      })}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between rounded border border-border bg-background/40 px-2 py-1.5">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-mono tabular-nums font-semibold text-foreground">
        {value}
      </span>
    </div>
  );
}

function recentNonZero(values: number[]): number {
  for (let i = values.length - 1; i >= 0; i -= 1) {
    if (values[i] > 0) return values[i];
  }
  return 0;
}
