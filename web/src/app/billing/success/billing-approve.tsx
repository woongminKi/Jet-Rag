'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { apiGet, apiPost } from '@/lib/api/client';

type Phase = 'processing' | 'done' | 'error';

export function BillingApprove({ pgToken }: { pgToken: string | null }) {
  const [phase, setPhase] = useState<Phase>(pgToken ? 'processing' : 'error');

  useEffect(() => {
    if (!pgToken) return;
    let cancelled = false;
    apiPost(`/payments/subscribe/approve?pg_token=${encodeURIComponent(pgToken)}`)
      .then(() => {
        if (!cancelled) setPhase('done');
      })
      .catch(async () => {
        // 새로고침 등으로 approve 가 실패(예: 409 pending 없음)해도 이미 구독됐을 수 있다.
        // /me/subscription 을 재확인해 active/past_due 면 성공으로 처리.
        try {
          const sub = await apiGet<{ status: string }>('/me/subscription');
          if (!cancelled) {
            setPhase(sub.status === 'active' || sub.status === 'past_due' ? 'done' : 'error');
          }
        } catch {
          if (!cancelled) setPhase('error');
        }
      });
    return () => {
      cancelled = true;
    };
  }, [pgToken]);

  return (
    <main className="mx-auto max-w-md px-4 py-16 text-center">
      {phase === 'processing' && (
        <p className="text-sm text-gray-500">결제를 확인하는 중입니다…</p>
      )}
      {phase === 'done' && (
        <>
          <h1 className="text-xl font-bold">구독이 완료되었습니다 🎉</h1>
          <p className="mt-2 text-sm">이제 Pro 기능을 이용할 수 있습니다.</p>
          <Link href="/settings" className="mt-4 inline-block rounded border px-4 py-2 text-sm">
            설정으로 이동
          </Link>
        </>
      )}
      {phase === 'error' && (
        <>
          <h1 className="text-xl font-bold">결제 승인에 실패했습니다</h1>
          <p className="mt-2 text-sm text-gray-500">
            결제가 완료되지 않았습니다. 다시 시도해 주세요.
          </p>
          <Link href="/settings" className="mt-4 inline-block rounded border px-4 py-2 text-sm">
            설정으로 돌아가기
          </Link>
        </>
      )}
    </main>
  );
}
