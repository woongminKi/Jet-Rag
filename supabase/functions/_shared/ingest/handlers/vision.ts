/**
 * `vision` 작업 핸들러 — 텍스트 PDF 를 페이지 창 단위로 vision 보강한다.
 *
 * ## 왜 extract 와 분리된 스테이지인가
 * 1. `reingest-missing` 이 **vision 전용** 경로다. 텍스트 재추출 없이 누락된 페이지만
 *    다시 채워야 하는데, extract 안에 묶여 있으면 그게 안 된다.
 * 2. CPU 예산이 단계별로 분리된다. extract 는 10 페이지 창을 유지하고 vision 만
 *    4 페이지 창을 쓴다(`VISION_PAGES_PER_TASK` — 실측 근거는 그쪽 주석 참조).
 *
 * ## 섹션 순서가 원본과 같아야 한다
 * 원본은 PyMuPDF 섹션을 **전부** 깔고 그 뒤에 vision 섹션을 붙인다. 그래서 아티팩트를
 * `stage='extract'` 와 `stage='vision'` 으로 나눠 두고, `chunk` 가 extract 전부 →
 * vision 전부 순으로 이어 붙인다. 창 단위로 번갈아 섞이면 순서가 깨진다.
 *
 * ## 사전 검사는 첫 창에서만
 * 원본은 vision 진입 직전에 딱 한 번 비용 cap 을 본다. `from === 0` 일 때만 한다.
 * 전 페이지가 캐시 hit 이면 그 검사를 건너뛴다 — 누적 SUM 은 과거 비용이라, 신규 비용
 * 0 인 재인제스트가 과거 누적 때문에 막히던 회귀(2026-05-14 P1) 를 그대로 옮긴 것이다.
 *
 * ## 멱등성
 * `UNIQUE(job_id, stage, seq)` 위에 upsert 한다. 같은 창이 두 번 배달돼도 행은 하나다.
 * 다만 **vision 호출은 두 번 나갈 수 있다** — 그건 `vision_page_cache` 가 막는다
 * (첫 시도가 캐시에 넣었으면 두 번째는 hit).
 */

import type { SupabaseClient } from "@supabase/supabase-js";

import { pyFormatF, pyRound } from "../../pynum.ts";
import { countPdfPages } from "../pdf_raster.ts";
import { stripNulls } from "../strip_nul.ts";
import * as visionCache from "../vision_cache.ts";
import {
  clearStageProgress,
  emptyCarry,
  readVisionEnv,
  resolvePageCapForDoc,
  runVisionWindow,
  VISION_PAGES_PER_TASK,
  type VisionCarry,
  type VisionEnv,
} from "../vision_enrich.ts";
import { checkCombined, type BudgetStatus } from "../budget_guard.ts";
import type { TaskHandler, TaskPayload } from "../worker.ts";

export interface VisionDeps {
  client: SupabaseClient;
  bucket: string;
  env?: Record<string, string | undefined>;
  visionEnv?: VisionEnv;
  /** 테스트 주입. */
  download?: (path: string) => Promise<Uint8Array>;
  pagesPerTask?: number;
  nowMs?: () => number;
}

interface DocRow {
  doc_type?: string;
  storage_path?: string;
  sha256?: string | null;
  flags?: Record<string, unknown> | null;
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

/** 직전 창이 남긴 누적 카운터. `from === 0` 이면 새 문서라 빈 값이다. */
async function loadCarry(
  client: SupabaseClient,
  jobId: string,
  from: number,
): Promise<VisionCarry> {
  if (from <= 0) return emptyCarry();
  const { data, error } = await client
    .from("ingest_artifacts")
    .select("seq, payload")
    .eq("job_id", jobId)
    .eq("stage", "vision")
    .lt("seq", from)
    .order("seq", { ascending: false })
    .limit(1);
  if (error) throw new Error(`직전 vision 산출물 조회 실패: ${error.message}`);
  const row = (data ?? [])[0] as { payload?: Record<string, unknown> } | undefined;
  if (!row) {
    throw new Error(
      `직전 vision 창 산출물이 없다 (job=${jobId}, from=${from}). ` +
        "vision 은 순차로 돌아야 한다 — 순서가 깨졌다.",
    );
  }
  const c = row.payload?.["carry"];
  return (c && typeof c === "object" ? c : emptyCarry()) as VisionCarry;
}

/** 원본 `_mark_budget_exceeded_flag` — 기존 flags 를 보존하며 덧쓴다. */
async function markBudgetExceeded(
  client: SupabaseClient,
  docId: string,
  existing: Record<string, unknown>,
  status: BudgetStatus,
): Promise<void> {
  const updated = {
    ...existing,
    vision_budget_exceeded: true,
    vision_budget: {
      scope: status.scope,
      used_usd: pyRound(status.usedUsd, 6),
      cap_usd: pyRound(status.capUsd, 6),
      reason: status.reason,
    },
  };
  await client.from("documents").update({ flags: updated }).eq("id", docId);
}

/** 원본 `_mark_page_cap_exceeded_flag`. `used_usd` 에 호출 페이지 수가 들어 있다. */
async function markPageCapExceeded(
  client: SupabaseClient,
  docId: string,
  existing: Record<string, unknown>,
  status: BudgetStatus,
): Promise<void> {
  const updated = {
    ...existing,
    vision_page_cap_exceeded: true,
    vision_page_cap: {
      called_pages: Math.trunc(status.usedUsd), // Python `int()` = 절삭
      page_cap: Math.trunc(status.capUsd),
      reason: status.reason,
    },
  };
  await client.from("documents").update({ flags: updated }).eq("id", docId);
}

export function makeVisionHandler(deps: VisionDeps): TaskHandler {
  const env = deps.env ?? Deno.env.toObject();
  const pagesPerTask = deps.pagesPerTask ?? VISION_PAGES_PER_TASK;
  const now = deps.nowMs ?? (() => Date.now());

  return async (task: TaskPayload) => {
    const ve = deps.visionEnv ?? readVisionEnv(env);

    const { data: docs, error: docErr } = await deps.client
      .from("documents")
      .select("doc_type, storage_path, sha256, flags")
      .eq("id", task.doc_id)
      .limit(1);
    if (docErr) throw new Error(`documents 조회 실패: ${docErr.message}`);
    const doc = (docs ?? [])[0] as DocRow | undefined;
    if (!doc) throw new Error(`문서를 찾을 수 없다: ${task.doc_id}`);

    const flags = doc.flags ?? {};
    const from = task.from ?? 0;

    // 스캔 PDF 는 이미 vision 으로 처리된 문서다 — 원본도 여기서 제외한다.
    const skipReason = !ve.enabled
      ? "vision enrich 비활성 (JETRAG_PDF_VISION_ENRICH)"
      : doc.doc_type !== "pdf"
      ? `pdf 가 아니다 (${doc.doc_type})`
      : flags["scan"] === true
      ? "스캔 PDF — 이미 vision 처리됨"
      : null;
    if (skipReason !== null) {
      // 조용히 끝내되 이유는 남긴다. 다음 단계로 넘겨야 잡이 안 멈춘다.
      console.info(`vision 건너뜀 (${skipReason}) doc=${task.doc_id}`);
      await enqueueChunk(deps.client, task);
      return;
    }

    const path = doc.storage_path;
    if (!path) throw new Error(`storage_path 가 비었다: ${task.doc_id}`);
    if (path.startsWith("pending/")) {
      throw new Error(`storage_path 가 아직 pending 이다: ${path}`);
    }
    const bytes = deps.download
      ? await deps.download(path)
      : await defaultDownload(deps.client, deps.bucket, path);

    const totalPages = await countPdfPages(bytes);
    const processCount = Math.min(totalPages, ve.maxPages);
    const warnings: string[] = [];

    if (from === 0 && totalPages > ve.maxPages) {
      const msg = `vision_enrich: ${totalPages}페이지 중 첫 ${ve.maxPages}페이지만 처리 ` +
        `(paid tier RPM/latency 보호)`;
      warnings.push(msg);
      console.warn(`${msg} (doc=${task.doc_id})`);
    }

    // --- 첫 창에서만 사전 비용 검사 ---
    if (from === 0) {
      const pages: number[] = [];
      for (let p = 1; p <= processCount; p++) pages.push(p);
      const uncached = doc.sha256
        ? await visionCache.countUncachedPages(
          { client: deps.client, env }, doc.sha256, pages)
        : null;
      const allCached = uncached === 0 && processCount > 0;
      if (allCached) {
        console.info(
          `PDF vision enrich — 모든 페이지 cache hit, 사전 cap check 우회 (doc=${task.doc_id})`,
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
            `PDF vision enrich skip — budget cap (scope=${pre.scope}, ` +
              `used=$${pyFormatF(pre.usedUsd, 4)}, cap=$${pyFormatF(pre.capUsd, 4)}) doc=${task.doc_id}`,
          );
          await markBudgetExceeded(deps.client, task.doc_id, flags, pre);
          // 원본은 이때 vision 을 아예 안 돈다 — 아티팩트도 안 남기고 chunk 로 간다.
          await enqueueChunk(deps.client, task);
          return;
        }
      }
    }

    const carry = await loadCarry(deps.client, task.job_id, from);
    // 원본의 `page_cap_override` 는 운영 모드(fast/default/precise) UI 토글에서 온다.
    // 그 진입점(reingest 라우트)이 아직 안 옮겨졌으므로 지금은 항상 settings 값이다.
    // reingest 를 옮길 때 여기로 값을 흘려보내야 한다.
    const pageCap = resolvePageCapForDoc(null, ve.pageCapPerDoc);

    const result = await runVisionWindow(
      { client: deps.client, env, visionEnv: ve, nowMs: now() },
      {
        bytes,
        jobId: task.job_id,
        docId: task.doc_id,
        fileName: path.split("/").pop() ?? path,
        sha256: doc.sha256 ?? null,
        from,
        count: pagesPerTask,
        processCount,
        pageCap,
        carry,
      },
    );

    const payload = stripNulls({
      sections: result.sections,
      raw_text: result.rawParts.join("\n\n"),
      warnings: [...warnings, ...result.warnings],
      carry: result.carry,
      page_from: from,
      page_count: Math.min(pagesPerTask, Math.max(0, processCount - from)),
      process_count: processCount,
      total_pages: totalPages,
      failed_pages: result.failedPages,
      // 비어 있어야 정상 — 차면 비용 한도가 안 걸린다는 뜻이다.
      metric_errors: result.metricErrors,
    } as Record<string, unknown>);
    if (payload.removed > 0) payload.value["nul_removed"] = payload.removed;

    const { error: upErr } = await deps.client
      .from("ingest_artifacts")
      .upsert({
        job_id: task.job_id,
        doc_id: task.doc_id,
        stage: "vision",
        seq: from,
        payload: payload.value,
      }, { onConflict: "job_id,stage,seq" });
    if (upErr) throw new Error(`ingest_artifacts 저장 실패: ${upErr.message}`);

    if (result.metricErrors.length > 0) {
      console.error(
        `vision_usage_log 적재 실패 ${result.metricErrors.length}건 — 비용 한도가 ` +
          `안 걸린다. doc=${task.doc_id} ${result.metricErrors.join(" / ")}`,
      );
    }

    const nextFrom = from + pagesPerTask;
    const hasMore = !result.stopped && nextFrom < processCount;

    if (!hasMore) {
      // 원본은 루프가 끝난 뒤 cap flags 를 마킹한다. 실패해도 인제스트는 계속 간다.
      try {
        if (result.carry.budgetExceeded !== null) {
          await markBudgetExceeded(
            deps.client, task.doc_id, flags, result.carry.budgetExceeded);
        }
        if (result.carry.pageCapExceeded !== null) {
          await markPageCapExceeded(
            deps.client, task.doc_id, flags, result.carry.pageCapExceeded);
        }
      } catch (e) {
        console.warn(`vision cap flags 마킹 실패 (graceful): ${e} (doc=${task.doc_id})`);
      }
      console.info(
        `vision_enrich: doc=${task.doc_id} processed=${result.carry.completed} ` +
          `called=${result.carry.calledCount} ` +
          `skipped_need_score=${result.carry.skippedByNeedScore.length} ` +
          `(pages=[${result.carry.skippedByNeedScore.slice(0, 20).join(", ")}]) ` +
          `need_score_enabled=${ve.needScoreEnabled} page_cap=${pageCap} ` +
          `page_cap_exceeded=${result.carry.pageCapExceeded !== null}`,
      );
      await clearStageProgress(deps.client, task.job_id);
    }

    // **저장이 끝난 뒤에** 다음 작업을 넣는다 — extract 와 같은 계약이다.
    const next: TaskPayload = hasMore
      ? {
        job_id: task.job_id,
        doc_id: task.doc_id,
        stage: "vision",
        from: nextFrom,
      }
      : { job_id: task.job_id, doc_id: task.doc_id, stage: "chunk" };
    const { error: sendErr } = await deps.client.rpc("ingest_queue_send", { payload: next });
    if (sendErr) throw new Error(`다음 작업 enqueue 실패: ${sendErr.message}`);
  };
}

async function enqueueChunk(client: SupabaseClient, task: TaskPayload): Promise<void> {
  const { error } = await client.rpc("ingest_queue_send", {
    payload: { job_id: task.job_id, doc_id: task.doc_id, stage: "chunk" },
  });
  if (error) throw new Error(`다음 작업 enqueue 실패: ${error.message}`);
}
