import { createServerClient } from '@supabase/ssr';
import type { CookieOptions } from '@supabase/ssr';
import type { SupabaseClient } from '@supabase/supabase-js';
import { cookies } from 'next/headers';
import { setServerTokenResolver } from '@/lib/api/client';
import { getServerForwardToken } from '@/lib/api/server-token';

// D1 Phase B — 서버 전용 모듈 로드 시 API client 에 token resolver 등록 (plan §1.1).
// server.ts 는 server component/action/route 에서만 import 되므로 client 번들 누수 없음.
// layout.tsx → session.ts → server.ts 경로로 매 서버 렌더 초기에 로드돼 resolver 가
// apiGet 보다 먼저 등록된다.
setServerTokenResolver(getServerForwardToken);

/**
 * D1 Phase B — 서버 컨텍스트(server component / server action / route handler) 전용
 * Supabase 클라이언트 (아키텍처 B, plan §1.1).
 *
 * `@supabase/ssr` createServerClient 가 세션을 httpOnly 쿠키(`sb-<ref>-auth-token`)에
 * 저장한다. browser client 로 세션을 관리하지 않으므로 JS 가 토큰을 만지지 않는다
 * (진짜 httpOnly). 쿠키 도메인은 prod 에서 형제 서브도메인(jetrag / jetrag-api)이
 * 공유하도록 `.woong-s.com`, dev 는 host-only(localhost) 로 분기한다.
 *
 * graceful — Supabase ENV 미설정 환경(로컬 dev / auth 미활성)에서는 null 을 반환해
 * 호출부가 무인증 fallback 으로 동작하게 한다 (production 무중단, plan §2).
 */

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL;
const SUPABASE_ANON_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

// prod 에서 형제 서브도메인 공유용 쿠키 도메인. ENV 미설정(dev)이면 host-only.
// 예: production Vercel ENV `NEXT_PUBLIC_COOKIE_DOMAIN=.woong-s.com`.
const COOKIE_DOMAIN = process.env.NEXT_PUBLIC_COOKIE_DOMAIN || undefined;
const IS_PROD = process.env.NODE_ENV === 'production';

/** 세션 쿠키 공통 옵션 — httpOnly·secure(prod)·sameSite=lax·도메인 분기. */
function sessionCookieOptions(base: CookieOptions): CookieOptions {
  return {
    ...base,
    httpOnly: true,
    secure: IS_PROD,
    sameSite: 'lax',
    path: '/',
    ...(COOKIE_DOMAIN ? { domain: COOKIE_DOMAIN } : {}),
  };
}

export function isSupabaseConfigured(): boolean {
  return Boolean(SUPABASE_URL && SUPABASE_ANON_KEY);
}

/**
 * server component / server action / route handler 에서 호출하는 Supabase 클라이언트.
 * ENV 미설정 시 null (graceful).
 *
 * 주의: server component 렌더 중에는 쿠키 set 이 불가하다(Next 제약). 그 경로의 set 은
 * try/catch 로 무시하고, 세션 리프레시는 proxy.ts 가 담당한다(plan §1.1).
 */
export async function createSupabaseServerClient(): Promise<SupabaseClient | null> {
  if (!SUPABASE_URL || !SUPABASE_ANON_KEY) return null;

  const cookieStore = await cookies();

  return createServerClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
    cookies: {
      getAll() {
        return cookieStore.getAll();
      },
      setAll(cookiesToSet) {
        try {
          cookiesToSet.forEach(({ name, value, options }) => {
            cookieStore.set(name, value, sessionCookieOptions(options));
          });
        } catch {
          // server component 렌더 중 set 시도 — proxy 가 리프레시를 담당하므로 무시.
        }
      },
    },
  });
}
