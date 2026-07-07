import type { Metadata } from 'next';

export const metadata: Metadata = { title: '이용약관 · Jet-Rag' };

// ⚠️ 사용자 작성 슬롯 — 아래 문자열을 실제 이용약관 본문으로 교체한다.
// (플랜은 페이지 구조만 제공. 법적 본문은 사용자 초안으로 채운다 — 결정 이력 #3.)
const TERMS_BODY = `제1조(목적)
본 약관은 Jet-Rag(이하 "서비스")의 이용 조건을 규정합니다.

[사용자 초안으로 교체]`;

export default function TermsPage() {
  return (
    <main className="mx-auto max-w-2xl px-4 py-8">
      <h1 className="text-2xl font-bold">이용약관</h1>
      <p className="mt-2 text-sm text-muted-foreground">최종 개정일: 2026-07-07</p>
      <article className="mt-6 whitespace-pre-wrap text-sm leading-relaxed">
        {TERMS_BODY}
      </article>
    </main>
  );
}
