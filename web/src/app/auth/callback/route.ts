import { cookies } from 'next/headers';
import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';
import { createSupabaseServerClient } from '@/lib/supabase/server';
import { fetchAuthMe, redeemInviteOnServer } from '@/lib/auth/session';

/**
 * D1 Phase B — OAuth code exchange + 초대 게이트 (plan §1.1).
 *
 * 흐름:
 * 1. `?code` 를 세션으로 교환 (createServerClient 가 httpOnly 쿠키 set).
 * 2. `jetrag-pending-invite` 쿠키 있으면 → 그 코드로 redeem (신규 가입).
 *    실패 시 signOut + auth-error.
 * 3. 쿠키 없으면 → GET /auth/me 로 복귀 유저 판별. authorized=true 면 통과,
 *    false(코드 없는 신규)면 signOut + auth-error.
 *
 * 이로써 로그인 페이지의 Google 버튼(코드 없는 복귀 유저)은 통과, 신규는 코드 필수.
 */

const PENDING_INVITE_COOKIE = 'jetrag-pending-invite';

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

  const cookieStore = await cookies();
  const pendingInvite = cookieStore.get(PENDING_INVITE_COOKIE)?.value?.trim();

  if (pendingInvite) {
    // 신규 가입 — pending 쿠키 코드로 소진. 소비 후 쿠키 삭제.
    cookieStore.delete(PENDING_INVITE_COOKIE);
    try {
      await redeemInviteOnServer(pendingInvite);
    } catch {
      await supabase.auth.signOut();
      return NextResponse.redirect(`${origin}/auth/auth-error?reason=invite`);
    }
    return NextResponse.redirect(`${origin}${returnTo}`);
  }

  // 코드 없음 — 복귀 유저인지 /auth/me 로 판별.
  const authorized = await fetchAuthMe();
  if (!authorized) {
    await supabase.auth.signOut();
    return NextResponse.redirect(`${origin}/auth/auth-error?reason=no_invite`);
  }

  return NextResponse.redirect(`${origin}${returnTo}`);
}
