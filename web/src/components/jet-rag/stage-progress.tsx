import { Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { JobStatusValue, StageProgressDetail, StageValue } from '@/lib/api';
import { formatRemainingMs, formatStageProgress } from '@/lib/format';
import { STAGE_LABELS, STAGE_ORDER } from '@/lib/stages';

interface StageProgressProps {
  currentStage: StageValue | null;
  status: JobStatusValue;
  /** W25 D14 Sprint B — ingest_logs median + fallback 으로 추정한 남은 시간(ms).
   *  queued/running 시만 값. null 이면 ETA 미노출 (백엔드 미기동 환경 graceful). */
  estimatedRemainingMs?: number | null;
  /** W25 D14 — stage 안 sub-step 진행 (예: vision_enrich 페이지 12/41). */
  stageProgress?: StageProgressDetail | null;
}

// E1 1차 ship (2026-05-07) — current stage 의 progress bar 칸에 표시할 비율(%).
// stage_progress 가 유효하면 current/total *100, 아니면 indeterminate 시각화용 기본값.
function computeCurrentBarPct(stageProgress: StageProgressDetail | null | undefined): number {
  if (
    stageProgress &&
    Number.isFinite(stageProgress.current) &&
    Number.isFinite(stageProgress.total) &&
    stageProgress.total > 0
  ) {
    const pct = (stageProgress.current / stageProgress.total) * 100;
    return Math.max(0, Math.min(100, pct));
  }
  // stage_progress 없는 stage (chunk/load/embed 등) 는 50% — 단순 indeterminate.
  return 50;
}

export function StageProgress({
  currentStage,
  status,
  estimatedRemainingMs,
  stageProgress,
}: StageProgressProps) {
  const isDone = status === 'completed';
  const isFailed = status === 'failed';
  const inProgressStage = !isDone && currentStage !== 'done' ? currentStage : null;
  const currentIdx = inProgressStage ? STAGE_ORDER.indexOf(inProgressStage) : -1;
  // W25 D14 Sprint A — running 상태 진행 stage 옆 spinner. queued/done/failed 시 미노출.
  const showRunningSpinner = status === 'running' && inProgressStage !== null;
  // W25 D14 Sprint B — ETA 표시 (running/queued 만, completed/failed 시 null).
  const remainingLabel =
    !isDone && !isFailed ? formatRemainingMs(estimatedRemainingMs) : null;
  // E1 1차 ship (2026-05-07) — 첫 ingest (백엔드 sample <3) 시 ETA None.
  // estimatedRemainingMs == null + 진행 중 → 안내 카피 노출 (plan §8-5 default).
  const showFirstIngestNotice =
    !isDone && !isFailed && remainingLabel == null &&
    (estimatedRemainingMs == null) &&
    (status === 'running' || status === 'queued');
  // W25 D14 — stage 내 sub-step (예: "12/41 페이지").
  const subProgressLabel =
    !isDone && !isFailed ? formatStageProgress(stageProgress) : null;
  // E1 1차 ship — 현재 stage 칸 부분 색칠 비율 (0~100).
  const currentBarPct = computeCurrentBarPct(stageProgress);

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-1.5">
        {STAGE_ORDER.map((stage, idx) => {
          const failedHere = isFailed && idx === currentIdx;
          // 칸 별 색칠 비율: 완료 100, 진행 중은 stage_progress 비율, 미도달 0.
          // failed 인 stage 도 100% 채우되 색만 destructive 로.
          const fillPct = isDone
            ? 100
            : failedHere
              ? 100
              : idx < currentIdx
                ? 100
                : idx === currentIdx
                  ? currentBarPct
                  : 0;
          const fillColor = failedHere
            ? 'bg-destructive'
            : isDone
              ? 'bg-success'
              : 'bg-primary';
          return (
            <div
              key={stage}
              className="relative h-1.5 flex-1 overflow-hidden rounded-full bg-muted"
              aria-label={STAGE_LABELS[stage]}
              title={STAGE_LABELS[stage]}
            >
              <div
                className={cn('h-full rounded-full transition-[width]', fillColor)}
                style={{ width: `${fillPct}%` }}
              />
            </div>
          );
        })}
      </div>
      <div className="flex items-center justify-between text-[11px] text-muted-foreground">
        <span className="flex items-center gap-1.5">
          {showRunningSpinner && (
            <Loader2 className="h-3 w-3 animate-spin text-primary" aria-hidden />
          )}
          <span>
            {isDone
              ? '모든 단계 완료'
              : isFailed
                ? `실패 단계: ${currentStage ? STAGE_LABELS[currentStage] : '시작 전'}`
                : inProgressStage
                  ? `현재: ${STAGE_LABELS[inProgressStage]}${subProgressLabel ? ` (${subProgressLabel})` : ''}`
                  : status === 'queued'
                    ? '대기 중'
                    : '시작 전'}
          </span>
        </span>
        <span className="flex items-center gap-2">
          {remainingLabel && (
            <span className="text-muted-foreground/70">{remainingLabel}</span>
          )}
          {showFirstIngestNotice && (
            <span
              className="text-muted-foreground/70"
              title="ingest_logs sample 이 충분히 쌓이면 정확도가 올라갑니다"
            >
              처음에는 시간 추정이 부정확합니다
            </span>
          )}
          <span>
            {isDone
              ? `${STAGE_ORDER.length}/${STAGE_ORDER.length}`
              : isFailed
                ? '실패'
                : subProgressLabel
                  ? `${Math.max(0, currentIdx + 1)}/${STAGE_ORDER.length} · ${subProgressLabel}`
                  : `${Math.max(0, currentIdx + 1)}/${STAGE_ORDER.length}`}
          </span>
        </span>
      </div>
    </div>
  );
}
