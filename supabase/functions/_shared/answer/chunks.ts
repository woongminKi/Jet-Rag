/**
 * `/answer` 의 청크 수집 — `answer.py` 의 `_gather_chunks` 포팅.
 *
 * ## `search/rpc.ts` 를 재사용하지 않는다
 * 모양이 비슷해서 `runSearchRpc` 를 쓰고 싶어지지만 **부르는 RPC 가 다르다**:
 *
 * | 상황 | `/search` (`rpc.ts`) | `/answer` (원본) |
 * |---|---|---|
 * | dense 있음 | `search_hybrid_rrf` | `search_hybrid_rrf` (같음) |
 * | dense 실패 | `search_sparse_only` | **`search_sparse_only_pgroonga`** |
 *
 * 인자도 다르다 — `/answer` 의 sparse 경로는 `k_rrf` 를 안 넘긴다. 재사용했으면
 * 임베딩이 죽었을 때만 조용히 갈렸을 것이다.
 *
 * ## 집계 순서가 계약이다
 * `doc_id` 필터를 **먼저** 걸고, `dense_hits`·`sparse_hits`·`fused` 를 그 뒤에 센다.
 * 그다음에야 `top_k` 로 자른다. 순서를 바꾸면 `query_parsed` 숫자가 달라진다.
 *
 * ## `has_sparse` 는 벡터 유무가 아니다
 * `has_dense` 는 "임베딩을 얻었는가"(`dense_vec is not None`)인데
 * `has_sparse` 는 "sparse 로 잡힌 행이 있는가"(`sparse_hits > 0`)다. 비대칭이 원본 그대로다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

import { stripSynonymMarker } from "../search/snippet.ts";

/** `_RRF_K` · `_RPC_TOP_K`. `/search` 의 값과 우연히 같지만 별개 상수다. */
export const RRF_K = 60;
export const RPC_TOP_K = 50;

export interface AnswerRpcRow {
  chunk_id: string;
  doc_id: string;
  dense_rank?: number | null;
  sparse_rank?: number | null;
  rrf_score?: number | null;
  [k: string]: unknown;
}

export interface QueryParsedInfo {
  has_dense: boolean;
  has_sparse: boolean;
  dense_hits: number;
  sparse_hits: number;
  fused: number;
}

export interface EnrichedChunk {
  chunk_id: string;
  doc_id: string;
  doc_title: string | null;
  chunk_idx: number;
  text: string;
  page: number | null;
  section_title: string | null;
  score: number;
}

/** RPC 결과 → `query_parsed`. 필터가 끝난 행을 넘겨야 한다. */
export function buildQueryParsed(
  rows: readonly AnswerRpcRow[],
  hasDenseVec: boolean,
): QueryParsedInfo {
  const denseHits = rows.filter((r) => r.dense_rank !== null && r.dense_rank !== undefined).length;
  const sparseHits = rows.filter((r) => r.sparse_rank !== null && r.sparse_rank !== undefined).length;
  return {
    has_dense: hasDenseVec,
    has_sparse: sparseHits > 0,
    dense_hits: denseHits,
    sparse_hits: sparseHits,
    fused: rows.length,
  };
}

/**
 * 행 + chunks/documents 조회 결과를 합쳐 프롬프트·응답용 청크로.
 * chunks 에 없는 행은 **버린다**(원본 `if not c: continue`).
 */
export function enrichRows(
  rows: readonly AnswerRpcRow[],
  chunksById: Map<string, Record<string, unknown>>,
  docsById: Map<string, Record<string, unknown>>,
): EnrichedChunk[] {
  const out: EnrichedChunk[] = [];
  for (const r of rows) {
    const c = chunksById.get(r.chunk_id);
    if (!c) continue;
    const d = docsById.get(r.doc_id);
    out.push({
      chunk_id: r.chunk_id,
      doc_id: r.doc_id,
      doc_title: (d?.title ?? null) as string | null,
      chunk_idx: c.chunk_idx as number,
      // 인제스트가 붙인 `[검색어:...]` 마커를 여기서 뗀다 — 프롬프트·스니펫 어디에도
      // 노출되면 안 된다.
      text: stripSynonymMarker((c.text ?? "") as string),
      page: (c.page ?? null) as number | null,
      section_title: (c.section_title ?? null) as string | null,
      // `float(r.get("rrf_score") or 0.0)` — 0 과 null 을 같이 0.0 으로 떨어뜨린다.
      score: Number(r.rrf_score || 0),
    });
  }
  return out;
}

export interface GatherDeps {
  client: SupabaseClient;
  /** 임베딩. 실패 시 `null` 을 주면 sparse-only 경로로 간다. */
  embedQuery: (q: string) => Promise<number[] | null>;
  buildPgQuery: (q: string) => string;
}

export async function gatherChunks(
  opts: { query: string; docId: string | null; topK: number; userId: string },
  deps: GatherDeps,
): Promise<{ chunks: EnrichedChunk[]; queryParsed: QueryParsedInfo }> {
  const pgQ = deps.buildPgQuery(opts.query);
  const denseVec = await deps.embedQuery(opts.query);

  const { data, error } = denseVec !== null
    ? await deps.client.rpc("search_hybrid_rrf", {
      query_text: pgQ,
      query_dense: denseVec,
      k_rrf: RRF_K,
      top_k: RPC_TOP_K,
      user_id_arg: opts.userId,
    })
    // **`search_sparse_only` 가 아니다.** `k_rrf` 도 안 넘긴다.
    : await deps.client.rpc("search_sparse_only_pgroonga", {
      query_text: pgQ,
      user_id_arg: opts.userId,
      top_k: RPC_TOP_K,
    });
  if (error) throw new Error(error.message);

  let rows = (data ?? []) as AnswerRpcRow[];
  if (opts.docId) rows = rows.filter((r) => r.doc_id === opts.docId);

  const queryParsed = buildQueryParsed(rows, denseVec !== null);

  rows = rows.slice(0, opts.topK);
  if (rows.length === 0) return { chunks: [], queryParsed };

  const chunkIds = rows.map((r) => r.chunk_id);
  const { data: cData, error: cErr } = await deps.client
    .from("chunks")
    .select("id,doc_id,chunk_idx,text,page,section_title")
    .in("id", chunkIds);
  if (cErr) throw new Error(cErr.message);
  const chunksById = new Map<string, Record<string, unknown>>(
    ((cData ?? []) as Record<string, unknown>[]).map((c) => [c.id as string, c]),
  );

  // `list({...})` — 중복 제거. 순서는 결과에 영향이 없다(id 로 다시 찾는다).
  const docIds = [...new Set(rows.map((r) => r.doc_id))];
  const { data: dData, error: dErr } = await deps.client
    .from("documents")
    .select("id,title")
    .in("id", docIds);
  if (dErr) throw new Error(dErr.message);
  const docsById = new Map<string, Record<string, unknown>>(
    ((dData ?? []) as Record<string, unknown>[]).map((d) => [d.id as string, d]),
  );

  return { chunks: enrichRows(rows, chunksById, docsById), queryParsed };
}
