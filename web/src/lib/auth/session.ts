import { createSupabaseServerClient } from '@/lib/supabase/server';

/**
 * D1 Phase B — 서버 컨텍스트 세션 헬퍼 (plan §1.1).
 *
 * - getCurrentUser: server component / action 에서 검증된 호출자 식별 (없으면 null).
 * - getAccessToken: 서버에서 jetrag-api 로 forward 할 Bearer 토큰 추출.
 * - redeemInviteOnServer / fetchAuthMe: 가입 게이트·복귀 유저 판별용 백엔드 호출.
 *
 * 모두 graceful — Supabase 미설정 시 null 반환 (무인증 fallback, plan §2).
 *
 * 주의: next/headers cookies() 에 의존하므로 server 컨텍스트 전용. client 번들에는
 * 포함되지 않는다 (server.ts 가 next/headers 를 import → 클라이언트 import 시 빌드 에러로
 * 자연 차단). client.ts 의 서버 분기에서만 dynamic import 한다.
 */

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000';

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

/**
 * 가입 직후 초대 코드 소진 (plan §5). Bearer 토큰을 첨부해 백엔드 호출.
 * 성공 시 true, 실패 시 사용자 노출용 한국어 사유와 함께 throw.
 */
export async function redeemInviteOnServer(code: string): Promise<boolean> {
  const token = await getAccessToken();
  if (!token) throw new Error('세션을 확인할 수 없습니다. 다시 로그인해 주세요.');

  const res = await fetch(`${API_BASE}/auth/redeem-invite`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ code }),
    cache: 'no-store',
  });
  if (res.ok) return true;

  let detail = '초대 코드 처리에 실패했습니다.';
  try {
    const body = await res.json();
    if (typeof body?.detail === 'string') detail = body.detail;
  } catch {
    // 본문 파싱 실패 — 기본 메시지 유지.
  }
  throw new Error(detail);
}

/**
 * GET /auth/me — 호출자 승인 여부 (OAuth 복귀 유저 게이트, plan §1.1).
 * 호출 실패는 미승인(false) 으로 간주 (차단 우선).
 */
export async function fetchAuthMe(): Promise<boolean> {
  const token = await getAccessToken();
  if (!token) return false;
  try {
    const res = await fetch(`${API_BASE}/auth/me`, {
      headers: { Accept: 'application/json', Authorization: `Bearer ${token}` },
      cache: 'no-store',
    });
    if (!res.ok) return false;
    const body = await res.json();
    return Boolean(body?.authorized);
  } catch {
    return false;
  }
}
