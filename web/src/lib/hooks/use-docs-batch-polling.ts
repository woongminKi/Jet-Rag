'use client';

import { useEffect, useState } from 'react';
import { ApiError, getBatchStatus, type JobStatus } from '@/lib/api';

const POLL_INTERVAL_MS = 1500;
const MAX_POLL_DURATION_MS = 5 * 60 * 1000; // 5분
const MAX_CONSECUTIVE_ERRORS = 5;

const TERMINAL_STATUSES = new Set<JobStatus['status']>([
  'completed',
  'failed',
  'cancelled',
]);

export interface BatchPollingState {
  jobsByDocId: Record<string, JobStatus | null>;
  loading: boolean;
  error: string | null;
  timedOut: boolean;
}

/**
 * 여러 doc_id 의 인제스트 상태를 `/documents/batch-status` 1회 호출로 폴링.
 * per-doc 폴링 (`useJobStatusPolling`) 의 N→1 호출 최적화 (W2 §3.H, DE-51).
 *
 * 폴링 종료 조건
 * - 모든 doc 이 terminal status (completed/failed/cancelled)
 * - 5분 경과 (timedOut=true)
 * - 연속 5회 에러 (timedOut=true)
 *
 * `wakeUpKey` 가 변하면 effect 가 재실행되어 폴링이 재개된다.
 * (retry 등 외부 트리거로 새 job 이 생긴 경우 호출자가 증가시킴)
 */
export function useDocsBatchPolling(
  docIds: string[],
  enabled: boolean,
  wakeUpKey: number = 0,
): BatchPollingState {
  const [state, setState] = useState<BatchPollingState>(() => ({
    jobsByDocId: {},
    loading: enabled && docIds.length > 0,
    error: null,
    timedOut: false,
  }));

  // 정렬된 join 으로 docIds 내용 변화만 effect 트리거 (참조 변화 무시)
  const idsKey = [...docIds].sort().join(',');

  useEffect(() => {
    if (!enabled || docIds.length === 0) {
      // disabled / 빈 docIds — initial state 는 이미 loading=false 로 출발하지만,
      // props 변경 (예: enabled true→false) 시 loading=true 잔존 가능 → 일회성 sync.
      // cascading render 발생하지만 disabled 시 한 번만 → 성능 영향 0 (W7 Day 2 검토).
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setState((s) => ({ ...s, loading: false }));
      return;
    }

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const start = Date.now();
    let consecutiveErrors = 0;

    const isExpired = () => Date.now() - start > MAX_POLL_DURATION_MS;

    const schedule = () => {
      if (cancelled) return;
      timer = setTimeout(tick, POLL_INTERVAL_MS);
    };

    const tick = async () => {
      try {
        const res = await getBatchStatus(docIds);
        if (cancelled) return;
        consecutiveErrors = 0;
        const map: Record<string, JobStatus | null> = {};
        for (const item of res.items) {
          map[item.doc_id] = item.job;
        }
        setState({
          jobsByDocId: map,
          loading: false,
          error: null,
          timedOut: false,
        });
        const allDone = res.items.every(
          (it) => it.job && TERMINAL_STATUSES.has(it.job.status),
        );
        if (allDone) return;
        if (isExpired()) {
          setState((s) => ({ ...s, timedOut: true }));
          return;
        }
        schedule();
      } catch (err) {
        if (cancelled) return;
        consecutiveErrors += 1;
        const message = err instanceof ApiError ? err.detail : '상태 조회 실패';
        setState((s) => ({ ...s, loading: false, error: message }));
        if (consecutiveErrors >= MAX_CONSECUTIVE_ERRORS || isExpired()) {
          setState((s) => ({ ...s, timedOut: true }));
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
    // docIds 의 내용 변화는 idsKey 로 추적 (참조 변화는 무시)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idsKey, enabled, wakeUpKey]);

  return state;
}
