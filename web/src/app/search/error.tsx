'use client';

import { useEffect } from 'react';
import { AlertTriangle, Clock, RotateCw } from 'lucide-react';
import { Button } from '@/components/ui/button';

/**
 * /search 세그먼트 error boundary (Next.js 16, v16.2.0+ unstable_retry).
 *
 * Server Component 인 page.tsx 에서 throw 된 에러는 직렬화되어 client 로 전달된다.
 * Next.js 16 docs (`app/api-reference/file-conventions/error.md` §error.message) 에 따르면:
 *   - dev: 원본 `error.message` 보존 → ApiError 의 "[503] 검색 일시 오류..." 그대로 전달
 *   - prod: generic message + `error.digest` 로 sanitize → `error.message` 매칭 실패 가능
 *
 * 따라서 503 분기는 best-effort: message 에 status 코드가 보이면 503 톤,
 * 보이지 않으면 generic 톤 (prod fallback). 두 케이스 모두 한국어 톤 일관 + reset 버튼.
 *
 * 참고 docs:
 *   - node_modules/next/dist/docs/01-app/03-api-reference/03-file-conventions/error.md
 *     §"Reference > Props > error.message" — Server Component error sanitization 규칙
 *   - 같은 파일 §"Version History" — v16.2.0 unstable_retry 추가 (기존 reset 권장 대체)
 *   - node_modules/next/dist/docs/01-app/01-getting-started/10-error-handling.md
 *     §"Nested error boundaries" — 'use client' 필수, error.tsx 가 segment 단위 fallback
 */
export default function SearchError({
  error,
  unstable_retry,
}: {
  error: Error & { digest?: string };
  unstable_retry: () => void;
}) {
  useEffect(() => {
    // dev 에선 원본 message, prod 에선 sanitized + digest. 운영 진단은 digest 로 서버 로그 매칭.
    console.error('[SearchError]', error);
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

/**
 * `error.message` 패턴 기반 분류 — dev 환경에서만 정확 동작.
 *
 * ApiError(client.ts) 가 throw 시 message 는 `"[<status>] <detail>"` 포맷.
 * 503 매칭은 다음 두 패턴 중 하나라도 만족하면 service-unavailable:
 *   1) "[503]" 접두 — 가장 일반적 (dev)
 *   2) detail 의 "검색 일시 오류" 한국어 — 백엔드 메시지가 보존된 dev 케이스
 *
 * prod 에선 둘 다 매칭 실패 → generic. 사용자에겐 같은 reset 흐름이라 UX 손실 0,
 * 운영자만 server log + digest 로 503 빈도 추적.
 */
function classifyError(error: Error): ErrorVariant {
  const message = error.message ?? '';
  const isServiceUnavailable =
    message.includes('[503]') || message.includes('검색 일시 오류');

  if (isServiceUnavailable) {
    return {
      kind: 'service-unavailable',
      title: '검색 일시 오류',
      description: '잠시 후 다시 시도해주세요.',
    };
  }

  return {
    kind: 'generic',
    title: '검색 중 오류가 발생했습니다',
    description:
      '백엔드 API 연결에 일시적인 문제가 발생했어요. 잠시 후 다시 시도해 주세요.',
  };
}
