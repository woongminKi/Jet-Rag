/**
 * 인제스트 잔여 시간 추정 — `ingest/eta.py` 포팅.
 *
 * `/documents/active` 응답의 `estimated_remaining_ms` 하나를 위해 존재한다.
 * 프런트가 "약 N분 N초 남음" 을 그린다.
 *
 * ## `null` 도 의미 있는 값이다
 * 원본 주석대로 cold start(어느 stage 도 sample <3)면 `null` 을 주고, web 은
 * "처음에는 시간 추정이 부정확합니다" 카피로 분기한다. 그래서 **항상 `null` 로
 * 두면 기능이 죽는다** — 옮기지 않고 넘길 수 없었다.
 *
 * ## 캐시는 함수 인스턴스 수명만큼만 산다
 * 원본은 프로세스 전역 90 초 TTL 캐시다. Edge 는 인스턴스가 자주 죽고 살아나므로
 * 캐시 적중률이 원본보다 낮다 — **결과는 같고 DB 조회가 더 잦을 뿐이다**(ingest_logs
 * 500 행 + vision_usage_log 200 행). 락은 필요 없다(단일 스레드).
 *
 * ## Python `statistics.median` 과 맞춘다
 * 짝수 개면 두 중간값의 평균이다. `sort()` 기본값은 문자열 정렬이라 비교 함수를 반드시 준다.
 * (§23 에서 PDF `pageMedianSize` 를 옮길 때 같은 함정을 이미 겪었다.)
 */

import type { SupabaseClient } from "@supabase/supabase-js";

/** 파이프라인 단계 순서. 남은 단계를 합산할 때 쓴다. */
export const STAGE_ORDER: readonly string[] = [
  "extract",
  "chunk",
  "chunk_filter",
  "content_gate",
  "tag_summarize",
  "load",
  "embed",
  "doc_embed",
  "dedup",
];

/** cold start fallback — `ingest_logs` 가 없거나 sample 이 모자랄 때. */
const FALLBACK_STAGE_MS: Record<string, number> = {
  extract: 120000,
  chunk: 2000,
  chunk_filter: 1000,
  content_gate: 500,
  tag_summarize: 5000,
  load: 2000,
  embed: 35000,
  doc_embed: 1500,
  dedup: 500,
};

const FALLBACK_VISION_PER_PAGE_MS = 30000;
const VISION_SWEEP_BUFFER_FACTOR = 1.2;
const CACHE_TTL_MS = 90_000;
const SAMPLE_LIMIT = 500;
const VISION_SAMPLE_LIMIT = 200;
const MIN_SAMPLES_FOR_ETA = 3;
const ACTIVE_JOB_STATUSES = new Set(["queued", "running"]);

export interface StageProgress {
  current?: number;
  total?: number;
  unit?: string;
}

/** `statistics.median` — 짝수면 두 중간값의 평균. */
export function median(values: number[]): number {
  if (values.length === 0) return 0;
  const s = [...values].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 === 1 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

/** 원본 `_percentile` — nearest-rank, 보간 없음. */
export function percentile(values: number[], p: number): number {
  if (values.length === 0) return 0;
  const s = [...values].sort((a, b) => a - b);
  // Python `int(round(...))` 는 **은행가 반올림**이다. 0.5 에서 짝수로 간다.
  const raw = p * (s.length - 1);
  const rank = Math.max(0, Math.min(s.length - 1, bankersRound(raw)));
  return s[rank];
}

/** Python `round()` — .5 는 짝수 쪽으로. JS `Math.round` 는 항상 올림이라 다르다. */
function bankersRound(x: number): number {
  const f = Math.floor(x);
  const diff = x - f;
  if (diff > 0.5) return f + 1;
  if (diff < 0.5) return f;
  return f % 2 === 0 ? f : f + 1;
}

interface Baselines {
  medians: Record<string, number>;
  visionPerPageMs: number | null;
}

let cache: { value: Baselines; expiresAt: number } | null = null;

/** 테스트용 — 캐시를 비운다. */
export function resetEtaCache(): void {
  cache = null;
}

/** `ingest_logs` 최근 succeeded 에서 stage 별 median(duration_ms). 1 쿼리다. */
async function fetchStageMedians(client: SupabaseClient): Promise<Record<string, number>> {
  try {
    const { data, error } = await client
      .from("ingest_logs")
      .select("stage, duration_ms")
      .eq("status", "succeeded")
      .order("id", { ascending: false })
      .limit(SAMPLE_LIMIT);
    if (error) throw new Error(error.message);
    const byStage: Record<string, number[]> = {};
    for (const row of (data ?? []) as { stage?: string; duration_ms?: unknown }[]) {
      const d = row.duration_ms;
      // 원본은 `isinstance(duration, int)` — 실수는 제외된다.
      if (row.stage && typeof d === "number" && Number.isInteger(d) && d > 0) {
        (byStage[row.stage] ??= []).push(d);
      }
    }
    const out: Record<string, number> = {};
    for (const [stage, durations] of Object.entries(byStage)) {
      // sample 이 모자란 stage 는 **키를 안 넣는다** — caller 가 fallback 을 쓴다.
      if (durations.length >= MIN_SAMPLES_FOR_ETA) out[stage] = median(durations);
    }
    return out;
  } catch (e) {
    // DB 일시 장애는 fallback 으로 떨어뜨린다 — ETA 때문에 목록이 죽으면 안 된다.
    console.warn("ingest_logs sampling 실패 — fallback 사용:", e);
    return {};
  }
}

/** `vision_usage_log` 최근 succeeded latency 의 p95. 503 retry burst 를 흡수한다. */
async function fetchVisionPerPageMs(client: SupabaseClient): Promise<number | null> {
  try {
    const { data, error } = await client
      .from("vision_usage_log")
      .select("latency_ms, success")
      .eq("success", true)
      .order("called_at", { ascending: false })
      .limit(VISION_SAMPLE_LIMIT);
    if (error) throw new Error(error.message);
    const lat: number[] = [];
    for (const row of (data ?? []) as { latency_ms?: unknown }[]) {
      const v = row.latency_ms;
      if (typeof v === "number" && v > 0) lat.push(v);
    }
    if (lat.length < MIN_SAMPLES_FOR_ETA) return null;
    return percentile(lat, 0.95);
  } catch (e) {
    // 테이블이 없는 환경도 있다(D2 미진입) — 조용히 fallback.
    console.debug("vision_usage_log sampling 실패 — fallback 사용:", e);
    return null;
  }
}

async function getBaselines(client: SupabaseClient, nowMs: number): Promise<Baselines> {
  if (cache && nowMs < cache.expiresAt) return cache.value;
  const value: Baselines = {
    medians: await fetchStageMedians(client),
    visionPerPageMs: await fetchVisionPerPageMs(client),
  };
  cache = { value, expiresAt: nowMs + CACHE_TTL_MS };
  return value;
}

/** stage 별 시간 — DB median 우선, 없으면 fallback, 그것도 없으면 1000. */
function stageMs(stage: string, medians: Record<string, number>): number {
  if (stage in medians) return Math.trunc(medians[stage]);
  return FALLBACK_STAGE_MS[stage] ?? 1000;
}

/**
 * extract 의 vision sub-stage 남은 시간.
 * `unit === "pages"` 이고 `total > 0` 일 때만 산다. 아니면 `null` 로 분해를 끈다.
 */
export function visionRemainingMs(
  sp: StageProgress | null | undefined,
  visionPerPageMs: number | null,
): number | null {
  if (!sp) return null;
  if (sp.unit !== "pages") return null;
  const { current, total } = sp;
  if (typeof current !== "number" || typeof total !== "number") return null;
  if (total <= 0) return null;
  const remainingPages = Math.max(0, total - current);
  const perPage = visionPerPageMs ?? FALLBACK_VISION_PER_PAGE_MS;
  return Math.trunc(remainingPages * perPage * VISION_SWEEP_BUFFER_FACTOR);
}

/** 현재 stage 의 남은 시간. 분기 우선순위는 원본 주석 그대로다. */
export function currentStageRemainingMs(
  stage: string,
  medians: Record<string, number>,
  sp: StageProgress | null | undefined,
  visionPerPageMs: number | null,
): number {
  const full = stageMs(stage, medians);

  // ① extract 만 vision sub-stage 로 분해한다(`unit='pages'` 명시 시).
  if (stage === "extract") {
    const v = visionRemainingMs(sp, visionPerPageMs);
    if (v !== null) return v;
  }

  if (!sp) return full;
  const { current, total } = sp;
  if (typeof current !== "number" || typeof total !== "number") return full;
  if (total <= 0) return full;
  const ratio = Math.max(0, Math.min(1, current / total));
  return Math.trunc(full * (1 - ratio));
}

/**
 * 잡의 남은 시간(ms). `queued`/`running` 이 아니면 `null`.
 *
 * cold start(어느 stage 도 sample <3)도 `null` 이다 — 그게 web 의 카피 분기 신호다.
 */
export async function computeRemainingMs(
  client: SupabaseClient,
  opts: {
    jobStatus: string;
    currentStage: string | null;
    stageProgress?: StageProgress | null;
    nowMs?: number;
  },
): Promise<number | null> {
  if (!ACTIVE_JOB_STATUSES.has(opts.jobStatus)) return null;

  const { medians, visionPerPageMs } = await getBaselines(
    client,
    opts.nowMs ?? Date.now(),
  );

  // cold start — 한 번도 인제스트가 끝나지 않았으면 추정하지 않는다.
  if (Object.keys(medians).length === 0) return null;

  const sumAll = () => STAGE_ORDER.reduce((a, s) => a + stageMs(s, medians), 0);
  if (opts.jobStatus === "queued" || opts.currentStage === null) return sumAll();

  const idx = STAGE_ORDER.indexOf(opts.currentStage);
  if (idx < 0) return sumAll(); // 모르는 stage — 전체 합산 fallback

  const cur = currentStageRemainingMs(
    opts.currentStage,
    medians,
    opts.stageProgress,
    visionPerPageMs,
  );
  const later = STAGE_ORDER.slice(idx + 1).reduce((a, s) => a + stageMs(s, medians), 0);
  return cur + later;
}
