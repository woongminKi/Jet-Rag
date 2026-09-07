/**
 * `scan` 작업 핸들러 — 텍스트 레이어가 없는 PDF 를 vision 으로 읽는다.
 *
 * `extract` 가 문서 전체 `raw_text` 를 보고 스캔이라고 판정하면 여기로 온다.
 * 원본은 `run_extract_stage` 안에서 `result = _reroute_pdf_to_image(...)` 로 **결과를
 * 통째로 갈아끼운다.** 그래서 여기 산출물은 `extract` 산출물을 보강하는 게 아니라
 * **대체**한다 — `chunk` 가 `stage='scan'` 이 있으면 그쪽만 쓴다.
 *
 * ## 창은 나누지만 상한이 5 페이지다
 * `MAX_SCAN_PAGES = 5`. 태스크당 4 페이지(`VISION_PAGES_PER_TASK`)면 최대 2 태스크다.
 * 누적할 카운터가 없어 `vision` 만큼 상태를 이어받을 게 없다.
 *
 * ## `flags.scan = true`
 * 원본 `_mark_scan_flag`. 이게 붙으면 `vision` 핸들러가 스스로 비켜선다(이미 vision 으로
 * 읽은 문서를 또 읽지 않는다). `doc_type` 은 `pdf` 그대로 둔다 — DB CHECK 제약 때문이다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

import { countPdfPages } from "../pdf_raster.ts";
import { stripNulls } from "../strip_nul.ts";
import { readVisionEnv, VISION_PAGES_PER_TASK, type VisionEnv } from "../vision_enrich.ts";
import { MAX_SCAN_PAGES, runScanWindow } from "../vision_scan.ts";
import type { TaskHandler, TaskPayload } from "../worker.ts";

export interface ScanDeps {
  client: SupabaseClient;
  bucket: string;
  env?: Record<string, string | undefined>;
  visionEnv?: VisionEnv;
  download?: (path: string) => Promise<Uint8Array>;
  pagesPerTask?: number;
  nowMs?: () => number;
}

interface DocRow {
  doc_type?: string;
  storage_path?: string;
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

export function makeScanHandler(deps: ScanDeps): TaskHandler {
  const env = deps.env ?? Deno.env.toObject();
  const pagesPerTask = deps.pagesPerTask ?? VISION_PAGES_PER_TASK;
  const now = deps.nowMs ?? (() => Date.now());

  return async (task: TaskPayload) => {
    const ve = deps.visionEnv ?? readVisionEnv(env);
    const from = task.from ?? 0;

    const { data: docs, error: docErr } = await deps.client
      .from("documents")
      .select("doc_type, storage_path, flags")
      .eq("id", task.doc_id)
      .limit(1);
    if (docErr) throw new Error(`documents 조회 실패: ${docErr.message}`);
    const doc = (docs ?? [])[0] as DocRow | undefined;
    if (!doc) throw new Error(`문서를 찾을 수 없다: ${task.doc_id}`);
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
    const processCount = Math.min(totalPages, MAX_SCAN_PAGES);

    const warnings: string[] = [];
    if (from === 0) {
      console.info(
        `스캔 PDF 감지 → vision fallback. total=${totalPages} 처리=${processCount} ` +
          `doc=${task.doc_id}`,
      );
      if (totalPages > MAX_SCAN_PAGES) {
        const msg = `스캔 PDF ${totalPages}페이지 중 첫 ${MAX_SCAN_PAGES}페이지만 처리 ` +
          `(Vision API 비용 cap)`;
        warnings.push(msg);
        console.warn(`${msg} (file=${fileName})`);
      }
      // 원본 `_mark_scan_flag` — 기존 flags 를 보존하고 `scan` 만 켠다.
      // **vision 태스크가 돌기 전에** 켜져 있어야 그쪽이 비켜선다.
      const { error } = await deps.client
        .from("documents")
        .update({ flags: { ...(doc.flags ?? {}), scan: true } })
        .eq("id", task.doc_id);
      if (error) throw new Error(`flags.scan 마킹 실패: ${error.message}`);
    }

    const pages: number[] = [];
    for (let p = from; p < Math.min(processCount, from + pagesPerTask); p++) pages.push(p);

    const result = await runScanWindow(
      {
        client: deps.client,
        env,
        geminiApiKey: ve.geminiApiKey,
        nowMs: now(),
      },
      { bytes, docId: task.doc_id, fileName, pages },
    );

    if (result.metricErrors.length > 0) {
      console.error(
        `vision_usage_log 적재 실패 ${result.metricErrors.length}건 — 비용 한도가 ` +
          `안 걸린다. doc=${task.doc_id} ${result.metricErrors.join(" / ")}`,
      );
    }

    const payload = stripNulls({
      // 원본은 `source_type="pdf"` 를 유지한다 — 본질은 PDF 이고 `flags.scan` 으로 구분한다.
      source_type: "pdf",
      sections: result.sections,
      raw_text: result.rawParts.join("\n\n"),
      warnings: [...warnings, ...result.warnings],
      page_from: from,
      page_count: pages.length,
      process_count: processCount,
      total_pages: totalPages,
      called: result.calledCount,
      metric_errors: result.metricErrors,
    } as Record<string, unknown>);
    if (payload.removed > 0) payload.value["nul_removed"] = payload.removed;

    const { error: upErr } = await deps.client
      .from("ingest_artifacts")
      .upsert({
        job_id: task.job_id,
        doc_id: task.doc_id,
        stage: "scan",
        seq: from,
        payload: payload.value,
      }, { onConflict: "job_id,stage,seq" });
    if (upErr) throw new Error(`ingest_artifacts 저장 실패: ${upErr.message}`);

    // 저장이 끝난 뒤에 다음 작업을 넣는다 — 다른 핸들러와 같은 계약이다.
    const nextFrom = from + pagesPerTask;
    const next: TaskPayload = nextFrom < processCount
      ? { job_id: task.job_id, doc_id: task.doc_id, stage: "scan", from: nextFrom }
      : { job_id: task.job_id, doc_id: task.doc_id, stage: "chunk" };
    const { error: sendErr } = await deps.client.rpc("ingest_queue_send", { payload: next });
    if (sendErr) throw new Error(`다음 작업 enqueue 실패: ${sendErr.message}`);
  };
}
