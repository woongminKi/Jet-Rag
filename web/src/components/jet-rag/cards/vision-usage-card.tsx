import type { Stats } from '@/lib/api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

interface VisionUsageCardProps {
  stats: Stats;
}

// Gemini 2.5 Flash 무료 티어 RPD — 본 cap 은 사용자 환경 의존이라 안내용 기준값.
// (Vision OCR 외 tag_summarize·doc_embed 등 다른 stage 호출도 quota 공유)
const RPD_CAP = 20;

export function VisionUsageCard({ stats }: VisionUsageCardProps) {
  const { total_calls, success_calls, error_calls, last_called_at } =
    stats.vision_usage;

  // RPD 20 대비 사용량 (총 호출 기준 — 100% 초과 시 cap 표기 유지)
  const usageRatio = Math.min(1, total_calls / RPD_CAP);
  const usagePct = Math.round(usageRatio * 100);
  const overCap = total_calls > RPD_CAP;

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base font-semibold">
          Vision 사용량
          <span className="ml-2 text-[10px] font-normal text-muted-foreground">
            프로세스 시작 후 누적
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-1.5">
          <div className="flex items-center justify-between text-xs">
            <span className="text-muted-foreground">
              호출 (RPD 20 기준)
            </span>
            <span
              className={
                overCap
                  ? 'font-mono tabular-nums font-semibold text-destructive'
                  : 'font-mono tabular-nums font-semibold text-foreground'
              }
            >
              {total_calls.toLocaleString()} / {RPD_CAP}
            </span>
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
            <div
              className={
                overCap
                  ? 'h-full bg-destructive'
                  : usagePct >= 75
                    ? 'h-full bg-orange-500'
                    : 'h-full bg-primary'
              }
              style={{ width: `${usagePct}%` }}
            />
          </div>
          {overCap && (
            <p className="text-[10px] text-destructive">
              RPD cap 초과 — 추가 호출은 429 가능성 ↑
            </p>
          )}
        </div>

        <ul className="space-y-2 text-sm">
          <Row
            label="성공"
            value={success_calls.toLocaleString()}
            dotClass="bg-primary"
          />
          <Row
            label="실패 (4xx/5xx)"
            value={error_calls.toLocaleString()}
            dotClass="bg-destructive/70"
          />
          <Row
            label="마지막 호출"
            value={formatLastCalledAt(last_called_at)}
            dotClass="bg-muted-foreground/30"
          />
        </ul>
      </CardContent>
    </Card>
  );
}

function Row({
  label,
  value,
  dotClass,
}: {
  label: string;
  value: string;
  dotClass: string;
}) {
  return (
    <li className="flex items-center justify-between gap-2">
      <span className="flex items-center gap-1.5 text-muted-foreground">
        <span className={`h-2 w-2 rounded-full ${dotClass}`} />
        {label}
      </span>
      <span className="font-mono tabular-nums text-foreground">{value}</span>
    </li>
  );
}

function formatLastCalledAt(value: string | null): string {
  if (!value) return '없음';
  try {
    const dt = new Date(value);
    if (Number.isNaN(dt.getTime())) return value;
    const diffMs = Date.now() - dt.getTime();
    const diffMin = Math.floor(diffMs / 60_000);
    if (diffMin < 1) return '방금 전';
    if (diffMin < 60) return `${diffMin}분 전`;
    const diffHr = Math.floor(diffMin / 60);
    if (diffHr < 24) return `${diffHr}시간 전`;
    return dt.toLocaleString('ko-KR', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return value;
  }
}
