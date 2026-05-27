import type { Stats } from '@/lib/api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

interface MyDocStatsCardProps {
  stats: Stats;
}

export function MyDocStatsCard({ stats }: MyDocStatsCardProps) {
  const { documents, jobs } = stats;
  const hwpTotal =
    (documents.by_doc_type.hwp ?? 0) + (documents.by_doc_type.hwpx ?? 0);
  const inProgress =
    (jobs.by_status.queued ?? 0) + (jobs.by_status.running ?? 0);

  const rows: Array<{ label: string; value: string; accent?: boolean }> = [
    { label: '총 문서', value: `${documents.total}건` },
    {
      label: '이번달 추가',
      value: `+${documents.added_this_month}건`,
      accent: true,
    },
    { label: 'PDF', value: `${documents.by_doc_type.pdf ?? 0}건` },
    { label: 'HWP', value: `${hwpTotal}건` },
    { label: '스크린샷', value: `${documents.by_doc_type.image ?? 0}건` },
    { label: '처리 중', value: `${inProgress}건` },
  ];

  return (
    <Card className="overflow-hidden rounded-2xl">
      <CardHeader className="pb-3">
        <CardTitle className="text-lg font-semibold">내 문서 현황</CardTitle>
      </CardHeader>
      <CardContent>
        <ul className="space-y-3">
          {rows.map((row) => (
            <li
              key={row.label}
              className="flex items-center justify-between gap-3 text-sm"
            >
              <span className="min-w-0 truncate text-muted-foreground">{row.label}</span>
              <span
                className={
                  row.accent
                    ? 'shrink-0 font-semibold tabular-nums text-primary'
                    : 'shrink-0 font-semibold tabular-nums text-foreground'
                }
              >
                {row.value}
              </span>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
