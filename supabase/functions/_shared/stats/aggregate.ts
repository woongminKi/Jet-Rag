/**
 * `/stats` 의 순수 집계 — `routers/stats.py` 포팅.
 *
 * DB 를 모른다. 행 배열을 받아 응답에 실릴 수치를 만든다. I/O 는 `sources.ts` 가 한다.
 *
 * ## 하루·한 달 경계는 KST 다
 * `added_this_month` 는 **KST 이번 달 1일 00:00** 이후, `added_last_7d` 는 **KST 기준
 * 최근 168시간**이다. UTC 로 계산하면 한국 사용자 기준으로 경계가 9시간 어긋난다.
 *
 * ## 실패 문서는 집계에서 빠진다
 * `flags.failed` 인 문서는 `failed_count` 로만 노출하고 나머지 집계에서 전부 제외한다.
 * 단 **SLO 버킷은 실패 문서도 포함**한다 — `received_ms` 는 수신 단계만 반영하므로
 * 파이프라인 뒤에서 실패했어도 수신 자체는 성공한 유효 표본이다.
 *
 * ## 정수 합이라 fsum 이 필요 없다
 * 원본은 평균에 `statistics.fmean`(내부적으로 `math.fsum`, 정확 합산)을 쓴다. 그런데
 * 입력이 전부 정수(`dense_hits`·`received_ms` 등)고 표본이 수백 건이라 2^53 을 한참
 * 밑돈다 — 단순 합산과 결과가 같다. 실수 입력이 생기면 이 전제가 깨진다.
 */

import { pyRound } from "../pynum.ts";

/** 기획서 §10.11 — 수신 응답 < 2초. */
export const SLO_TARGET_MS = 2000;
/** `pdf_50p` 버킷의 크기 임계 — 50MB 한도의 절반. */
export const PDF_50P_THRESHOLD_BYTES = 25 * 1024 * 1024;
/** 한국 시간대. 단일 사용자 MVP 라 원본도 하드코딩이다. */
export const KST_OFFSET_MINUTES = 9 * 60;

export interface DocRow {
  doc_type?: string | null;
  source_channel?: string | null;
  size_bytes?: number | null;
  flags?: Record<string, unknown> | null;
  tags?: string[] | null;
  created_at?: string | null;
  received_ms?: number | null;
}

export interface DocumentsStats {
  total: number;
  by_doc_type: Record<string, number>;
  by_source_channel: Record<string, number>;
  extract_skipped: number;
  total_size_bytes: number;
  added_this_month: number;
  added_last_7d: number;
  failed_count: number;
}

export interface SloBucketStats {
  p95_ms: number | null;
  sample_count: number;
  pass_rate: number | null;
}

export interface IngestSloAggregate {
  total_samples: number;
  overall_pass_rate: number | null;
  buckets_with_samples: string[];
}

/** UTC 시각을 KST 로 옮긴 "벽시계" epoch. 날짜 경계 계산용이다. */
function toKstWallMs(iso: string | null | undefined): number | null {
  if (!iso) return null;
  // Postgres 는 보통 `+00:00` 을 쓰지만 `Z` 도 방어적으로 받는다(원본과 같다).
  const normalized = iso.endsWith("Z") ? `${iso.slice(0, -1)}+00:00` : iso;
  const t = Date.parse(normalized);
  if (Number.isNaN(t)) return null;
  return t + KST_OFFSET_MINUTES * 60_000;
}

/** `now` 기준 KST 이번 달 1일 00:00 의 벽시계 epoch. */
function kstMonthStartMs(nowMs: number): number {
  const w = new Date(nowMs + KST_OFFSET_MINUTES * 60_000);
  return Date.UTC(w.getUTCFullYear(), w.getUTCMonth(), 1);
}

export function computeDocumentsStats(
  allDocs: readonly DocRow[],
  nowMs: number,
): { stats: DocumentsStats; popularTags: { tag: string; count: number }[] } {
  const failed = allDocs.filter((d) => Boolean((d.flags ?? {})["failed"]));
  const docs = allDocs.filter((d) => !(d.flags ?? {})["failed"]);

  const monthStart = kstMonthStartMs(nowMs);
  const weekAgo = nowMs + KST_OFFSET_MINUTES * 60_000 - 7 * 86_400_000;

  const byDocType: Record<string, number> = {};
  const bySourceChannel: Record<string, number> = {};
  let totalSize = 0;
  let extractSkipped = 0;
  let addedThisMonth = 0;
  let addedLast7d = 0;

  for (const d of docs) {
    // 원본은 `d["doc_type"]` 로 직접 읽는다 — 없으면 KeyError 로 500 이다.
    const dt = d.doc_type as string;
    byDocType[dt] = (byDocType[dt] ?? 0) + 1;
    const sc = d.source_channel as string;
    bySourceChannel[sc] = (bySourceChannel[sc] ?? 0) + 1;
    totalSize += d.size_bytes ?? 0;
    if ((d.flags ?? {})["extract_skipped"]) extractSkipped++;

    const createdKst = toKstWallMs(d.created_at);
    if (createdKst !== null) {
      if (createdKst >= monthStart) addedThisMonth++;
      if (createdKst >= weekAgo) addedLast7d++;
    }
  }

  // `Counter.most_common(10)` — 개수 내림차순, 동수는 **처음 등장한 순서**를 지킨다.
  const counts = new Map<string, number>();
  for (const d of docs) {
    for (const tag of d.tags ?? []) counts.set(tag, (counts.get(tag) ?? 0) + 1);
  }
  const popularTags = [...counts.entries()]
    .sort((a, b) => b[1] - a[1]) // 안정 정렬이라 동수는 삽입 순서 유지
    .slice(0, 10)
    .map(([tag, count]) => ({ tag, count }));

  return {
    stats: {
      total: docs.length,
      by_doc_type: byDocType,
      by_source_channel: bySourceChannel,
      extract_skipped: extractSkipped,
      total_size_bytes: totalSize,
      added_this_month: addedThisMonth,
      added_last_7d: addedLast7d,
      failed_count: failed.length,
    },
    popularTags,
  };
}

/**
 * 5개 SLO 버킷의 `received_ms` 집계.
 *
 * 소형 비스캔 PDF·docx·pptx·txt·md 는 **어느 버킷에도 안 들어간다** — 명세가 이 5종만
 * 측정 대상으로 잡았다. `received_ms` 가 없는 문서(측정 이전 업로드분)도 제외한다.
 */
export function computeSloBuckets(
  allDocs: readonly DocRow[],
): Record<string, SloBucketStats> {
  const buckets: Record<string, number[]> = {
    pdf_50p: [],
    image: [],
    pdf_scan: [],
    hwp: [],
    url: [],
  };
  for (const d of allDocs) {
    const ms = d.received_ms;
    if (ms === null || ms === undefined) continue;
    const docType = d.doc_type;
    const size = d.size_bytes ?? 0;
    const isScan = Boolean((d.flags ?? {})["scan"]);

    if (docType === "pdf") {
      if (isScan) buckets.pdf_scan.push(ms);
      else if (size >= PDF_50P_THRESHOLD_BYTES) buckets.pdf_50p.push(ms);
    } else if (docType === "image") buckets.image.push(ms);
    else if (docType === "hwp" || docType === "hwpx") buckets.hwp.push(ms);
    else if (docType === "url") buckets.url.push(ms);
  }
  const out: Record<string, SloBucketStats> = {};
  for (const [name, samples] of Object.entries(buckets)) out[name] = bucketStats(samples);
  return out;
}

/** nearest-rank p95 — `int(0.95 * (n - 1))`. 작은 n 에서도 안전하게 인덱스가 잡힌다. */
export function bucketStats(samples: readonly number[]): SloBucketStats {
  const n = samples.length;
  if (n === 0) return { p95_ms: null, sample_count: 0, pass_rate: null };
  const sorted = [...samples].sort((a, b) => a - b);
  const p95 = sorted[Math.trunc(0.95 * (n - 1))];
  const passCount = samples.filter((ms) => ms < SLO_TARGET_MS).length;
  return { p95_ms: p95, sample_count: n, pass_rate: pyRound(passCount / n, 4) };
}

/** 5 버킷의 `sample_count` 가중 평균. 표본 0 건 버킷은 빠진다. */
export function computeSloAggregate(
  buckets: Record<string, SloBucketStats>,
): IngestSloAggregate {
  let weightedSum = 0;
  let totalSamples = 0;
  const withSamples: string[] = [];
  for (const [name, b] of Object.entries(buckets)) {
    if (b.sample_count > 0 && b.pass_rate !== null) {
      weightedSum += b.pass_rate * b.sample_count;
      totalSamples += b.sample_count;
      withSamples.push(name);
    }
  }
  return {
    total_samples: totalSamples,
    overall_pass_rate: totalSamples > 0 ? pyRound(weightedSum / totalSamples, 4) : null,
    buckets_with_samples: withSamples,
  };
}

/** `ingest_jobs` 상태 집계. 원본은 **사용자로 안 거른다** — 그대로 옮겼다. */
export function computeJobsStats(
  jobs: readonly { status?: string | null }[],
  failedSample: readonly Record<string, unknown>[],
): { total: number; by_status: Record<string, number>; failed_sample: unknown[] } {
  const byStatus: Record<string, number> = {};
  for (const j of jobs) {
    const s = j.status as string;
    byStatus[s] = (byStatus[s] ?? 0) + 1;
  }
  return { total: jobs.length, by_status: byStatus, failed_sample: [...failedSample] };
}

// --- `/search` SLO -----------------------------------------------------------

/** `fallback_reason` 은 이 셋 중 하나로만 집계된다 — 0 이라도 키가 항상 있어야 한다. */
const FALLBACK_VALUES = ["transient_5xx", "permanent_4xx"] as const;
const NONE_KEY = "none";
export const VALID_SEARCH_MODES = ["hybrid", "dense", "sparse"] as const;

export interface SearchSloSample {
  took_ms: number;
  dense_hits: number;
  sparse_hits: number;
  fused: number;
  has_dense: boolean;
  fallback_reason: string | null;
  embed_cache_hit: boolean;
  mode: string;
}

export interface SearchSloBlock {
  p50_ms: number | null;
  p95_ms: number | null;
  sample_count: number;
  avg_dense_hits: number | null;
  avg_sparse_hits: number | null;
  avg_fused: number | null;
  fallback_count: number;
  fallback_breakdown: Record<string, number>;
  cache_hit_count: number;
  cache_hit_rate: number | null;
}

/** nearest-rank 백분위 — `int(q * (n - 1))`. `/stats` 의 버킷 p95 와 같은 공식이다. */
function percentileNearestRank(sorted: readonly number[], q: number): number {
  return Math.trunc(sorted[Math.trunc(q * (sorted.length - 1))]);
}

/** 표본 묶음 하나의 SLO 통계. 전체와 mode 별이 같은 함수를 쓴다. */
export function computeSearchSloFor(samples: readonly SearchSloSample[]): SearchSloBlock {
  const breakdown: Record<string, number> = {};
  for (const k of [...FALLBACK_VALUES, NONE_KEY]) breakdown[k] = 0;

  const n = samples.length;
  if (n === 0) {
    return {
      p50_ms: null,
      p95_ms: null,
      sample_count: 0,
      avg_dense_hits: null,
      avg_sparse_hits: null,
      avg_fused: null,
      fallback_count: 0,
      fallback_breakdown: breakdown,
      cache_hit_count: 0,
      cache_hit_rate: null,
    };
  }

  const took = samples.map((e) => e.took_ms).sort((a, b) => a - b);
  const mean = (pick: (e: SearchSloSample) => number) =>
    pyRound(samples.reduce((acc, e) => acc + pick(e), 0) / n, 2);

  for (const e of samples) {
    const key = e.fallback_reason === null ? NONE_KEY : e.fallback_reason;
    // 모르는 값은 세지 않는다 — 원본도 3개 키만 채운다.
    if (key in breakdown) breakdown[key] += 1;
  }
  const fallbackCount = FALLBACK_VALUES.reduce((acc, k) => acc + breakdown[k], 0);
  const cacheHits = samples.filter((e) => e.embed_cache_hit).length;

  return {
    p50_ms: percentileNearestRank(took, 0.5),
    p95_ms: percentileNearestRank(took, 0.95),
    sample_count: n,
    avg_dense_hits: mean((e) => e.dense_hits),
    avg_sparse_hits: mean((e) => e.sparse_hits),
    avg_fused: mean((e) => e.fused),
    fallback_count: fallbackCount,
    fallback_breakdown: breakdown,
    cache_hit_count: cacheHits,
    cache_hit_rate: pyRound(cacheHits / n, 4),
  };
}

/** 전체 + mode 별. mode 키는 표본이 0 건이어도 항상 세 개 다 나온다. */
export function computeSearchSlo(
  samples: readonly SearchSloSample[],
  source: string,
): SearchSloBlock & { by_mode: Record<string, SearchSloBlock>; source: string } {
  const overall = computeSearchSloFor(samples);
  const byMode: Record<string, SearchSloBlock> = {};
  for (const m of VALID_SEARCH_MODES) {
    byMode[m] = computeSearchSloFor(samples.filter((e) => (e.mode || "hybrid") === m));
  }
  return { ...overall, by_mode: byMode, source };
}
