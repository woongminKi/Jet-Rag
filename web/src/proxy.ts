import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';
import {
  createSupabaseProxyClient,
  isSupabaseConfiguredForProxy,
} from '@/lib/supabase/middleware-client';

/**
 * D1 Phase B — Next 16 proxy (middleware 후속, plan §1.1).
 *
 * 역할:
 * 1. Supabase 세션 리프레시 + 갱신된 httpOnly 쿠키 응답 재set.
 * 2. 보호 경로 미인증 접근 → /login 리다이렉트 (returnTo 보존).
 *
 * 무중단(plan §2): Supabase ENV 미설정 / auth 미활성 환경에서는 graceful 통과
 * (NextResponse.next()) — 무한 리다이렉트 금지. 공개 경로는 항상 통과.
 */

// 인증 없이 접근 가능한 경로 (로그인 플로우 자체).
const PUBLIC_PATHS = [
  '/login',
  '/signup',
  '/auth/callback',
  '/auth/auth-error',
];

function isPublicPath(pathname: string): boolean {
  return PUBLIC_PATHS.some(
    (p) => pathname === p || pathname.startsWith(`${p}/`),
  );
}

export async function proxy(request: NextRequest): Promise<NextResponse> {
  // request 쿠키를 그대로 이어받는 기본 통과 응답. 세션 리프레시 결과 쿠키가 여기 쌓인다.
  const response = NextResponse.next({ request });

  // ENV 미설정/미활성 → 세션 관리 불가. 보호도 하지 않고 그대로 통과 (무한루프 금지).
  if (!isSupabaseConfiguredForProxy()) {
    return response;
  }

  const supabase = createSupabaseProxyClient(request, response);
  if (!supabase) return response;

  // getUser() — Auth 서버 검증(권위). getSession() 은 미검증이라 인가 판단에 부적합.
  // 토큰 만료 시 ssr 가 리프레시하고 setAll 로 response 쿠키를 갱신한다.
  const {
    data: { user },
  } = await supabase.auth.getUser();

  const { pathname } = request.nextUrl;

  // 인증 사용자가 로그인/가입 페이지 접근 → 홈으로.
  if (user && (pathname === '/login' || pathname === '/signup')) {
    const url = request.nextUrl.clone();
    url.pathname = '/';
    url.search = '';
    return NextResponse.redirect(url);
  }

  // 보호 경로 미인증 → /login (원래 목적지 returnTo 로 보존).
  if (!user && !isPublicPath(pathname)) {
    const url = request.nextUrl.clone();
    url.pathname = '/login';
    url.search = '';
    url.searchParams.set('returnTo', pathname);
    return NextResponse.redirect(url);
  }

  // auth 쿠키를 set 한 응답은 캐시 금지 (사용자 간 세션 누수 방지).
  response.headers.set('Cache-Control', 'private, no-store');
  return response;
}

export const config = {
  // 정적 자산·이미지·favicon 제외 전 경로. (RSC data 경로는 의도적으로 포함됨.)
  matcher: [
    '/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)',
  ],
};
