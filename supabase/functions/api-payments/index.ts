/**
 * `api-payments` — 카카오페이 정기결제.
 *
 * | 경로 | 메서드 | 인증 |
 * |---|---|---|
 * | `/payments/subscribe/ready` | POST | 로그인 JWT |
 * | `/payments/subscribe/approve` | POST | 로그인 JWT (+ `?pg_token=`) |
 * | `/payments/subscribe/cancel` | POST | 로그인 JWT |
 * | `/billing/run` | POST | `X-Billing-Cron-Secret` 공유 secret |
 *
 * ## 한 함수에 인증 모델 둘을 둔다
 * `/billing/run` 의 호출자는 사람이 아니라 cron 이라 JWT 가 없다. 원본도 같은 이유로
 * 이 경로만 별도 라우터(인증 dependency 없음)로 뺐다. 함수를 하나 더 만들면 배포 단위만
 * 늘어나므로 `/ingest/email` 과 같은 방식으로 한 함수 안에서 갈랐다.
 *
 * ## 라우팅 먼저, 인증 나중
 * FastAPI 는 경로·메서드 매칭이 dependency 보다 앞이다. 인증을 먼저 걸면 없는 경로가
 * 404 대신 401 이 되어 원본과 갈린다(§Edge 이관 — 라우팅이 인증보다 먼저).
 *
 * ## 이 함수는 돈을 움직인다
 * `/billing/run` 은 실제로 카드를 긁는다. secret 이 없으면 **503 으로 꺼져 있는 상태**가
 * 기본값이고, 그게 안전한 쪽이다. 프록시 전환도 secret 을 넣은 뒤에 해야 한다.
 */

import { loadSettings } from "../_shared/config.ts";
import { applyCorsHeaders, preflightResponse } from "../_shared/cors.ts";
import { getCurrentUser, requireAuthenticatedUser } from "../_shared/current_user.ts";
import { createServiceClient } from "../_shared/db.ts";
import { jsonResponse, methodNotAllowed, notFound, toResponse } from "../_shared/errors.ts";
import {
  type BillingSettings,
  handleBillingRun,
  handleSubscribeApprove,
  handleSubscribeCancel,
  handleSubscribeReady,
  type PaymentsRouteDeps,
} from "../_shared/billing/routes.ts";

const FUNCTION_PREFIX = "/api-payments";

function resolvePath(req: Request): string {
  const forwarded = req.headers.get("X-Forwarded-Path");
  const path = forwarded ?? new URL(req.url).pathname;
  const stripped = !forwarded && path.startsWith(FUNCTION_PREFIX)
    ? path.slice(FUNCTION_PREFIX.length) || "/"
    : path;
  return stripped.length > 1 && stripped.endsWith("/") ? stripped.slice(0, -1) : stripped;
}

Deno.serve(async (req: Request): Promise<Response> => {
  const settings = loadSettings();
  const pre = preflightResponse(req, settings);
  if (pre) return pre;

  try {
    const path = resolvePath(req);
    const billingSettings: BillingSettings = {
      paymentProvider: settings.paymentProvider,
      kakaopaySecretKey: settings.kakaopaySecretKey,
      kakaopayCid: settings.kakaopayCid,
      billingKeyEncryptionKey: settings.billingKeyEncryptionKey,
      billingCronSecret: settings.billingCronSecret,
      billingRedirectBase: settings.billingRedirectBase,
    };

    // ---- cron 진입점 — JWT 가 아니라 공유 secret ----
    if (path === "/billing/run") {
      if (req.method !== "POST") {
        return applyCorsHeaders(req, methodNotAllowed(), settings);
      }
      const deps: PaymentsRouteDeps = {
        client: createServiceClient(settings),
        settings: billingSettings,
      };
      const r = await handleBillingRun(
        deps,
        req.headers.get("x-billing-cron-secret") ?? "",
      );
      return applyCorsHeaders(req, jsonResponse(r.body, r.status), settings);
    }

    // ---- 구독 3종 — 라우팅 먼저, 인증 나중 ----
    const SUBSCRIBE_ROUTES = new Set([
      "/payments/subscribe/ready",
      "/payments/subscribe/approve",
      "/payments/subscribe/cancel",
    ]);
    if (!SUBSCRIBE_ROUTES.has(path)) {
      return applyCorsHeaders(req, notFound(), settings);
    }
    if (req.method !== "POST") {
      return applyCorsHeaders(req, methodNotAllowed(), settings);
    }

    // 라우터 전체에 걸린 `require_authenticated_user` 자리.
    // 익명 fallback 이 owner 컨텍스트라, 이 게이트가 없으면 익명 방문자가 owner 의
    // 구독을 해지시킬 수 있다.
    const user = requireAuthenticatedUser(await getCurrentUser(req, settings));
    const deps: PaymentsRouteDeps = {
      client: createServiceClient(settings),
      settings: billingSettings,
    };

    let r;
    if (path === "/payments/subscribe/ready") {
      r = await handleSubscribeReady(deps, user.userId);
    } else if (path === "/payments/subscribe/approve") {
      r = await handleSubscribeApprove(
        deps,
        user.userId,
        new URL(req.url).searchParams.get("pg_token"),
      );
    } else {
      r = await handleSubscribeCancel(deps, user.userId);
    }
    return applyCorsHeaders(req, jsonResponse(r.body, r.status), settings);
  } catch (e) {
    return applyCorsHeaders(req, toResponse(e), settings);
  }
});
