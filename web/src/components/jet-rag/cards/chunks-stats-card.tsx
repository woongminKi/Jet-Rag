import type { Stats } from '@/lib/api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

interface ChunksStatsCardProps {
  stats: Stats;
}

const REASON_LABEL: Record<string, string> = {
  table_noise: '표 노이즈',
  header_footer: '헤더·푸터',
  empty: '빈 청크',
  extreme_short: '초단편',
};

export function ChunksStatsCard({ stats }: ChunksStatsCardProps) {
  const { total, effective, filtered_breakdown, filtered_ratio } = stats.chunks;
  const filtered = total - effective;
  const filteredPct = Math.round(filtered_ratio * 100);
  const effectivePct = 100 - filteredPct;

  const breakdownEntries = Object.entries(filtered_breakdown).sort(
    (a, b) => b[1] - a[1],
  );

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base font-semibold">청크 분포</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center gap-4">
          <Donut effectivePct={effectivePct} />
          <div className="flex-1 space-y-1.5 text-sm">
            <Row
              dotClass="bg-primary"
              label="검색 대상"
              value={`${effective.toLocaleString()}건 (${effectivePct}%)`}
            />
            <Row
              dotClass="bg-muted-foreground/40"
              label="필터 마킹"
              value={`${filtered.toLocaleString()}건 (${filteredPct}%)`}
            />
            <div className="pt-1 text-[10px] text-muted-foreground">
              총 {total.toLocaleString()}건
            </div>
          </div>
        </div>

        {breakdownEntries.length > 0 && (
          <div className="space-y-2 border-t border-border pt-3">
            <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
              마킹 사유별 분포
            </div>
            <ul className="space-y-1.5">
              {breakdownEntries.map(([reason, count]) => {
                const pct = filtered > 0 ? (count / filtered) * 100 : 0;
                return (
                  <li key={reason} className="space-y-1">
                    <div className="flex items-center justify-between text-xs">
                      <span className="text-muted-foreground">
                        {REASON_LABEL[reason] ?? reason}
                      </span>
                      <span className="font-mono tabular-nums text-foreground">
                        {count.toLocaleString()}
                      </span>
                    </div>
                    <div className="h-1 w-full overflow-hidden rounded-full bg-muted">
                      <div
                        className="h-full bg-muted-foreground/50"
                        style={{ width: `${pct.toFixed(1)}%` }}
                      />
                    </div>
                  </li>
                );
              })}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function Donut({ effectivePct }: { effectivePct: number }) {
  // SVG 도넛 — 외부 의존성 없이 stroke-dasharray 로 구현
  const r = 28;
  const c = 2 * Math.PI * r;
  const effectiveLen = (effectivePct / 100) * c;
  const filteredLen = c - effectiveLen;

  return (
    <svg width="72" height="72" viewBox="0 0 72 72" className="shrink-0">
      <circle
        cx="36"
        cy="36"
        r={r}
        fill="none"
        className="stroke-muted-foreground/30"
        strokeWidth="10"
      />
      <circle
        cx="36"
        cy="36"
        r={r}
        fill="none"
        className="stroke-primary"
        strokeWidth="10"
        strokeDasharray={`${effectiveLen} ${filteredLen}`}
        strokeDashoffset={c / 4}
        transform="rotate(-90 36 36)"
      />
      <text
        x="36"
        y="40"
        textAnchor="middle"
        className="fill-foreground text-[14px] font-semibold tabular-nums"
      >
        {effectivePct}%
      </text>
    </svg>
  );
}

function Row({
  dotClass,
  label,
  value,
}: {
  dotClass: string;
  label: string;
  value: string;
}) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="flex items-center gap-1.5 text-muted-foreground">
        <span className={`h-2 w-2 rounded-full ${dotClass}`} />
        {label}
      </span>
      <span className="font-medium tabular-nums text-foreground">{value}</span>
    </div>
  );
}
