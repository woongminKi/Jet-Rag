import { createServerClient } from '@supabase/ssr';
import type { CookieOptions } from '@supabase/ssr';
import type { SupabaseClient } from '@supabase/supabase-js';
import type { NextRequest, NextResponse } from 'next/server';

/**
 * D1 Phase B — proxy.ts 전용 Supabase 클라이언트 (아키텍처 B, plan §1.1).
 *
 * proxy 에서 세션을 리프레시하고 갱신된 httpOnly 쿠키를 응답에 재set 한다.
 * NextRequest 에서 쿠키를 읽고, NextResponse 에 쿠키를 써서 브라우저로 흘려보낸다.
 * 도메인/secure/sameSite 분기는 server.ts 와 동일 정책.
 *
 * graceful — ENV 미설정 시 null 반환 (proxy 가 통과 처리, 무한루프 금지).
 */

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL;
const SUPABASE_ANON_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
const COOKIE_DOMAIN = process.env.NEXT_PUBLIC_COOKIE_DOMAIN || undefined;
const IS_PROD = process.env.NODE_ENV === 'production';

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

export function isSupabaseConfiguredForProxy(): boolean {
  return Boolean(SUPABASE_URL && SUPABASE_ANON_KEY);
}

/**
 * proxy.ts 에서 쓰는 클라이언트. request 쿠키 read + response 쿠키 write 브리지.
 * setAll 은 request(다음 핸들러용)와 response(브라우저용) 양쪽에 써야 세션이 일관된다.
 */
export function createSupabaseProxyClient(
  request: NextRequest,
  response: NextResponse,
): SupabaseClient | null {
  if (!SUPABASE_URL || !SUPABASE_ANON_KEY) return null;

  return createServerClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
    cookies: {
      getAll() {
        return request.cookies.getAll();
      },
      setAll(cookiesToSet) {
        cookiesToSet.forEach(({ name, value }) => {
          request.cookies.set(name, value);
        });
        cookiesToSet.forEach(({ name, value, options }) => {
          response.cookies.set(name, value, sessionCookieOptions(options));
        });
      },
    },
  });
}
