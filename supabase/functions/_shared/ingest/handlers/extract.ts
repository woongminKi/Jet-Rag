/**
 * `extract` 작업 핸들러 — Storage 에서 파일을 받아 파싱하고 산출물을 저장한다.
 *
 * ## 지금은 HWP 만이다
 * 다른 포맷은 파서를 아직 안 옮겼다. **모르는 포맷은 던진다** — 조용히 건너뛰면 잡이
 * 영원히 running 으로 남고, 어디서 멈췄는지도 안 보인다. 던지면 워커가 재시도하고
 * 한도를 넘기면 archive + 잡 failed 로 마감한다(`worker.ts` 계약).
 *
 * ## 다음 단계를 큐에 넣지 않는다 (아직)
 * `chunk` 핸들러가 없다. 넣으면 "모르는 stage" 로 즉시 archive 되고 잡이 failed 가 된다.
 * 그래서 여기서는 산출물 저장까지만 하고 **잡을 completed 로 만들지도 않는다** —
 * 청킹이 안 끝났으므로 완료가 아니다. 사실대로 running 에 둔다.
 *
 * ## 멱등성
 * vt 만료로 같은 작업이 두 번 배달될 수 있다. `ingest_artifacts` 의
 * `UNIQUE(job_id, stage, seq)` 위에 **upsert** 해서 두 번 돌아도 행이 하나다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

import { extractHwp } from "../hwp_extract.ts";
import type { TaskHandler, TaskPayload } from "../worker.ts";

/** 지금 처리할 수 있는 `documents.doc_type`. */
export const SUPPORTED_DOC_TYPES = new Set(["hwp"]);

export interface ExtractDeps {
  client: SupabaseClient;
  bucket: string;
  /** 테스트 주입 — 실제 Storage 없이 바이트를 넣는다. */
  download?: (path: string) => Promise<Uint8Array>;
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
 * `extract` 작업 1건. 산출물을 `ingest_artifacts` 에 upsert 한다.
 *
 * 페이지 분할은 아직 없다 — HWP 는 페이지 개념 없이 한 번에 추출한다(원본도 그렇다).
 * PDF 를 붙일 때 `task.from`/`task.count` 로 페이지 범위를 받게 된다.
 */
export function makeExtractHandler(deps: ExtractDeps): TaskHandler {
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

    const result = await extractHwp(bytes);

    const { error: upErr } = await deps.client
      .from("ingest_artifacts")
      .upsert({
        job_id: task.job_id,
        doc_id: task.doc_id,
        stage: "extract",
        seq: task.from ?? 0,
        payload: result,
      }, { onConflict: "job_id,stage,seq" });
    if (upErr) throw new Error(`ingest_artifacts 저장 실패: ${upErr.message}`);
  };
}
