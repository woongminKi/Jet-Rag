/**
 * `load` 작업 핸들러 — chunk 산출물을 `chunks` 테이블에 upsert 한다.
 *
 * 원본은 `ingest/stages/load.py` + `SupabasePgVectorStore.upsert_chunks` 다.
 *
 * ## part 하나씩 처리한다
 * `chunk` 가 아티팩트를 `CHUNKS_PER_ARTIFACT` 개씩 쪼개 저장한다. 여기서는 **part 하나만**
 * 읽어 upsert 하고, 남았으면 다음 part 를 큐에 넣는다. 전부 읽으면 SK 최대 문서에서
 * 13MB 를 한 번에 들어야 하는데 Edge 메모리 상한이 240MB 다.
 *
 * ## upsert 는 다시 batch 로 쪼갠다
 * 원본이 `chunk_upsert_batch_size`(기본 50)로 자른다. 이유도 원본 주석 그대로 —
 * Supabase `statement_timeout`(약 30~60s) 안에 들어가야 한다. part 당 1,000 개면
 * 한 번에 보내기엔 크다.
 *
 * ## 마지막 part 에서 `embed` 를 넣는다
 * `chunks` 행은 들어갔지만 `dense_vec` 이 NULL 이라 아직 dense 검색이 안 된다.
 * 마지막 part 를 적재한 뒤 `embed` 를 큐에 넣어 벡터를 채우게 한다.
 *
 * 잡을 `completed` 로는 만들지 않는다 — `tag_summarize` · `doc_embed` 등이 아직
 * 없으므로 완료가 아니다. 사실대로 running 에 둔다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

import type { ChunkRecord } from "../chunk_records.ts";
import { chunkRecordToRow } from "../chunk_row.ts";
import type { TaskHandler, TaskPayload } from "../worker.ts";

/** 원본 `chunk_upsert_batch_size` 기본값. ENV 로 조정 가능. */
export const DEFAULT_UPSERT_BATCH = 50;

export interface LoadDeps {
  client: SupabaseClient;
  /** 테스트 주입 — 한 번에 보낼 행 수. */
  batchSize?: number;
}

interface ChunkArtifact {
  seq: number;
  payload: {
    part?: number;
    total_parts?: number;
    records?: ChunkRecord[];
  } | null;
}

function readBatchSize(deps: LoadDeps): number {
  if (deps.batchSize !== undefined) return Math.max(1, deps.batchSize);
  const raw = Deno.env.get("JETRAG_CHUNK_UPSERT_BATCH_SIZE");
  const n = raw ? Number(raw) : NaN;
  // 원본과 같은 clamp — 0/음수/파싱 실패는 기본값으로.
  return Number.isFinite(n) && n >= 1 ? Math.floor(n) : DEFAULT_UPSERT_BATCH;
}

export function makeLoadHandler(deps: LoadDeps): TaskHandler {
  return async (task: TaskPayload) => {
    const part = task.from ?? 0;

    const { data, error } = await deps.client
      .from("ingest_artifacts")
      .select("seq, payload")
      .eq("job_id", task.job_id)
      .eq("stage", "chunk")
      .eq("seq", part)
      .limit(1);
    if (error) throw new Error(`chunk 산출물 조회 실패 (part=${part}): ${error.message}`);

    const row = (data ?? [])[0] as ChunkArtifact | undefined;
    if (!row) {
      // 조용히 넘기면 그 part 의 청크가 통째로 사라지고, 검색이 안 되는 이유를
      // 나중에 찾을 수 없다.
      throw new Error(
        `chunk 산출물 part ${part} 이 없다 (job=${task.job_id}). 순서가 깨졌다.`,
      );
    }

    const records = row.payload?.records ?? [];
    const totalParts = row.payload?.total_parts ?? 1;

    if (records.length > 0) {
      const batch = readBatchSize(deps);
      for (let i = 0; i < records.length; i += batch) {
        const rows = records.slice(i, i + batch).map(chunkRecordToRow);
        const { error: upErr } = await deps.client
          .from("chunks")
          .upsert(rows, { onConflict: "doc_id,chunk_idx" });
        if (upErr) {
          throw new Error(
            `chunks upsert 실패 (part=${part}, ${i}~${i + rows.length}): ${upErr.message}`,
          );
        }
      }
    }

    // 남은 part 가 있으면 이어가고, 마지막이면 embed 로 넘긴다.
    const next = part + 1 < totalParts
      ? { job_id: task.job_id, doc_id: task.doc_id, stage: "load", from: part + 1 }
      : { job_id: task.job_id, doc_id: task.doc_id, stage: "embed" };
    const { error: sendErr } = await deps.client.rpc("ingest_queue_send", { payload: next });
    if (sendErr) throw new Error(`다음 작업 enqueue 실패 (${next.stage}): ${sendErr.message}`);
  };
}
