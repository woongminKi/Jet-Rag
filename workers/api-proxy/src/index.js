/**
 * `jetrag-api.woong-s.com` → Supabase Edge Functions 리버스 프록시.
 *
 * ## 두 가지를 동시에 푼다
 * 1. **쿠키 도메인** — 프론트가 `credentials: 'include'` 로 보내는 `.woong-s.com` 쿠키는
 *    `*.supabase.co` 로는 전송되지 않는다. 도메인을 그대로 유지해야 세션이 살아 있다.
 * 2. **경로 구조** — `/auth/me` 를 `/functions/v1/api-account/auth/me` 로 매핑해
 *    **프론트 코드 변경을 0 으로** 만든다.
 *
 * ## 이게 전환 장치다
 * `routes.js` 의 `ROUTES` 에 한 줄을 더하면 그 경로만 Supabase 로 넘어가고, 지우면 즉시
 * Railway 로 되돌아온다. 52,890 LOC 를 한 번에 넘기지 않고 경로 단위로 옮기기 위한 구조다.
 *
 * ## `LEGACY_ORIGIN` 은 Railway 의 **기본 도메인**이어야 한다
 * `jetrag-api.woong-s.com` 은 이 Worker 가 가로채므로, 그 주소를 fallback 으로 쓰면
 * Worker 가 자기 자신을 부르는 무한 루프가 된다. `jet-rag-production.up.railway.app` 를 쓴다
 * (실측 확인: `/health` 200). 아래 루프 가드도 같은 이유다.
 */

import { resolveTarget } from "./routes.js";

/** Worker 가 자기 자신으로 되돌아가는 것을 막는다. */
function isSelf(targetUrl, requestUrl) {
  return new URL(targetUrl).host === new URL(requestUrl).host;
}

export default {
  /**
   * @param {Request} request
   * @param {{ SUPABASE_FUNCTIONS_BASE: string, LEGACY_ORIGIN: string,
   *           SUPABASE_FUNCTION_REGION?: string }} env
   */
  async fetch(request, env) {
    const url = new URL(request.url);
    const target = resolveTarget(url.pathname);

    if (target === null) {
      // 아직 이관하지 않은 경로 — 기존 백엔드로 넘긴다.
      if (!env.LEGACY_ORIGIN) {
        // Phase 6 에서 이 값을 비우면 Railway 의존이 끝난다.
        return Response.json({ detail: "Not Found" }, { status: 404 });
      }
      const legacy = new URL(url.pathname + url.search, env.LEGACY_ORIGIN);
      if (isSelf(legacy, request.url)) {
        // 설정 실수로 자기 자신을 가리키면 즉시 드러나게 한다 — 무한 루프보다 낫다.
        return Response.json(
          { detail: "LEGACY_ORIGIN 이 프록시 자신을 가리킵니다 (설정 오류)." },
          { status: 500 },
        );
      }
      return fetch(new Request(legacy, request));
    }

    const proxied = new Request(
      `${env.SUPABASE_FUNCTIONS_BASE}/${target}${url.pathname}${url.search}`,
      request,
    );
    // Edge Function 이 원본 경로로 라우팅한다. 이게 없으면 함수는
    // `/api-account/auth/me` 를 보게 되고, 프록시를 거치지 않은 호출과 경로가 갈린다.
    proxied.headers.set("X-Forwarded-Path", url.pathname);
    // 함수를 **DB 와 같은 지역**에서 돌린다.
    //
    // Supabase 는 기본적으로 *호출자* 에 가까운 지역으로 함수를 보낸다. 그런데 호출자는
    // 사용자가 아니라 이 Worker 고, Worker 는 사용자 근처 Cloudflare PoP 에서 돈다.
    // 그래서 미국에서 접속하면 함수가 us-west-1 에서 뜨고, DB(ap-northeast-2)까지
    // 요청마다 태평양을 건넌다 — 검색은 DB 왕복이 여러 번이라 그대로 지연이 된다.
    //
    // 실측(2026-09-05, 같은 함수·같은 질의):
    //   x-region: ap-northeast-2 → 서버 처리 132ms
    //   x-region: us-west-1      → 서버 처리 828ms  (6.3 배)
    //
    // 미설정이면 헤더를 안 붙여 Supabase 기본 라우팅을 그대로 쓴다.
    if (env.SUPABASE_FUNCTION_REGION) {
      proxied.headers.set("x-region", env.SUPABASE_FUNCTION_REGION);
    }
    return fetch(proxied);
  },
};
