import type { Metadata, Viewport } from 'next';
import localFont from 'next/font/local';
import Script from 'next/script';
import { Toaster } from 'sonner';
import './globals.css';
import { Header } from '@/components/jet-rag/header';
import { Footer } from '@/components/jet-rag/footer';
import { ActiveDocsProvider } from '@/lib/contexts/active-docs-context';
import { AuthProvider } from '@/lib/auth/auth-context';
import { getCurrentUser } from '@/lib/auth/session';
import { cn } from '@/lib/utils';

// design.md §3.1 — Pretendard 가변 폰트로 전환 (next/font/local, 신규 의존성 아님 — 폰트 파일만 도입).
// weight '45 920' — Pretendard Variable 의 실제 가변 축 범위.
const pretendard = localFont({
  src: '../fonts/PretendardVariable.woff2',
  variable: '--font-sans',
  weight: '45 920',
  display: 'swap',
});

// design.md §8.3 — FOUC 방지: 첫 페인트 전 <html> 에 .dark 클래스를 동기 적용.
// 저장된 수동 오버라이드(localStorage.theme) 우선, 없으면 시스템 설정(prefers-color-scheme) 따름.
const THEME_INIT_SCRIPT = `
(function () {
  try {
    var stored = localStorage.getItem('theme');
    var dark = stored === 'dark' || (!stored && window.matchMedia('(prefers-color-scheme: dark)').matches);
    if (dark) document.documentElement.classList.add('dark');
  } catch (e) {}
})();
`;

export const metadata: Metadata = {
  title: 'Jet-Rag',
  description: '한국어 멀티포맷 RAG 개인 지식 에이전트',
  // W28 — PWA manifest + iOS A2HS + share_target (Android PDF 공유 진입점).
  // Next.js 16: themeColor / viewportFit 은 별도 `viewport` export 로 이동 (deprecation 회피).
  manifest: '/manifest.json',
  icons: {
    icon: '/icon-192.png',
    apple: '/icon-192.png',
  },
  appleWebApp: {
    capable: true,
    statusBarStyle: 'black-translucent',
    title: 'Jet-Rag',
  },
};

// W28 — viewport-fit=cover: iOS notch / home-indicator safe-area 대응 (CSS 의 env(safe-area-inset-*) 활성화 조건).
// themeColor: standalone PWA 의 상태바 / Android Chrome 의 주소창 색상.
// design.md §8 — 다크모드 도입으로 media 배열화. 수동 오버라이드까지 meta 동기화는 과설계이므로
// 시스템(prefers-color-scheme) 기준만 반영 — manifest.json 의 theme_color/background_color 는 라이트 기본값 유지.
export const viewport: Viewport = {
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: '#FFFFFF' },
    { media: '(prefers-color-scheme: dark)', color: '#1B1C1E' },
  ],
  width: 'device-width',
  initialScale: 1,
  viewportFit: 'cover',
};

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  // D1 Phase B — 검증된 초기 user 를 client 로 주입 (httpOnly 라 browser 가 못 읽음).
  // Supabase 미설정 시 null (무인증 fallback, plan §2).
  const currentUser = await getCurrentUser();
  const authUser = currentUser
    ? { id: currentUser.id, email: currentUser.email }
    : null;

  return (
    // suppressHydrationWarning — THEME_INIT_SCRIPT 가 하이드레이션 전에 .dark 클래스를 직접
    // DOM 에 추가하므로, React 가 기대하는 서버 렌더 결과(클래스 없음)와 실제 DOM 이 달라 발생하는
    // 경고를 의도적으로 억제 (design.md §8.3 표준 패턴).
    <html
      lang="ko"
      suppressHydrationWarning
      className={cn('h-full antialiased', pretendard.variable)}
    >
      {/* W26 — min-h-dvh: iOS Safari 100vh 부정확 회피 (dvh = dynamic viewport height).
          safe-area inset: notch / home-indicator 영역 회피. */}
      <body className="flex min-h-dvh flex-col font-sans">
        <Script id="theme-init" strategy="beforeInteractive">
          {THEME_INIT_SCRIPT}
        </Script>
        <AuthProvider user={authUser}>
          <ActiveDocsProvider>
            <Header />
            {children}
            <Footer />
          </ActiveDocsProvider>
        </AuthProvider>
        <Toaster position="top-right" richColors closeButton />
      </body>
    </html>
  );
}
