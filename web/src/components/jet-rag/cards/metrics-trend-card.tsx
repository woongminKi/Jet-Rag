import type { TrendResponse } from '@/lib/api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

interface MetricsTrendCardProps {
  trend: TrendResponse;
}

// SVG sparkline canvas — 의존성 회피 (recharts/visx 미도입).
const SVG_WIDTH = 280;
const SVG_HEIGHT = 48;
const SVG_PADDING = 4;

export function MetricsTrendCard({ trend }: MetricsTrendCardProps) {
  const { metric, range, buckets, error_code: errorCode } = trend;
  const totalSamples = buckets.reduce((sum, b) => sum + b.sample_count, 0);
  const hasSamples = totalSamples > 0;

  // metric 별 표시값 — search: p95_ms 시계열, vision: sample_count 시계열
  const series: number[] =
    metric === 'search'
      ? buckets.map((b) => b.p95_ms ?? 0)
      : buckets.map((b) => b.sample_count);
  const seriesMax = Math.max(...series, 1);

  // sparkline path — viewBox 좌상단 = 가장 오래된 bucket
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
  const seriesLabel =
    metric === 'search' ? 'p95 (ms)' : '호출 수';
  const rangeLabel = RANGE_LABELS[range];

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base font-semibold">
          {titleText}
          <span className="ml-2 text-[10px] font-normal text-muted-foreground">
            {rangeLabel} 기준
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {errorCode === 'migrations_pending' ? (
          <p className="text-sm text-muted-foreground">
            마이그레이션 005·006·007 적용 후 자동 활성됩니다.
          </p>
        ) : !hasSamples ? (
          <p className="text-sm text-muted-foreground">
            측정 데이터가 없습니다. 잠시 후 다시 확인해주세요.
          </p>
        ) : (
          <>
            <div className="rounded border border-border bg-background/40 p-2">
              <svg
                viewBox={`0 0 ${SVG_WIDTH} ${SVG_HEIGHT}`}
                className="h-12 w-full"
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

const RANGE_LABELS: Record<string, string> = {
  '24h': '최근 24시간',
  '7d': '최근 7일',
  '30d': '최근 30일',
};
