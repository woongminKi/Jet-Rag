'use client';

/**
 * S1 D4 ship — `/admin/feedback` 페이지의 client component.
 *
 * 책임
 * - range (7d/14d/30d) 토글 + Server Component 가 fetch 한 initial 응답 우선 표시
 * - 토글 시 `getAdminFeedbackStats` refetch — useTransition 으로 race 방지
 * - 5 섹션: 헤더 / 요약 4 카드 / 일별 sparkline / 코멘트 카테고리 분포 / 최근 코멘트 리스트
 * - graceful — `error_code='migrations_pending'`, row 0건, 백엔드 미기동 모두 별도 안내
 *
 * 패턴 정합 (web/AGENTS.md) — `queries-dashboard.tsx` 와 동일.
 * - §1 Server Component 첫 fetch + Client Component refetch
 * - §2 React 19 lint — useEffect 동기 setState 금지, handler 분리
 * - §3 useTransition + interruptible navigation
 * - §4 SVG/CSS 직접 시각화 (recharts/visx 의존성 0)
 * - §5 mobile-first responsive (sm: breakpoint)
 */

import { useEffect, useState, useTransition } from 'react';
import {
  getAdminFeedbackStats,
  type AdminFeedbackCategory,
  type AdminFeedbackRating,
  type AdminFeedbackStatsResponse,
  type AdminRange,
} from '@/lib/api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

interface FeedbackDashboardProps {
  /** Server Component 가 초기 fetch 한 stats. 토글 시 client refetch. */
  initialStats: AdminFeedbackStatsResponse;
}

const RANGE_OPTIONS: AdminRange[] = ['7d', '14d', '30d'];

// SVG sparkline canvas — recharts 미사용. queries-dashboard 패턴 동일.
const SVG_WIDTH = 480;
const SVG_HEIGHT = 80;
const SVG_PADDING = 6;

// 카테고리 4종 한국어 표기 — backend `_COMMENT_CATEGORIES` 와 동기.
const CATEGORY_LABELS_KO: Record<AdminFeedbackCategory, string> = {
  search_issue: '검색 결과 문제',
  answer_issue: '답변 정확도',
  source_issue: '출처·근거',
  other: '그 외',
};

const CATEGORY_ORDER: AdminFeedbackCategory[] = [
  'search_issue',
  'answer_issue',
  'source_issue',
  'other',
];

export function FeedbackDashboard({ initialStats }: FeedbackDashboardProps) {
  const [range, setRange] = useState<AdminRange>(initialStats.range);
  const [fetched, setFetched] = useState<AdminFeedbackStatsResponse | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();
  const [retryToken, setRetryToken] = useState(0);

  const isInitialQuery = retryToken === 0 && range === initialStats.range;

  useEffect(() => {
    if (isInitialQuery) return;
    let cancelled = false;
    getAdminFeedbackStats(range)
      .then((next) => {
        if (cancelled) return;
        setFetched(next);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setFetchError(
          err instanceof Error
            ? err.message
            : '사용자 피드백 통계를 불러오지 못했습니다.',
        );
      });
    return () => {
      cancelled = true;
    };
  }, [range, isInitialQuery, retryToken]);

  const handleRangeChange = (next: AdminRange) => {
    if (next === range) return;
    startTransition(() => {
      setRange(next);
      setFetchError(null);
    });
  };

  const handleRetry = () => {
    setFetchError(null);
    setRetryToken((n) => n + 1);
  };

  const stats: AdminFeedbackStatsResponse | null =
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
      ) : stats.total_feedback === 0 ? (
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
          사용자 피드백
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          최근 {range} 동안 답변에 누른 👍/👎 + 코멘트 분포 — S1 D4 (master plan §6).
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
        사용자 피드백 통계를 불러오는 중…
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
        마이그레이션 011 (answer_feedback) 적용 후 자동으로 활성됩니다. 현재 누적된 피드백이 없어
        대시보드를 표시할 수 없습니다.
      </CardContent>
    </Card>
  );
}

function EmptyDataCard() {
  return (
    <Card>
      <CardContent className="p-6 text-sm text-muted-foreground">
        아직 누적된 피드백이 없습니다. 답변에 👍/👎 를 몇 번 눌러본 뒤 다시 확인해주세요.
      </CardContent>
    </Card>
  );
}

// ----------------------- 본 대시보드 섹션 -----------------------

function DashboardSections({
  stats,
  isPending,
}: {
  stats: AdminFeedbackStatsResponse;
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
      <CommentCategoriesCard stats={stats} />
      <RecentCommentsCard stats={stats} />
    </div>
  );
}

function SummaryCards({ stats }: { stats: AdminFeedbackStatsResponse }) {
  const satisfactionPct =
    stats.satisfaction_rate !== null
      ? Math.round(stats.satisfaction_rate * 100)
      : null;
  // 코멘트 비율 = comment_count / total_feedback. total=0 이면 EmptyDataCard 분기로 빠지므로 여기선 0 보장 X.
  const commentPct =
    stats.total_feedback > 0
      ? Math.round((stats.comment_count / stats.total_feedback) * 100)
      : 0;
  const downCount = stats.rating_distribution.down ?? 0;
  return (
    <section className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <SummaryCard
        label="총 피드백"
        value={`${stats.total_feedback.toLocaleString()}건`}
      />
      <SummaryCard
        label="만족률"
        value={satisfactionPct !== null ? `${satisfactionPct}%` : '—'}
      />
      <SummaryCard label="코멘트 비율" value={`${commentPct}%`} />
      <SummaryCard
        label="부정 평가"
        value={`${downCount.toLocaleString()}건`}
      />
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

function DailySparklineCard({ stats }: { stats: AdminFeedbackStatsResponse }) {
  const totals = stats.daily.map((d) => d.total);
  const maxCount = Math.max(...totals, 1);
  const points = totals
    .map((v, i) => {
      const x =
        SVG_PADDING +
        ((SVG_WIDTH - 2 * SVG_PADDING) * i) /
          Math.max(totals.length - 1, 1);
      const y =
        SVG_HEIGHT -
        SVG_PADDING -
        ((SVG_HEIGHT - 2 * SVG_PADDING) * v) / maxCount;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');

  // 부정 평가 누적 비율 — 사용자 평가 신호의 경향성 (sparkline 1줄 옆 텍스트로).
  const totalUp = stats.daily.reduce((acc, d) => acc + d.up, 0);
  const totalDown = stats.daily.reduce((acc, d) => acc + d.down, 0);
  const sumDailyTotal = totalUp + totalDown;
  const downRatio = sumDailyTotal > 0 ? Math.round((totalDown / sumDailyTotal) * 100) : 0;

  const firstLabel = stats.daily[0]?.date ?? '';
  const lastLabel = stats.daily[stats.daily.length - 1]?.date ?? '';

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base font-semibold">일별 피드백 분포</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 pb-4">
        <div className="rounded border border-border bg-background/40 p-2">
          <svg
            viewBox={`0 0 ${SVG_WIDTH} ${SVG_HEIGHT}`}
            preserveAspectRatio="none"
            className="h-20 w-full text-foreground"
            role="img"
            aria-label={`일별 피드백 수 sparkline (${totals.length}일)`}
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
          <span>
            {totals.length}일 · 최대 {maxCount.toLocaleString()}건/일 · 부정 비율 {downRatio}%
          </span>
          <span>{lastLabel}</span>
        </div>
      </CardContent>
    </Card>
  );
}

function CommentCategoriesCard({
  stats,
}: {
  stats: AdminFeedbackStatsResponse;
}) {
  const categories = stats.comment_categories;
  // 4 카테고리 max 정규화 — 0 회피 (max=1 보장).
  const maxValue = Math.max(...CATEGORY_ORDER.map((k) => categories[k] ?? 0), 1);

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base font-semibold">코멘트 카테고리 분포</CardTitle>
      </CardHeader>
      <CardContent className="pb-4">
        {stats.comment_count === 0 ? (
          <p className="text-sm text-muted-foreground">
            누적된 코멘트가 없습니다. 사용자가 👍/👎 와 함께 코멘트를 남기면 자동 분류됩니다.
          </p>
        ) : (
          <ul className="space-y-2">
            {CATEGORY_ORDER.map((key) => {
              const count = categories[key] ?? 0;
              const widthPct = Math.max((count / maxValue) * 100, 2);
              return (
                <li
                  key={key}
                  className="grid grid-cols-[120px_1fr_48px] items-center gap-2 text-xs sm:grid-cols-[160px_1fr_56px] sm:gap-3 sm:text-sm"
                >
                  <span className="truncate text-foreground">
                    {CATEGORY_LABELS_KO[key]}
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
        )}
      </CardContent>
    </Card>
  );
}

function RecentCommentsCard({ stats }: { stats: AdminFeedbackStatsResponse }) {
  const comments = stats.recent_comments;
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base font-semibold">
          최근 코멘트 ({comments.length}건)
        </CardTitle>
      </CardHeader>
      <CardContent className="pb-4">
        {comments.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            최근 범위에서 코멘트가 첨부된 피드백이 없습니다.
          </p>
        ) : (
          <ul className="space-y-2">
            {comments.map((c, i) => (
              <li
                key={`${c.ts}-${i}`}
                className="rounded border border-border bg-background/40 p-2 text-xs sm:text-sm"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <RatingBadge rating={c.rating} />
                  <CategoryBadge category={c.category} />
                  <span className="font-mono text-[10px] text-muted-foreground sm:text-xs">
                    {formatTs(c.ts)}
                  </span>
                </div>
                <p className="mt-1 break-words text-muted-foreground">
                  query: {c.query || '(빈 query)'}
                </p>
                <p className="mt-1 break-words text-foreground">{c.comment}</p>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function RatingBadge({ rating }: { rating: AdminFeedbackRating }) {
  const isUp = rating === 'up';
  // up = 만족, down = 불만족. shadcn token 만 사용.
  const className = isUp
    ? 'bg-foreground/10 text-foreground border-border'
    : 'bg-destructive/15 text-destructive border-destructive/40';
  const label = isUp ? '👍 만족' : '👎 불만족';
  return (
    <span
      className={`inline-flex rounded border px-1.5 py-0.5 text-[10px] font-medium sm:text-xs ${className}`}
    >
      {label}
    </span>
  );
}

function CategoryBadge({ category }: { category: AdminFeedbackCategory }) {
  const label = CATEGORY_LABELS_KO[category];
  // category 별 톤 — source/search/answer 는 tone 차이 미세, other 는 muted.
  const className =
    category === 'source_issue'
      ? 'bg-destructive/10 text-destructive border-destructive/30'
      : category === 'search_issue'
        ? 'bg-foreground/10 text-foreground border-border'
        : category === 'answer_issue'
          ? 'bg-foreground/5 text-foreground border-border'
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

function formatTs(iso: string): string {
  if (!iso) return '';
  return iso.replace('T', ' ').slice(0, 16);
}
