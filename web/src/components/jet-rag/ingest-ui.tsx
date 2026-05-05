'use client';

import { useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { ApiError, uploadDocument } from '@/lib/api';
import { emitDocUploaded } from '@/lib/notifications/upload-event';
import { DropZone } from '@/components/jet-rag/drop-zone';
import { UploadList } from '@/components/jet-rag/upload-list';
import type { UploadItemData } from '@/components/jet-rag/upload-item';

interface IngestUIProps {
  /** W25 D14 Sprint 0 — 새로고침 후에도 진행 중·실패 doc 자동 표시.
   *  page.tsx (RSC) 가 GET /documents/active 결과를 placeholder 형태로 변환해 hydrate. */
  initialItems: UploadItemData[];
}

export function IngestUI({ initialItems }: IngestUIProps) {
  const router = useRouter();
  const [items, setItems] = useState<UploadItemData[]>(initialItems);

  // W2 §3.M / DE-28 — 자동 이동 정책: "단일=자동, 다중=첫 완료만 자동".
  // restored item 이 있으면 이미 background 진행 중이므로 자동 라우팅을 막아 사용자 의도 보존.
  const autoRoutedRef = useRef(initialItems.length > 0);

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
    setItems((prev) => [...placeholders, ...prev]);

    await Promise.all(
      placeholders.map(async (placeholder, idx) => {
        const file = files[idx];
        try {
          const res = await uploadDocument(file, 'drag-drop');
          setItems((prev) =>
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
          // W25 D14 — 헤더 indicator 즉시 갱신 (Realtime INSERT event 도착 대기 없이)
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
          setItems((prev) =>
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
    setItems((prev) =>
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
      <DropZone onFiles={handleFiles} />
      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-foreground">처리 현황</h2>
        <UploadList
          items={items}
          onReingest={handleReingest}
          onCompleted={handleCompleted}
        />
      </section>
    </>
  );
}
