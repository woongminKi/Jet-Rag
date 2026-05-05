const RELATIVE_THRESHOLDS: Array<{ unit: Intl.RelativeTimeFormatUnit; ms: number }> = [
  { unit: 'year', ms: 365 * 24 * 60 * 60 * 1000 },
  { unit: 'month', ms: 30 * 24 * 60 * 60 * 1000 },
  { unit: 'day', ms: 24 * 60 * 60 * 1000 },
  { unit: 'hour', ms: 60 * 60 * 1000 },
  { unit: 'minute', ms: 60 * 1000 },
  { unit: 'second', ms: 1000 },
];

const KO_RELATIVE = new Intl.RelativeTimeFormat('ko', { numeric: 'auto' });

export function formatRelativeTime(iso: string, now: Date = new Date()): string {
  const target = new Date(iso);
  if (Number.isNaN(target.getTime())) return '';
  const diffMs = target.getTime() - now.getTime();
  const absMs = Math.abs(diffMs);
  if (absMs < 1000) return '방금';
  for (const { unit, ms } of RELATIVE_THRESHOLDS) {
    if (absMs >= ms || unit === 'second') {
      const value = Math.round(diffMs / ms);
      return KO_RELATIVE.format(value, unit);
    }
  }
  return '';
}

const SIZE_UNITS = ['B', 'KB', 'MB', 'GB', 'TB'];

export function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return '0 B';
  const i = Math.min(Math.floor(Math.log(n) / Math.log(1024)), SIZE_UNITS.length - 1);
  const value = n / Math.pow(1024, i);
  const formatted = value >= 10 || i === 0 ? value.toFixed(0) : value.toFixed(1);
  return `${formatted} ${SIZE_UNITS[i]}`;
}

/** W25 D14 Sprint B — ETA 표시용 한국어 자연어 포맷.
 *  - < 10s : "약 10초 남음" (보수적 round-up)
 *  - < 60s : "약 N초 남음"
 *  - < 60m : "약 N분 남음" (1분 미만 절상)
 *  - >= 60m: "약 N시간 N분 남음"
 *  null/음수/0 → null (호출부에서 미노출 책임). */
export function formatRemainingMs(ms: number | null | undefined): string | null {
  if (ms == null || !Number.isFinite(ms) || ms <= 0) return null;
  const totalSec = Math.ceil(ms / 1000);
  if (totalSec < 10) return '약 10초 남음';
  if (totalSec < 60) return `약 ${totalSec}초 남음`;
  const totalMin = Math.ceil(totalSec / 60);
  if (totalMin < 60) return `약 ${totalMin}분 남음`;
  const hours = Math.floor(totalMin / 60);
  const mins = totalMin % 60;
  return mins > 0 ? `약 ${hours}시간 ${mins}분 남음` : `약 ${hours}시간 남음`;
}
