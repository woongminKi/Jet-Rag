/**
 * `doc_embed` 작업 핸들러 — 문서 대표 벡터를 만든다.
 *
 * 원본 자리: `embed → **doc_embed** → dedup`. 벡터를 못 채우면 `dedup` 은 건너뛴다
 * (원본 `if doc_embedded`) — 비교할 값이 없기 때문이다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

import { runDocEmbedStage } from "../doc_embed.ts";
import type { EmbedDeps } from "../embed_provider.ts";
import { loadRawText } from "../raw_text.ts";
import type { TaskHandler, TaskPayload } from "../worker.ts";

/** 요약이 없을 때 쓰는 본문 앞부분 길이(원본 `_RAW_FALLBACK_CHARS`). */
const RAW_FALLBACK_CHARS = 3000;

export interface DocEmbedHandlerDeps {
  client: SupabaseClient;
  embedDeps: EmbedDeps;
  embed?: (texts: string[]) => Promise<number[][]>;
  rawText?: (jobId: string) => Promise<string>;
}

export function makeDocEmbedHandler(deps: DocEmbedHandlerDeps): TaskHandler {
  return async (task: TaskPayload) => {
    const rawText = deps.rawText
      ? await deps.rawText(task.job_id)
      : await loadRawText(deps.client, task.job_id, RAW_FALLBACK_CHARS);

    const embedded = await runDocEmbedStage(
      { client: deps.client, embedDeps: deps.embedDeps, embed: deps.embed },
      task.doc_id,
      rawText,
    );

    // 벡터가 없으면 dedup 이 할 일이 없다 — 원본도 그때는 아예 안 부른다.
    const next = embedded
      ? { job_id: task.job_id, doc_id: task.doc_id, stage: "dedup" }
      : { job_id: task.job_id, doc_id: task.doc_id, stage: "finish" };
    const { error } = await deps.client.rpc("ingest_queue_send", { payload: next });
    if (error) throw new Error(`${next.stage} enqueue 실패: ${error.message}`);
  };
}
