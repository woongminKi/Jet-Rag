/**
 * `services/billing.py` 포팅 — 카카오페이 정기결제 서비스 로직.
 *
 * ```
 * lifecycle : startSubscription(ready) → approveSubscription(SID 저장·active)
 * 배치      : chargeDueSubscriptions(만료 자동결제) + sweepPastDue(7일 grace 후 canceled)
 * 해지      : cancelSubscription(KakaoPay inactive + canceled)
 * 상태 머신  : active → (결제 실패) past_due → (7일) canceled  ※ 데이터는 보존
 * ```
 *
 * ## 여기서 지켜야 하는 성질 셋
 * 1. **유저별 격리** — 1 건이 실패해도 나머지는 계속 돈다. 그래서 루프 안 모든 DB 호출에
 *    개별 try 가 붙어 있다. 원본 그대로다.
 * 2. **멱등성** — 결제는 성공했는데 기간 갱신이 실패할 수 있다. 그때 다음 배치가 다시
 *    청구하면 **이중 청구**다. `payment_history` 의 `charge_success` 마커를
 *    `detail = period_key` 로 남겨 다음 배치가 건너뛴다.
 * 3. **grace clock 을 리셋하지 않는다** — `past_due_since` 는 **최초 실패만** 기록한다.
 *    매번 덮어쓰면 7 일이 영원히 안 온다.
 *
 * ## 실패 종류마다 처리가 다르다 — 뭉치면 안 된다
 * | 실패 | 처리 | 이유 |
 * |---|---|---|
 * | `billing_key` 없음 | `past_due` | 조용한 무한 skip 방지 |
 * | SID 복호화 실패 | **skip only** (past_due 아님) | 설정 오류다. grace clock 을 건드리면 안 된다 |
 * | 결제 거절·네트워크 | `past_due` | 진짜 결제 실패 |
 * | 결제 성공 후 DB 오류 | 로그만 | 멱등 마커가 다음 배치를 막는다 |
 */

import type { SupabaseClient } from "@supabase/supabase-js";

import { pyStrError } from "../pyerror.ts";
import { fernetDecrypt, fernetEncrypt } from "./fernet.ts";
import type { KakaoPayClient, ReadyResult } from "./kakaopay.ts";
import { addOneMonth, formatIso, minusDays, parseIso, type PyDateParts, utcParts, ymd } from "./pydate.ts";

const PRICE_KRW = 6900;
/** 결제 실패 후 canceled 까지 grace (결정 이력 #7). */
const GRACE_DAYS = 7;

export class SubscriptionNotPendingError extends Error {}

export interface ChargeReport {
  charged: number;
  failed: number;
  user_ids_charged: string[];
  user_ids_failed: string[];
}

export interface SweepReport {
  canceled: number;
  user_ids: string[];
}

export interface BillingDeps {
  client: SupabaseClient;
  provider: KakaoPayClient;
  /** `JETRAG_BILLING_KEY_ENCRYPTION_KEY`. 빈 값이면 암호화 호출이 던진다. */
  encryptionKey: string;
  billingRedirectBase: string;
  nowMs: number;
}

/** 원본 `_amount_for` — 청구가 일어난 이벤트에만 금액이 붙는다. */
function amountFor(event: string): number | null {
  return event === "subscribe" || event === "charge_success" ? PRICE_KRW : null;
}

/**
 * 원본 `_insert_history` — 실패하면 **던진다**(호출자가 처리). 멱등 마커용이다.
 *
 * `detail[:500] or None` — Python 슬라이싱은 **코드포인트** 기준이고, 빈 문자열은
 * `None` 이 된다. 멱등 조회가 `detail` 로 매칭하므로 여기가 어긋나면 마커를 못 찾는다.
 */
async function insertHistory(
  client: SupabaseClient,
  userId: string,
  event: string,
  detail: string,
): Promise<void> {
  const cut = [...detail].slice(0, 500).join("");
  const { error } = await client.from("payment_history").insert({
    user_id: userId,
    event,
    amount_krw: amountFor(event),
    detail: cut === "" ? null : cut,
  });
  if (error) throw new Error(error.message);
}

/** 원본 `_log_history` — best-effort. 실패해도 흐름을 막지 않는다(비-멱등 이벤트용). */
async function logHistory(
  client: SupabaseClient,
  userId: string,
  event: string,
  detail = "",
): Promise<void> {
  try {
    await insertHistory(client, userId, event, detail);
  } catch (e) {
    console.warn(`payment_history 기록 실패 (user=${userId}, event=${event}): ${e}`);
  }
}

/** 원본 `_redirect_urls`. `approval_url` 에 KakaoPay 가 `?pg_token=` 을 붙인다. */
export function redirectUrls(base: string): [string, string, string] {
  const b = base.replace(/\/+$/, "");
  return [`${b}/billing/success`, `${b}/billing/cancel`, `${b}/billing/fail`];
}

/**
 * 원본 `start_subscription` — ready 호출 후 `tid` 를 `pending_tid` 에 보관.
 *
 * **기존 구독자(active/past_due)가 재클릭해도 `status`·`plan_code`·`billing_key` 를
 * 건드리지 않는다.** 재클릭 한 번으로 Pro 접근이 끊기는 사고를 막으려는 것이다
 * (W5 코드리뷰 CRITICAL). 신규 유저만 미활성 placeholder 행을 만든다.
 */
export async function startSubscription(
  deps: BillingDeps,
  userId: string,
): Promise<ReadyResult> {
  const [approvalUrl, cancelUrl, failUrl] = redirectUrls(deps.billingRedirectBase);
  const result = await deps.provider.ready({
    partnerOrderId: userId,
    partnerUserId: userId,
    approvalUrl,
    cancelUrl,
    failUrl,
  });

  const nowIso = formatIso(utcParts(deps.nowMs));
  const { data, error } = await deps.client
    .from("subscriptions").select("status").eq("user_id", userId).limit(1);
  if (error) throw new Error(error.message);
  const existing = (data ?? []) as unknown[];

  if (existing.length > 0) {
    const { error: e } = await deps.client
      .from("subscriptions")
      .update({ pending_tid: result.tid, updated_at: nowIso })
      .eq("user_id", userId);
    if (e) throw new Error(e.message);
  } else {
    const { error: e } = await deps.client.from("subscriptions").insert({
      user_id: userId,
      plan_code: "free",
      status: "canceled",
      pending_tid: result.tid,
      updated_at: nowIso,
    });
    if (e) throw new Error(e.message);
  }
  return result;
}

/** 원본 `approve_subscription` — `pending_tid` 로 승인 → SID 암호화 저장 + active. */
export async function approveSubscription(
  deps: BillingDeps,
  userId: string,
  pgToken: string,
): Promise<void> {
  const { data, error } = await deps.client
    .from("subscriptions").select("pending_tid").eq("user_id", userId).limit(1);
  if (error) throw new Error(error.message);
  const row = ((data ?? []) as { pending_tid?: string | null }[])[0];
  const tid = row?.pending_tid;
  if (!tid) {
    throw new SubscriptionNotPendingError(`진행 중인 결제 요청 없음 (user=${userId})`);
  }

  const approved = await deps.provider.approve({
    tid,
    partnerOrderId: userId,
    partnerUserId: userId,
    pgToken,
  });

  const now = utcParts(deps.nowMs);
  const periodEnd = addOneMonth(now);
  const { error: e } = await deps.client.from("subscriptions").update({
    plan_code: "pro",
    status: "active",
    billing_key: await fernetEncrypt(deps.encryptionKey, approved.sid),
    current_period_end: formatIso(periodEnd),
    pending_tid: null,
    past_due_since: null,
    updated_at: formatIso(now),
  }).eq("user_id", userId);
  if (e) throw new Error(e.message);
  await logHistory(deps.client, userId, "subscribe", "구독 등록 완료");
}

/** 원본 `_mark_past_due` — `past_due_since` 는 **최초 실패만** 기록한다. */
async function markPastDue(
  client: SupabaseClient,
  userId: string,
  row: Record<string, unknown>,
  atIso: string,
  detail: string,
): Promise<void> {
  const update: Record<string, unknown> = { status: "past_due", updated_at: atIso };
  if (!row["past_due_since"]) update["past_due_since"] = atIso;
  const { error } = await client.from("subscriptions").update(update).eq("user_id", userId);
  if (error) throw new Error(error.message);
  await logHistory(client, userId, "charge_failed", detail);
}

/**
 * 원본 `_already_charged` — 이번 주기에 `charge_success` 마커가 있으면 `true`.
 *
 * **조회 실패는 `false`** 다. 마커 부재와 같게 취급해 재청구를 시도한다 — 원본이 그렇게
 * 정했다(주석: "보수적으로 재청구 시도"). 반대로 두면 조회 장애 때 청구가 통째로 멈춘다.
 */
async function alreadyCharged(
  client: SupabaseClient,
  userId: string,
  periodKey: string | null,
): Promise<boolean> {
  if (!periodKey) return false;
  try {
    const { data, error } = await client
      .from("payment_history")
      .select("id")
      .eq("user_id", userId)
      .eq("event", "charge_success")
      .eq("detail", periodKey)
      .limit(1);
    if (error) throw new Error(error.message);
    return ((data ?? []) as unknown[]).length > 0;
  } catch (e) {
    console.warn(`멱등 조회 실패 (user=${userId}): ${e}`);
    return false;
  }
}

interface DueRow {
  user_id: string;
  billing_key?: string | null;
  status?: string;
  current_period_end?: string | null;
  past_due_since?: string | null;
}

/** 원본 `charge_due_subscriptions` — 만료 도래 구독 자동결제. */
export async function chargeDueSubscriptions(deps: BillingDeps): Promise<ChargeReport> {
  const at = utcParts(deps.nowMs);
  const atIso = formatIso(at);

  const { data, error } = await deps.client
    .from("subscriptions")
    .select("user_id, billing_key, status, current_period_end, past_due_since")
    .in("status", ["active", "past_due"])
    .lte("current_period_end", atIso);
  if (error) throw new Error(error.message);
  const due = (data ?? []) as DueRow[];

  const charged: string[] = [];
  const failed: string[] = [];

  for (const row of due) {
    const userId = String(row.user_id);
    const periodKey = row.current_period_end ?? null;
    const enc = row.billing_key;

    // 1) 선결제 검증 — billing_key 가 없으면 past_due. 조용히 계속 건너뛰면 안 된다.
    if (!enc) {
      try {
        await markPastDue(
          deps.client,
          userId,
          row as unknown as Record<string, unknown>,
          atIso,
          "billing_key 없음",
        );
      } catch (e) {
        console.error(`past_due 처리 실패 (user=${userId}): ${e}`);
      }
      failed.push(userId);
      continue;
    }

    // 2) 결제 — 이번 주기에 이미 성공했으면 건너뛴다(멱등).
    if (!(await alreadyCharged(deps.client, userId, periodKey))) {
      let sid: string;
      try {
        sid = await fernetDecrypt(deps.encryptionKey, enc);
      } catch (e) {
        // 복호화 실패 = 설정 오류다. **grace clock 을 건드리지 않는다.**
        console.error(`SID 복호화 실패 — skip (user=${userId}): ${e}`);
        failed.push(userId);
        continue;
      }
      try {
        await deps.provider.subscribe({
          sid,
          partnerOrderId: `${userId}-${ymd(at)}`,
          partnerUserId: userId,
        });
      } catch (e) {
        try {
          await markPastDue(
            deps.client,
            userId,
            row as unknown as Record<string, unknown>,
            atIso,
            [...pyStrError(e)].slice(0, 400).join(""),
          );
        } catch (inner) {
          console.error(`past_due 처리 실패 (user=${userId}): ${inner}`);
        }
        failed.push(userId);
        continue;
      }
      // 결제 성공 — 멱등 마커. 실패해도 **결제는 이미 됐다**(수동 확인 로그).
      try {
        await insertHistory(deps.client, userId, "charge_success", periodKey ?? "");
      } catch (e) {
        console.error(
          `결제 성공했으나 이력 기록 실패 (user=${userId}, period=${periodKey}): ${e} — 수동 확인 필요`,
        );
      }
    }

    // 3) 후처리 — 기간 갱신 + active 복귀. 실패해도 재청구되지 않는다.
    try {
      const base: PyDateParts = parseIso(periodKey) ?? at;
      const newEnd = addOneMonth(base);
      const { error: e } = await deps.client.from("subscriptions").update({
        status: "active",
        current_period_end: formatIso(newEnd),
        past_due_since: null,
        updated_at: atIso,
      }).eq("user_id", userId);
      if (e) throw new Error(e.message);
    } catch (e) {
      console.error(
        `결제 성공했으나 기간 갱신 실패 (user=${userId}): ${e} — 다음 배치가 멱등 처리`,
      );
    }
    charged.push(userId);
  }

  console.info(`billing charge — 성공 ${charged.length}, 실패 ${failed.length}`);
  return {
    charged: charged.length,
    failed: failed.length,
    user_ids_charged: charged,
    user_ids_failed: failed,
  };
}

/** 원본 `sweep_past_due` — `past_due_since` 가 7 일을 넘으면 canceled(Free 강등). */
export async function sweepPastDue(deps: BillingDeps): Promise<SweepReport> {
  const at = utcParts(deps.nowMs);
  const atIso = formatIso(at);
  const threshold = formatIso(minusDays(at, GRACE_DAYS));

  const { data, error } = await deps.client
    .from("subscriptions")
    .select("user_id, billing_key, past_due_since")
    .eq("status", "past_due")
    .lte("past_due_since", threshold);
  if (error) throw new Error(error.message);
  const overdue = (data ?? []) as DueRow[];

  const canceled: string[] = [];
  for (const row of overdue) {
    const userId = String(row.user_id);
    try {
      const enc = row.billing_key;
      if (enc) {
        try {
          await deps.provider.inactivate(await fernetDecrypt(deps.encryptionKey, enc));
        } catch (e) {
          // 원격 실패해도 **로컬 canceled 는 진행한다** — 과금을 계속할 수 없다.
          console.warn(`SID inactive 실패 (user=${userId}): ${e}`);
        }
      }
      const { error: e } = await deps.client
        .from("subscriptions")
        .update({ status: "canceled", updated_at: atIso })
        .eq("user_id", userId);
      if (e) throw new Error(e.message);
      canceled.push(userId);
      await logHistory(deps.client, userId, "cancel", "7일 grace 초과 자동 해지");
    } catch (e) {
      console.error(`sweep 처리 실패 (user=${userId}): ${e}`);
      continue;
    }
  }

  console.info(`billing sweep — ${canceled.length}건 canceled`);
  return { canceled: canceled.length, user_ids: canceled };
}

/** 원본 `cancel_subscription` — 즉시 Free 강등. 데이터는 지우지 않는다. */
export async function cancelSubscription(deps: BillingDeps, userId: string): Promise<void> {
  const { data, error } = await deps.client
    .from("subscriptions").select("billing_key").eq("user_id", userId).limit(1);
  if (error) throw new Error(error.message);
  const enc = ((data ?? []) as { billing_key?: string | null }[])[0]?.billing_key;
  if (enc) {
    try {
      await deps.provider.inactivate(await fernetDecrypt(deps.encryptionKey, enc));
    } catch (e) {
      // 원격 실패해도 로컬 해지는 진행한다.
      console.warn(`해지 시 SID inactive 실패 (user=${userId}): ${e}`);
    }
  }
  const { error: e } = await deps.client
    .from("subscriptions")
    .update({ status: "canceled", updated_at: formatIso(utcParts(deps.nowMs)) })
    .eq("user_id", userId);
  if (e) throw new Error(e.message);
  await logHistory(deps.client, userId, "cancel", "사용자 요청 해지");
}
