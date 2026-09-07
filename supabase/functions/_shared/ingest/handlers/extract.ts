/**
 * `extract` 작업 핸들러 — Storage 에서 파일을 받아 파싱하고 산출물을 저장한다.
 *
 * ## 포맷별 처리 단위가 다르다
 * | doc_type | 단위 | 이유 |
 * |---|---|---|
 * | `pdf` | **페이지 `PDF_PAGES_PER_TASK` 개씩** | 1,513 페이지 문서가 있다. CPU 2s 안에 못 끝낸다 |
 * | `hwp` | 문서 전체 1 회 | 페이지 개념이 없다(원본도 통째로 추출한다) |
 *
 * ## PDF 는 **순차**로 돌아야 한다
 * `current_title` 이 문서 전체 sticky 라, 페이지 범위를 병렬로 처리하면 제목이 어긋난다.
 * 그래서 범위를 한꺼번에 큐에 넣지 않고 **직전 범위가 끝날 때 다음 하나만** 넣는다.
 * 직전 범위의 `next_title` 은 그 아티팩트에서 읽는다 — 큐 메시지에 실어 나르면 재시도
 * 때 낡은 값이 따라올 수 있다.
 *
 * ## 모르는 포맷은 던진다
 * 조용히 건너뛰면 잡이 영원히 running 으로 남고 어디서 멈췄는지도 안 보인다. 던지면
 * 워커가 재시도하고 한도를 넘기면 archive + 잡 failed 로 마감한다(`worker.ts` 계약).
 *
 * ## 멱등성
 * vt 만료로 같은 작업이 두 번 배달될 수 있다. `ingest_artifacts` 의
 * `UNIQUE(job_id, stage, seq)` 위에 **upsert** 해서 두 번 돌아도 행이 하나다.
 * 다음 태스크 enqueue 는 중복될 수 있지만, 그 태스크도 같은 seq 에 upsert 하므로
 * 결과는 같다 — 낭비일 뿐 오염이 아니다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

import { extractHwp } from "../hwp_extract.ts";
import { PDF_PAGES_PER_TASK } from "../pdf_extract.ts";
import { extractPdfRange, type PdfRangeResult } from "../pdf_open.ts";
import { stripNulls } from "../strip_nul.ts";
import { readVisionEnv } from "../vision_enrich.ts";
import type { TaskHandler, TaskPayload } from "../worker.ts";

/** 지금 처리할 수 있는 `documents.doc_type`. */
export const SUPPORTED_DOC_TYPES = new Set(["hwp", "pdf"]);

export interface ExtractDeps {
  client: SupabaseClient;
  bucket: string;
  /** 테스트 주입 — 실제 Storage 없이 바이트를 넣는다. */
  download?: (path: string) => Promise<Uint8Array>;
  /** 테스트 주입 — mupdf WASM 없이 페이지 범위 결과를 넣는다. */
  extractPdf?: (
    bytes: Uint8Array,
    opts: { from: number; count: number; carryTitle: string | null },
  ) => Promise<PdfRangeResult>;
  /** 한 태스크가 맡을 페이지 수. */
  pagesPerTask?: number;
  /** 테스트 주입 — ENV 를 직접 준다. */
  env?: Record<string, string | undefined>;
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

/**
 * 직전 범위가 남긴 sticky title 을 읽는다.
 *
 * `from === 0` 이면 문서 시작이라 `null` 이다. 그 외에는 **`seq < from` 중 가장 큰
 * seq** 의 아티팩트에서 가져온다. 직전 태스크가 끝나야 다음이 큐에 들어가므로
 * (순차 보장) 그 행은 이미 있어야 한다 — 없으면 순서가 깨진 것이므로 던진다.
 */
async function loadCarryTitle(
  client: SupabaseClient,
  jobId: string,
  from: number,
): Promise<string | null> {
  if (from <= 0) return null;
  const { data, error } = await client
    .from("ingest_artifacts")
    .select("seq, payload")
    .eq("job_id", jobId)
    .eq("stage", "extract")
    .lt("seq", from)
    .order("seq", { ascending: false })
    .limit(1);
  if (error) throw new Error(`직전 extract 산출물 조회 실패: ${error.message}`);
  const row = (data ?? [])[0] as { payload?: Record<string, unknown> } | undefined;
  if (!row) {
    throw new Error(
      `직전 페이지 범위 산출물이 없다 (job=${jobId}, from=${from}). ` +
        "PDF extract 는 순차로 돌아야 한다 — 순서가 깨졌다.",
    );
  }
  const t = row.payload?.["next_title"];
  return typeof t === "string" ? t : null;
}

/** `extract` 작업 1건. 산출물을 `ingest_artifacts` 에 upsert 한다. */
export function makeExtractHandler(deps: ExtractDeps): TaskHandler {
  const pagesPerTask = deps.pagesPerTask ?? PDF_PAGES_PER_TASK;
  // PDF 이고 vision 이 켜져 있으면 chunk 앞에 vision 단계가 하나 더 붙는다.
  // 스캔 PDF 여부는 vision 핸들러가 판단해 스스로 chunk 로 넘긴다 — 여기서 flags 까지
  // 보면 extract 가 vision 정책을 알아야 해서 책임이 번진다.
  const visionEnabled = readVisionEnv(deps.env ?? Deno.env.toObject()).enabled;

  return async (task: TaskPayload) => {
    const { data: docs, error: docErr } = await deps.client
      .from("documents")
      .select("id, doc_type, storage_path")
      .eq("id", task.doc_id)
      .limit(1);
    if (docErr) throw new Error(`documents 조회 실패: ${docErr.message}`);
    const doc = (docs ?? [])[0] as { doc_type?: string; storage_path?: string } | undefined;
    if (!doc) throw new Error(`문서를 찾을 수 없다: ${task.doc_id}`);

    const docType = doc.doc_type ?? "";
    if (!SUPPORTED_DOC_TYPES.has(docType)) {
      // **조용히 넘기지 않는다.** 아직 못 하는 건 못 한다고 말해야 한다.
      throw new Error(`아직 이식되지 않은 포맷: ${docType || "(없음)"}`);
    }
    const path = doc.storage_path;
    if (!path) throw new Error(`storage_path 가 비었다: ${task.doc_id}`);
    // 업로드 직후 잠깐 남는 placeholder — 아직 파일이 없다. 재시도 대상이다.
    if (path.startsWith("pending/")) {
      throw new Error(`storage_path 가 아직 pending 이다: ${path}`);
    }

    const bytes = deps.download
      ? await deps.download(path)
      : await defaultDownload(deps.client, deps.bucket, path);

    const from = task.from ?? 0;
    let payload: Record<string, unknown>;
    /** 이 문서에 아직 남은 페이지가 있으면 다음 범위 시작. 없으면 null. */
    let nextFrom: number | null = null;

    if (docType === "pdf") {
      const count = task.count ?? pagesPerTask;
      const run = deps.extractPdf ?? extractPdfRange;
      const r = await run(bytes, {
        from,
        count,
        carryTitle: await loadCarryTitle(deps.client, task.job_id, from),
      });
      payload = {
        source_type: "pdf",
        sections: r.sections,
        raw_text: r.rawParts.join("\n\n"),
        warnings: [],
        metadata: {},
        next_title: r.nextTitle,
        page_from: from,
        page_count: r.processed,
        total_pages: r.totalPages,
      };
      const done = from + r.processed;
      if (done < r.totalPages && r.processed > 0) nextFrom = done;
    } else {
      // HWP — 페이지 개념이 없다. 한 번에 끝난다.
      const result = await extractHwp(bytes);
      payload = { ...result, next_title: null, page_from: 0, page_count: 0, total_pages: 0 };
    }

    // Postgres jsonb 는 U+0000 을 아예 못 받는다. arXiv(LaTeX) PDF 가 실제로 뱉어서
    // `unsupported Unicode escape sequence` 로 죽었다. 원본도 저장 직전에 지운다
    // (`SupabasePgVectorStore._strip_null_bytes`) — 시점만 더 이르다.
    const cleaned = stripNulls(payload);
    if (cleaned.removed > 0) cleaned.value["nul_removed"] = cleaned.removed;

    const { error: upErr } = await deps.client
      .from("ingest_artifacts")
      .upsert({
        job_id: task.job_id,
        doc_id: task.doc_id,
        stage: "extract",
        seq: from,
        payload: cleaned.value,
      }, { onConflict: "job_id,stage,seq" });
    if (upErr) throw new Error(`ingest_artifacts 저장 실패: ${upErr.message}`);

    // **저장이 끝난 뒤에** 다음 작업을 넣는다. 순서가 반대면 다음 태스크가 아직 없는
    // 아티팩트에서 carryTitle 을 찾다가 던진다.
    const afterExtract = visionEnabled && docType === "pdf" ? "vision" : "chunk";
    const next: TaskPayload = nextFrom !== null
      ? { job_id: task.job_id, doc_id: task.doc_id, stage: "extract", from: nextFrom, count: pagesPerTask }
      : afterExtract === "vision"
      ? { job_id: task.job_id, doc_id: task.doc_id, stage: "vision", from: 0 }
      : { job_id: task.job_id, doc_id: task.doc_id, stage: "chunk" };
    const { error: sendErr } = await deps.client.rpc("ingest_queue_send", { payload: next });
    if (sendErr) throw new Error(`다음 작업 enqueue 실패: ${sendErr.message}`);
  };
}
