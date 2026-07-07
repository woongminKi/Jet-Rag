import type { Metadata } from 'next';

export const metadata: Metadata = { title: '개인정보처리방침 · Jet-Rag' };

// ⚠️ 사용자 작성 슬롯 — 아래 문자열을 실제 개인정보처리방침 본문으로 교체한다.
const PRIVACY_BODY = `1. 수집하는 개인정보 항목
- 이메일 주소, 결제 정보(카카오페이 빌링키), 업로드 문서

[사용자 초안으로 교체]`;

export default function PrivacyPage() {
  return (
    <main className="mx-auto max-w-2xl px-4 py-8">
      <h1 className="text-2xl font-bold">개인정보처리방침</h1>
      <p className="mt-2 text-sm text-muted-foreground">최종 개정일: 2026-07-07</p>
      <article className="mt-6 whitespace-pre-wrap text-sm leading-relaxed">
        {PRIVACY_BODY}
      </article>
    </main>
  );
}
