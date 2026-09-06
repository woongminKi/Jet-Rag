/**
 * `api-account` — Phase 1 의 end-to-end 증명 대상.
 *
 * 지금은 `/health`·`/auth/me`·`/stats`·`/stats/trend` 다. Phase 2 의 나머지(`/me/plan`,
 * `/admin/*`)가 이어서 붙는다.
 *
 * ## `/stats` 는 두 지표를 DB 에서 읽는다
 * `search_slo` 와 `vision_usage` 는 원본이 **프로세스 안 카운터**로 냈다. Edge 는 isolate 가
 * 휘발성이라 그 구조가 성립하지 않아서, 두 지표 모두 원장 테이블 기준으로 옮겼다
 * (`search_metrics_log` 최근 500행 · `vision_usage_log` 오늘(KST)).
 * Railway 쪽도 같은 기준으로 먼저 바꿔 둬서 전환 시 값이 안 흔들린다.
 *
 * ## 경로를 어떻게 아는가
 * Supabase 는 이 함수를 `/functions/v1/api-account/...` 로 서빙한다. 프론트가 부르는 건
 * `https://jetrag-api.woong-s.com/auth/me` 라서, Cloudflare 프록시(Task 1.7)가 원본 경로를
 * `X-Forwarded-Path` 로 실어 보낸다. 헤더가 없으면(직접 호출·로컬) URL 경로에서
 * 함수 이름 접두사를 떼고 쓴다.
 *
 * ## `access_token` 은 장식이 아니다
 * 프론트가 `supabase.realtime.setAuth(token)` 으로 주입해 `ingest_jobs` publication 을
 * 구독하고, 그게 RLS 를 통과시킨다(`009_realtime_ingest_jobs.sql`). 빠뜨리면
 * **인제스트 진행률 UI 가 에러 없이 멈춘다** — 아무 로그도 안 남아서 발견이 늦다.
 *
 * ## `authorized` 는 항상 true 다
 * 무효 토큰은 `getCurrentUser` 가 이미 401 로 끊는다. 그래서 핸들러에 도달했다는 건
 * 익명 데모 방문자이거나 인증된 사용자라는 뜻이고, 원본은 둘 다 `true` 를 준다.
 * 익명도 `authorized: true` + owner user_id 를 받는 게 현행 동작이다(운영 실측으로 확인).
 */

import { loadSettings } from "../_shared/config.ts";
import { applyCorsHeaders, preflightResponse } from "../_shared/cors.ts";
import { getCurrentUser, requestToken } from "../_shared/current_user.ts";
import { jsonResponse, methodNotAllowed, notFound, toResponse } from "../_shared/errors.ts";
import { createServiceClient } from "../_shared/db.ts";
import { buildStats, buildTrend, validateTrendParams } from "../_shared/stats/pipeline.ts";

/**
 * 함수 안에서 보이는 경로 접두사.
 *
 * 실측(2026-09-04): `https://<ref>.supabase.co/functions/v1/api-account/health` 로 부르면
 * 함수가 보는 `URL.pathname` 은 **`/api-account/health`** 다. `/functions/v1` 은 게이트웨이가
 * 떼고 함수 이름은 남는다. 처음엔 `/functions/v1/api-account` 를 뗄 거라 짐작해 전부 404 가 났다.
 */
const FUNCTION_PREFIX = "/api-account";

/**
 * 프록시가 준 원본 경로를 우선 쓰고, 없으면 URL 에서 접두사를 뗀다.
 * 접두사를 떼지 않으면 `/functions/v1/api-account/auth/me` 가 어떤 라우트에도 안 걸린다.
 */
function resolvePath(req: Request): string {
  const forwarded = req.headers.get("X-Forwarded-Path");
  const path = forwarded ?? new URL(req.url).pathname;
  const stripped = !forwarded && path.startsWith(FUNCTION_PREFIX)
    ? path.slice(FUNCTION_PREFIX.length) || "/"
    : path;
  return stripTrailingSlash(stripped);
}

/**
 * 후행 슬래시를 하나 떼어낸다.
 *
 * 원본(FastAPI)은 `redirect_slashes` 로 `/search/` → **307** 리다이렉트를 낸다. 그런데
 * 그 `Location` 이 `http://jet-rag-production.up.railway.app/...` 라 **Railway 호스트를
 * 직접 가리킨다**(실측). 제거 대상 호스트로 보내는 리다이렉트를 재현하는 건 이관 목적에
 * 역행하므로, 여기서는 **그냥 받아서 200 을 준다.** 리다이렉트를 따르는 클라이언트
 * (브라우저 `fetch` 포함)에게는 최종 결과가 같다.
 *
 * 안 맞추면 `/search/` 가 Railway 200, Edge 404 로 갈린다 — 실제로 그랬다.
 */
function stripTrailingSlash(path: string): string {
  return path.length > 1 && path.endsWith("/") ? path.slice(0, -1) : path;
}

Deno.serve(async (req: Request) => {
  const settings = loadSettings();

  // preflight 은 라우팅 이전에 끝낸다 — 원본에서도 미들웨어가 앱보다 앞이다.
  const pre = preflightResponse(req, settings);
  if (pre) return pre;

  let response: Response;
  try {
    const path = resolvePath(req);

    if (path === "/health") {
      // 원본 실측: {"status":"ok"}
      response = req.method === "GET" ? jsonResponse({ status: "ok" }) : methodNotAllowed();
    } else if (path === "/auth/me") {
      if (req.method !== "GET") {
        // preflight 이 아닌 OPTIONS 도 여기로 온다 — FastAPI 와 같이 405 다.
        response = methodNotAllowed();
      } else {
        const user = await getCurrentUser(req, settings);
        response = jsonResponse({
          authorized: true,
          user_id: user.userId,
          email: user.email,
          // auth 가 꺼져 있으면 원본과 같이 null. 켜져 있으면 검증에 쓴 토큰을 그대로 넘긴다.
          access_token: settings.authEnabled ? requestToken(req, settings) : null,
        });
      }
    } else if (path === "/stats") {
      if (req.method !== "GET") {
        response = methodNotAllowed();
      } else {
        // 원본도 익명을 통과시킨다 (`LEGACY_DEFAULT_USER`) — owner 데모 read.
        const user = await getCurrentUser(req, settings);
        response = jsonResponse(
          await buildStats(user.userId, { client: createServiceClient(settings) }),
        );
      }
    } else if (path === "/stats/trend") {
      if (req.method !== "GET") {
        response = methodNotAllowed();
      } else {
        const v = validateTrendParams(new URL(req.url).searchParams);
        if (!v.ok) {
          response = jsonResponse({ detail: v.detail }, 422);
        } else {
          response = jsonResponse(
            await buildTrend(v.params, { client: createServiceClient(settings) }),
          );
        }
      }
    } else {
      response = notFound();
    }
  } catch (e) {
    response = toResponse(e);
  }

  // 오류 응답에도 CORS 헤더가 붙어야 한다 — 없으면 프론트가 상태코드조차 못 읽는다.
  return applyCorsHeaders(req, response, settings);
});
