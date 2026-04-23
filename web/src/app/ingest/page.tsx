'use client';

import { useState } from 'react';
import { ApiError, uploadDocument } from '@/lib/api';
import { DropZone } from '@/components/jet-rag/drop-zone';
import { UploadList } from '@/components/jet-rag/upload-list';
import type { UploadItemData } from '@/components/jet-rag/upload-item';

export default function IngestPage() {
  const [items, setItems] = useState<UploadItemData[]>([]);

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

  // 재시도 성공 시 같은 doc_id 의 새 job 으로 갱신 + retryNonce 증가로
  // useJobStatusPolling 의 effect 를 강제 재실행시킨다.
  const handleReingest = (localId: string, jobId: string) => {
    setItems((prev) =>
      prev.map((it) =>
        it.localId === localId
          ? { ...it, jobId, retryNonce: it.retryNonce + 1 }
          : it,
      ),
    );
  };

  return (
    <main className="container mx-auto flex-1 px-4 py-8 md:px-6 md:py-12">
      <div className="mx-auto max-w-3xl space-y-6">
        <header className="space-y-2">
          <h1 className="text-2xl font-bold tracking-tight text-foreground md:text-3xl">
            문서 업로드
          </h1>
          <p className="text-sm text-muted-foreground">
            한국어 PDF, HWP, DOCX, 이미지 등을 올리면 자동으로 청킹·태그·요약·임베딩까지 처리됩니다.
          </p>
        </header>

        <DropZone onFiles={handleFiles} />

        <section className="space-y-3">
          <h2 className="text-sm font-semibold text-foreground">처리 현황</h2>
          <UploadList items={items} onReingest={handleReingest} />
        </section>
      </div>
    </main>
  );
}
