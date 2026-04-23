'use client';

import { CheckCircle2, FileIcon, Loader2, XCircle } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Card } from '@/components/ui/card';
import { useJobStatusPolling } from '@/lib/hooks/use-job-status-polling';
import { formatBytes } from '@/lib/format';
import { inferDocType } from '@/lib/stages';
import { StageProgress } from './stage-progress';

export interface UploadItemData {
  localId: string;
  fileName: string;
  sizeBytes: number;
  docId: string | null;
  jobId: string | null;
  duplicated: boolean;
  uploadError?: string | null;
}

interface UploadItemProps {
  data: UploadItemData;
}

export function UploadItem({ data }: UploadItemProps) {
  const enabled = !!data.docId && !data.duplicated;
  const polling = useJobStatusPolling(data.docId, enabled);
  const job = polling.job;
  const status = job?.status ?? (data.duplicated ? 'duplicated' : data.uploadError ? 'error' : 'queued');

  return (
    <Card className="p-4">
      <div className="flex items-start gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-muted">
          <FileIcon className="h-5 w-5 text-muted-foreground" />
        </div>
        <div className="min-w-0 flex-1 space-y-3">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-foreground">
                {data.fileName}
              </p>
              <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
                <Badge variant="outline" className="h-5 px-1.5 text-[10px]">
                  {inferDocType(data.fileName)}
                </Badge>
                <span>{formatBytes(data.sizeBytes)}</span>
              </div>
            </div>
            <StatusBadge status={status} timedOut={polling.timedOut} />
          </div>

          {data.uploadError ? (
            <p className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
              업로드 실패: {data.uploadError}
            </p>
          ) : data.duplicated ? (
            <p className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-warning-foreground">
              이미 같은 내용이 등록되어 있어요. 기존 문서와 연결됩니다.
            </p>
          ) : (
            <>
              <StageProgress
                currentStage={job?.current_stage ?? null}
                status={(job?.status ?? 'queued') as never}
              />
              {polling.timedOut && (
                <p className="text-xs text-muted-foreground">
                  처리가 오래 걸리고 있어요. 잠시 후 새로고침해 보세요.
                </p>
              )}
              {job?.status === 'failed' && job.error_msg && (
                <p className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
                  {job.error_msg}
                </p>
              )}
              {polling.error && !job && (
                <p className="text-xs text-muted-foreground">
                  상태를 가져오지 못했습니다: {polling.error}
                </p>
              )}
            </>
          )}
        </div>
      </div>
    </Card>
  );
}

function StatusBadge({ status, timedOut }: { status: string; timedOut: boolean }) {
  if (status === 'duplicated')
    return <Badge variant="outline">중복</Badge>;
  if (status === 'error')
    return (
      <Badge variant="destructive" className="gap-1">
        <XCircle className="h-3 w-3" /> 실패
      </Badge>
    );
  if (status === 'succeeded')
    return (
      <Badge className="gap-1 bg-success text-success-foreground hover:bg-success/90">
        <CheckCircle2 className="h-3 w-3" /> 완료
      </Badge>
    );
  if (status === 'failed')
    return (
      <Badge variant="destructive" className="gap-1">
        <XCircle className="h-3 w-3" /> 실패
      </Badge>
    );
  if (timedOut) return <Badge variant="outline">지연</Badge>;
  return (
    <Badge variant="secondary" className="gap-1">
      <Loader2 className="h-3 w-3 animate-spin" />
      {status === 'queued' ? '대기 중' : '처리 중'}
    </Badge>
  );
}
