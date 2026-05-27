import type { Metadata } from 'next';
import { Noto_Sans_KR } from 'next/font/google';
import { Toaster } from 'sonner';
import './globals.css';
import { Header } from '@/components/jet-rag/header';
import { ActiveDocsProvider } from '@/lib/contexts/active-docs-context';
// PORTFOLIO MODE — Auth 우회. 복원 시 아래 import 주석 해제.
// import { AuthProvider } from '@/lib/auth/auth-context';
// import { getCurrentUser } from '@/lib/auth/session';
import { cn } from '@/lib/utils';

const notoSansKr = Noto_Sans_KR({
  subsets: ['latin'],
  weight: ['300', '400', '500', '600', '700'],
  variable: '--font-sans',
  display: 'swap',
});

export const metadata: Metadata = {
  title: 'Jet-Rag',
  description: '한국어 멀티포맷 RAG 개인 지식 에이전트',
};

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  // PORTFOLIO MODE — 로그인 우회. 복원 시 아래 블록 주석 해제 + AuthProvider 재wrap.
  // // D1 Phase B — 검증된 초기 user 를 client 로 주입 (httpOnly 라 browser 가 못 읽음).
  // // Supabase 미설정 시 null (무인증 fallback, plan §2).
  // const currentUser = await getCurrentUser();
  // const authUser = currentUser
  //   ? { id: currentUser.id, email: currentUser.email }
  //   : null;

  return (
    <html lang="ko" className={cn('h-full antialiased', notoSansKr.variable)}>
      <body className="min-h-full flex flex-col font-sans">
        {/* PORTFOLIO MODE — AuthProvider 미주입. 복원 시 <AuthProvider user={authUser}> 로 wrap. */}
        <ActiveDocsProvider>
          <Header />
          {children}
        </ActiveDocsProvider>
        <Toaster position="top-right" richColors closeButton />
      </body>
    </html>
  );
}
