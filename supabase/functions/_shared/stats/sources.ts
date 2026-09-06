/**
 * `/stats` 의 데이터 조회 — `routers/stats.py` 의 DB 접근부 포팅.
 *
 * 집계는 `aggregate.ts` 가 한다. 여기는 **어떤 질의를 던지는가**만 담당한다.
 *
 * ## 1,000 행 상한을 원본 그대로 둔다
 * `documents` 와 `ingest_jobs` 는 원본이 `limit` 없이 select 한다 → PostgREST 기본
 * 1,000 행에서 잘린다. 지금은 문서 13 · 잡 52 라 안 걸린다(2026-09-06 실측).
 * **여기서 페이지네이션을 넣으면 원본보다 많이 세게 되어 응답이 갈린다** — 고치려면
 * 양쪽을 함께 고쳐야 한다.
 *
 * ## vision 사용량은 `count` 질의로 센다
 * 행을 받아 `len()` 하면 같은 1,000 행 상한에 걸린다. 실제로 밟았다 — 2,090 행짜리
 * 창을 1,000 으로 세고 있었고, 검사기도 같은 방식이라 나란히 틀린 채 통과했다.
 * 그래서 개수는 `count`, 최근 시각은 `limit(1)` 로 받는다(상한과 무관, 전송량 O(1)).
 */

import type { SupabaseClient } from "@supabase/supabase-js";
import { pyRound } from "../pynum.ts";
import type { DocRow, SearchSloSample } from "./aggregate.ts";

export const DOCUMENTS_SELECT = "doc_type, source_channel, size_bytes, flags, tags, created_at, received_ms";
export const FAILED_JOB_SELECT = "id, doc_id, current_stage, error_msg, queued_at";
/** `search_metrics` 의 ring 크기와 같은 창. 1,000 미만이라 상한에 안 걸린다. */
export const SLO_SAMPLE_LIMIT = 500;

export async function fetchDocuments(
  client: SupabaseClient,
  userId: string,
): Promise<DocRow[]> {
  const { data, error } = await client
    .from("documents")
    .select(DOCUMENTS_SELECT)
    .eq("user_id", userId)
    .is("deleted_at", null);
  if (error) throw new Error(`documents 조회 실패: ${error.message}`);
  return (data ?? []) as DocRow[];
}

/** 원본은 `ingest_jobs` 를 **사용자로 안 거른다** — 그대로 옮겼다. */
export async function fetchJobs(
  client: SupabaseClient,
): Promise<{ status?: string | null }[]> {
  const { data, error } = await client.from("ingest_jobs").select("status");
  if (error) throw new Error(`ingest_jobs 조회 실패: ${error.message}`);
  return (data ?? []) as { status?: string | null }[];
}

export async function fetchFailedJobSample(
  client: SupabaseClient,
): Promise<Record<string, unknown>[]> {
  const { data, error } = await client
    .from("ingest_jobs")
    .select(FAILED_JOB_SELECT)
    .eq("status", "failed")
    .order("queued_at", { ascending: false })
    .limit(5);
  if (error) throw new Error(`실패 job 조회 실패: ${error.message}`);
  return (data ?? []) as Record<string, unknown>[];
}

export interface ChunksStats {
  total: number;
  effective: number;
  filtered_breakdown: Record<string, number>;
  filtered_ratio: number;
}

const EMPTY_CHUNKS: ChunksStats = {
  total: 0,
  effective: 0,
  filtered_breakdown: {},
  filtered_ratio: 0.0,
};

/**
 * 마이그 019 의 `get_chunks_stats_for_user` RPC 1회. 마이그 미적용·일시 장애는
 * **빈 통계로 graceful** — 코드 배포가 마이그 적용보다 앞서도 안전해야 한다(원본 의도).
 */
export async function fetchChunksStats(
  client: SupabaseClient,
  userId: string,
): Promise<{ total: number; stats: ChunksStats }> {
  let rows: Record<string, unknown>[];
  try {
    const { data, error } = await client.rpc("get_chunks_stats_for_user", {
      user_id_arg: userId,
    });
    if (error) throw new Error(error.message);
    rows = (data ?? []) as Record<string, unknown>[];
  } catch (e) {
    console.warn("get_chunks_stats_for_user RPC 미적용/실패 — 빈 통계:", e);
    return { total: 0, stats: EMPTY_CHUNKS };
  }
  if (rows.length === 0) return { total: 0, stats: EMPTY_CHUNKS };

  const row = rows[0];
  const total = Math.trunc(Number(row.total ?? 0)) || 0;
  const filtered = Math.trunc(Number(row.filtered ?? 0)) || 0;
  const raw = (row.breakdown ?? {}) as Record<string, unknown>;
  const breakdown: Record<string, number> = {};
  for (const [k, v] of Object.entries(raw)) breakdown[String(k)] = Math.trunc(Number(v));
  return {
    total,
    stats: {
      total,
      effective: Math.max(total - filtered, 0),
      filtered_breakdown: breakdown,
      // 원본은 `round(ratio, 4)` — 은행가 반올림이라 `pyRound` 를 쓴다.
      filtered_ratio: total ? pyRound(filtered / total, 4) : 0.0,
    },
  };
}

/** `search_metrics_log` 최근 N 행 → SLO 표본. 실패는 null(호출부가 빈 통계로 간다). */
export async function fetchSearchSloSamples(
  client: SupabaseClient,
): Promise<SearchSloSample[] | null> {
  try {
    const { data, error } = await client
      .from("search_metrics_log")
      .select(
        "took_ms, dense_hits, sparse_hits, fused, has_dense, " +
          "fallback_reason, embed_cache_hit, mode",
      )
      .order("recorded_at", { ascending: false })
      .limit(SLO_SAMPLE_LIMIT);
    if (error) throw new Error(error.message);
    const out: SearchSloSample[] = [];
    for (const r of (data ?? []) as unknown as Record<string, unknown>[]) {
      // 컬럼은 전부 NOT NULL 이지만(17,561행 실측) 방어적으로 형변환한다.
      try {
        out.push({
          took_ms: Math.trunc(Number(r.took_ms)),
          dense_hits: Math.trunc(Number(r.dense_hits)),
          sparse_hits: Math.trunc(Number(r.sparse_hits)),
          fused: Math.trunc(Number(r.fused)),
          has_dense: Boolean(r.has_dense),
          fallback_reason: (r.fallback_reason as string | null) ?? null,
          embed_cache_hit: Boolean(r.embed_cache_hit),
          mode: (r.mode as string) || "hybrid",
        });
      } catch {
        // 한 행이 이상해도 나머지는 살린다 (원본과 같다).
      }
    }
    return out;
  } catch (e) {
    console.warn("search_metrics_log 조회 실패:", e);
    return null;
  }
}

export interface VisionUsage {
  total_calls: number;
  success_calls: number;
  error_calls: number;
  last_called_at: string | null;
  last_quota_exhausted_at: string | null;
  source: string;
}

/** KST 자정(오늘)의 ISO 문자열. `vision_usage_log` 창의 하한이다. */
export function kstTodayStartIso(nowMs: number): string {
  const w = new Date(nowMs + 9 * 60 * 60_000);
  const p = (n: number, len = 2) => String(n).padStart(len, "0");
  return `${p(w.getUTCFullYear(), 4)}-${p(w.getUTCMonth() + 1)}-${p(w.getUTCDate())}` +
    `T00:00:00+09:00`;
}

/**
 * 오늘(KST) vision 호출. 창을 하루로 잡은 건 프론트 카드가 RPD(일일 한도) 대비로
 * 그리기 때문이다 — 자세한 근거는 `services/vision_metrics.py` 주석 참조.
 */
export async function fetchVisionUsage(
  client: SupabaseClient,
  nowMs: number,
): Promise<VisionUsage> {
  const since = kstTodayStartIso(nowMs);
  try {
    const count = async (eq?: [string, unknown]) => {
      let q = client
        .from("vision_usage_log")
        .select("called_at", { count: "exact", head: true })
        .gte("called_at", since);
      if (eq) q = q.eq(eq[0], eq[1]);
      const { count: c, error } = await q;
      if (error) throw new Error(error.message);
      return c ?? 0;
    };
    const latest = async (eq?: [string, unknown]) => {
      let q = client.from("vision_usage_log").select("called_at").gte("called_at", since);
      if (eq) q = q.eq(eq[0], eq[1]);
      const { data, error } = await q.order("called_at", { ascending: false }).limit(1);
      if (error) throw new Error(error.message);
      const rows = (data ?? []) as { called_at: string }[];
      return rows.length ? rows[0].called_at : null;
    };
    const total = await count();
    const success = await count(["success", true]);
    return {
      total_calls: total,
      success_calls: success,
      error_calls: total - success,
      last_called_at: await latest(),
      last_quota_exhausted_at: await latest(["quota_exhausted", true]),
      source: "db",
    };
  } catch (e) {
    console.warn("vision_usage_log 조회 실패:", e);
    // Edge 에는 in-memory 카운터가 없다 — 되돌아갈 곳이 없으므로 0 을 내되
    // `source` 로 그 사실을 드러낸다.
    return {
      total_calls: 0,
      success_calls: 0,
      error_calls: 0,
      last_called_at: null,
      last_quota_exhausted_at: null,
      source: "unavailable",
    };
  }
}

/**
 * **다섯 부가 필드가 언제나 다 실린다.** pydantic `model_dump()` 는 선언된 필드를 전부
 * 내보내므로, metric 이 안 쓰는 필드도 `null` 로 응답에 들어간다(실측으로 확인).
 * 생략하면 프론트가 `'p50_ms' in bucket` 같은 검사를 할 때 동작이 갈린다.
 */
export interface TrendBucket {
  bucket_start: string;
  sample_count: number;
  p50_ms: number | null;
  p95_ms: number | null;
  fallback_count: number | null;
  success_count: number | null;
  quota_exhausted_count: number | null;
}

/** 마이그 007 의 RPC 2종. 미적용 시 `migrations_pending` 으로 graceful. */
export async function fetchTrend(
  client: SupabaseClient,
  metric: "search" | "vision",
  range: string,
  mode: string,
): Promise<{ buckets: TrendBucket[]; errorCode: string | null }> {
  try {
    const { data, error } = metric === "search"
      ? await client.rpc("get_search_metrics_trend", {
        range_label: range,
        mode_label: mode,
      })
      : await client.rpc("get_vision_usage_trend", { range_label: range });
    if (error) throw new Error(error.message);
    const rows = (data ?? []) as Record<string, unknown>[];
    return { buckets: rows.map((r) => rowToBucket(metric, r)), errorCode: null };
  } catch (e) {
    console.warn("stats_trend RPC graceful skip:", e);
    return { buckets: [], errorCode: "migrations_pending" };
  }
}

/** RPC row → bucket. metric 별로 채우는 필드가 다르다. */
export function rowToBucket(
  metric: "search" | "vision",
  row: Record<string, unknown>,
): TrendBucket {
  const bucketStart = String(row.bucket_start ?? "");
  const sampleCount = Math.trunc(Number(row.sample_count ?? 0)) || 0;
  const base = {
    bucket_start: bucketStart,
    sample_count: sampleCount,
    p50_ms: null as number | null,
    p95_ms: null as number | null,
    fallback_count: null as number | null,
    success_count: null as number | null,
    quota_exhausted_count: null as number | null,
  };
  if (metric === "search") {
    return {
      ...base,
      p50_ms: Math.trunc(Number(row.p50_ms ?? 0)) || 0,
      p95_ms: Math.trunc(Number(row.p95_ms ?? 0)) || 0,
      fallback_count: Math.trunc(Number(row.fallback_count ?? 0)) || 0,
    };
  }
  return {
    ...base,
    success_count: Math.trunc(Number(row.success_count ?? 0)) || 0,
    quota_exhausted_count: Math.trunc(Number(row.quota_exhausted_count ?? 0)) || 0,
  };
}
