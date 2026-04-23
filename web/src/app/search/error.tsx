'use client';

import { useEffect } from 'react';
import { AlertTriangle, RotateCw } from 'lucide-react';
import { Button } from '@/components/ui/button';

export default function SearchError({
  error,
  unstable_retry,
}: {
  error: Error & { digest?: string };
  unstable_retry: () => void;
}) {
  useEffect(() => {
    console.error('[SearchError]', error);
  }, [error]);

  return (
    <main className="flex flex-1 items-center justify-center px-4 py-24">
      <div className="flex max-w-md flex-col items-center gap-4 text-center">
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-destructive/10 text-destructive">
          <AlertTriangle className="h-6 w-6" />
        </div>
        <h2 className="text-xl font-semibold text-foreground">검색 결과를 불러오지 못했습니다</h2>
        <p className="text-sm text-muted-foreground">
          백엔드 API 연결에 일시적인 문제가 발생했어요. 잠시 후 다시 시도해 주세요.
        </p>
        <Button onClick={() => unstable_retry()} className="gap-2">
          <RotateCw className="h-4 w-4" />
          다시 시도
        </Button>
      </div>
    </main>
  );
}
