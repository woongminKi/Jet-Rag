'use client';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { Loader2 } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { formatRemainingMs, formatStageProgress } from '@/lib/format';
import { STAGE_LABELS } from '@/lib/stages';
import { useActiveDocs } from '@/lib/contexts/active-docs-context';

/** W25 D14 Phase 1 — 글로벌 헤더 indicator + dropdown panel.
 *
 *  - useActiveDocsRealtime: Supabase Realtime 구독, terminal 전이 시 toast 알림
 *  - badge: running/queued 카운트 노출 (0 이면 미노출)
 *  - panel: 클릭 시 진행 중 doc 리스트 (file_name + 현재 stage + ETA + "전체 보기")
 *  - click-outside / ESC 로 닫기 (외부 의존성 0)
 */
export function ActiveDocsIndicator() {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const { items } = useActiveDocs();

  // click-outside / ESC
  useEffect(() => {
    if (!open) return;
    const onPointer = (e: PointerEvent) => {
      if (!containerRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    window.addEventListener('pointerdown', onPointer);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('pointerdown', onPointer);
      window.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const count = items.length;
  if (count === 0) return null;

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((p) => !p)}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={`처리 중 ${count}건`}
        className={cn(
          'flex h-8 items-center gap-1.5 rounded-md border border-border bg-card px-2 text-xs',
          'hover:bg-muted transition-colors',
        )}
      >
        <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" aria-hidden />
        <span className="hidden sm:inline">처리 중</span>
        <Badge variant="secondary" className="h-5 px-1.5 text-[10px]">
          {count}
        </Badge>
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="진행 중 문서"
          className="absolute right-0 top-10 z-50 w-80 max-w-[calc(100vw-2rem)] rounded-lg border border-border bg-popover shadow-lg"
        >
          <div className="border-b border-border px-3 py-2 text-xs font-semibold text-foreground">
            진행 중 문서 ({count})
          </div>
          <ul className="max-h-80 overflow-y-auto py-1">
            {items.map((item) => {
              const stageLabel = item.job.current_stage
                ? (STAGE_LABELS[item.job.current_stage] ?? item.job.current_stage)
                : item.job.status === 'queued'
                  ? '대기 중'
                  : '시작 전';
              const subProgress = formatStageProgress(item.job.stage_progress);
              const fullStageLabel = subProgress
                ? `${stageLabel} (${subProgress})`
                : stageLabel;
              const eta = formatRemainingMs(item.job.estimated_remaining_ms);
              return (
                <li key={item.doc_id}>
                  <Link
                    href={`/doc/${item.doc_id}`}
                    onClick={() => setOpen(false)}
                    className="flex flex-col gap-0.5 px-3 py-2 text-xs hover:bg-muted"
                  >
                    <span className="truncate text-foreground" title={item.file_name}>
                      {item.file_name}
                    </span>
                    <span className="flex items-center justify-between gap-2 text-muted-foreground">
                      <span className="flex items-center gap-1">
                        <Loader2
                          className="h-3 w-3 animate-spin text-primary"
                          aria-hidden
                        />
                        <span>현재: {fullStageLabel}</span>
                      </span>
                      {eta && <span>{eta}</span>}
                    </span>
                  </Link>
                </li>
              );
            })}
          </ul>
          <div className="border-t border-border px-3 py-2 text-right">
            {/* PORTFOLIO MODE C+ — /ingest 비활성. /docs 로 redirect. */}
            <Link
              href="/docs"
              onClick={() => setOpen(false)}
              className="text-xs text-primary hover:underline"
            >
              전체 보기 →
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}
