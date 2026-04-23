'use client';

import { useEffect, useState } from 'react';
import { ApiError, getDocumentStatus, type JobStatus } from '@/lib/api';

const POLL_INTERVAL_MS = 1500;
const MAX_POLL_DURATION_MS = 5 * 60 * 1000; // 5분

export interface PollingState {
  job: JobStatus | null;
  loading: boolean;
  error: string | null;
  timedOut: boolean;
}

export function useJobStatusPolling(
  docId: string | null,
  enabled: boolean,
): PollingState {
  const [state, setState] = useState<PollingState>(() => ({
    job: null,
    loading: enabled && !!docId,
    error: null,
    timedOut: false,
  }));

  useEffect(() => {
    if (!enabled || !docId) return;

    let cancelled = false;
    const start = Date.now();

    const tick = async () => {
      try {
        const res = await getDocumentStatus(docId);
        if (cancelled) return;
        setState((prev) => ({
          ...prev,
          job: res.job,
          loading: false,
          error: null,
        }));
        const status = res.job?.status;
        if (status === 'succeeded' || status === 'failed') return;
        if (Date.now() - start > MAX_POLL_DURATION_MS) {
          setState((prev) => ({ ...prev, timedOut: true }));
          return;
        }
        setTimeout(tick, POLL_INTERVAL_MS);
      } catch (err) {
        if (cancelled) return;
        const message = err instanceof ApiError ? err.detail : '상태 조회 실패';
        setState((prev) => ({ ...prev, loading: false, error: message }));
        setTimeout(tick, POLL_INTERVAL_MS);
      }
    };

    tick();

    return () => {
      cancelled = true;
    };
  }, [docId, enabled]);

  return state;
}
