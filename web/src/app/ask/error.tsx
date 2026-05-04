'use client';

import { useEffect } from 'react';
import { AlertTriangle, Clock, RotateCw } from 'lucide-react';
import { Button } from '@/components/ui/button';

/**
 * /ask 세그먼트 error boundary — `/search` error.tsx 패턴 동일.
 *
 * 백엔드 `/answer` 503 응답 (Gemini quota / HF transient / LLM 호출 실패) 분류.
 * dev 에선 ApiError message 보존, prod 에선 sanitized + digest.
 */
export default function AskError({
  error,
  unstable_retry,
}: {
  error: Error & { digest?: string };
  unstable_retry: () => void;
}) {
  useEffect(() => {
    console.error('[AskError]', error);
  }, [error]);

  const variant = classifyError(error);

  return (
    <main className="flex flex-1 items-center justify-center px-4 py-24">
      <div className="flex max-w-md flex-col items-center gap-4 text-center">
        <div
          className={`flex h-12 w-12 items-center justify-center rounded-full ${
            variant.kind === 'service-unavailable'
              ? 'bg-warning/10 text-warning'
              : 'bg-destructive/10 text-destructive'
          }`}
        >
          {variant.kind === 'service-unavailable' ? (
            <Clock className="h-6 w-6" />
          ) : (
            <AlertTriangle className="h-6 w-6" />
          )}
        </div>
        <h2 className="text-xl font-semibold text-foreground">{variant.title}</h2>
        <p className="text-sm text-muted-foreground">{variant.description}</p>
        {error.digest && (
          <p className="text-[11px] font-mono text-muted-foreground/70">
            error_id: {error.digest}
          </p>
        )}
        <Button onClick={() => unstable_retry()} className="gap-2">
          <RotateCw className="h-4 w-4" />
          다시 시도
        </Button>
      </div>
    </main>
  );
}

type ErrorVariant = {
  kind: 'service-unavailable' | 'generic';
  title: string;
  description: string;
};

function classifyError(error: Error): ErrorVariant {
  const message = error.message ?? '';
  const isServiceUnavailable =
    message.includes('[503]') ||
    message.includes('답변 생성 일시 오류') ||
    message.includes('quota');

  if (isServiceUnavailable) {
    return {
      kind: 'service-unavailable',
      title: '답변 생성 일시 오류',
      description:
        '잠시 후 다시 시도해주세요. 일일 quota 가 소진되었을 수 있습니다.',
    };
  }

  return {
    kind: 'generic',
    title: '답변 생성 중 오류가 발생했습니다',
    description:
      '백엔드 API 연결에 일시적인 문제가 발생했어요. 잠시 후 다시 시도해 주세요.',
  };
}
