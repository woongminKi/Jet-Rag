'use client';

/**
 * S1 D3 ship — `/admin/queries` 페이지의 client component.
 *
 * 책임
 * - range (7d/14d/30d) 토글 + Server Component 가 fetch 한 initial 응답 우선 표시
 * - 토글 시 `getAdminQueriesStats` refetch — useTransition 으로 race 방지
 * - 4 섹션: 요약 4 카드 / 일별 sparkline / query_type 분포 / 실패 케이스
 * - graceful — `error_code='migrations_pending'`, row 0건, classify 실패 모두 별도 안내
 *
 * 패턴 정합 (web/AGENTS.md)
 * - §1 Server Component 첫 fetch + Client Component refetch
 * - §2 React 19 lint — useEffect 동기 setState 금지, handler 분리
 * - §3 useTransition + interruptible navigation
 * - §4 SVG/CSS 직접 시각화 (recharts/visx 의존성 0)
 * - §5 mobile-first responsive (sm: breakpoint)
 */

import { useEffect, useState, useTransition } from 'react';
import {
  getAdminQueriesStats,
  type AdminQueriesStatsResponse,
  type AdminRange,
  type AdminFailureReason,
} from '@/lib/api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

interface QueriesDashboardProps {
  /** Server Component 가 초기 fetch 한 stats. 토글 시 client refetch. */
  initialStats: AdminQueriesStatsResponse;
}

const RANGE_OPTIONS: AdminRange[] = ['7d', '14d', '30d'];

// SVG sparkline canvas — recharts 미사용. metrics-trend-card 패턴 동일.
const SVG_WIDTH = 480;
const SVG_HEIGHT = 80;
const SVG_PADDING = 6;

// query_type 9 라벨 한국어 표기 — backend `_QUERY_TYPE_LABELS` 와 동기.
const QUERY_TYPE_LABELS_KO: Record<string, string> = {
  exact_fact: '단편 사실',
  fuzzy_memory: '흐릿한 기억',
  vision_diagram: '그림·다이어그램',
  table_lookup: '표·목록 조회',
  numeric_lookup: '숫자·금액 조회',
  cross_doc: '여러 문서 비교',
  summary: '요약·정리',
  synonym_mismatch: '동의어 미스매치',
  out_of_scope: '범위 외',
};

const QUERY_TYPE_ORDER: string[] = [
  'exact_fact',
  'fuzzy_memory',
  'vision_diagram',
  'table_lookup',
  'numeric_lookup',
  'cross_doc',
  'summary',
  'synonym_mismatch',
  'out_of_scope',
];

const FAILURE_REASON_LABELS_KO: Record<AdminFailureReason, string> = {
  permanent_4xx: 'HF 영구 오류',
  transient_5xx: 'HF 일시 오류',
  no_hits: '검색 결과 0건',
};

export function QueriesDashboard({ initialStats }: QueriesDashboardProps) {
  const [range, setRange] = useState<AdminRange>(initialStats.range);
  const [fetched, setFetched] = useState<AdminQueriesStatsResponse | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();
  // 재시도 토큰 — fetchError 후 사용자 클릭 시 useEffect 재실행 트리거.
  const [retryToken, setRetryToken] = useState(0);

  // initialStats 와 같은 range 면 초기 SSR 데이터 사용 — fetch 생략.
  const isInitialQuery = retryToken === 0 && range === initialStats.range;

  useEffect(() => {
    if (isInitialQuery) return;
    let cancelled = false;
    getAdminQueriesStats(range)
      .then((next) => {
        if (cancelled) return;
        setFetched(next);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setFetchError(
          err instanceof Error
            ? err.message
            : '실 query 통계를 불러오지 못했습니다.',
        );
      });
    return () => {
      cancelled = true;
    };
  }, [range, isInitialQuery, retryToken]);

  const handleRangeChange = (next: AdminRange) => {
    if (next === range) return;
    // useTransition 으로 race 방지 — 빠른 클릭 시 React 가 stale transition cancel.
    startTransition(() => {
      setRange(next);
      setFetchError(null);
    });
  };

  const handleRetry = () => {
    setFetchError(null);
    setRetryToken((n) => n + 1);
  };

  // 활성 stats — initialStats 와 range 일치 시 우선, 아니면 fetched.
  const stats: AdminQueriesStatsResponse | null =
    initialStats.range === range
      ? initialStats
      : fetched && fetched.range === range
        ? fetched
        : null;

  return (
    <div className="space-y-6">
      <DashboardHeader
        range={range}
        onRangeChange={handleRangeChange}
        disabled={isPending}
      />

      {fetchError ? (
        <ErrorCard message={fetchError} onRetry={handleRetry} />
      ) : stats === null ? (
        <LoadingCard />
      ) : stats.error_code === 'migrations_pending' ? (
        <MigrationsPendingCard />
      ) : stats.total_queries === 0 ? (
        <EmptyDataCard />
      ) : (
        <DashboardSections stats={stats} isPending={isPending} />
      )}
    </div>
  );
}

// ----------------------- 헤더 + 토글 -----------------------

function DashboardHeader({
  range,
  onRangeChange,
  disabled,
}: {
  range: AdminRange;
  onRangeChange: (next: AdminRange) => void;
  disabled?: boolean;
}) {
  return (
    <header className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <h1 className="text-xl font-bold tracking-tight text-foreground sm:text-2xl">
          실 query 로그
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          최근 {range} 동안 사용자가 실제로 검색한 query 분포 — S1 D3 (master plan §6).
        </p>
      </div>
      <RangeToggle
        value={range}
        onChange={onRangeChange}
        disabled={disabled}
      />
    </header>
  );
}

function RangeToggle({
  value,
  onChange,
  disabled,
}: {
  value: AdminRange;
  onChange: (next: AdminRange) => void;
  disabled?: boolean;
}) {
  return (
    <div
      role="radiogroup"
      aria-label="시간 범위"
      className="inline-flex rounded border border-border bg-background/40 text-xs"
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
            aria-busy={disabled && active}
            className={`px-3 py-1.5 transition-colors disabled:opacity-50 ${
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

// ----------------------- 분기별 카드 -----------------------

function LoadingCard() {
  return (
    <Card>
      <CardContent className="p-6 text-sm text-muted-foreground">
        실 query 통계를 불러오는 중…
      </CardContent>
    </Card>
  );
}

function ErrorCard({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <Card>
      <CardContent className="space-y-3 p-6">
        <p className="text-sm text-destructive">{message}</p>
        <button
          type="button"
          onClick={onRetry}
          className="rounded border border-border bg-background/40 px-3 py-1.5 text-xs text-foreground hover:bg-foreground/5"
        >
          다시 시도
        </button>
      </CardContent>
    </Card>
  );
}

function MigrationsPendingCard() {
  return (
    <Card>
      <CardContent className="p-6 text-sm text-muted-foreground">
        마이그레이션 006 (search_metrics_log) 적용 후 자동으로 활성됩니다. 현재 누적된 query 가 없어
        대시보드를 표시할 수 없습니다.
      </CardContent>
    </Card>
  );
}

function EmptyDataCard() {
  return (
    <Card>
      <CardContent className="p-6 text-sm text-muted-foreground">
        아직 누적된 query 가 없습니다. 검색을 몇 번 사용한 뒤 다시 확인해주세요.
      </CardContent>
    </Card>
  );
}

// ----------------------- 본 대시보드 섹션 -----------------------

function DashboardSections({
  stats,
  isPending,
}: {
  stats: AdminQueriesStatsResponse;
  isPending: boolean;
}) {
  return (
    <div
      className={`space-y-6 transition-opacity ${
        isPending ? 'opacity-60' : 'opacity-100'
      }`}
      aria-busy={isPending}
    >
      <SummaryCards stats={stats} />
      <DailySparklineCard stats={stats} />
      <QueryTypeDistributionCard stats={stats} />
      <FailedSamplesCard stats={stats} />
    </div>
  );
}

function SummaryCards({ stats }: { stats: AdminQueriesStatsResponse }) {
  const failedCount = stats.total_queries - successFromStats(stats);
  const successPct =
    stats.success_rate !== null ? Math.round(stats.success_rate * 100) : null;
  return (
    <section className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <SummaryCard
        label="총 query"
        value={`${stats.total_queries.toLocaleString()}건`}
      />
      <SummaryCard
        label="성공률"
        value={successPct !== null ? `${successPct}%` : '—'}
      />
      <SummaryCard
        label="평균 latency"
        value={
          stats.avg_latency_ms !== null
            ? `${stats.avg_latency_ms.toLocaleString()}ms`
            : '—'
        }
      />
      <SummaryCard label="실패 건수" value={`${failedCount.toLocaleString()}건`} />
    </section>
  );
}

function SummaryCard({ label, value }: { label: string; value: string }) {
  return (
    <Card>
      <CardContent className="p-3 sm:p-4">
        <p className="text-[11px] text-muted-foreground sm:text-xs">{label}</p>
        <p className="mt-1 font-mono text-base font-semibold tabular-nums text-foreground sm:text-lg">
          {value}
        </p>
      </CardContent>
    </Card>
  );
}

function DailySparklineCard({ stats }: { stats: AdminQueriesStatsResponse }) {
  const counts = stats.daily.map((d) => d.count);
  const maxCount = Math.max(...counts, 1);
  const points = counts
    .map((v, i) => {
      const x =
        SVG_PADDING +
        ((SVG_WIDTH - 2 * SVG_PADDING) * i) /
          Math.max(counts.length - 1, 1);
      const y =
        SVG_HEIGHT -
        SVG_PADDING -
        ((SVG_HEIGHT - 2 * SVG_PADDING) * v) / maxCount;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');

  // 첫·마지막 날짜만 라벨 — 너무 빽빽하지 않게.
  const firstLabel = stats.daily[0]?.date ?? '';
  const lastLabel = stats.daily[stats.daily.length - 1]?.date ?? '';

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base font-semibold">일별 query 분포</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 pb-4">
        <div className="rounded border border-border bg-background/40 p-2">
          <svg
            viewBox={`0 0 ${SVG_WIDTH} ${SVG_HEIGHT}`}
            preserveAspectRatio="none"
            className="h-20 w-full text-foreground"
            role="img"
            aria-label={`일별 query 수 sparkline (${counts.length}일)`}
          >
            <polyline
              points={points}
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
            />
          </svg>
        </div>
        <div className="flex items-center justify-between text-[10px] text-muted-foreground">
          <span>{firstLabel}</span>
          <span>{counts.length}일 · 최대 {maxCount.toLocaleString()}건/일</span>
          <span>{lastLabel}</span>
        </div>
      </CardContent>
    </Card>
  );
}

function QueryTypeDistributionCard({
  stats,
}: {
  stats: AdminQueriesStatsResponse;
}) {
  const dist = stats.query_type_distribution;
  // distribution 비어있으면 (classify 실패) 안내만 노출.
  if (Object.keys(dist).length === 0) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base font-semibold">query_type 분포</CardTitle>
        </CardHeader>
        <CardContent className="pb-4 text-sm text-muted-foreground">
          query_type 분류기 (`evals/auto_goldenset.py`) 를 불러오지 못했습니다.
        </CardContent>
      </Card>
    );
  }

  // 가장 큰 값을 max 로 정규화 — 0 회피 (max=1 보장).
  const maxValue = Math.max(...QUERY_TYPE_ORDER.map((k) => dist[k] ?? 0), 1);

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base font-semibold">query_type 분포</CardTitle>
      </CardHeader>
      <CardContent className="pb-4">
        <ul className="space-y-2">
          {QUERY_TYPE_ORDER.map((key) => {
            const count = dist[key] ?? 0;
            const widthPct = Math.max((count / maxValue) * 100, 2);
            return (
              <li
                key={key}
                className="grid grid-cols-[120px_1fr_48px] items-center gap-2 text-xs sm:grid-cols-[160px_1fr_56px] sm:gap-3 sm:text-sm"
              >
                <span className="truncate text-foreground">
                  {QUERY_TYPE_LABELS_KO[key] ?? key}
                </span>
                <div
                  className="h-2 overflow-hidden rounded-sm bg-border"
                  role="progressbar"
                  aria-valuenow={count}
                  aria-valuemax={maxValue}
                >
                  <div
                    className="h-full bg-foreground/60"
                    style={{ width: `${widthPct}%` }}
                  />
                </div>
                <span className="text-right font-mono tabular-nums text-muted-foreground">
                  {count.toLocaleString()}
                </span>
              </li>
            );
          })}
        </ul>
      </CardContent>
    </Card>
  );
}

function FailedSamplesCard({ stats }: { stats: AdminQueriesStatsResponse }) {
  const samples = stats.failed_samples;
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base font-semibold">
          실패 케이스 (최근 {samples.length}건)
        </CardTitle>
      </CardHeader>
      <CardContent className="pb-4">
        {samples.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            최근 범위에서 실패한 query 가 없습니다.
          </p>
        ) : (
          <ul className="space-y-2">
            {samples.map((s, i) => (
              <li
                key={`${s.ts}-${i}`}
                className="rounded border border-border bg-background/40 p-2 text-xs sm:text-sm"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <FailureBadge reason={s.reason} />
                  <span className="font-mono text-[10px] text-muted-foreground sm:text-xs">
                    {formatTs(s.ts)}
                  </span>
                </div>
                <p className="mt-1 break-words text-foreground">{s.query || '(빈 query)'}</p>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function FailureBadge({ reason }: { reason: AdminFailureReason }) {
  const label = FAILURE_REASON_LABELS_KO[reason];
  // reason 별 색 미세 차이 — destructive (영구), warning-tone (일시), muted (no_hits).
  // shadcn token 만 사용 (CSS color 직접 지정 X).
  const className =
    reason === 'permanent_4xx'
      ? 'bg-destructive/15 text-destructive border-destructive/40'
      : reason === 'transient_5xx'
        ? 'bg-foreground/10 text-foreground border-border'
        : 'bg-muted text-muted-foreground border-border';
  return (
    <span
      className={`inline-flex rounded border px-1.5 py-0.5 text-[10px] font-medium sm:text-xs ${className}`}
    >
      {label}
    </span>
  );
}

// ----------------------- helpers -----------------------

function successFromStats(stats: AdminQueriesStatsResponse): number {
  if (stats.success_rate === null) return 0;
  return Math.round(stats.success_rate * stats.total_queries);
}

function formatTs(iso: string): string {
  if (!iso) return '';
  // 단순 슬라이스 — full Date 변환 없이 ISO 그대로 (서버가 UTC ISO).
  // YYYY-MM-DD HH:MM 형태로 잘라 표시.
  return iso.replace('T', ' ').slice(0, 16);
}
