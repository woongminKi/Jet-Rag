'use client';

import { useEffect, useState } from 'react';
import { ApiError, getDocumentStatus, type JobStatus } from '@/lib/api';

const POLL_INTERVAL_MS = 1500;
const MAX_POLL_DURATION_MS = 5 * 60 * 1000; // 5분
const MAX_CONSECUTIVE_ERRORS = 5;

export interface PollingState {
  job: JobStatus | null;
  loading: boolean;
  error: string | null;
  timedOut: boolean;
}

export function useJobStatusPolling(
  docId: string | null,
  enabled: boolean,
  retryNonce: number = 0,
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
    let timer: ReturnType<typeof setTimeout> | null = null;
    const start = Date.now();
    let consecutiveErrors = 0;
    // 첫 tick 응답이 도착할 때까지는 이전 결과(timedOut/failed) 가 화면에 남는 걸
    // 막기 위해, 첫 응답 시점에 한 번만 timedOut/error 를 명시적으로 reset.
    let firstTick = true;

    const isExpired = () => Date.now() - start > MAX_POLL_DURATION_MS;

    const schedule = () => {
      if (cancelled) return;
      timer = setTimeout(tick, POLL_INTERVAL_MS);
    };

    const tick = async () => {
      try {
        const res = await getDocumentStatus(docId);
        if (cancelled) return;
        consecutiveErrors = 0;
        const wasFirstTick = firstTick;
        firstTick = false;
        setState((prev) => ({
          job: res.job,
          loading: false,
          error: null,
          timedOut: wasFirstTick ? false : prev.timedOut,
        }));
        const status = res.job?.status;
        if (status === 'completed' || status === 'failed' || status === 'cancelled') return;
        if (isExpired()) {
          setState((prev) => ({ ...prev, timedOut: true }));
          return;
        }
        schedule();
      } catch (err) {
        if (cancelled) return;
        consecutiveErrors += 1;
        firstTick = false;
        const message = err instanceof ApiError ? err.detail : '상태 조회 실패';
        setState((prev) => ({ ...prev, loading: false, error: message }));
        if (consecutiveErrors >= MAX_CONSECUTIVE_ERRORS || isExpired()) {
          setState((prev) => ({ ...prev, timedOut: true }));
          return;
        }
        schedule();
      }
    };

    tick();

    return () => {
      cancelled = true;
      if (timer !== null) clearTimeout(timer);
    };
  }, [docId, enabled, retryNonce]);

  return state;
}
