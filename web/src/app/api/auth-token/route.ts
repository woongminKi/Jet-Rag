import { NextResponse } from 'next/server';
import { getServerForwardToken } from '@/lib/api/server-token';

/**
 * D2 (2026-05-20, plan §5) — Realtime client 가 JWT 를 가져오는 same-origin endpoint.
 *
 * 동작:
 *  - server-only `getServerForwardToken()` 로 Supabase auth httpOnly 쿠키에서
 *    access_token 디코드 (D1 cookie token 추출 재사용).
 *  - same-origin (Next.js 기본 — cross-origin CORS 미설정) 이라 외부 출처 접근 차단.
 *  - 토큰 없음 / 비로그인 / 디코드 실패 → `{ access_token: null }`.
 *
 * 호출처: `web/src/lib/hooks/use-active-docs-realtime.ts` — channel subscribe 직전
 *         fetch + `sb.realtime.setAuth(token)` 호출. 토큰 null 또는 실패 시 polling
 *         fallback 유지 (회귀 0).
 *
 * 보안:
 *  - service_role 노출 0 (anon key + 사용자 본인 JWT 만 client 로 흘러감).
 *  - JWT 자체는 이미 브라우저 httpOnly 쿠키에 존재 — 본 endpoint 는 동일 토큰을
 *    JS 가 읽을 수 있는 응답 본문으로만 노출 (httpOnly 쿠키는 직접 읽기 불가).
 */
export async function GET(): Promise<NextResponse> {
  const accessToken = await getServerForwardToken();
  return NextResponse.json({ access_token: accessToken ?? null }, {
    headers: {
      // 캐시 금지 — 사용자별 토큰이라 edge/CDN 캐시 누출 방지.
      'Cache-Control': 'no-store',
    },
  });
}
