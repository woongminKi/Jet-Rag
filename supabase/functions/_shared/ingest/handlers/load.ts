/**
 * `load` 작업 핸들러 — chunk 산출물을 `chunks` 테이블에 upsert 한다.
 *
 * 원본은 `ingest/stages/load.py` + `SupabasePgVectorStore.upsert_chunks` 다.
 *
 * ## part 하나씩 처리한다
 * `chunk` 가 창 하나를 아티팩트 한 행(`seq = 창 인덱스`)으로 남긴다. 여기서는
 * **part 하나만** 읽어 upsert 하고, 남았으면 다음 part 를 큐에 넣는다. 전부 읽으면 SK
 * 최대 문서에서 13MB 를 한 번에 들어야 하는데 Edge 메모리 상한이 240MB 다.
 *
 * ## 머리말/꼬리말 마킹이 여기 있다
 * `chunk_filter` 의 `header_footer` 는 **문서 전체**에서 3회 이상 반복되는 짧은
 * 텍스트다. `chunk` 가 창 단위로 돌면서 그 판정을 못 하게 됐으므로, 마지막 창이 남긴
 * `header_footer_texts` 를 받아 여기서 마킹한다. 나머지 사유(`empty`·`extreme_short`·
 * `table_noise`)는 청크 하나만 보면 되지만 **판정 순서가 규칙의 일부**라 같이 돈다.
 *
 * ## upsert 는 다시 batch 로 쪼갠다
 * 원본이 `chunk_upsert_batch_size`(기본 50)로 자른다. 이유도 원본 주석 그대로 —
 * Supabase `statement_timeout`(약 30~60s) 안에 들어가야 한다.
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
import { runChunkFilterStage } from "../chunk_filter.ts";
import { chunkRecordToRow } from "../chunk_row.ts";
import type { TaskHandler, TaskPayload } from "../worker.ts";

/** 원본 `chunk_upsert_batch_size` 기본값. ENV 로 조정 가능. */
export const DEFAULT_UPSERT_BATCH = 50;

/** 원본과 같은 경고 임계 — 이보다 높으면 오탐이 늘었다는 신호일 수 있다. */
const FILTER_RATIO_WARN = 0.05;

export interface LoadDeps {
  client: SupabaseClient;
  /** 테스트 주입 — 한 번에 보낼 행 수. */
  batchSize?: number;
}

/**
 * `load` 가 실제로 쓰는 필드만. `payload` 를 통째로 읽으면 `carry`(SK 실측 0.64MB) 가
 * 따라온다 — 이 단계에서는 한 번도 안 보는 값이다.
 */
interface ChunkArtifact {
  seq: number;
  records?: ChunkRecord[] | null;
  total_parts?: number | null;
}

function readBatchSize(deps: LoadDeps): number {
  if (deps.batchSize !== undefined) return Math.max(1, deps.batchSize);
  const raw = Deno.env.get("JETRAG_CHUNK_UPSERT_BATCH_SIZE");
  const n = raw ? Number(raw) : NaN;
  // 원본과 같은 clamp — 0/음수/파싱 실패는 기본값으로.
  return Number.isFinite(n) && n >= 1 ? Math.floor(n) : DEFAULT_UPSERT_BATCH;
}

/**
 * `chunk` 의 **마지막 창**이 남긴 머리말/꼬리말 목록.
 *
 * 마지막 seq 를 `order desc limit 1` 로 찾으면 안 된다 — 재인제스트로 창 수가 줄었을 때
 * 잔존 행이 더 큰 seq 를 갖고 있으면 그게 잡히고, 그 행에는 목록이 없어 **머리말 마킹이
 * 통째로 조용히 빠진다**. 이번 잡의 `total_parts` 로 정확히 지목한다.
 *
 * 목록만 읽고 `records` 는 안 읽는다 — part 하나가 수백 KB 다.
 */
async function loadHeaderFooterTexts(
  client: SupabaseClient,
  jobId: string,
  lastSeq: number,
): Promise<Set<string>> {
  const { data, error } = await client
    .from("ingest_artifacts")
    .select("seq, texts:payload->header_footer_texts")
    .eq("job_id", jobId)
    .eq("stage", "chunk")
    .eq("seq", lastSeq)
    .limit(1);
  if (error) throw new Error(`머리말 목록 조회 실패: ${error.message}`);
  const texts = ((data ?? [])[0] as { texts?: unknown } | undefined)?.texts;
  if (!Array.isArray(texts)) {
    // 창 분할 배포 **직전에** chunk 를 끝낸 잡은 이 필드가 없다. 그런 산출물은 이미
    // `chunk` 가 `filtered_reason` 을 붙여 뒀으므로 빈 집합으로 둬도 결과가 같다
    // (이미 붙은 flags 는 아래에서 보존한다). 조용히 넘기지 않고 남긴다.
    console.warn(
      `load: chunk 산출물 seq=${lastSeq} 에 header_footer_texts 가 없다 (job=${jobId}) — ` +
        "창 분할 이전 산출물로 보고 머리말 마킹을 건너뛴다",
    );
    return new Set();
  }
  return new Set(texts.map((t) => String(t)));
}

/**
 * 문서 전체 마킹 비율 경고 — **마지막 part 에서 1 회**.
 *
 * part 단위로 재면 노이즈가 몰린 part 하나 때문에 계속 경고가 뜬다. 원본이 보던 값은
 * 문서 전체 비율이므로 `chunks` 를 직접 세서 같은 값을 만든다.
 *
 * 진단용이라 **실패해도 잡을 죽이지 않는다** — 세는 데 실패했다고 적재를 무르면
 * 손해가 더 크다.
 */
async function warnFilterRatio(
  client: SupabaseClient,
  docId: string,
): Promise<void> {
  try {
    const total = await client
      .from("chunks").select("chunk_idx", { count: "exact", head: true }).eq("doc_id", docId);
    if (total.error) throw new Error(total.error.message);
    const marked = await client
      .from("chunks").select("chunk_idx", { count: "exact", head: true })
      .eq("doc_id", docId)
      .not("flags->>filtered_reason", "is", null);
    if (marked.error) throw new Error(marked.error.message);

    const n = total.count ?? 0;
    const m = marked.count ?? 0;
    if (n === 0) return;
    const ratio = m / n;
    console.info(
      `chunk_filter: doc=${docId} total=${n} marked=${m} filter_ratio=${ratio.toFixed(3)}`,
    );
    if (ratio > FILTER_RATIO_WARN) {
      // 원본과 같은 경고 — 오탐이 늘어난 신호일 수 있다.
      console.warn(
        `chunk_filter: doc=${docId} 마킹 비율 ${(ratio * 100).toFixed(1)}% > 5% — ` +
          "false positive risk 검토 필요",
      );
    }
  } catch (e) {
    console.warn(`chunk_filter 비율 집계 실패 (doc=${docId}): ${(e as Error).message}`);
  }
}

export function makeLoadHandler(deps: LoadDeps): TaskHandler {
  return async (task: TaskPayload) => {
    const part = task.from ?? 0;

    const { data, error } = await deps.client
      .from("ingest_artifacts")
      .select("seq, records:payload->records, total_parts:payload->total_parts")
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

    const raw = row.records ?? [];
    const totalParts = row.total_parts ?? 1;

    // 원본 파이프라인 순서: chunk → **chunk_filter** → content_gate → … → load.
    // 청크를 지우지 않는다. 표시만 남기고 검색 쪽 쿼리가 그걸 보고 거른다.
    const hfTexts = raw.length > 0
      ? await loadHeaderFooterTexts(deps.client, task.job_id, totalParts - 1)
      : new Set<string>();
    const filtered = runChunkFilterStage(raw, hfTexts);
    const records = filtered.chunks;
    if (records.length > 0) {
      console.info(
        `chunk_filter: doc=${task.doc_id} part=${part} total=${records.length} ` +
          `table_noise=${filtered.counts.table_noise} ` +
          `header_footer=${filtered.counts.header_footer} ` +
          `empty=${filtered.counts.empty} extreme_short=${filtered.counts.extreme_short}`,
      );
    }

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

    const isLast = part + 1 >= totalParts;
    if (isLast) await warnFilterRatio(deps.client, task.doc_id);

    // 남은 part 가 있으면 이어가고, 마지막이면 embed 로 넘긴다.
    const next = isLast
      ? { job_id: task.job_id, doc_id: task.doc_id, stage: "embed" }
      : { job_id: task.job_id, doc_id: task.doc_id, stage: "load", from: part + 1 };
    const { error: sendErr } = await deps.client.rpc("ingest_queue_send", { payload: next });
    if (sendErr) throw new Error(`다음 작업 enqueue 실패 (${next.stage}): ${sendErr.message}`);
  };
}
