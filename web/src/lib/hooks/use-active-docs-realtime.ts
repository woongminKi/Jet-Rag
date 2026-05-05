'use client';

import { useEffect, useRef, useState } from 'react';
import { getActiveDocs, type ActiveDocItem } from '@/lib/api';
import { getBrowserSupabase } from '@/lib/supabase/client';

/** W25 D14 Phase 1 — 글로벌 active docs 상태 (Supabase Realtime 기반).
 *
 *  동작:
 *   1) 마운트 시 1회 GET /documents/active fetch (initial state)
 *   2) Supabase Realtime 으로 ingest_jobs INSERT/UPDATE 구독 — 변경 즉시 부분 갱신
 *   3) status 가 terminal (completed/failed/cancelled) 로 전이된 doc 은 onTerminal 콜백
 *      호출 후 active 리스트에서 제거 (헤더 indicator 카운트 감소)
 *
 *  graceful: Realtime 미설정 시 polling (15s) fallback — UX 약간 늦어지지만 동작.
 */

const FALLBACK_POLL_MS = 15000;
const SAFETY_RESYNC_MS = 60000; // Realtime 가 missing event 등 발생 시 ground truth 보정
const TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled']);

export interface ActiveDocsState {
  items: ActiveDocItem[];
  loading: boolean;
}

export function useActiveDocsRealtime(
  onTerminal?: (item: ActiveDocItem, terminalStatus: string) => void,
): ActiveDocsState {
  const [items, setItems] = useState<ActiveDocItem[]>([]);
  const [loading, setLoading] = useState(true);
  const onTerminalRef = useRef(onTerminal);
  // React 19 — ref update 는 effect 에서만 (lint react-hooks/refs)
  useEffect(() => {
    onTerminalRef.current = onTerminal;
  }, [onTerminal]);

  useEffect(() => {
    let cancelled = false;
    const itemsByJobId = new Map<string, ActiveDocItem>();
    const itemsByDocId = new Map<string, ActiveDocItem>();

    const upsertItem = (item: ActiveDocItem) => {
      itemsByJobId.set(item.job.job_id, item);
      itemsByDocId.set(item.doc_id, item);
    };
    const removeItem = (docId: string, jobId: string) => {
      itemsByJobId.delete(jobId);
      itemsByDocId.delete(docId);
    };
    const flush = () => {
      if (cancelled) return;
      setItems(Array.from(itemsByDocId.values()));
    };
    /** ground truth 동기화 — getActiveDocs 응답 기반 전체 교체.
     *  응답에 없는 doc (이미 completed) 제거 + 신규/갱신 upsert. Realtime UPDATE
     *  missing 이나 RLS 차단 등으로 terminal event 누락 시에도 자동 정정. */
    const resyncFromBackend = async () => {
      try {
        const res = await getActiveDocs(24);
        if (cancelled) return;
        const responseDocIds = new Set(res.items.map((i) => i.doc_id));
        for (const docId of Array.from(itemsByDocId.keys())) {
          if (!responseDocIds.has(docId)) {
            const stale = itemsByDocId.get(docId);
            if (stale) removeItem(docId, stale.job.job_id);
          }
        }
        for (const it of res.items) upsertItem(it);
        flush();
      } catch {
        /* graceful */
      }
    };

    const initial = async () => {
      await resyncFromBackend();
      if (!cancelled) setLoading(false);
    };

    initial();

    const sb = getBrowserSupabase();
    if (!sb) {
      // fallback polling (Realtime 미설정 환경)
      const tick = setInterval(() => {
        if (cancelled) return;
        // 전체 교체 (단순 fallback) — resyncFromBackend 의 incremental 보다 강한 의미
        itemsByJobId.clear();
        itemsByDocId.clear();
        resyncFromBackend();
      }, FALLBACK_POLL_MS);
      return () => {
        cancelled = true;
        clearInterval(tick);
      };
    }

    const channel = sb
      .channel('jet-rag:ingest_jobs')
      .on(
        'postgres_changes',
        { event: '*', schema: 'public', table: 'ingest_jobs' },
        (payload) => {
          if (cancelled) return;
          // payload.new 는 row snapshot. queued_at < 24h 필터는 frontend 에서 단순화.
          const next = payload.new as
            | {
                id: string;
                doc_id: string;
                status: string;
                current_stage: string | null;
                attempts: number;
                error_msg: string | null;
                queued_at: string;
                started_at: string | null;
                finished_at: string | null;
              }
            | undefined;

          if (!next || !next.id || !next.doc_id) return;

          const existing = itemsByJobId.get(next.id) ?? itemsByDocId.get(next.doc_id);

          if (TERMINAL_STATUSES.has(next.status)) {
            // 완료·실패·취소 → 제거 + onTerminal 콜백
            if (existing) {
              const finalItem: ActiveDocItem = {
                ...existing,
                job: {
                  ...existing.job,
                  status: next.status as ActiveDocItem['job']['status'],
                  current_stage: next.current_stage as ActiveDocItem['job']['current_stage'],
                  attempts: next.attempts,
                  error_msg: next.error_msg,
                  finished_at: next.finished_at,
                },
              };
              removeItem(next.doc_id, next.id);
              flush();
              onTerminalRef.current?.(finalItem, next.status);
            }
            return;
          }

          // queued/running — upsert 후 flush. file_name/size 는 active fetch 결과 보존,
          // 신규 row 면 백엔드 fetch 1회 보강 (heavy 없음, 단건 GET).
          if (existing) {
            upsertItem({
              ...existing,
              job: {
                ...existing.job,
                status: next.status as ActiveDocItem['job']['status'],
                current_stage: next.current_stage as ActiveDocItem['job']['current_stage'],
                attempts: next.attempts,
                error_msg: next.error_msg,
                queued_at: next.queued_at,
                started_at: next.started_at,
                finished_at: next.finished_at,
              },
            });
            flush();
          } else {
            // 신규 doc — 메타 보강 + ground truth 동기화 (전체 교체로 stale 제거)
            resyncFromBackend();
          }
        },
      )
      .subscribe();

    // safety net — Realtime 가 RLS 차단·missing event 등으로 terminal 누락해도
    // 60s 마다 ground truth 정정. polling 1.5s/5s 보다 훨씬 가벼움.
    const safetyTick = setInterval(() => {
      if (cancelled) return;
      resyncFromBackend();
    }, SAFETY_RESYNC_MS);

    return () => {
      cancelled = true;
      sb.removeChannel(channel);
      clearInterval(safetyTick);
    };
  }, []);

  return { items, loading };
}
