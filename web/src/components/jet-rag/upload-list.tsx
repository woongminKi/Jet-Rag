'use client';

import type { UploadItemData } from './upload-item';
import { UploadItem } from './upload-item';

interface UploadListProps {
  items: UploadItemData[];
  onReingest?: (localId: string, jobId: string) => void;
}

export function UploadList({ items, onReingest }: UploadListProps) {
  if (items.length === 0) {
    return (
      <p className="text-center text-sm text-muted-foreground">
        업로드한 파일이 여기에 표시됩니다.
      </p>
    );
  }
  return (
    <ul className="space-y-3">
      {items.map((item) => (
        <li key={item.localId}>
          <UploadItem data={item} onReingest={onReingest} />
        </li>
      ))}
    </ul>
  );
}
