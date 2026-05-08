'use client';

import { useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  ApiError,
  uploadDocument,
  type ActiveDocItem,
  type IngestMode,
} from '@/lib/api';
import { useActiveDocs } from '@/lib/contexts/active-docs-context';
import { emitDocUploaded } from '@/lib/notifications/upload-event';
import { DropZone } from '@/components/jet-rag/drop-zone';
import {
  IngestModeSelect,
  useLastIngestMode,
} from '@/components/jet-rag/ingest-mode-select';
import { UploadList } from '@/components/jet-rag/upload-list';
import type { UploadItemData } from '@/components/jet-rag/upload-item';

function activeDocToUploadItem(active: ActiveDocItem): UploadItemData {
  return {
    localId: `active-${active.doc_id}`,
    fileName: active.file_name,
    sizeBytes: active.size_bytes,
    docId: active.doc_id,
    jobId: active.job.job_id,
    duplicated: false,
    retryNonce: 0,
  };
}

export function IngestUI() {
  const router = useRouter();

  // 진행 중 업로드 placeholder (docId 없는 동안 또는 uploadError/duplicated 표시 용)
  const [transientItems, setTransientItems] = useState<UploadItemData[]>([]);

  // S2 D3 — 운영 모드 (fast/default/precise). localStorage prefill (Q-S2-1f).
  //
  // 두 source 결합:
  //   - useLastIngestMode (useSyncExternalStore) — SSR 'default', CSR localStorage 값
  //   - userOverride — 사용자가 select 변경 시 즉시 반영 (handler 가 localStorage 도 동기)
  //
  // effective mode = userOverride ?? lastFromStore. handler 호출 시 setUserOverride 만
  // 변경하고, IngestModeSelect 의 onChange 가 localStorage 도 persist (다음 mount 시
  // store 값이 일치하므로 결국 userOverride 와 lastFromStore 가 같은 값에 수렴).
  //
  // React 19 `react-hooks/set-state-in-effect` 회피 — useEffect + setState 패턴 0.
  const lastFromStore = useLastIngestMode();
  const [userOverride, setUserOverride] = useState<IngestMode | null>(null);
  const mode: IngestMode = userOverride ?? lastFromStore;
  const setMode = (next: IngestMode) => setUserOverride(next);

  // 헤더 indicator 와 동일 source — ActiveDocsProvider 가 singleton 으로 관리.
  // terminal 시 Provider 가 notifyDocTerminal 호출 (한 번만, 중복 토스트 0).
  const { items: activeItems } = useActiveDocs();

  // 자동 이동 정책 — 단일=자동, 다중=첫 완료만 자동 (W2 §3.M / DE-28)
  // active 가 이미 있으면 백그라운드 진행 중이므로 자동 이동 막아 사용자 의도 보존
  const autoRoutedRef = useRef(activeItems.length > 0);

  // active 와 transient 합치기 — transient placeholder 가 active 에 들어왔으면 active 우선
  const mergedItems: UploadItemData[] = useMemo(() => {
    const activeByDocId = new Map<string, ActiveDocItem>();
    for (const a of activeItems) activeByDocId.set(a.doc_id, a);

    // transient 중 active 에 들어온 docId 는 제거 (active 가 fresher data + Realtime 갱신)
    const stillTransient = transientItems.filter((t) => {
      if (t.uploadError) return true; // 업로드 실패 placeholder 는 사용자가 dismiss 까지 유지
      if (t.duplicated) return true; // duplicated 도 표시 유지 (즉시 router.push 직전 1회)
      if (!t.docId) return true; // 아직 POST 응답 못 받음
      return !activeByDocId.has(t.docId); // active 안 들어왔으면 placeholder 유지
    });

    const fromActive = activeItems.map(activeDocToUploadItem);
    return [...stillTransient, ...fromActive];
  }, [activeItems, transientItems]);

  const handleFiles = async (files: File[]) => {
    const placeholders: UploadItemData[] = files.map((file) => ({
      localId: `${file.name}-${file.size}-${Date.now()}-${Math.random()}`,
      fileName: file.name,
      sizeBytes: file.size,
      docId: null,
      jobId: null,
      duplicated: false,
      retryNonce: 0,
    }));
    setTransientItems((prev) => [...placeholders, ...prev]);

    await Promise.all(
      placeholders.map(async (placeholder, idx) => {
        const file = files[idx];
        try {
          const res = await uploadDocument(file, 'drag-drop', mode);
          setTransientItems((prev) =>
            prev.map((it) =>
              it.localId === placeholder.localId
                ? {
                    ...it,
                    docId: res.doc_id,
                    jobId: res.job_id,
                    duplicated: res.duplicated,
                  }
                : it,
            ),
          );
          if (!res.duplicated) {
            emitDocUploaded({ docId: res.doc_id });
          }
          if (res.duplicated && !autoRoutedRef.current) {
            autoRoutedRef.current = true;
            router.push(`/doc/${res.doc_id}?duplicated=1`);
          }
        } catch (err) {
          const message =
            err instanceof ApiError ? err.detail : '알 수 없는 오류가 발생했습니다.';
          setTransientItems((prev) =>
            prev.map((it) =>
              it.localId === placeholder.localId
                ? { ...it, uploadError: message }
                : it,
            ),
          );
        }
      }),
    );
  };

  const handleReingest = (localId: string, jobId: string) => {
    setTransientItems((prev) =>
      prev.map((it) =>
        it.localId === localId
          ? { ...it, jobId, retryNonce: it.retryNonce + 1 }
          : it,
      ),
    );
  };

  const handleCompleted = (docId: string) => {
    if (autoRoutedRef.current) return;
    autoRoutedRef.current = true;
    router.push(`/doc/${docId}?uploaded=1`);
  };

  return (
    <>
      <div className="space-y-4">
        <IngestModeSelect value={mode} onChange={setMode} />
        <DropZone onFiles={handleFiles} />
      </div>
      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-foreground">처리 현황</h2>
        <UploadList
          items={mergedItems}
          onReingest={handleReingest}
          onCompleted={handleCompleted}
        />
      </section>
    </>
  );
}
