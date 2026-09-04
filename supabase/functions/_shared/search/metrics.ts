/**
 * 검색 지표 기록 — `services/search_metrics.py` 의 write-through 부분 포팅.
 *
 * `/search` 한 번마다 `search_metrics_log` 에 한 줄 남긴다. 2026-09-05 기준 17,378 행이
 * 쌓여 있고, 컬럼은 아래 그대로다.
 *
 * ## 인메모리 ring 은 옮기지 않았다 — 옮길 수 없다
 * 원본은 DB 쓰기와 **별도로** 프로세스 안에 최근 N 건짜리 ring buffer 를 두고,
 * `/stats` 의 `search_slo` 가 그걸 읽는다. Edge 는 isolate 가 휘발성이라 그 구조가
 * 성립하지 않는다.
 *
 * 그래서 이건 이식 품질과 무관하게 **전환 자체가 만드는 회귀**다 — `/search` 가 Edge 로
 * 넘어가면 Railway 프로세스의 ring 에는 아무것도 안 쌓이고, `/stats.search_slo` 는
 * 계속 0 을 보고한다(매일 02:00 UTC cron 이 그 값을 본다).
 *
 * 다행히 대체 경로가 있다 — `search_metrics_log` 에 `get_search_slo()` 가 계산하는
 * 필드가 전부 들어 있다(실측). `/stats` 를 DB 기준으로 바꾸면 어디서 검색이 돌든
 * 지표가 유지된다. **Task 2.9 전환 전에 처리할 항목**으로 플랜에 올려 뒀다.
 *
 * ## 쓰기는 응답을 막지 않는다
 * 원본은 스레드풀에 fire-and-forget 한다(`JET_RAG_METRICS_PERSIST_ASYNC` 기본 on).
 * Edge 의 대응물은 `EdgeRuntime.waitUntil` 이다. 실패는 삼킨다 — 지표 때문에 검색이
 * 실패하면 안 된다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

const VALID_MODES: ReadonlySet<string> = new Set(["hybrid", "dense", "sparse"]);

export interface SearchEvent {
  tookMs: number;
  denseHits: number;
  sparseHits: number;
  fused: number;
  hasDense: boolean;
  /** `null` / `"transient_5xx"` / `"permanent_4xx"`. */
  fallbackReason: string | null;
  embedCacheHit: boolean;
  mode: string;
  queryText: string | null;
}

/** `JET_RAG_METRICS_PERSIST_ENABLED` — `"0"` 일 때만 끈다. */
export function persistEnabled(read: (k: string) => string | undefined): boolean {
  return (read("JET_RAG_METRICS_PERSIST_ENABLED") ?? "1") !== "0";
}

/** `JET_RAG_QUERY_TEXT_HASH=1` 이면 질의를 SHA-256 hex 로 바꿔 저장한다. */
export async function maybeHashQuery(
  queryText: string | null,
  read: (k: string) => string | undefined,
): Promise<string | null> {
  if (!queryText) return queryText;
  if ((read("JET_RAG_QUERY_TEXT_HASH") ?? "0") !== "1") return queryText;
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(queryText));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** 저장할 행 모양. 모르는 mode 는 `hybrid` 로 강제한다(원본 동작). */
export function buildMetricsRow(
  event: SearchEvent,
  recordedAt: Date,
  queryText: string | null,
): Record<string, unknown> {
  return {
    recorded_at: recordedAt.toISOString(),
    took_ms: Math.trunc(event.tookMs),
    dense_hits: Math.trunc(event.denseHits),
    sparse_hits: Math.trunc(event.sparseHits),
    fused: Math.trunc(event.fused),
    has_dense: Boolean(event.hasDense),
    fallback_reason: event.fallbackReason,
    embed_cache_hit: Boolean(event.embedCacheHit),
    mode: VALID_MODES.has(event.mode) ? event.mode : "hybrid",
    query_text: queryText,
  };
}

export interface MetricsDeps {
  read: (k: string) => string | undefined;
  client: SupabaseClient;
  waitUntil?: (p: Promise<unknown>) => void;
  /** 시각 주입 — 테스트에서 고정하려고 열어 둔다. */
  now?: () => Date;
}

/**
 * 검색 1 회를 기록한다. 응답을 늦추지 않도록 백그라운드로 넘기고, 실패는 삼킨다.
 */
export function recordSearch(event: SearchEvent, deps: MetricsDeps): void {
  if (!persistEnabled(deps.read)) return;
  const recordedAt = (deps.now ?? (() => new Date()))();

  const task = (async () => {
    try {
      const queryText = await maybeHashQuery(event.queryText, deps.read);
      await deps.client
        .from("search_metrics_log")
        .insert(buildMetricsRow(event, recordedAt, queryText));
    } catch {
      // 지표 실패가 검색을 막으면 안 된다 — 원본도 삼킨다.
    }
  })();

  if (deps.waitUntil) deps.waitUntil(task);
}
