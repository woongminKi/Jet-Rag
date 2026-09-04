/**
 * `api-search` — `/search` Edge Function 의 HTTP 껍데기.
 *
 * 여기서 하는 일은 넷뿐이다: 경로 판정 · CORS · 사용자 판별 · 파라미터 검증.
 * 검색 자체는 `_shared/search/pipeline.ts` 가 한다. 나눠 둔 이유는 패리티 검사기가
 * **토큰 없이 in-process 로** Python `search()` 와 나란히 돌려 응답을 비교할 수 있게
 * 하기 위해서다 — HTTP 를 태우면 인증·네트워크 변수가 섞여 대조가 흐려진다.
 *
 * ## 인증이 아니라 "사용자 판별" 이다
 * 원본 `/search` 의 의존성은 `CurrentUserDep = LEGACY_DEFAULT_USER` 라 **익명도 통과**하고
 * 기본 사용자로 검색한다(무효 토큰만 401). 그래서 `requireAuthenticatedUser` 가 아니라
 * `getCurrentUser` 를 쓴다 — 여기서 강제하면 익명 데모가 401 로 막힌다.
 *
 * ## 경로
 * 함수 안에서 보이는 pathname 은 `/api-search/...` 다(Phase 1 실측 — 배포 전에는
 * `/functions/v1/...` 로 짐작했다가 전 경로가 404 났다). 프록시가 원본 경로를
 * `X-Forwarded-Path` 로 실어 주면 그걸 우선한다.
 */

import { loadSettings } from "../_shared/config.ts";
import { createServiceClient } from "../_shared/db.ts";
import { applyCorsHeaders, preflightResponse } from "../_shared/cors.ts";
import { getCurrentUser } from "../_shared/current_user.ts";
import { jsonResponse, methodNotAllowed, notFound, toResponse } from "../_shared/errors.ts";
import { validateSearchParams } from "../_shared/search/params.ts";
import { runSearch, SearchHttpError } from "../_shared/search/pipeline.ts";

const FUNCTION_PREFIX = "/api-search";

function resolvePath(req: Request): string {
  const forwarded = req.headers.get("X-Forwarded-Path");
  if (forwarded) return forwarded;
  const path = new URL(req.url).pathname;
  return path.startsWith(FUNCTION_PREFIX) ? path.slice(FUNCTION_PREFIX.length) || "/" : path;
}

/** `EdgeRuntime.waitUntil` 이 있으면 쓴다 — 지표·캐시 쓰기가 응답을 늦추지 않게. */
function resolveWaitUntil(): ((p: Promise<unknown>) => void) | undefined {
  const rt = (globalThis as { EdgeRuntime?: { waitUntil?: (p: Promise<unknown>) => void } })
    .EdgeRuntime;
  return typeof rt?.waitUntil === "function" ? rt.waitUntil.bind(rt) : undefined;
}

Deno.serve(async (req: Request) => {
  const settings = loadSettings();

  // preflight 은 라우팅 이전에 끝낸다 — 원본에서도 미들웨어가 앱보다 앞이다.
  const pre = preflightResponse(req, settings);
  if (pre) return pre;

  let response: Response;
  try {
    const path = resolvePath(req);
    if (path !== "/search") {
      response = notFound();
    } else if (req.method !== "GET") {
      response = methodNotAllowed();
    } else {
      const user = await getCurrentUser(req, settings);
      const url = new URL(req.url);
      const validated = validateSearchParams(url.searchParams);
      if (!validated.ok) {
        response = jsonResponse({ detail: validated.detail }, validated.status);
      } else {
        try {
          const { body, headers } = await runSearch(validated.params, user.userId, {
            client: createServiceClient(settings),
            read: (k) => Deno.env.get(k),
            waitUntil: resolveWaitUntil(),
          });
          response = jsonResponse(body, 200, headers);
        } catch (e) {
          if (e instanceof SearchHttpError) {
            response = jsonResponse({ detail: e.detail }, e.status, e.headers);
          } else {
            throw e;
          }
        }
      }
    }
  } catch (e) {
    response = toResponse(e);
  }

  // 오류 응답에도 CORS 헤더가 붙어야 한다 — 없으면 프론트가 상태코드조차 못 읽는다.
  return applyCorsHeaders(req, response, settings);
});
