'use client';

import { useEffect, useRef, useState } from 'react';
import { apiGet, apiPost } from '@/lib/api/client';
import { SubscriptionSection } from '@/components/jet-rag/subscription-section';

interface MePlan {
  plan_code: string;
  max_documents: number;
  answers_per_day: number;
  answers_used_today: number;
  documents_count: number;
}

interface EmailIngest {
  address: string;
  pro: boolean;
  plan_code: string;
}

export default function SettingsPage() {
  const [plan, setPlan] = useState<MePlan | null>(null);
  const [email, setEmail] = useState<EmailIngest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rotating, setRotating] = useState(false);
  const rotatedRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      apiGet<MePlan>('/me/plan'),
      apiGet<EmailIngest>('/me/email-ingest'),
    ])
      .then(([p, e]) => {
        if (cancelled) return;
        setPlan(p);
        // 재발급이 먼저 완료된 경우 늦게 도착한 초기 주소로 덮어쓰지 않음.
        if (!rotatedRef.current) setEmail(e);
        setError(null);
      })
      .catch(() => {
        if (cancelled) return;
        setError('설정을 불러오지 못했습니다. 로그인 상태를 확인해 주세요.');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const rotate = async () => {
    if (!window.confirm('주소를 재발급하면 기존 주소는 즉시 무효화됩니다. 계속할까요?')) return;
    setRotating(true);
    try {
      const e = await apiPost<EmailIngest>('/me/email-ingest/rotate');
      rotatedRef.current = true;
      setEmail(e);
    } catch {
      setError('주소 재발급에 실패했습니다. 잠시 후 다시 시도해 주세요.');
    } finally {
      setRotating(false);
    }
  };

  return (
    <main className="mx-auto max-w-2xl px-4 py-8">
      <h1 className="text-2xl font-bold">설정</h1>

      {error && <p className="mt-4 text-sm text-red-600">{error}</p>}

      <section className="mt-6 rounded-lg border p-4">
        <h2 className="font-semibold">내 플랜</h2>
        {plan ? (
          <ul className="mt-2 space-y-1 text-sm">
            <li>플랜: <strong>{plan.plan_code === 'pro' ? 'Pro' : 'Free'}</strong></li>
            <li>오늘 답변: {plan.answers_used_today} / {plan.answers_per_day}회</li>
            <li>보유 문서: {plan.documents_count} / {plan.max_documents}개</li>
          </ul>
        ) : (
          <p className="mt-2 text-sm text-gray-500">불러오는 중…</p>
        )}
      </section>

      <SubscriptionSection />

      <section className="mt-6 rounded-lg border p-4">
        <h2 className="font-semibold">이메일로 문서 보내기</h2>
        {email ? (
          <>
            <p className="mt-2 text-sm">
              아래 주소로 첨부파일(PDF·HWP·HWPX·DOCX·이미지)을 보내면 자동으로 수집됩니다.
              <strong> 가입한 이메일에서 보낸 메일만</strong> 처리됩니다.
            </p>
            <code className="mt-2 block select-all rounded bg-gray-100 px-3 py-2 text-sm">
              {email.address}
            </code>
            {!email.pro && (
              <p className="mt-2 text-sm text-amber-600">
                이메일 인제스트는 Pro 전용 기능입니다. 업그레이드 후 이용해 주세요.
              </p>
            )}
            <button
              type="button"
              onClick={() => void rotate()}
              disabled={rotating}
              className="mt-3 rounded border px-3 py-1 text-sm disabled:opacity-50"
            >
              {rotating ? '재발급 중…' : '주소 재발급 (스팸 대응)'}
            </button>
          </>
        ) : (
          <p className="mt-2 text-sm text-gray-500">불러오는 중…</p>
        )}
      </section>
    </main>
  );
}
