'use client';

/**
 * S2 D3 (2026-05-09) — vision page cap 도달 안내 + 재처리 카드.
 *
 * master plan §6 S2 D3. 사용자 결정 Q-S2-1d (precise 재처리 CTA 포함).
 *
 * 노출 조건 (부모 책임):
 *   doc.flags.vision_page_cap_exceeded === true && doc.doc_type === 'pdf'
 *
 * cost cap 카드 (`VisionBudgetExceededCard`) 와 직교 — 같은 doc 안 둘 다 도달 가능.
 * 부모가 cost cap 카드를 위에 두는 순서 책임. 본 카드는 PageCap 만 담당.
 *
 * 비용 안내는 inline only (Q-S2-1g — confirm dialog 금지).
 */

import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { AlertCircle, Loader2, Sparkles } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import {
  ApiError,
  reingestMissingVision,
  type DocumentDetailResponse,
  type IngestMode,
} from '@/lib/api';
import {
  IngestModeSelect,
  loadLastIngestMode,
} from '@/components/jet-rag/ingest-mode-select';

interface VisionPageCapExceededCardProps {
  doc: DocumentDetailResponse;
}

const MODE_LABEL: Record<IngestMode, string> = {
  fast: '빠른',
  default: '기본',
  precise: '정밀',
};

export function VisionPageCapExceededCard({ doc }: VisionPageCapExceededCardProps) {
  const router = useRouter();

  const f = (doc.flags || {}) as Record<string, unknown>;
  const cap = (f.vision_page_cap && typeof f.vision_page_cap === 'object'
    ? (f.vision_page_cap as Record<string, unknown>)
    : {}) as { called_pages?: number; page_cap?: number; reason?: string };
  const calledPages =
    typeof cap.called_pages === 'number' ? cap.called_pages : null;
  const pageCap = typeof cap.page_cap === 'number' ? cap.page_cap : null;

  // 현재 doc 의 mode (백엔드가 doc-level 보존). 미지정 시 'default' fallback.
  const currentMode: IngestMode =
    (f.ingest_mode as IngestMode | undefined) ?? 'default';
  const currentModeLabel = MODE_LABEL[currentMode];

  // 재처리 mode 선택 — initial 'precise' (본 카드의 일반적인 다음 단계),
  // 단 사용자가 마지막에 다른 모드를 골랐다면 그쪽 우선. mount 후 동기.
  const [selectedMode, setSelectedMode] = useState<IngestMode>(() => {
    if (typeof window === 'undefined') return 'precise';
    const last = loadLastIngestMode();
    // 본 카드의 핵심 액션은 'precise 로 더 깊게' 라 default 는 항상 precise 로 prefill —
    // 사용자가 명시적으로 다른 mode 를 last 로 골랐을 때만 그것을 따름.
    return last === 'default' ? 'precise' : last;
  });

  const [retrying, setRetrying] = useState(false);
  const [retryError, setRetryError] = useState<string | null>(null);
  const [retryQueued, setRetryQueued] = useState(false);

  const handleRetry = async () => {
    setRetrying(true);
    setRetryError(null);
    try {
      await reingestMissingVision(doc.id, selectedMode);
      setRetryQueued(true);
      router.refresh();
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.detail : '재처리 요청에 실패했습니다.';
      setRetryError(msg);
    } finally {
      setRetrying(false);
    }
  };

  const pageCapDisplay = pageCap !== null ? `${pageCap}페이지` : '제한';
  const calledPagesDisplay =
    calledPages !== null ? `${calledPages}페이지 처리됨` : '일부 페이지 처리됨';

  return (
    <Card className="space-y-3 border-warning/40 bg-warning/5 p-5">
      <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
        <AlertCircle className="h-4 w-4 text-warning" />
        페이지 한도 도달
      </div>
      <p className="text-xs leading-relaxed text-muted-foreground">
        이 문서는 {pageCapDisplay}까지만 분석됐어요. ({calledPagesDisplay}, 모드:{' '}
        {currentModeLabel})
      </p>
      <p className="text-xs leading-relaxed text-muted-foreground">
        큰 PDF 의 후반부 내용을 검색하려면 정밀 모드로 다시 처리할 수 있어요.
      </p>
      <p className="text-[11px] leading-relaxed text-muted-foreground/80">
        정밀 모드는 일일 비용 한도까지 vision API 를 호출합니다.
      </p>

      <div className="space-y-2 rounded-md border border-warning/30 bg-card p-3">
        <IngestModeSelect
          id={`page-cap-mode-${doc.id}`}
          value={selectedMode}
          onChange={setSelectedMode}
          disabled={retrying || retryQueued}
          showHint
        />
      </div>

      {retryError && (
        <p className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
          {retryError}
        </p>
      )}
      {retryQueued && !retryError && (
        <p className="rounded-md border border-primary/30 bg-primary/5 px-3 py-2 text-xs text-primary">
          {selectedMode === 'precise' ? '정밀 모드 ' : ''}재처리 큐잉됨 — 진행 중
        </p>
      )}
      <div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={handleRetry}
          disabled={retrying || retryQueued}
          className="gap-1"
        >
          {retrying ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <Sparkles className="h-3 w-3" />
          )}
          {retrying
            ? '재처리 요청 중...'
            : selectedMode === 'precise'
              ? '정밀 모드로 재처리'
              : `${MODE_LABEL[selectedMode]} 모드로 재처리`}
        </Button>
      </div>
    </Card>
  );
}
