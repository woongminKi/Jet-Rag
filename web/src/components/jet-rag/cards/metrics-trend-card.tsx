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
  /** Server Component 가 초기 fetch 한 trend (default range='7d').
   *  토글 시 client refetch — initialTrend.range 와 동일 range 면 fetch 생략. */
  initialTrend: TrendResponse;
  metric?: TrendMetric;
  mode?: TrendMode;
}

const RANGE_OPTIONS: TrendRange[] = ['24h', '7d', '30d'];

// SVG sparkline canvas — 의존성 회피 (recharts/visx 미도입).
const SVG_WIDTH = 280;
const SVG_HEIGHT = 48;
const SVG_PADDING = 4;

export function MetricsTrendCard({
  initialTrend,
  metric = 'search',
  mode = 'all',
}: MetricsTrendCardProps) {
  const [range, setRange] = useState<TrendRange>(initialTrend.range);
  const [fetchedTrend, setFetchedTrend] = useState<TrendResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);

  useEffect(() => {
    // 초기 range 면 SSR 데이터 그대로 — fetch 생략 (isLoading 은 handler 가 false 유지)
    if (range === initialTrend.range) return;
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
  }, [range, metric, mode, initialTrend]);

  // range 토글 — fetch 시작 시점의 동기 state (isLoading=true / fetchError reset) 는
  // handler 에서 처리. useEffect 안에서 동기 setState 금지 (React 19 lint).
  const handleRangeChange = (next: TrendRange) => {
    if (next === range) return;
    setRange(next);
    if (next !== initialTrend.range) {
      setIsLoading(true);
      setFetchError(null);
    } else {
      // SSR 데이터로 즉시 복귀 — fetch 안 함
      setIsLoading(false);
      setFetchError(null);
    }
  };

  // derived — range 가 initialTrend.range 면 SSR 데이터, 아니면 fetched (range 일치 시) or null
  const trend: TrendResponse | null =
    range === initialTrend.range
      ? initialTrend
      : fetchedTrend?.range === range
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
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
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
