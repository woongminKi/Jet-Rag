'use server';

import { redirect } from 'next/navigation';
import { createSupabaseServerClient } from '@/lib/supabase/server';

/**
 * D1 Phase B — Auth server actions (plan §1.1).
 *
 * 모든 세션 변경은 server action 에서 처리 → `@supabase/ssr` createServerClient 가
 * httpOnly 쿠키를 set (browser client 미사용 — 진짜 httpOnly).
 *
 * useActionState 호환 — { error: string | null } 형태 반환. 성공 시 redirect().
 *
 * W31 follow-up — 초대 코드 게이트 제거 (공개 가입 정책).
 */

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? 'http://localhost:3001';

export interface AuthActionState {
  error: string | null;
}

// 'use server' 모듈은 async 함수만 export 가능 — 헬퍼는 모듈 내부에 둔다.
function fail(message: string): AuthActionState {
  return { error: message };
}

function configError(): AuthActionState {
  return fail('인증 설정이 완료되지 않았습니다. 잠시 후 다시 시도해 주세요.');
}

/** Email/PW 로그인. 성공 시 returnTo(또는 홈)로 이동. */
export async function signInWithPassword(
  _prev: AuthActionState,
  formData: FormData,
): Promise<AuthActionState> {
  const email = String(formData.get('email') ?? '').trim();
  const password = String(formData.get('password') ?? '');
  const returnTo = sanitizeReturnTo(formData.get('returnTo'));
  if (!email || !password) return fail('이메일과 비밀번호를 입력해 주세요.');

  const supabase = await createSupabaseServerClient();
  if (!supabase) return configError();

  const { error } = await supabase.auth.signInWithPassword({ email, password });
  if (error) return fail('이메일 또는 비밀번호가 올바르지 않습니다.');

  redirect(returnTo);
}

/** Email/PW 가입 (Email confirm OFF — 즉시 세션). 공개 가입. */
export async function signUp(
  _prev: AuthActionState,
  formData: FormData,
): Promise<AuthActionState> {
  const email = String(formData.get('email') ?? '').trim();
  const password = String(formData.get('password') ?? '');
  if (!email || !password) return fail('이메일과 비밀번호를 입력해 주세요.');

  const supabase = await createSupabaseServerClient();
  if (!supabase) return configError();

  const { error } = await supabase.auth.signUp({ email, password });
  if (error) return fail('가입에 실패했습니다. 이미 가입된 이메일일 수 있습니다.');

  redirect('/');
}

/** 로그아웃 — 세션 쿠키 제거 후 로그인 페이지로. */
export async function signOut(): Promise<void> {
  const supabase = await createSupabaseServerClient();
  if (supabase) await supabase.auth.signOut();
  redirect('/login');
}

/**
 * Google OAuth 시작. 콜백이 code exchange 후 세션 수립 (plan §1.1).
 */
export async function signInWithGoogle(formData: FormData): Promise<void> {
  const returnTo = sanitizeReturnTo(formData.get('returnTo'));

  const supabase = await createSupabaseServerClient();
  if (!supabase) redirect('/auth/auth-error?reason=config');

  const callbackUrl = new URL('/auth/callback', SITE_URL);
  if (returnTo !== '/') callbackUrl.searchParams.set('returnTo', returnTo);

  const { data, error } = await supabase!.auth.signInWithOAuth({
    provider: 'google',
    options: { redirectTo: callbackUrl.toString() },
  });
  if (error || !data?.url) redirect('/auth/auth-error?reason=oauth');

  redirect(data.url);
}

/** open-redirect 방어 — 내부 경로(슬래시 1개로 시작)만 허용. */
function sanitizeReturnTo(raw: FormDataEntryValue | null): string {
  const value = typeof raw === 'string' ? raw : '';
  if (value.startsWith('/') && !value.startsWith('//')) return value;
  return '/';
}
