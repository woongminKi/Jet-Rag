/**
 * `embed` 작업 핸들러 — `dense_vec` 이 NULL 인 **검색 대상** 청크를 BGE-M3 로 채운다.
 *
 * 원본은 `ingest/stages/embed.py` 다.
 *
 * ## offset 을 쓰지 않는다
 * 원본은 문서의 NULL 청크를 **한 번에** 가져와 배치로 돈다. Edge 는 그럴 수 없어
 * 쪼개야 하는데, `dense_vec IS NULL` 은 **처리하면서 사라지는 조건**이라 offset 을
 * 들고 다니면 건너뛰는 청크가 생긴다. 그래서 매번 **NULL 인 앞쪽 N 개**를 집는다.
 * 자연히 멱등이고, 재시도해도 이미 채운 것을 다시 부르지 않는다.
 *
 * ## `filtered_reason` 이 붙은 청크는 건너뛴다
 * `search_dense_only` · `search_hybrid_rrf` 는 `(flags->>'filtered_reason') IS NULL` 인
 * 청크만 본다. 마킹된 청크의 `dense_vec` 은 **어떤 쿼리도 읽지 않는다** — 채우면
 * DeepInfra 호출과 HNSW 인덱스 크기만 늘어난다. 그래서 조회에서 아예 뺀다.
 * `dense_vec` 은 NULL 로 남고, 마킹을 되돌리면 그때 채워진다(조건이 다시 참이 된다).
 *
 * 마킹은 **`load` 단계에서 끝난다**(`runChunkFilterStage`). `embed` 가 도는 시점에
 * `filtered_reason` 은 이미 확정이라 "나중에 마킹돼서 헛일한" 케이스는 없다.
 *
 * ## 저장은 RPC 로 묶어서 — 태스크당 fsync 2회
 * 처음엔 단건 UPDATE 를 64번 각각 커밋했다(원본의 "upsert 는 안 보낸 컬럼을 NULL 로 만든다"
 * 회귀를 피하려고). 그게 2026-09-16 사고 4 의 원인이었다: `synchronous_commit=on` 이라
 * 커밋마다 WAL fsync 가 나고, Micro 의 I/O 예산이 얕아지면 UPDATE 하나가 8s statement
 * timeout 에 걸려 태스크가 실패 → 11분 뒤 재시도 → 그 사이 검색 15~61s.
 * 지금은 `chunks_set_dense_vec(p_rows)`(마이그 037) 에 `[{id, vec}]` 를 32개씩 넘겨
 * **한 문장·한 트랜잭션**으로 쓴다(태스크당 fsync 2회). upsert 가 아니라 UPDATE 라 다른 컬럼은 건드리지 않는다.
 * 반환값(갱신 행수)이 보낸 개수와 다르면 던진다 — 그 사이 청크가 지워졌다는 뜻이다.
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

/**
 * `chunks_set_dense_vec` 한 번에 보내는 행수. 태스크(64)를 2번에 나눠 쓴다 — fsync 2회.
 * 1번(64)으로 하면 RPC 하나가 실패할 때 임베딩 64개(DeepInfra 비용)를 다 버린다. 32 는 그 손실을
 * 반으로 줄이면서 fsync 는 사고 4 의 64회 대비 1/32 이다. Python 원본도 같은 값을 쓴다.
 */
export const EMBED_WRITE_SLICE = 32;

/**
 * halfvec(1024) 는 유효숫자 ~3.3자리(fp16)라 그 이상은 DB 캐스팅에서 버려진다. float64 를 그대로
 * JSON 으로 보내면 값당 ~19자(64행 = 1.24MB). 소수 6자리로 줄이면 저장값 변화 0, 본문 ~60% 감소.
 */
export function roundForHalfvec(v: number[]): number[] {
  return v.map((x) => Math.round(x * 1e6) / 1e6);
}

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
      // 필터된 청크는 검색이 절대 보지 않는다 — 임베딩하면 DeepInfra 비용과 HNSW
      // 인덱스만 늘어난다(2026-09-15 실측: 전체 청크의 40.9%, 인덱스 237MB).
      // PostgREST 로는 `flags->>filtered_reason=is.null` 로 나간다(실측 확인).
      .is("flags->>filtered_reason", null)
      .order("chunk_idx", { ascending: true })
      .limit(perTask);
    if (error) throw new Error(`chunks 조회 실패: ${error.message}`);

    const rows = (data ?? []) as { id: string; text: string }[];
    if (rows.length === 0) {
      // 채울 게 없다 — 다음 단계로 넘긴다.
      await enqueueDocEmbed(deps.client, task);
      return;
    }

    // 임베딩은 제공자 배치 단위로 받되, 저장은 태스크 끝에 한 번만 한다.
    const payload: { id: string; vec: number[] }[] = [];
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
        payload.push({ id: batch[k].id, vec: roundForHalfvec(vectors[k]) });
      }
    }

    for (let i = 0; i < payload.length; i += EMBED_WRITE_SLICE) {
      const slice = payload.slice(i, i + EMBED_WRITE_SLICE);
      const { data: written, error: upErr } = await deps.client.rpc("chunks_set_dense_vec", {
        p_rows: slice,
      });
      if (upErr) throw new Error(`dense_vec 저장 실패 (${slice.length}건): ${upErr.message}`);
      if (typeof written !== "number") {
        throw new Error(`dense_vec 저장 응답이 숫자가 아니다: ${JSON.stringify(written)}`);
      }
      if (written !== slice.length) {
        throw new Error(`dense_vec 저장 개수 불일치: wrote=${written}, expect=${slice.length}`);
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
