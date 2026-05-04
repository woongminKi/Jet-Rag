'use client';

import { Info } from 'lucide-react';
import { Progress } from '@/components/ui/progress';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';

/**
 * W25 D3 Phase 1 — hydration mismatch fix.
 *
 * Radix Tooltip 은 SSR 시 자식 트리와 client hydration 결과가 어긋남
 * (server: 단순 <span>, client: <button data-state="closed">).
 * result-card.tsx 를 RSC 로 보존하기 위해 Tooltip 영역만 client island 분리.
 *
 * 영역: "매칭 강도" 라벨 + ⓘ 아이콘 + 100% 약화 표시 + Progress 막대 통째로.
 * Progress 만 RSC 에 남기면 layout (`flex justify-between`) 분할이 어색하여
 * `<div className="w-32 shrink-0 space-y-1">` 컨테이너 전체를 옮김.
 *
 * 키보드 접근성 (D5 후속 큐) — TooltipTrigger 의 기본 자식이 button 이 되도록
 * 명시적 `<button type="button">` 사용 (focus / Enter / Esc 동작 보장).
 */
interface RelevanceLabelProps {
  relevancePct: number;
}

export function RelevanceLabel({ relevancePct }: RelevanceLabelProps) {
  return (
    <div className="w-32 shrink-0 space-y-1">
      <div className="flex items-center justify-between text-[10px] text-muted-foreground">
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              className="inline-flex cursor-help items-center gap-0.5 rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              매칭 강도
              <Info className="h-2.5 w-2.5" aria-hidden />
              <span className="sr-only">매칭 강도 안내</span>
            </button>
          </TooltipTrigger>
          <TooltipContent side="bottom">
            이 결과 집합 내에서의 상대적 매칭 강도예요. 정답 신뢰도와는 다릅니다.
          </TooltipContent>
        </Tooltip>
        <span className="font-normal text-muted-foreground/70">
          {relevancePct}%
        </span>
      </div>
      <Progress value={relevancePct} className="h-1.5" />
    </div>
  );
}
