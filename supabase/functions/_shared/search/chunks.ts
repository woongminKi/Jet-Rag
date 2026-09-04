/**
 * 후보 청크 본문 fetch — `search.py` 2-b) 단계 포팅.
 *
 * RPC 가 돌려준 행에서 **처음 나온 순서대로** 중복 없는 chunk_id 를 뽑아 한 번에
 * 본문을 가져온다. 이 한 번의 fetch 가 세 곳에 쓰인다 — 가드 판정 재료, 응답 조립,
 * (이식 안 한) reranker 입력.
 *
 * ## user_id 로 다시 거르지 않는다
 * 원본은 `chunks` 를 id 로만 조회한다. 사용자 격리는 그 앞 RPC(`user_id_arg`)가 이미
 * 했다는 전제다. 여기서 조건을 더 붙이면 원본과 결과가 달라지므로 그대로 뒀다.
 * (RLS 를 우회하는 service role 로 도는 경로라 이 전제가 깨지면 문서가 새므로,
 * RPC 의 user 필터가 이 파일의 안전 근거다.)
 *
 * ## 순서가 계약이다
 * `candidateChunkIds` 의 순서는 rpc_rows 의 등장 순서다. 지금은 조회에만 쓰지만,
 * 순서를 바꾸면 `in.(...)` 인자 순서가 달라져 응답 행 순서가 달라질 수 있다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";
import { buildGuardMeta, type GuardMeta } from "./guards.ts";
import type { ChunkRow } from "./assemble.ts";
import type { RpcRow } from "./rrf.ts";

export const CHUNKS_SELECT = "id, doc_id, chunk_idx, page, section_title, text, metadata";

/** rpc_rows → 중복 없는 chunk_id 목록 (처음 나온 순서 유지). */
export function candidateChunkIds(rows: readonly RpcRow[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const r of rows) {
    if (!seen.has(r.chunk_id)) {
      seen.add(r.chunk_id);
      out.push(r.chunk_id);
    }
  }
  return out;
}

export interface ChunkFetchResult {
  chunksById: Map<string, ChunkRow>;
  guardMeta: Map<string, GuardMeta>;
}

/** 후보 청크 본문을 가져오고 가드 판정 재료까지 만들어 돌려준다. */
export async function fetchCandidateChunks(
  client: SupabaseClient,
  ids: readonly string[],
): Promise<ChunkFetchResult> {
  const chunksById = new Map<string, ChunkRow>();
  const guardMeta = new Map<string, GuardMeta>();
  if (ids.length === 0) return { chunksById, guardMeta };

  const { data, error } = await client
    .from("chunks")
    .select(CHUNKS_SELECT)
    .in("id", ids as string[]);
  if (error) throw new Error(`chunks 조회 실패: ${error.message}`);

  for (const row of (data ?? []) as ChunkRow[]) {
    chunksById.set(row.id, row);
    guardMeta.set(row.id, buildGuardMeta(row));
  }
  return { chunksById, guardMeta };
}
