'use client';

import { useEffect, useState } from 'react';
import { apiGet, apiPost } from '@/lib/api/client';

interface Subscription {
  plan_code: string;
  status: string; // active | past_due | canceled | none
  current_period_end: string | null;
}

function statusLabel(status: string): string {
  switch (status) {
    case 'active':
      return '구독 중';
    case 'past_due':
      return '결제 실패 (유예 기간)';
    case 'canceled':
      return '해지됨';
    default:
      return '미구독';
  }
}

function formatDate(iso: string | null): string {
  if (!iso) return '-';
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? '-'
    : d.toLocaleDateString('ko-KR', { year: 'numeric', month: 'long', day: 'numeric' });
}

export function SubscriptionSection() {
  const [sub, setSub] = useState<Subscription | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    apiGet<Subscription>('/me/subscription')
      .then((s) => {
        if (cancelled) return;
        setSub(s);
        setError(null);
      })
      .catch(() => {
        if (cancelled) return;
        setError('구독 정보를 불러오지 못했습니다.');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const subscribe = async () => {
    setBusy(true);
    setError(null);
    try {
      const { redirect_url } = await apiPost<{ redirect_url: string }>(
        '/payments/subscribe/ready',
      );
      window.location.href = redirect_url; // KakaoPay 결제창으로 이동
    } catch {
      setError('결제창을 여는 데 실패했습니다. 잠시 후 다시 시도해 주세요.');
      setBusy(false);
    }
  };

  const cancel = async () => {
    if (!window.confirm('구독을 해지하면 다음 결제일부터 Free 로 전환됩니다. 계속할까요?')) return;
    setBusy(true);
    setError(null);
    try {
      await apiPost('/payments/subscribe/cancel');
      setSub((prev) => (prev ? { ...prev, status: 'canceled' } : prev));
    } catch {
      setError('구독 해지에 실패했습니다. 잠시 후 다시 시도해 주세요.');
    } finally {
      setBusy(false);
    }
  };

  const isActive = sub?.status === 'active' || sub?.status === 'past_due';

  return (
    <section className="mt-6 rounded-lg border p-4">
      <h2 className="font-semibold">구독</h2>
      {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
      {sub ? (
        <>
          <ul className="mt-2 space-y-1 text-sm">
            <li>
              상태: <strong>{statusLabel(sub.status)}</strong>
            </li>
            {isActive && <li>다음 결제일: {formatDate(sub.current_period_end)}</li>}
            <li>Pro 요금: 월 6,900원 (문서 200개 · 답변 일 50회 · 이메일 인제스트)</li>
          </ul>
          {sub.status === 'past_due' && (
            <p className="mt-2 text-sm text-amber-600">
              결제에 실패했습니다. 7일 내 결제되지 않으면 자동 해지됩니다.
            </p>
          )}
          {isActive ? (
            <button
              type="button"
              onClick={() => void cancel()}
              disabled={busy}
              className="mt-3 rounded border px-3 py-1 text-sm disabled:opacity-50"
            >
              {busy ? '처리 중…' : '구독 해지'}
            </button>
          ) : (
            <button
              type="button"
              onClick={() => void subscribe()}
              disabled={busy}
              className="mt-3 rounded bg-primary px-3 py-1 text-sm text-primary-foreground disabled:opacity-50"
            >
              {busy ? '이동 중…' : 'Pro 구독하기 (월 6,900원)'}
            </button>
          )}
        </>
      ) : (
        <p className="mt-2 text-sm text-gray-500">불러오는 중…</p>
      )}
    </section>
  );
}
