import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';
import { createSupabaseServerClient } from '@/lib/supabase/server';

/**
 * D1 Phase B — OAuth code exchange (plan §1.1).
 *
 * 흐름:
 * 1. `?code` 를 세션으로 교환 (createServerClient 가 httpOnly 쿠키 set).
 * 2. returnTo 로 리다이렉트.
 *
 * W31 follow-up — 초대 코드 게이트 제거 (공개 가입 정책). exchange 성공 시 즉시 통과.
 */

function sanitizeReturnTo(raw: string | null): string {
  if (raw && raw.startsWith('/') && !raw.startsWith('//')) return raw;
  return '/';
}

export async function GET(request: NextRequest): Promise<NextResponse> {
  const { searchParams, origin } = request.nextUrl;
  const code = searchParams.get('code');
  const returnTo = sanitizeReturnTo(searchParams.get('returnTo'));

  if (!code) {
    return NextResponse.redirect(`${origin}/auth/auth-error?reason=missing_code`);
  }

  const supabase = await createSupabaseServerClient();
  if (!supabase) {
    return NextResponse.redirect(`${origin}/auth/auth-error?reason=config`);
  }

  const { error } = await supabase.auth.exchangeCodeForSession(code);
  if (error) {
    return NextResponse.redirect(`${origin}/auth/auth-error?reason=exchange`);
  }

  return NextResponse.redirect(`${origin}${returnTo}`);
}
