/**
 * `vision_missing` 작업 핸들러 — `run_incremental_vision_pipeline` 포팅.
 *
 * 기존 chunks 를 **그대로 두고** vision 이 아직 안 닿은 페이지만 덧붙인다.
 * 전체 재인제스트처럼 chunks 를 다 지우지 않으므로, vision 이 503 으로 한 페이지만
 * 실패해도 나머지 답변이 사라지지 않는다.
 *
 * ## 큐 흐름이 전체 경로와 다르다
 * ```
 * vision_missing(4p/태스크) → … → embed
 * ```
 * `chunk` 를 거치지 않는다. 원본이 `run_load_stage(chunks=...)` 로 **직접** 적재하기
 * 때문이다 — chunk 단계를 태우면 기존 청크를 재구성하게 되어 "보존" 이 아니게 된다.
 * 그래서 여기서 `chunks` 에 직접 upsert 한다.
 *
 * ## 남은 페이지를 다시 계산하지 않고 **이어받는다**
 * 매 태스크마다 `chunks` 에서 누락을 다시 구하면, `needs_vision` 이 false 인 페이지는
 * 청크가 안 생기므로 영원히 "누락" 으로 남아 **무한 루프**가 된다. 첫 태스크에서 한 번
 * 구한 목록을 아티팩트로 넘긴다.
 *
 * ## chunk_idx
 * 원본은 sweep 이 끝난 뒤 `max(chunk_idx) + 1` 부터 한 번에 매긴다. 여기서는 창마다
 * 다시 구하는데, 창이 페이지 오름차순이고 창 안 순서도 같으므로 최종 배정은 같다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

import { pyFormatF, pyRound } from "../../pynum.ts";
import { readChunkEnv } from "../chunk_records.ts";
import { chunkRecordToRow } from "../chunk_row.ts";
import { countPdfPages } from "../pdf_raster.ts";
import { stripNulls } from "../strip_nul.ts";
import * as visionCache from "../vision_cache.ts";
import { type BudgetStatus, checkCombined } from "../budget_guard.ts";
import { asIngestMode, DEFAULT_INGEST_MODE, resolvePageCap } from "../ingest_mode.ts";
import {
  emptyCarry,
  readVisionEnv,
  runVisionWindow,
  VISION_PAGES_PER_TASK,
  type VisionCarry,
  type VisionEnv,
} from "../vision_enrich.ts";
import { maxChunkIdx, sectionsToChunks, visionProcessedPages } from "../vision_incremental.ts";
import { injectSynonyms } from "../synonym_inject.ts";
import type { TaskHandler, TaskPayload } from "../worker.ts";

/** `load` 와 같은 이유로 같은 값 — Supabase statement_timeout 안에 들어가야 한다. */
const UPSERT_BATCH = 50;

export interface VisionMissingDeps {
  client: SupabaseClient;
  bucket: string;
  env?: Record<string, string | undefined>;
  visionEnv?: VisionEnv;
  download?: (path: string) => Promise<Uint8Array>;
  pagesPerTask?: number;
  batchSize?: number;
  nowMs?: () => number;
}

interface DocRow {
  doc_type?: string;
  storage_path?: string;
  sha256?: string | null;
  flags?: Record<string, unknown> | null;
}

/** 창 사이로 넘기는 상태. 남은 페이지 목록이 핵심이다(1-based). */
interface MissingCarry {
  carry: VisionCarry;
  remaining: number[];
  /** 1 차 sweep 대상 전체 수 — cap 메시지의 "남은 페이지 N" 이 이걸 쓴다. */
  pendingTotal: number;
  /** 지금까지 소비한 개수 = 다음 창의 `pendingIndexBase`. */
  consumed: number;
}

async function defaultDownload(
  client: SupabaseClient,
  bucket: string,
  path: string,
): Promise<Uint8Array> {
  const { data, error } = await client.storage.from(bucket).download(path);
  if (error) throw new Error(`Storage 다운로드 실패 (${path}): ${error.message}`);
  if (!data) throw new Error(`Storage 응답이 비었다 (${path})`);
  return new Uint8Array(await data.arrayBuffer());
}

async function loadPrev(
  client: SupabaseClient,
  jobId: string,
  from: number,
): Promise<MissingCarry | null> {
  if (from <= 0) return null;
  const { data, error } = await client
    .from("ingest_artifacts")
    .select("seq, payload")
    .eq("job_id", jobId)
    .eq("stage", "vision_missing")
    .lt("seq", from)
    .order("seq", { ascending: false })
    .limit(1);
  if (error) throw new Error(`직전 vision_missing 산출물 조회 실패: ${error.message}`);
  const row = (data ?? [])[0] as { payload?: Record<string, unknown> } | undefined;
  if (!row) {
    throw new Error(
      `직전 vision_missing 창 산출물이 없다 (job=${jobId}, from=${from}). 순서가 깨졌다.`,
    );
  }
  return {
    carry: (row.payload?.["carry"] ?? emptyCarry()) as VisionCarry,
    remaining: (row.payload?.["remaining"] ?? []) as number[],
    pendingTotal: Number(row.payload?.["pending_total"] ?? 0),
    consumed: Number(row.payload?.["consumed"] ?? 0),
  };
}

/** 원본의 cost cap flags 마킹 — 사전/사후 양쪽이 같은 payload 를 쓴다. */
async function markBudgetExceeded(
  client: SupabaseClient,
  docId: string,
  status: BudgetStatus,
): Promise<void> {
  const { data } = await client.from("documents").select("flags").eq("id", docId).limit(1);
  const existing = ((data ?? [])[0] as { flags?: Record<string, unknown> } | undefined)
    ?.flags ?? {};
  await client.from("documents").update({
    flags: {
      ...existing,
      vision_budget_exceeded: true,
      vision_budget: {
        scope: status.scope,
        used_usd: pyRound(status.usedUsd, 6),
        cap_usd: pyRound(status.capUsd, 6),
        reason: status.reason,
      },
    },
  }).eq("id", docId);
}

async function markPageCapExceeded(
  client: SupabaseClient,
  docId: string,
  status: BudgetStatus,
): Promise<void> {
  const { data } = await client.from("documents").select("flags").eq("id", docId).limit(1);
  const existing = ((data ?? [])[0] as { flags?: Record<string, unknown> } | undefined)
    ?.flags ?? {};
  await client.from("documents").update({
    flags: {
      ...existing,
      vision_page_cap_exceeded: true,
      vision_page_cap: {
        called_pages: Math.trunc(status.usedUsd),
        page_cap: Math.trunc(status.capUsd),
        reason: status.reason,
      },
    },
  }).eq("id", docId);
}

export function makeVisionMissingHandler(deps: VisionMissingDeps): TaskHandler {
  const env = deps.env ?? Deno.env.toObject();
  const pagesPerTask = deps.pagesPerTask ?? VISION_PAGES_PER_TASK;
  const batchSize = Math.max(1, deps.batchSize ?? UPSERT_BATCH);
  const now = deps.nowMs ?? (() => Date.now());

  return async (task: TaskPayload) => {
    const ve = deps.visionEnv ?? readVisionEnv(env);
    const from = task.from ?? 0;

    const { data: docs, error: docErr } = await deps.client
      .from("documents")
      .select("doc_type, storage_path, sha256, flags")
      .eq("id", task.doc_id)
      .limit(1);
    if (docErr) throw new Error(`documents 조회 실패: ${docErr.message}`);
    const doc = (docs ?? [])[0] as DocRow | undefined;
    if (!doc) throw new Error(`documents 레코드 없음: ${task.doc_id}`);
    if (doc.doc_type !== "pdf") {
      throw new Error(`incremental vision 은 PDF 만 지원 (doc_type=${doc.doc_type})`);
    }
    const flags = doc.flags ?? {};
    const path = doc.storage_path;
    if (!path) throw new Error(`storage_path 가 비었다: ${task.doc_id}`);
    if (path.startsWith("pending/")) {
      throw new Error(`storage_path 가 아직 pending 이다: ${path}`);
    }
    const fileName = path.split("/").pop() ?? path;

    const bytes = deps.download
      ? await deps.download(path)
      : await defaultDownload(deps.client, deps.bucket, path);
    const totalPages = await countPdfPages(bytes);

    // --- 남은 페이지 결정 ---
    let state: MissingCarry;
    if (from === 0) {
      const processed = await visionProcessedPages(deps.client, task.doc_id);
      const missing: number[] = [];
      for (let p = 1; p <= totalPages; p++) if (!processed.has(p)) missing.push(p);
      console.info(
        `incremental_vision: doc=${task.doc_id} total=${totalPages} ` +
          `processed=${processed.size} missing=${missing.length} ` +
          `[${missing.slice(0, 20).join(", ")}]`,
      );
      if (missing.length === 0) {
        // 원본은 여기서 잡을 completed 로 마감한다. Edge 는 embed 까지 태워야
        // dense_vec NULL 이 남아 있을 때 회수되므로 embed 로 넘긴다(빈 작업이면 즉시 끝난다).
        console.info(`incremental_vision: 누락 0 — skip (doc=${task.doc_id})`);
        await enqueue(deps.client, { job_id: task.job_id, doc_id: task.doc_id, stage: "embed" });
        return;
      }

      // --- 사전 비용 검사 (전 페이지 캐시 hit 이면 우회) ---
      const uncached = doc.sha256
        ? await visionCache.countUncachedPages(
          { client: deps.client, env },
          doc.sha256,
          missing,
        )
        : null;
      if (uncached === 0) {
        console.info(
          `incremental_vision — missing ${missing.length} 페이지 모두 cache hit, ` +
            `사전 cap check 우회 (doc=${task.doc_id})`,
        );
      } else {
        const pre = await checkCombined(
          { client: deps.client, env, nowMs: now() },
          {
            docId: task.doc_id,
            docCapUsd: ve.docBudgetUsd,
            dailyCapUsd: ve.dailyBudgetUsd,
            sliding24hCapUsd: ve.sliding24hBudgetUsd,
          },
        );
        if (!pre.allowed) {
          console.warn(
            `incremental_vision skip — budget cap (scope=${pre.scope}, ` +
              `used=$${pyFormatF(pre.usedUsd, 4)}, cap=$${pyFormatF(pre.capUsd, 4)}) ` +
              `doc=${task.doc_id}`,
          );
          await markBudgetExceeded(deps.client, task.doc_id, pre);
          await enqueue(deps.client, {
            job_id: task.job_id,
            doc_id: task.doc_id,
            stage: "embed",
          });
          return;
        }
      }
      state = {
        carry: emptyCarry(),
        remaining: missing,
        pendingTotal: missing.length,
        consumed: 0,
      };
    } else {
      state = (await loadPrev(deps.client, task.job_id, from))!;
    }

    // --- 이 창이 맡을 페이지 (0-based 로 바꿔 넘긴다) ---
    const windowPages1 = state.remaining.slice(0, pagesPerTask);
    const rest = state.remaining.slice(windowPages1.length);
    const pageCap = resolvePageCap(
      asIngestMode(flags["ingest_mode"]) ?? DEFAULT_INGEST_MODE,
      { pageCapPerDoc: ve.pageCapPerDoc, env },
    );

    const result = await runVisionWindow(
      { client: deps.client, env, visionEnv: ve, nowMs: now() },
      {
        bytes,
        jobId: task.job_id,
        docId: task.doc_id,
        fileName,
        sha256: doc.sha256 ?? null,
        pages: windowPages1.map((p) => p - 1),
        pendingTotal: state.pendingTotal,
        pendingIndexBase: state.consumed,
        pageCap,
        carry: state.carry,
        label: "incremental_vision",
        // 원본 증분 경로는 진행 표시를 갱신하지 않는다.
        progressTotal: null,
      },
    );

    // --- 청크로 만들어 바로 적재 ---
    const startIdx = (await maxChunkIdx(deps.client, task.doc_id)) + 1;
    const records = sectionsToChunks(result.sections, {
      docId: task.doc_id,
      startChunkIdx: startIdx,
      // `readChunkEnv` 는 getter 를 받는다 — 주입한 env 를 그대로 쓰게 감싼다.
      env: readChunkEnv((k: string) => env[k]),
      // 증분 경로도 같은 주입기를 쓴다. 원본은 doc-level LLM 후보를 여기서 만들지
      // 않으므로(`sectionsToChunks` 는 dict 만) `null` 을 넘긴다 —
      // `metadata.synonym_source` 가 항상 "dict" 인 것도 그래서다.
      injectSynonyms: (text: string) => injectSynonyms(text, null),
    });
    for (let i = 0; i < records.length; i += batchSize) {
      const rows = records.slice(i, i + batchSize).map(chunkRecordToRow);
      const { error: upErr } = await deps.client
        .from("chunks").upsert(rows, { onConflict: "doc_id,chunk_idx" });
      if (upErr) {
        throw new Error(`chunks upsert 실패 (${i}~${i + rows.length}): ${upErr.message}`);
      }
    }

    if (result.metricErrors.length > 0) {
      console.error(
        `vision_usage_log 적재 실패 ${result.metricErrors.length}건 — 비용 한도가 ` +
          `안 걸린다. doc=${task.doc_id} ${result.metricErrors.join(" / ")}`,
      );
    }

    const done = result.stopped || rest.length === 0;
    const payload = stripNulls({
      carry: result.carry,
      remaining: done ? [] : rest,
      pending_total: state.pendingTotal,
      consumed: state.consumed + windowPages1.length,
      pages: windowPages1,
      chunks_inserted: records.length,
      start_chunk_idx: startIdx,
      warnings: result.warnings,
      failed_pages: result.failedPages,
      metric_errors: result.metricErrors,
      total_pages: totalPages,
    } as Record<string, unknown>);
    if (payload.removed > 0) payload.value["nul_removed"] = payload.removed;

    const { error: artErr } = await deps.client
      .from("ingest_artifacts")
      .upsert({
        job_id: task.job_id,
        doc_id: task.doc_id,
        stage: "vision_missing",
        seq: from,
        payload: payload.value,
      }, { onConflict: "job_id,stage,seq" });
    if (artErr) throw new Error(`ingest_artifacts 저장 실패: ${artErr.message}`);

    if (done) {
      // 원본은 sweep 이 끝난 뒤 사후 cost cap 을 한 번 더 본다(이미 처리한 페이지는 보존).
      try {
        const post = await checkCombined(
          { client: deps.client, env, nowMs: now() },
          {
            docId: task.doc_id,
            docCapUsd: ve.docBudgetUsd,
            dailyCapUsd: ve.dailyBudgetUsd,
            sliding24hCapUsd: ve.sliding24hBudgetUsd,
          },
        );
        if (!post.allowed) await markBudgetExceeded(deps.client, task.doc_id, post);
        if (result.carry.pageCapExceeded !== null) {
          await markPageCapExceeded(deps.client, task.doc_id, result.carry.pageCapExceeded);
        }
      } catch (e) {
        console.warn(`incremental_vision flags 마킹 실패 (graceful): ${e} (doc=${task.doc_id})`);
      }
      console.info(
        `incremental_vision done: doc=${task.doc_id} called=${result.carry.calledCount} ` +
          `skipped_need_score=${result.carry.skippedByNeedScore.length} page_cap=${pageCap} ` +
          `page_cap_exceeded=${result.carry.pageCapExceeded !== null}`,
      );
    }

    // 저장이 끝난 뒤에 다음 작업을 넣는다 — 다른 핸들러와 같은 계약이다.
    await enqueue(
      deps.client,
      done ? { job_id: task.job_id, doc_id: task.doc_id, stage: "embed" } : {
        job_id: task.job_id,
        doc_id: task.doc_id,
        stage: "vision_missing",
        from: from + 1,
      },
    );
  };
}

async function enqueue(client: SupabaseClient, payload: TaskPayload): Promise<void> {
  const { error } = await client.rpc("ingest_queue_send", { payload });
  if (error) throw new Error(`다음 작업 enqueue 실패 (${payload.stage}): ${error.message}`);
}
