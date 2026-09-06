/**
 * `/stats` · `/stats/trend` 조립 — `routers/stats.py` 의 핸들러 포팅.
 *
 * HTTP 를 모른다. 검증된 파라미터와 user_id 를 받아 응답 객체를 만든다 —
 * `/search` 와 같은 이유로, 패리티 검사기가 토큰 없이 in-process 로 비교할 수 있게.
 *
 * ## 질의 순서를 원본대로 둔다
 * documents → chunks RPC → jobs → 실패 job → search SLO → vision. 순서 자체가 응답을
 * 바꾸지는 않지만, `generated_at` 이 마지막에 찍히므로 전체 소요가 그대로 반영된다.
 * (원본은 순차 실행이다. 병렬로 바꾸면 더 빠르지만 원본과 다른 시점 스냅샷이 섞인다.)
 */

import type { SupabaseClient } from "@supabase/supabase-js";

// `pytime.ts` 로 뽑았다 — `/me` 의 `rotated_at` 도 같은 규칙을 쓴다.
// **마이크로초 0 이면 소수부를 생략**하는 규칙이 여기 들어 있다. 예전 구현은 늘
// `.mmm000+00:00` 을 붙였는데, `ms % 1000 === 0` 인 순간(천 번에 한 번)에는 원본이
// `+00:00` 만 낸다. 검증기가 그 경계를 고정으로 태운다.
import { pyIsoUtc as utcIsoLikePython } from "../pytime.ts";
export { utcIsoLikePython };

import {
  computeDocumentsStats,
  computeJobsStats,
  computeSearchSlo,
  computeSloAggregate,
  computeSloBuckets,
} from "./aggregate.ts";
import {
  fetchChunksStats,
  fetchDocuments,
  fetchFailedJobSample,
  fetchJobs,
  fetchSearchSloSamples,
  fetchTrend,
  fetchVisionUsage,
} from "./sources.ts";

export interface StatsDeps {
  client: SupabaseClient;
  /** 시각 주입 — 테스트·대조에서 고정하려고 열어 둔다. */
  now?: () => number;
}

export async function buildStats(
  userId: string,
  deps: StatsDeps,
): Promise<Record<string, unknown>> {
  const nowMs = (deps.now ?? Date.now)();
  const client = deps.client;

  const allDocs = await fetchDocuments(client, userId);
  const { stats: documents, popularTags } = computeDocumentsStats(allDocs, nowMs);

  const { total: chunksTotal, stats: chunks } = await fetchChunksStats(client, userId);

  const jobs = await fetchJobs(client);
  const failedSample = await fetchFailedJobSample(client);

  // SLO 버킷은 **실패 문서도 포함**한다 — received_ms 는 수신 단계만 반영하므로
  // 파이프라인 뒤에서 실패했어도 수신 자체는 성공한 유효 표본이다.
  const sloBuckets = computeSloBuckets(allDocs);
  const ingestSloAggregate = computeSloAggregate(sloBuckets);

  const samples = await fetchSearchSloSamples(client);
  // Edge 에는 되돌아갈 in-memory ring 이 없다 — 조회가 실패하면 그 사실을 `source` 로 밝힌다.
  const searchSlo = computeSearchSlo(samples ?? [], samples === null ? "unavailable" : "db");

  const visionUsage = await fetchVisionUsage(client, nowMs);

  return {
    documents,
    chunks_total: chunksTotal,
    chunks,
    jobs: computeJobsStats(jobs, failedSample),
    popular_tags: popularTags,
    slo_buckets: sloBuckets,
    ingest_slo_aggregate: ingestSloAggregate,
    search_slo: searchSlo,
    vision_usage: visionUsage,
    generated_at: utcIsoLikePython((deps.now ?? Date.now)()),
  };
}

export const TREND_RANGES = ["24h", "7d", "30d"] as const;
export const TREND_MODES = ["all", "hybrid", "dense", "sparse"] as const;
export const TREND_METRICS = ["search", "vision"] as const;

export interface TrendParams {
  range: string;
  mode: string;
  metric: string;
}

/**
 * 쿼리 파라미터 검증. 원본은 FastAPI `Literal` 이라 **422 + 구조화 배열**을 낸다 —
 * `/search` 의 pydantic 오류와 같은 모양이므로 형태를 맞춘다.
 */
export function validateTrendParams(
  sp: URLSearchParams,
): { ok: true; params: TrendParams } | { ok: false; detail: unknown[] } {
  const errors: unknown[] = [];
  // pydantic 의 문구 규칙 — **마지막만 `or`, 나머지는 쉼표**다.
  //   3 개: `'24h', '7d' or '30d'`   2 개: `'search' or 'vision'`
  // 전부 `or` 로 이으면 2 개일 때만 우연히 맞고 3 개 이상에서 갈린다 —
  // in-process 대조로는 안 잡히고(핸들러를 직접 부르면 FastAPI 검증을 안 거친다)
  // 배포 후 HTTP 로 재고서야 드러났다.
  const expectedText = (allowed: readonly string[]) => {
    const q = allowed.map((v) => `'${v}'`);
    if (q.length <= 1) return q.join("");
    return `${q.slice(0, -1).join(", ")} or ${q[q.length - 1]}`;
  };
  const pick = (name: string, allowed: readonly string[], dflt: string): string => {
    const raw = sp.get(name);
    if (raw === null) return dflt;
    if (!allowed.includes(raw)) {
      const expected = expectedText(allowed);
      errors.push({
        type: "literal_error",
        loc: ["query", name],
        msg: `Input should be ${expected}`,
        input: raw,
        ctx: { expected },
      });
      return dflt;
    }
    return raw;
  };
  // 선언 순서가 곧 오류 배열 순서다 — range → mode → metric.
  const range = pick("range", TREND_RANGES, "7d");
  const mode = pick("mode", TREND_MODES, "all");
  const metric = pick("metric", TREND_METRICS, "search");
  if (errors.length) return { ok: false, detail: errors };
  return { ok: true, params: { range, mode, metric } };
}

export async function buildTrend(
  params: TrendParams,
  deps: StatsDeps,
): Promise<Record<string, unknown>> {
  const nowMs = (deps.now ?? Date.now)();
  const generatedAt = utcIsoLikePython(nowMs);
  const metric = params.metric as "search" | "vision";
  // `mode` 는 search 에만 의미가 있다 — vision 이면 응답에서 null 이다.
  const responseMode = metric === "search" ? params.mode : null;

  const { buckets, errorCode } = await fetchTrend(
    deps.client,
    metric,
    params.range,
    params.mode,
  );
  return {
    metric,
    range: params.range,
    mode: responseMode,
    buckets,
    error_code: errorCode,
    generated_at: generatedAt,
  };
}
