/**
 * `api/app/services/budget_guard.py` 포팅 — vision 비용·페이지 한도 가드.
 *
 * 네 가지 한도가 있고 성격이 다르다:
 * - `doc`         : 문서 하나의 vision 누적 비용
 * - `daily`       : UTC 자정~현재 누적 (자정에 리셋)
 * - `24h_sliding` : 현재-24h~현재 누적 (자정 무관 rolling — 자정 넘겨 우회하는 걸 막는다)
 * - `page_cap`    : 문서 하나에서 vision 호출한 페이지 수 (DB 안 본다, 비용과 직교)
 *
 * ## 실패는 통과시킨다
 * DB 조회가 깨지면 `allowed=true` 다. 인제스트를 멈추는 것보다 한도를 못 재는 편이
 * 낫다는 원본의 판단이고, 그대로 옮겼다. 대신 첫 1회만 warn 한다.
 *
 * ## 비용 합산이 SUM 쿼리가 아니다
 * PostgREST 에 SUM 이 없어 원본은 row 를 다 받아 클라이언트에서 더한다. 그대로 옮겼다 —
 * 여기서 집계 방식을 바꾸면 같은 데이터로 다른 값이 나올 수 있다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";
import { pyFloat, pyFormatF } from "../pynum.ts";

export type BudgetScope =
  | "doc"
  | "daily"
  | "24h_sliding"
  | "page_cap"
  | "query_decomposition";

export interface BudgetStatus {
  allowed: boolean;
  /** 누적 사용량(USD). 0 = 미측정(graceful). */
  usedUsd: number;
  capUsd: number;
  scope: BudgetScope;
  /** 한국어 — 로그 + `warnings[]` 를 통해 사용자에게도 보인다. */
  reason: string;
}

/** D5 — 의미가 24시간으로 고정이라 ENV 로 안 뺀다. */
const SLIDING_WINDOW_HOURS = 24;

/** 원본 `is_disabled()` — `"1"` 만 인식하고 나머지는 전부 활성(보수적). */
export function isDisabled(env: Record<string, string | undefined>): boolean {
  return env["JETRAG_BUDGET_GUARD_DISABLE"] === "1";
}

let firstWarnLogged = false;
function warnFirst(msg: string): void {
  if (!firstWarnLogged) {
    firstWarnLogged = true;
    console.warn(`${msg} — 마이그 014(vision_usage_log_enhanced) 적용 후 자동 회복.`);
  } else {
    console.debug(msg);
  }
}

/** 테스트용 — 첫 warn 플래그 초기화. */
export function resetFirstWarnForTest(): void {
  firstWarnLogged = false;
}

/** 원본 `_sum_cost_rows` — None / 변환 불가 값은 건너뛴다. */
export function sumCostRows(rows: Array<Record<string, unknown>>): number {
  let total = 0.0;
  for (const r of rows) {
    const cost = r["estimated_cost"];
    if (cost === null || cost === undefined) continue;
    const v = pyFloat(cost);
    if (v === null) continue;
    total += v;
  }
  return total;
}

async function sumCost(
  client: SupabaseClient,
  build: (q: ReturnType<SupabaseClient["from"]>) => unknown,
  label: string,
): Promise<number | null> {
  try {
    // deno-lint-ignore no-explicit-any
    const q = (client.from("vision_usage_log").select("estimated_cost,success")) as any;
    const { data, error } = await (build(q) as Promise<
      { data: Array<Record<string, unknown>> | null; error: unknown }
    >);
    if (error) throw error;
    return sumCostRows(data ?? []);
  } catch (err) {
    warnFirst(`budget_guard ${label} SUM 실패 (graceful): ${err}`);
    return null;
  }
}

/** 오늘 UTC 자정 ISO8601. Python `datetime.combine(date, time.min, utc).isoformat()`. */
export function utcMidnightIso(nowMs: number): string {
  const d = new Date(nowMs);
  const midnight = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());
  // Python 은 `+00:00` 을 붙이고 마이크로초가 0 이면 생략한다.
  return `${new Date(midnight).toISOString().replace(/\.\d{3}Z$/, "")}+00:00`;
}

/** now - 24h ISO8601. Python `datetime.isoformat()` 은 마이크로초 6자리를 쓴다. */
export function slidingCutoffIso(nowMs: number): string {
  const cutoff = new Date(nowMs - SLIDING_WINDOW_HOURS * 3600_000);
  const iso = cutoff.toISOString(); // ...THH:MM:SS.mmmZ
  const ms = iso.slice(20, 23);
  return `${iso.slice(0, 19)}.${ms}000+00:00`;
}

export interface GuardDeps {
  client: SupabaseClient;
  env: Record<string, string | undefined>;
  /** 테스트 결정성을 위한 주입점. 원본의 `now` 인자와 같은 자리. */
  nowMs: number;
}

export async function checkDocBudget(
  deps: GuardDeps,
  opts: { docId: string; capUsd: number },
): Promise<BudgetStatus> {
  const { docId, capUsd } = opts;
  if (isDisabled(deps.env)) {
    return { allowed: true, usedUsd: 0.0, capUsd, scope: "doc", reason: "가드 비활성 (ENV)" };
  }
  if (!docId) {
    return {
      allowed: true, usedUsd: 0.0, capUsd, scope: "doc",
      reason: "doc_id 미지정 (단독 이미지 호출)",
    };
  }
  const used = await sumCost(
    deps.client,
    // deno-lint-ignore no-explicit-any
    (q) => (q as any).eq("doc_id", docId).eq("success", true),
    "doc",
  );
  if (used === null) {
    return {
      allowed: true, usedUsd: 0.0, capUsd, scope: "doc",
      reason: "DB 조회 실패 — 가드 graceful (allowed)",
    };
  }
  if (used > capUsd) {
    return {
      allowed: false, usedUsd: used, capUsd, scope: "doc",
      reason: `문서당 비용 한도 초과 ` +
        `($${pyFormatF(used, 4)} > $${pyFormatF(capUsd, 4)}) — vision 보강 일부 생략`,
    };
  }
  return { allowed: true, usedUsd: used, capUsd, scope: "doc", reason: "문서 한도 내" };
}

export async function checkDailyBudget(
  deps: GuardDeps,
  opts: { capUsd: number },
): Promise<BudgetStatus> {
  const { capUsd } = opts;
  if (isDisabled(deps.env)) {
    return { allowed: true, usedUsd: 0.0, capUsd, scope: "daily", reason: "가드 비활성 (ENV)" };
  }
  const midnight = utcMidnightIso(deps.nowMs);
  const used = await sumCost(
    deps.client,
    // deno-lint-ignore no-explicit-any
    (q) => (q as any).gte("called_at", midnight).eq("success", true),
    "daily",
  );
  if (used === null) {
    return {
      allowed: true, usedUsd: 0.0, capUsd, scope: "daily",
      reason: "DB 조회 실패 — 가드 graceful (allowed)",
    };
  }
  if (used > capUsd) {
    return {
      allowed: false, usedUsd: used, capUsd, scope: "daily",
      reason: `일일 비용 한도 초과 ` +
        `($${pyFormatF(used, 4)} > $${pyFormatF(capUsd, 4)}) — vision 보강 일부 생략`,
    };
  }
  return { allowed: true, usedUsd: used, capUsd, scope: "daily", reason: "일일 한도 내" };
}

export async function check24hSlidingBudget(
  deps: GuardDeps,
  opts: { capUsd: number },
): Promise<BudgetStatus> {
  const { capUsd } = opts;
  if (isDisabled(deps.env)) {
    return {
      allowed: true, usedUsd: 0.0, capUsd, scope: "24h_sliding", reason: "가드 비활성 (ENV)",
    };
  }
  const cutoff = slidingCutoffIso(deps.nowMs);
  const used = await sumCost(
    deps.client,
    // deno-lint-ignore no-explicit-any
    (q) => (q as any).gte("called_at", cutoff).eq("success", true),
    "24h_sliding",
  );
  if (used === null) {
    return {
      allowed: true, usedUsd: 0.0, capUsd, scope: "24h_sliding",
      reason: "DB 조회 실패 — 가드 graceful (allowed)",
    };
  }
  if (used > capUsd) {
    return {
      allowed: false, usedUsd: used, capUsd, scope: "24h_sliding",
      reason: `최근 24시간 비용 한도 초과 ` +
        `($${pyFormatF(used, 4)} > $${pyFormatF(capUsd, 4)}) — vision 보강 일부 생략`,
    };
  }
  return {
    allowed: true, usedUsd: used, capUsd, scope: "24h_sliding", reason: "24시간 한도 내",
  };
}

/**
 * doc → daily → 24h_sliding 순으로 좁은 범위부터 검사. 하나라도 걸리면 그 상태를 반환.
 *
 * 셋 다 통과하면 **가장 마지막에 본 것**을 돌려준다 — sliding 을 검사했으면 sliding,
 * 아니면 daily. (`doc` 상태는 통과 시 버려진다. 원본 그대로다.)
 */
export async function checkCombined(
  deps: GuardDeps,
  opts: {
    docId: string;
    docCapUsd: number;
    dailyCapUsd: number;
    /** null 이면 sliding 검사 skip (D4 호환). */
    sliding24hCapUsd?: number | null;
  },
): Promise<BudgetStatus> {
  if (isDisabled(deps.env)) {
    return {
      allowed: true, usedUsd: 0.0, capUsd: opts.docCapUsd, scope: "doc",
      reason: "가드 비활성 (ENV)",
    };
  }
  if (opts.docId) {
    const docStatus = await checkDocBudget(deps, {
      docId: opts.docId, capUsd: opts.docCapUsd,
    });
    if (!docStatus.allowed) return docStatus;
  }
  const dailyStatus = await checkDailyBudget(deps, { capUsd: opts.dailyCapUsd });
  if (!dailyStatus.allowed) return dailyStatus;

  if (opts.sliding24hCapUsd !== undefined && opts.sliding24hCapUsd !== null) {
    return await check24hSlidingBudget(deps, { capUsd: opts.sliding24hCapUsd });
  }
  return dailyStatus;
}

/**
 * S2 D2 — 문서 하나에서 vision 호출한 페이지 수 한도. **DB 를 안 본다** — 호출부의
 * in-memory 카운터를 받아 비교만 하므로 매 페이지 불러도 latency 0 이다.
 *
 * `pageCap <= 0` 이면 무한(회복 토글).
 */
export function checkDocPageCap(
  env: Record<string, string | undefined>,
  opts: { calledPages: number; pageCap: number },
): BudgetStatus {
  const { calledPages, pageCap } = opts;
  if (isDisabled(env)) {
    return {
      allowed: true, usedUsd: 0.0, capUsd: pageCap, scope: "page_cap",
      reason: "가드 비활성 (ENV)",
    };
  }
  if (pageCap <= 0) {
    return {
      allowed: true, usedUsd: calledPages, capUsd: 0.0, scope: "page_cap",
      reason: "페이지 cap 무한 (ENV 0)",
    };
  }
  if (calledPages >= pageCap) {
    return {
      allowed: false, usedUsd: calledPages, capUsd: pageCap, scope: "page_cap",
      reason: `문서당 vision 페이지 한도 도달 ` +
        `(${calledPages}/${pageCap}) — vision 보강 일부 생략`,
    };
  }
  return {
    allowed: true, usedUsd: calledPages, capUsd: pageCap, scope: "page_cap",
    reason: "페이지 한도 내",
  };
}
