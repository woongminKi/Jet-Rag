import { cn } from '@/lib/utils';
import type { JobStatusValue, StageValue } from '@/lib/api';
import { STAGE_LABELS, STAGE_ORDER } from '@/lib/stages';

interface StageProgressProps {
  currentStage: StageValue | null;
  status: JobStatusValue;
}

export function StageProgress({ currentStage, status }: StageProgressProps) {
  const currentIdx = currentStage ? STAGE_ORDER.indexOf(currentStage) : -1;
  const isFailed = status === 'failed';
  const isDone = status === 'succeeded';

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-1.5">
        {STAGE_ORDER.map((stage, idx) => {
          const reached = isDone || idx <= currentIdx;
          const failedHere = isFailed && idx === currentIdx;
          return (
            <div
              key={stage}
              className={cn(
                'h-1.5 flex-1 rounded-full transition-colors',
                failedHere
                  ? 'bg-destructive'
                  : reached
                    ? isDone
                      ? 'bg-success'
                      : 'bg-primary'
                    : 'bg-muted',
              )}
              aria-label={STAGE_LABELS[stage]}
              title={STAGE_LABELS[stage]}
            />
          );
        })}
      </div>
      <div className="flex items-center justify-between text-[11px] text-muted-foreground">
        <span>
          {currentStage
            ? `현재: ${STAGE_LABELS[currentStage]}`
            : status === 'queued'
              ? '대기 중'
              : '시작 전'}
        </span>
        <span>
          {isDone ? '완료' : isFailed ? '실패' : `${Math.max(0, currentIdx + 1)}/${STAGE_ORDER.length}`}
        </span>
      </div>
    </div>
  );
}
