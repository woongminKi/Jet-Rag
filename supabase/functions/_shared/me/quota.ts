/**
 * 플랜·사용량 조회 — `services/quota.py` 중 `/me/*` 가 쓰는 부분 포팅.
 *
 * ## 전부 fail-open 이다
 * DB 가 흔들려도 사용자를 막지 않는다. 실패 시 `getEffectivePlan` 은 `null`,
 * `countActiveDocuments` 는 `null`, `getTodaysCount` 는 `0`, `getSubscriptionView` 는
 * free·none 을 돌려준다. **이 값들로 제한을 걸면 안 된다** — 장애가 "사용량 없음" 으로
 * 보이기 때문이다(원본 주석의 경고를 그대로 옮긴다).
 *
 * ## 날짜 기준이 `/stats` 와 다르다
 * `getTodaysCount` 는 **UTC 날짜**를 쓴다. `/stats` 의 월·주 집계와 vision 사용량은
 * KST 다. 한 응답 안에 두 기준이 섞여 있는 게 원본 상태이고, 여기서 통일하면 값이 바뀐다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

/** `active` 와 `past_due` 만 유효 — `past_due` 는 유예 기간이라 플랜을 유지한다. */
const EFFECTIVE_STATUSES = new Set(["active", "past_due"]);

export interface PlanLimits {
  code: string;
  max_documents: number;
  answers_per_day: number;
}

export interface SubscriptionView {
  plan_code: string;
  /** `active` · `past_due` · `canceled` · `none`(행 없음). */
  status: string;
  current_period_end: string | null;
}

const FREE_VIEW: SubscriptionView = {
  plan_code: "free",
  status: "none",
  current_period_end: null,
};

/** 구독 표시용. 행이 없거나 조회가 실패하면 free·none. */
export async function getSubscriptionView(
  client: SupabaseClient,
  userId: string,
): Promise<SubscriptionView> {
  try {
    const { data, error } = await client
      .from("subscriptions")
      .select("plan_code, status, current_period_end")
      .eq("user_id", userId)
      .limit(1);
    if (error) throw new Error(error.message);
    const rows = (data ?? []) as Record<string, unknown>[];
    if (rows.length === 0) return { ...FREE_VIEW };
    const r = rows[0];
    return {
      // 원본은 `r.get("plan_code", "free")` — 키가 **없을 때만** 기본값이다.
      // 값이 `null` 이면 `null` 이 그대로 나간다.
      plan_code: ("plan_code" in r ? r.plan_code : "free") as string,
      status: ("status" in r ? r.status : "none") as string,
      current_period_end: (r.current_period_end ?? null) as string | null,
    };
  } catch (e) {
    console.warn(`구독 조회 실패 (user=${userId}):`, e);
    return { ...FREE_VIEW };
  }
}

/**
 * 유효 플랜 한도. 조회 실패는 `null`(fail-open).
 *
 * 구독 행이 없거나 `canceled` 면 `free`, `active`/`past_due` 면 그 `plan_code` 를 쓴다.
 * 그 코드로 `plans` 를 한 번 더 조회하는데, **거기 행이 없으면 `null`** 이다 —
 * 플랜 테이블이 비어 있으면 조용히 free 로 떨어지지 않고 호출부가 503 을 낸다.
 */
export async function getEffectivePlan(
  client: SupabaseClient,
  userId: string,
): Promise<PlanLimits | null> {
  try {
    const { data: subData, error: subErr } = await client
      .from("subscriptions")
      .select("plan_code, status")
      .eq("user_id", userId)
      .limit(1);
    if (subErr) throw new Error(subErr.message);
    const subRows = (subData ?? []) as Record<string, unknown>[];

    let code = "free";
    if (subRows.length > 0 && EFFECTIVE_STATUSES.has(subRows[0].status as string)) {
      code = subRows[0].plan_code as string;
    }

    const { data: planData, error: planErr } = await client
      .from("plans")
      .select("code, max_documents, answers_per_day")
      .eq("code", code)
      .limit(1);
    if (planErr) throw new Error(planErr.message);
    const planRows = (planData ?? []) as Record<string, unknown>[];
    if (planRows.length === 0) {
      console.warn(`plans 테이블에 code=${code} 없음 — quota fail-open`);
      return null;
    }
    const row = planRows[0];
    return {
      code: row.code as string,
      max_documents: Math.trunc(Number(row.max_documents)),
      answers_per_day: Math.trunc(Number(row.answers_per_day)),
    };
  } catch (e) {
    console.warn(`플랜 조회 실패 — quota fail-open (user=${userId}):`, e);
    return null;
  }
}

/** 보유 문서 수(삭제 제외). 실패는 `null`. `count` 질의라 1,000 행 상한과 무관하다. */
export async function countActiveDocuments(
  client: SupabaseClient,
  userId: string,
): Promise<number | null> {
  try {
    // 원본과 같은 모양 — `limit(1)` 로 payload 를 줄이되 `count` 는 전체를 받는다.
    // (`head: true` 로 바꾸면 HTTP 메서드가 HEAD 가 돼 원본과 다른 요청이 나간다.)
    const { count, error } = await client
      .from("documents")
      .select("id", { count: "exact" })
      .eq("user_id", userId)
      .is("deleted_at", null)
      .limit(1);
    if (error) throw new Error(error.message);
    return Math.trunc(Number(count ?? 0));
  } catch (e) {
    console.warn(`문서 수 카운트 실패 — quota fail-open (user=${userId}):`, e);
    return null;
  }
}

/** `YYYY-MM-DD` (**UTC**). `/stats` 의 KST 기준과 다르다 — 원본 그대로다. */
export function utcTodayIso(nowMs: number): string {
  return new Date(nowMs).toISOString().slice(0, 10);
}

/** 금일 사용량(표시용). 실패는 `0` — **제한 판정에 쓰면 안 된다.** */
export async function getTodaysCount(
  client: SupabaseClient,
  userKey: string,
  metric: string,
  nowMs: number,
): Promise<number> {
  try {
    const { data, error } = await client
      .from("usage_counters")
      .select("count")
      .eq("user_key", userKey)
      .eq("metric", metric)
      .eq("period_date", utcTodayIso(nowMs))
      .limit(1);
    if (error) throw new Error(error.message);
    const rows = (data ?? []) as { count: number }[];
    return rows.length ? Math.trunc(Number(rows[0].count)) : 0;
  } catch (e) {
    console.warn(`금일 사용량 조회 실패 — 0 반환 (key=${userKey}):`, e);
    return 0;
  }
}
