import { createSupabaseServerClient } from '@/lib/supabase/server';

/**
 * D1 Phase B — 서버 컨텍스트 세션 헬퍼 (plan §1.1).
 *
 * - getCurrentUser: server component / action 에서 검증된 호출자 식별 (없으면 null).
 * - getAccessToken: 서버에서 jetrag-api 로 forward 할 Bearer 토큰 추출.
 *
 * 모두 graceful — Supabase 미설정 시 null 반환 (무인증 fallback, plan §2).
 *
 * 주의: next/headers cookies() 에 의존하므로 server 컨텍스트 전용. client 번들에는
 * 포함되지 않는다 (server.ts 가 next/headers 를 import → 클라이언트 import 시 빌드 에러로
 * 자연 차단). client.ts 의 서버 분기에서만 dynamic import 한다.
 *
 * W31 follow-up — 초대 코드 게이트 제거에 따라 redeemInviteOnServer / fetchAuthMe 삭제.
 */

export interface CurrentUser {
  id: string;
  email: string | null;
}

/** 검증된 호출자(없으면 null). getUser() 는 Auth 서버 검증이라 인가 판단에 안전. */
export async function getCurrentUser(): Promise<CurrentUser | null> {
  const supabase = await createSupabaseServerClient();
  if (!supabase) return null;
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) return null;
  return { id: user.id, email: user.email ?? null };
}

/** 서버에서 jetrag-api 에 붙일 access_token (없으면 null). */
export async function getAccessToken(): Promise<string | null> {
  const supabase = await createSupabaseServerClient();
  if (!supabase) return null;
  const {
    data: { session },
  } = await supabase.auth.getSession();
  return session?.access_token ?? null;
}
