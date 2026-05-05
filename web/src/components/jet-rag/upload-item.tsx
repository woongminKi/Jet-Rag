'use client';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { CheckCircle2, FileIcon, Loader2, RefreshCw, XCircle } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { formatBytes } from '@/lib/format';
import { inferDocType } from '@/lib/stages';
import { ApiError, reingestDocument, type JobStatus } from '@/lib/api';
import { DEFAULT_INGEST_HINTS, RotatingHint } from './rotating-hint';
import { StageProgress } from './stage-progress';

export interface UploadItemData {
  localId: string;
  fileName: string;
  sizeBytes: number;
  docId: string | null;
  jobId: string | null;
  duplicated: boolean;
  uploadError?: string | null;
  retryNonce: number;
}

interface UploadItemProps {
  data: UploadItemData;
  /** 부모(`UploadList`)의 batch 폴링 결과 — 해당 doc_id 의 latest job. */
  job: JobStatus | null;
  /** 부모 폴러가 5분 / 연속 5회 에러로 timed out 상태 진입했는지. */
  timedOut: boolean;
  /** 부모 폴러의 마지막 호출 에러 메시지 (있을 때만 노출). */
  pollingError?: string | null;
  onReingest?: (localId: string, jobId: string) => void;
  /** 인제스트가 completed 로 전이된 시점 1회 호출 (중복 호출 방지). */
  onCompleted?: (docId: string) => void;
}

export function UploadItem({
  data,
  job,
  timedOut,
  pollingError,
  onReingest,
  onCompleted,
}: UploadItemProps) {
  const status = job?.status ?? (data.duplicated ? 'duplicated' : data.uploadError ? 'error' : 'queued');

  // completed 전이 1회 알림 (자동 이동 트리거)
  const completedFiredRef = useRef(false);
  useEffect(() => {
    if (
      job?.status === 'completed' &&
      !completedFiredRef.current &&
      data.docId &&
      onCompleted
    ) {
      completedFiredRef.current = true;
      onCompleted(data.docId);
    }
  }, [job?.status, data.docId, onCompleted]);

  // retry 후 새 job 진입 시 onCompleted 한 번 더 발동되도록 ref 리셋
  useEffect(() => {
    completedFiredRef.current = false;
  }, [data.retryNonce]);

  const [retryLoading, setRetryLoading] = useState(false);
  const [retryError, setRetryError] = useState<string | null>(null);

  const canRetry =
    !!data.docId && job?.status === 'failed' && !!onReingest && !retryLoading;

  const handleRetry = async () => {
    if (!data.docId || !onReingest) return;
    setRetryLoading(true);
    setRetryError(null);
    try {
      const res = await reingestDocument(data.docId);
      onReingest(data.localId, res.job_id);
    } catch (err) {
      const message =
        err instanceof ApiError ? err.detail : '재시도 요청에 실패했습니다.';
      setRetryError(message);
    } finally {
      setRetryLoading(false);
    }
  };

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
            <div className="flex items-center gap-2">
              {canRetry && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-7 gap-1 px-2 text-xs"
                  onClick={handleRetry}
                  disabled={retryLoading}
                >
                  <RefreshCw
                    className={`h-3 w-3 ${retryLoading ? 'animate-spin' : ''}`}
                  />
                  재시도
                </Button>
              )}
              {(job?.status === 'completed' || data.duplicated) && data.docId && (
                <Button
                  asChild
                  variant="outline"
                  size="sm"
                  className="h-7 px-2 text-xs"
                >
                  <Link href={`/doc/${data.docId}`}>상세 보기</Link>
                </Button>
              )}
              <StatusBadge status={status} timedOut={timedOut} />
            </div>
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
                estimatedRemainingMs={job?.estimated_remaining_ms ?? null}
              />
              {/* W25 D14 Sprint A — timedOut 시 fixed 메시지, 그 외 진행 중에는 90초마다 회전 안내문. */}
              {timedOut ? (
                <p className="text-xs text-muted-foreground">
                  처리가 오래 걸리고 있어요. 잠시 후 새로고침해 보세요.
                </p>
              ) : (
                (job?.status === 'running' || job?.status === 'queued' || !job) && (
                  <RotatingHint messages={DEFAULT_INGEST_HINTS} />
                )
              )}
              {job?.status === 'failed' && job.error_msg && (
                <p className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
                  {job.error_msg}
                </p>
              )}
              {retryError && (
                <p className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
                  {retryError}
                </p>
              )}
              {pollingError && !job && (
                <p className="text-xs text-muted-foreground">
                  상태를 가져오지 못했습니다: {pollingError}
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
  if (status === 'completed')
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
