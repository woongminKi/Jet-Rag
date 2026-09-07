/**
 * `routers/payments.py` 포팅 — `/payments/subscribe/*` + `POST /billing/run`.
 *
 * ## 인증 모델이 둘이다
 * | 경로 | 인증 | 이유 |
 * |---|---|---|
 * | `/payments/subscribe/*` | 로그인 JWT | 본인 구독만 건드린다 |
 * | `/billing/run` | 공유 secret 헤더 | 호출자가 cron 이라 JWT 가 없다 |
 *
 * 원본이 `/billing/run` 을 admin 라우터에 두지 않은 이유가 이거다 — `require_admin` 은
 * owner JWT 가 없는 cron 호출자를 403 으로 막는다.
 *
 * ## 오류를 502 로 뭉친다
 * 원본은 결제 공급자 오류를 전부 `502 + 한국어 문구` 로 바꾼다. KakaoPay 응답 본문이
 * 그대로 사용자에게 나가면 안 되기 때문이다. **문구까지 원본과 같아야** 프런트가
 * 바뀌지 않는다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

import {
  approveSubscription,
  type BillingDeps,
  cancelSubscription,
  chargeDueSubscriptions,
  startSubscription,
  SubscriptionNotPendingError,
  sweepPastDue,
} from "./billing.ts";
import { getPaymentProvider } from "./kakaopay.ts";

export interface RouteResult {
  status: number;
  body: Record<string, unknown>;
}

export interface BillingSettings {
  paymentProvider: string;
  kakaopaySecretKey: string;
  kakaopayCid: string;
  billingKeyEncryptionKey: string;
  billingCronSecret: string;
  billingRedirectBase: string;
}

export interface PaymentsRouteDeps {
  client: SupabaseClient;
  settings: BillingSettings;
  nowMs?: () => number;
  /** 테스트 주입 — 실제 KakaoPay 를 부르지 않는다. */
  fetchFn?: typeof fetch;
}

/** 원본 `_ensure_enabled` — 둘 중 **하나라도** 비면 503. */
function ensureEnabled(s: BillingSettings): RouteResult | null {
  if (!s.kakaopaySecretKey || !s.billingKeyEncryptionKey) {
    return {
      status: 503,
      body: { detail: "결제 기능이 비활성 상태입니다. 잠시 후 다시 시도해 주세요." },
    };
  }
  return null;
}

function makeDeps(deps: PaymentsRouteDeps): BillingDeps {
  return {
    client: deps.client,
    provider: getPaymentProvider({
      paymentProvider: deps.settings.paymentProvider,
      kakaopaySecretKey: deps.settings.kakaopaySecretKey,
      kakaopayCid: deps.settings.kakaopayCid,
      fetchFn: deps.fetchFn,
    }),
    encryptionKey: deps.settings.billingKeyEncryptionKey,
    billingRedirectBase: deps.settings.billingRedirectBase,
    nowMs: (deps.nowMs ?? (() => Date.now()))(),
  };
}

/** `POST /payments/subscribe/ready` — 프런트가 `redirect_url` 로 사용자를 보낸다. */
export async function handleSubscribeReady(
  deps: PaymentsRouteDeps,
  userId: string,
): Promise<RouteResult> {
  const off = ensureEnabled(deps.settings);
  if (off) return off;
  try {
    const result = await startSubscription(makeDeps(deps), userId);
    return { status: 200, body: { redirect_url: result.redirect_url } };
  } catch (e) {
    console.warn(`subscribe ready 실패 (user=${userId}): ${e}`);
    return {
      status: 502,
      body: { detail: "결제창 생성에 실패했습니다. 잠시 후 다시 시도해 주세요." },
    };
  }
}

/**
 * `POST /payments/subscribe/approve?pg_token=…`
 *
 * `pg_token` 은 **쿼리 파라미터**다(KakaoPay 가 approval_url 에 붙여 보낸다).
 * 원본은 `Query(..., min_length=1)` 이라 없거나 빈 문자열이면 **422** 다.
 */
export async function handleSubscribeApprove(
  deps: PaymentsRouteDeps,
  userId: string,
  pgToken: string | null,
): Promise<RouteResult> {
  if (pgToken === null || pgToken === "") {
    return { status: 422, body: { detail: "`pg_token` 이 필요합니다." } };
  }
  const off = ensureEnabled(deps.settings);
  if (off) return off;
  try {
    await approveSubscription(makeDeps(deps), userId, pgToken);
  } catch (e) {
    if (e instanceof SubscriptionNotPendingError) {
      return {
        status: 409,
        body: { detail: "진행 중인 결제 요청이 없습니다. 다시 시도해 주세요." },
      };
    }
    console.warn(`subscribe approve 실패 (user=${userId}): ${e}`);
    return {
      status: 502,
      body: { detail: "결제 승인에 실패했습니다. 다시 시도해 주세요." },
    };
  }
  return { status: 200, body: { status: "active" } };
}

/** `POST /payments/subscribe/cancel` — 즉시 Free 강등. 데이터는 보존한다. */
export async function handleSubscribeCancel(
  deps: PaymentsRouteDeps,
  userId: string,
): Promise<RouteResult> {
  const off = ensureEnabled(deps.settings);
  if (off) return off;
  try {
    await cancelSubscription(makeDeps(deps), userId);
  } catch (e) {
    console.warn(`subscribe cancel 실패 (user=${userId}): ${e}`);
    return {
      status: 502,
      body: { detail: "구독 해지에 실패했습니다. 잠시 후 다시 시도해 주세요." },
    };
  }
  return { status: 200, body: { status: "canceled" } };
}

/** `hmac.compare_digest` — 길이가 같을 때 상수 시간으로 비교한다. */
function constantTimeEquals(a: string, b: string): boolean {
  const ab = new TextEncoder().encode(a);
  const bb = new TextEncoder().encode(b);
  if (ab.length !== bb.length) return false;
  let diff = 0;
  for (let i = 0; i < ab.length; i++) diff |= ab[i] ^ bb[i];
  return diff === 0;
}

/**
 * `POST /billing/run` — 만료 자동결제 + 7 일 grace sweep.
 *
 * 순서가 정해져 있다: **charge 먼저, sweep 나중.** 뒤집으면 이번 배치에서 결제에
 * 성공했어야 할 유저가 먼저 해지될 수 있다.
 */
export async function handleBillingRun(
  deps: PaymentsRouteDeps,
  secretHeader: string,
): Promise<RouteResult> {
  const s = deps.settings;
  if (!s.billingCronSecret) {
    return {
      status: 503,
      body: { detail: "billing cron 이 비활성 상태입니다 (JETRAG_BILLING_CRON_SECRET 미설정)." },
    };
  }
  if (!constantTimeEquals(secretHeader, s.billingCronSecret)) {
    return { status: 401, body: { detail: "cron secret 불일치" } };
  }
  const off = ensureEnabled(s);
  if (off) return off;

  const d = makeDeps(deps);
  const charge = await chargeDueSubscriptions(d);
  const sweep = await sweepPastDue(d);
  return {
    status: 200,
    body: { charged: charge.charged, failed: charge.failed, canceled: sweep.canceled },
  };
}
