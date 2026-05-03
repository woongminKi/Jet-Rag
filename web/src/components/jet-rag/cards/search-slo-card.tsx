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
            <div className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
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

            {/* W14 Day 4 (한계 #83) — by_mode 분리 측정 (ablation 비교)
                W20 Day 4 — p50 비교 bar 추가 (직관적 ablation 시각) */}
            {slo.by_mode &&
              Object.values(slo.by_mode).some((m) => m.sample_count > 0) && (
                <div className="space-y-1.5 border-t border-border pt-2">
                  <div className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                    mode 별 비교 (ablation)
                  </div>
                  {(() => {
                    const entries = (['hybrid', 'dense', 'sparse'] as const)
                      .map((m) => ({ mode: m, entry: slo.by_mode?.[m] }))
                      .filter((x) => x.entry && x.entry.sample_count > 0);
                    if (entries.length === 0) return null;
                    const maxP50 = Math.max(
                      ...entries.map((x) => x.entry!.p50_ms ?? 0),
                      1,
                    );
                    return (
                      <ul className="space-y-1">
                        {entries.map(({ mode: m, entry }) => {
                          if (!entry) return null;
                          const p50 = entry.p50_ms ?? 0;
                          const widthPct = Math.max(
                            (p50 / maxP50) * 100,
                            4, // 0% 시 보이지 않으므로 최소 4% 보장
                          );
                          return (
                            <li key={m} className="space-y-0.5">
                              <div className="flex items-center justify-between text-xs">
                                <span className="font-mono text-muted-foreground">
                                  {m}
                                </span>
                                <span className="font-mono tabular-nums text-foreground">
                                  p50 {p50}ms · n {entry.sample_count}
                                </span>
                              </div>
                              <div
                                className="h-1 overflow-hidden rounded-sm bg-border"
                                role="progressbar"
                                aria-valuenow={p50}
                                aria-valuemin={0}
                                aria-valuemax={maxP50}
                                aria-label={`${m} p50 ${p50}ms`}
                              >
                                <div
                                  className="h-full bg-foreground/60"
                                  style={{ width: `${widthPct}%` }}
                                />
                              </div>
                            </li>
                          );
                        })}
                      </ul>
                    );
                  })()}
                </div>
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
