/**
 * `/admin/*` 응답 조립 — `api/app/routers/admin.py` 의 핸들러 본문 포팅.
 *
 * ## DB 상한을 **손대지 않는다**
 * 원본은 `search_metrics_log` / `answer_feedback` 을 `limit` 없이 읽는다. PostgREST 는
 * 1,000 행에서 조용히 자르므로 원본도 그 지점부터 잘린 통계를 낸다. 여기서 페이지네이션을
 * 넣으면 **더 정확해지지만 원본과 갈린다** — 이관 중에는 재현이 우선이다.
 * (2026-09-06 실측: 30 일치가 627 행이라 아직 상한 밖. 정렬이 `desc` 라 잘려도 최근 것이
 * 남는 것까지 양쪽이 같다.)
 *
 * ## 예외는 삼킨다
 * 마이그 006 / 011 미적용 환경에서 테이블이 없을 수 있어, 원본은 조회 실패를 통째로 잡고
 * `error_code="migrations_pending"` + 빈 집계를 낸다. 그 모양 그대로 옮겼다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

import { pyIsoUtc } from "../pytime.ts";
import { makeLiteralPicker } from "../pydantic_errors.ts";
import { classifyQueryType, QUERY_TYPE_LABELS } from "../query_classifier.ts";
import {
  avgLatencyMs,
  buildCommentAnalysis,
  buildDailyBuckets,
  buildFeedbackDailyBuckets,
  buildQueryTypeDistribution,
  COMMENT_CATEGORIES,
  extractFailedSamples,
  type FeedbackRow,
  type MetricsRow,
  RANGE_TO_DAYS,
  satisfactionRate,
  successRate,
} from "./aggregate.ts";

export const ADMIN_RANGES = ["7d", "14d", "30d"] as const;

export interface AdminDeps {
  client: SupabaseClient;
  /** 시각 주입 — 대조에서 고정한다. */
  now?: () => number;
}

/** `range` 쿼리 검증. 허용 밖이면 pydantic 과 같은 422 배열. */
export function validateRange(
  sp: URLSearchParams,
): { ok: true; range: string } | { ok: false; detail: unknown[] } {
  const { pick, errors } = makeLiteralPicker(sp);
  const range = pick("range", ADMIN_RANGES, "7d");
  if (errors.length) return { ok: false, detail: errors };
  return { ok: true, range };
}

/** ISO(UTC) 문자열 — `datetime.now(utc) - timedelta(days=n)` 과 같은 형식. */
function sinceIso(nowMs: number, days: number): string {
  return pyIsoUtc(nowMs - days * 86_400_000);
}

// ------------------------------------------------------------ queries/stats

export async function buildQueriesStats(
  range: string,
  deps: AdminDeps,
): Promise<Record<string, unknown>> {
  const nowMs = (deps.now ?? Date.now)();
  // 원본은 `generated_at` 을 먼저 만들고 `since` 를 위해 `now()` 를 다시 부른다.
  // 두 값이 미세하게 다르지만 관측 가능한 차이가 아니라 한 번만 읽는다.
  const generatedAt = pyIsoUtc(nowMs);
  const days = RANGE_TO_DAYS[range];

  let rows: MetricsRow[];
  try {
    const { data, error } = await deps.client
      .from("search_metrics_log")
      .select("recorded_at, took_ms, fused, fallback_reason, query_text")
      .gte("recorded_at", sinceIso(nowMs, days))
      .order("recorded_at", { ascending: false });
    if (error) throw new Error(error.message);
    rows = (data ?? []) as MetricsRow[];
  } catch (e) {
    console.warn("admin_queries_stats DB graceful skip:", e);
    return {
      range,
      daily: [],
      query_type_distribution: {},
      failed_samples: [],
      total_queries: 0,
      success_rate: null,
      avg_latency_ms: null,
      error_code: "migrations_pending",
      generated_at: generatedAt,
    };
  }

  return {
    range,
    daily: buildDailyBuckets(rows, days, nowMs),
    query_type_distribution: buildQueryTypeDistribution(
      rows,
      QUERY_TYPE_LABELS,
      (q) => classifyQueryType(q),
    ),
    failed_samples: extractFailedSamples(rows),
    total_queries: rows.length,
    success_rate: successRate(rows),
    avg_latency_ms: avgLatencyMs(rows),
    // `classify_unavailable` 은 deprecated — 원본도 항상 null 을 넣는다.
    error_code: null,
    generated_at: generatedAt,
  };
}

// ----------------------------------------------------------- feedback/stats

export async function buildFeedbackStats(
  range: string,
  deps: AdminDeps,
): Promise<Record<string, unknown>> {
  const nowMs = (deps.now ?? Date.now)();
  const generatedAt = pyIsoUtc(nowMs);
  const days = RANGE_TO_DAYS[range];

  let rows: FeedbackRow[];
  try {
    const { data, error } = await deps.client
      .from("answer_feedback")
      .select("created_at, helpful, comment, query")
      .gte("created_at", sinceIso(nowMs, days))
      .order("created_at", { ascending: false });
    if (error) throw new Error(error.message);
    rows = (data ?? []) as FeedbackRow[];
  } catch (e) {
    console.warn("admin_feedback_stats DB graceful skip:", e);
    const empty: Record<string, number> = {};
    for (const k of COMMENT_CATEGORIES) empty[k] = 0;
    return {
      range,
      daily: [],
      rating_distribution: { up: 0, down: 0 },
      satisfaction_rate: null,
      comment_categories: empty,
      recent_comments: [],
      total_feedback: 0,
      comment_count: 0,
      error_code: "migrations_pending",
      generated_at: generatedAt,
    };
  }

  const up = rows.filter((r) => r.helpful === true).length;
  const down = rows.filter((r) => r.helpful === false).length;
  const { categories, comments } = buildCommentAnalysis(rows);

  return {
    range,
    daily: buildFeedbackDailyBuckets(rows, days, nowMs),
    rating_distribution: { up, down },
    satisfaction_rate: satisfactionRate(up, down),
    comment_categories: categories,
    recent_comments: comments,
    // **`up + down` 이다** — `rows.length` 가 아니다. helpful 이 null 인 행은 빠진다.
    total_feedback: up + down,
    comment_count: Object.values(categories).reduce((a, b) => a + b, 0),
    error_code: null,
    generated_at: generatedAt,
  };
}

// ------------------------------------------------------------ subscriptions

export interface SubscriptionUpsertPayload {
  user_id: string;
  plan_code: string;
  status: string;
  current_period_end: string | null;
}

/**
 * `POST /admin/subscriptions` 가 쓰는 행. **실행과 분리해 둔다** — 대조에서 실제로
 * 쓰지 않고 모양만 보기 위해서다(`/me` 의 rotate 와 같은 이유).
 */
export function buildSubscriptionRow(
  payload: SubscriptionUpsertPayload,
  nowMs: number,
): Record<string, unknown> {
  return {
    user_id: payload.user_id,
    plan_code: payload.plan_code,
    status: payload.status,
    current_period_end: payload.current_period_end,
    updated_at: pyIsoUtc(nowMs),
  };
}

/** upsert 질의. 조립을 호출부에 복붙하지 않으려고 함수로 뺐다(대조가 이걸 직접 부른다). */
export function buildSubscriptionUpsertQuery(
  client: SupabaseClient,
  row: Record<string, unknown>,
): unknown {
  return client.from("subscriptions").upsert(row, { onConflict: "user_id" });
}

function toItem(r: Record<string, unknown>): Record<string, unknown> {
  return {
    user_id: String(r.user_id),
    plan_code: r.plan_code,
    status: r.status,
    current_period_end: r.current_period_end ?? null,
    updated_at: r.updated_at ?? null,
  };
}

export async function upsertSubscription(
  payload: SubscriptionUpsertPayload,
  deps: AdminDeps,
): Promise<Record<string, unknown>> {
  const nowMs = (deps.now ?? Date.now)();
  const row = buildSubscriptionRow(payload, nowMs);
  const { data, error } = await (buildSubscriptionUpsertQuery(
    deps.client,
    row,
  ) as { select: () => Promise<{ data: unknown; error: unknown }> }).select();
  if (error) throw new Error(String((error as { message?: string }).message ?? error));
  const rows = (data ?? []) as Record<string, unknown>[];
  // 원본 `(resp.data or [row])[0]` — 응답이 비면 보낸 행을 그대로 돌려준다.
  return toItem(rows.length ? rows[0] : row);
}

export async function listSubscriptions(
  deps: AdminDeps,
): Promise<{ items: Record<string, unknown>[] }> {
  const { data, error } = await deps.client
    .from("subscriptions")
    .select("user_id, plan_code, status, current_period_end, updated_at")
    .order("updated_at", { ascending: false })
    .limit(100);
  if (error) throw new Error(error.message);
  const rows = (data ?? []) as Record<string, unknown>[];
  return { items: rows.map(toItem) };
}
