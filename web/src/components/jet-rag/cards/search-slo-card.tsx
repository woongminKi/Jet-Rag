import type { Stats } from '@/lib/api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

interface SearchSloCardProps {
  stats: Stats;
}

// 기획서 §13.1 KPI — P95 검색 응답 자체 목표 500ms / 절대 목표 3000ms.
const SLO_SELF_TARGET_MS = 500;
const SLO_ABSOLUTE_TARGET_MS = 3000;

export function SearchSloCard({ stats }: SearchSloCardProps) {
  const slo = stats.search_slo;
  const hasSamples = slo.sample_count > 0;

  const p95Class = !hasSamples
    ? 'text-muted-foreground'
    : (slo.p95_ms ?? 0) > SLO_ABSOLUTE_TARGET_MS
      ? 'text-destructive'
      : (slo.p95_ms ?? 0) > SLO_SELF_TARGET_MS
        ? 'text-orange-500'
        : 'text-foreground';

  const cacheHitPct =
    slo.cache_hit_rate != null ? Math.round(slo.cache_hit_rate * 100) : null;

  // fallback_breakdown 키 (transient_5xx / permanent_4xx / none) 안전 접근
  const fallback = (slo as { fallback_breakdown?: Record<string, number> })
    .fallback_breakdown ?? {};
  const transientCount = fallback['transient_5xx'] ?? 0;
  const permanentCount = fallback['permanent_4xx'] ?? 0;

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base font-semibold">
          검색 응답 SLO
          <span className="ml-2 text-[10px] font-normal text-muted-foreground">
            최근 500건 (in-memory)
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {!hasSamples ? (
          <p className="text-sm text-muted-foreground">
            측정 데이터가 없습니다. 검색을 한 번 실행하면 채워집니다.
          </p>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-3 text-sm">
              <Metric
                label="p50"
                value={slo.p50_ms != null ? `${slo.p50_ms}ms` : '—'}
                tone="default"
              />
              <Metric
                label="p95"
                value={slo.p95_ms != null ? `${slo.p95_ms}ms` : '—'}
                tone="custom"
                customClass={p95Class}
              />
              <Metric
                label="샘플"
                value={`${slo.sample_count.toLocaleString()}건`}
                tone="muted"
              />
              <Metric
                label="cache hit"
                value={cacheHitPct != null ? `${cacheHitPct}%` : '—'}
                tone="default"
              />
            </div>

            {(transientCount > 0 || permanentCount > 0) && (
              <ul className="space-y-1.5 border-t border-border pt-2 text-xs">
                {transientCount > 0 && (
                  <li className="flex justify-between">
                    <span className="text-muted-foreground">transient 5xx fallback</span>
                    <span className="font-mono tabular-nums text-orange-500">
                      {transientCount}
                    </span>
                  </li>
                )}
                {permanentCount > 0 && (
                  <li className="flex justify-between">
                    <span className="text-muted-foreground">permanent 4xx (503)</span>
                    <span className="font-mono tabular-nums text-destructive">
                      {permanentCount}
                    </span>
                  </li>
                )}
              </ul>
            )}

            <div className="border-t border-border pt-2 text-[10px] text-muted-foreground">
              자체 목표 ≤ {SLO_SELF_TARGET_MS}ms · 절대 ≤ {SLO_ABSOLUTE_TARGET_MS}ms
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

function Metric({
  label,
  value,
  tone,
  customClass,
}: {
  label: string;
  value: string;
  tone: 'default' | 'muted' | 'custom';
  customClass?: string;
}) {
  const valueClass =
    tone === 'custom'
      ? `font-mono tabular-nums font-semibold ${customClass ?? ''}`
      : tone === 'muted'
        ? 'font-mono tabular-nums text-muted-foreground'
        : 'font-mono tabular-nums font-semibold text-foreground';
  return (
    <div className="flex items-center justify-between rounded border border-border bg-background/40 px-2 py-1.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className={valueClass}>{value}</span>
    </div>
  );
}
