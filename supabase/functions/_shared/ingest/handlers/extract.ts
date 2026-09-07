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
import { isHwpmlBytes } from "../../documents/hwpml_sniff.ts";
import {
  extractDocxResult,
  extractHwpmlResult,
  extractHwpxResult,
  extractPptxResult,
} from "../xml_extract.ts";
import { PDF_PAGES_PER_TASK } from "../pdf_extract.ts";
import { extractPdfRange, type PdfRangeResult } from "../pdf_open.ts";
import { finishJob } from "../finish.ts";
import { stripNulls } from "../strip_nul.ts";
import { parseImage } from "../image_parser.ts";
import { readVisionEnv } from "../vision_enrich.ts";
import { isScanPdf } from "../vision_scan.ts";
import { pyIsSpace } from "../../pychar.ts";
import type { TaskHandler, TaskPayload } from "../worker.ts";

/** 지금 처리할 수 있는 `documents.doc_type`. */
export const SUPPORTED_DOC_TYPES = new Set([
  "hwp", "pdf", "hwpx", "docx", "pptx", "image",
]);

/**
 * **원본에도 파서가 없는** doc_type — 원본은 `flags.extract_skipped` 를 켜고 잡을
 * **정상 완료**시킨다(`extract.py:166` parser is None → `skip_stage` → `finish_job`).
 *
 * 이 목록이 없으면 `.txt` 업로드가 202 로 받아진 뒤 extract 에서 실패한다. 업로드
 * 화이트리스트(`ALLOWED_EXTENSIONS`)에는 있는데 여기 없어서 생긴 이관 회귀였다 —
 * 실측으로 잡았다(`e2e_upload_chain.ts probe.txt` → `extract:failed`).
 *
 * **`image` · `url` 은 여기 넣으면 안 된다.** 원본은 그 둘을 실제로 파싱한다
 * (`ImageParser` · `UrlParser`). 아직 못 옮긴 것이므로 조용히 완료시키지 말고
 * 시끄럽게 실패해야 한다 — 완료로 뒤집으면 빈 문서가 조용히 쌓인다.
 */
export const GRACEFUL_SKIP_DOC_TYPES = new Set(["txt", "md"]);

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
  nowMs?: () => number;
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
  const env = deps.env ?? Deno.env.toObject();
  const visionEnabled = readVisionEnv(env).enabled;
  const now = deps.nowMs ?? (() => Date.now());

  return async (task: TaskPayload) => {
    const { data: docs, error: docErr } = await deps.client
      .from("documents")
      .select("id, doc_type, storage_path, flags")
      .eq("id", task.doc_id)
      .limit(1);
    if (docErr) throw new Error(`documents 조회 실패: ${docErr.message}`);
    const doc = (docs ?? [])[0] as {
      doc_type?: string;
      storage_path?: string;
      flags?: Record<string, unknown> | null;
    } | undefined;
    if (!doc) throw new Error(`문서를 찾을 수 없다: ${task.doc_id}`);

    const docType = doc.doc_type ?? "";
    if (GRACEFUL_SKIP_DOC_TYPES.has(docType)) {
      // 원본 `_mark_unsupported_format` — 기존 flags 를 보존하고 두 키만 얹는다.
      const { error: flagErr } = await deps.client
        .from("documents")
        .update({
          flags: {
            ...(doc.flags ?? {}),
            extract_skipped: true,
            extract_skipped_reason:
              `doc_type=${docType} 는 아직 지원되지 않는 포맷입니다 (W2 예정).`,
          },
        })
        .eq("id", task.doc_id);
      if (flagErr) throw new Error(`extract_skipped 마킹 실패: ${flagErr.message}`);
      // 원본 `pipeline.py:48` — 후속 스테이지를 걸지 않고 **잡은 정상 완료**다.
      // 다음 작업을 enqueue 하지 않는 것이 곧 사슬 종료다.
      await finishJob(deps.client, task.job_id, now());
      return {
        logStatus: "skipped",
        logError: `${docType} 포맷은 아직 지원되지 않습니다 (후속 어댑터 도입 예정).`,
      };
    }
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
    // 원본은 `os.path.basename(storage_path)` 를 파서에 넘긴다 — 확장자로 mime 을
    // 추정하는 데 쓰이므로 경로가 아니라 파일명이어야 한다.
    const fileName = path.split("/").pop() ?? path;

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
        // 스캔 PDF 판정은 **문서 전체** raw_text 를 봐야 한다(§37). 창 단위로 나뉜 걸
        // 정확히 되붙이려면 두 값이 필요하다:
        // - `raw_part_count` — 0 이면 그 창은 join 에서 빠져야 한다. 안 그러면
        //   구분자 `\n\n` 가 하나 더 끼어 길이가 어긋난다.
        // - `raw_nonspace_len` — 이것만 합쳐도 50 을 넘으면 스캔이 아님이 **확정**된다
        //   (strip 은 공백 아닌 글자를 못 지운다). 큰 문서에서 본문을 다시 안 읽어도 된다.
        raw_part_count: r.rawParts.length,
        raw_nonspace_len: nonSpaceLen(r.rawParts.join("\n\n")),
      };
      const done = from + r.processed;
      if (done < r.totalPages && r.processed > 0) nextFrom = done;
    } else {
      // PDF 말고는 전부 문서 하나를 한 번에 읽는다 — 페이지 범위 개념이 없다.
      const result = docType === "image"
        // 단독 이미지는 **여기서 Gemini 를 부른다**. PDF 처럼 vision 스테이지를 따로
        // 두지 않은 건 원본과 같다 — 이미지 1 장은 호출도 1 회라 나눌 창이 없다.
        // 실패하면 던진다(잡 failed). 조용히 빈 문서를 만들지 않는다.
        ? await (async () => {
          const ve = readVisionEnv(env);
          if (!ve.geminiApiKey) {
            throw new Error("GEMINI_API_KEY 가 없다 — 이미지 문서를 읽을 수 없다");
          }
          const r = await parseImage({
            client: deps.client,
            env,
            geminiApiKey: ve.geminiApiKey,
            nowMs: now(),
          }, { data: bytes, fileName, docId: task.doc_id });
          if (r.metricErrors.length > 0) {
            console.error(
              `vision_usage_log 적재 실패 ${r.metricErrors.length}건 — 비용 한도가 ` +
                `안 걸린다. doc=${task.doc_id} ${r.metricErrors.join(" / ")}`,
            );
          }
          return r.result;
        })()
        : docType === "hwp"
        // 확장자가 `.hwp` 여도 내용이 HWPML(XML) 인 파일이 있다 — **바이트로** 가른다.
        // 원본 `run_extract_stage` 가 같은 자리에서 같은 판정을 한다.
        ? (isHwpmlBytes(bytes.subarray(0, 4096))
          ? extractHwpmlResult(bytes)
          : await extractHwp(bytes))
        : docType === "hwpx"
        ? extractHwpxResult(bytes)
        : docType === "docx"
        ? extractDocxResult(bytes)
        : extractPptxResult(bytes);
      payload = {
        ...result,
        next_title: null,
        page_from: 0,
        page_count: 0,
        total_pages: 0,
        // 창이 하나뿐이라 `raw_text` 가 곧 전체다. `raw_text.ts` 가 빈 창을 거를 때
        // 쓰는 값이므로 섹션이 있으면 1 이상이어야 한다.
        raw_part_count: result.sections.length,
      };
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
    // 마지막 창이면 문서 전체가 스캔 PDF 인지 판정한다. 원본은 파서가 문서를 통째로
    // 읽은 직후에 보므로 여기가 같은 자리다 — **vision enrich 보다 먼저**다(원본 elif).
    let afterExtract: "scan" | "vision" | "chunk" = "chunk";
    if (nextFrom === null && docType === "pdf") {
      if (await isScanDocument(deps.client, task.job_id)) afterExtract = "scan";
      else if (visionEnabled) afterExtract = "vision";
    }
    const next: TaskPayload = nextFrom !== null
      ? { job_id: task.job_id, doc_id: task.doc_id, stage: "extract", from: nextFrom, count: pagesPerTask }
      : afterExtract === "scan"
      ? { job_id: task.job_id, doc_id: task.doc_id, stage: "scan", from: 0 }
      : afterExtract === "vision"
      ? { job_id: task.job_id, doc_id: task.doc_id, stage: "vision", from: 0 }
      : { job_id: task.job_id, doc_id: task.doc_id, stage: "chunk" };
    const { error: sendErr } = await deps.client.rpc("ingest_queue_send", { payload: next });
    if (sendErr) throw new Error(`다음 작업 enqueue 실패: ${sendErr.message}`);
  };
}

/** 공백이 아닌 코드포인트 수. `strip()` 이 절대 못 지우는 글자들이다. */
function nonSpaceLen(s: string): number {
  let n = 0;
  for (const ch of s) if (!pyIsSpace(ch)) n++;
  return n;
}

/**
 * 문서 전체 `raw_text` 로 스캔 PDF 판정 — 원본 `_is_scan_pdf`.
 *
 * 두 단계로 본다. 대부분의 문서는 1 단계에서 끝나 본문을 다시 안 읽는다.
 * 1. 창별 `raw_nonspace_len` 합이 50 초과 → 스캔 아님 **확정**(strip 은 공백만 지운다)
 * 2. 아니면 창별 `raw_text` 를 원본과 같은 순서로 이어 붙여 정확히 판정
 */
async function isScanDocument(
  client: SupabaseClient,
  jobId: string,
): Promise<boolean> {
  const { data, error } = await client
    .from("ingest_artifacts")
    .select("seq, payload->raw_part_count, payload->raw_nonspace_len")
    .eq("job_id", jobId)
    .eq("stage", "extract")
    .order("seq", { ascending: true });
  if (error) throw new Error(`스캔 판정용 산출물 조회 실패: ${error.message}`);
  const rows = (data ?? []) as { seq: number; raw_part_count?: number; raw_nonspace_len?: number }[];

  let nonSpace = 0;
  for (const r of rows) nonSpace += Number(r.raw_nonspace_len ?? 0);
  if (nonSpace > 50) return false;

  const { data: full, error: fErr } = await client
    .from("ingest_artifacts")
    .select("seq, payload->>raw_text, payload->raw_part_count")
    .eq("job_id", jobId)
    .eq("stage", "extract")
    .order("seq", { ascending: true });
  if (fErr) throw new Error(`스캔 판정용 본문 조회 실패: ${fErr.message}`);
  const parts: string[] = [];
  for (const r of (full ?? []) as { raw_text?: string | null; raw_part_count?: number }[]) {
    // 원본 `raw_parts` 가 비어 있던 창은 join 대상이 아니다.
    if (Number(r.raw_part_count ?? 0) > 0) parts.push(r.raw_text ?? "");
  }
  return isScanPdf(parts.join("\n\n"));
}
