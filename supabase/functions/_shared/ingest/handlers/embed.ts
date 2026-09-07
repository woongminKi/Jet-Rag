/**
 * `embed` 작업 핸들러 — `dense_vec` 이 NULL 인 청크를 BGE-M3 로 채운다.
 *
 * 원본은 `ingest/stages/embed.py` 다.
 *
 * ## offset 을 쓰지 않는다
 * 원본은 문서의 NULL 청크를 **한 번에** 가져와 배치로 돈다. Edge 는 그럴 수 없어
 * 쪼개야 하는데, `dense_vec IS NULL` 은 **처리하면서 사라지는 조건**이라 offset 을
 * 들고 다니면 건너뛰는 청크가 생긴다. 그래서 매번 **NULL 인 앞쪽 N 개**를 집는다.
 * 자연히 멱등이고, 재시도해도 이미 채운 것을 다시 부르지 않는다.
 *
 * ## upsert 가 아니라 단건 UPDATE
 * 원본 주석 그대로다 — "supabase upsert 가 보내지 않은 컬럼을 NULL 로 처리하는
 * 케이스에서 `chunks.doc_id` NOT NULL 위반이 관찰되어 명확한 update 로 통일".
 * 배치 upsert 로 바꾸면 그 회귀가 되살아난다.
 *
 * ## 실패하면 남겨 둔다
 * 원본 정책(§10.10)대로 예외를 전파한다. `dense_vec` 이 NULL 로 남아도 **sparse
 * 검색은 동작한다.** 조용히 넘기면 "검색이 반만 되는" 상태가 이유 없이 남는다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

import { embedBatch, type EmbedDeps } from "../embed_provider.ts";
import type { TaskHandler, TaskPayload } from "../worker.ts";

/** 원본 `_BATCH_SIZE` — API 호출 하나에 묶는 텍스트 수. */
export const EMBED_BATCH_SIZE = 16;

/**
 * 한 태스크가 맡는 청크 수.
 *
 * 임베딩은 CPU 가 아니라 **네트워크 대기**다(Edge 는 CPU 시간만 센다). 제약은 요청
 * wall clock 이라 배치 몇 번이 안전한지로 정한다. 64 = 배치 4 회.
 */
export const EMBED_CHUNKS_PER_TASK = 64;

export interface EmbedHandlerDeps {
  client: SupabaseClient;
  token: string;
  chunksPerTask?: number;
  batchSize?: number;
  /** 테스트 주입 — 실제 API 를 때리지 않는다. */
  embed?: (texts: string[], deps: EmbedDeps) => Promise<number[][]>;
  embedDeps?: Partial<EmbedDeps>;
  /** 테스트 주입 — `finished_at` 을 고정한다. */
  nowMs?: () => number;
}

export function makeEmbedHandler(deps: EmbedHandlerDeps): TaskHandler {
  const perTask = deps.chunksPerTask ?? EMBED_CHUNKS_PER_TASK;
  const batchSize = deps.batchSize ?? EMBED_BATCH_SIZE;
  const run = deps.embed ?? embedBatch;

  return async (task: TaskPayload) => {
    if (!deps.token) {
      throw new Error("DEEPINFRA_API_TOKEN 이 없다 — embed 를 돌릴 수 없다.");
    }

    const { data, error } = await deps.client
      .from("chunks")
      .select("id, text")
      .eq("doc_id", task.doc_id)
      .is("dense_vec", null)
      .order("chunk_idx", { ascending: true })
      .limit(perTask);
    if (error) throw new Error(`chunks 조회 실패: ${error.message}`);

    const rows = (data ?? []) as { id: string; text: string }[];
    if (rows.length === 0) {
      // 채울 게 없다 — 다음 단계로 넘긴다.
      await enqueueDocEmbed(deps.client, task);
      return;
    }

    for (let i = 0; i < rows.length; i += batchSize) {
      const batch = rows.slice(i, i + batchSize);
      const vectors = await run(batch.map((r) => r.text), {
        token: deps.token,
        ...deps.embedDeps,
      });
      if (vectors.length !== batch.length) {
        throw new Error(`임베딩 개수 불일치: got=${vectors.length}, expect=${batch.length}`);
      }
      for (let k = 0; k < batch.length; k++) {
        const { error: upErr } = await deps.client
          .from("chunks")
          .update({ dense_vec: vectors[k] })
          .eq("id", batch[k].id);
        if (upErr) throw new Error(`dense_vec 저장 실패 (${batch[k].id}): ${upErr.message}`);
      }
    }

    // 이번에 가득 채웠으면 아직 남았을 수 있다. 덜 찼으면 그게 마지막이다.
    if (rows.length === perTask) {
      const { error: sendErr } = await deps.client.rpc("ingest_queue_send", {
        payload: { job_id: task.job_id, doc_id: task.doc_id, stage: "embed" },
      });
      if (sendErr) throw new Error(`다음 embed 작업 enqueue 실패: ${sendErr.message}`);
    } else {
      await enqueueDocEmbed(deps.client, task);
    }
  };
}

async function enqueueDocEmbed(client: SupabaseClient, task: TaskPayload): Promise<void> {
  const { error } = await client.rpc("ingest_queue_send", {
    payload: { job_id: task.job_id, doc_id: task.doc_id, stage: "doc_embed" },
  });
  if (error) throw new Error(`doc_embed enqueue 실패: ${error.message}`);
}
