/**
 * 검색 RPC 호출 — `search.py` 2) 단계 포팅.
 *
 * RRF 융합은 Postgres 함수 안에 있고, 여기서 하는 일은 **mode 에 맞는 함수를 고르고
 * 인자를 맞춰 부르는 것**이다. 함수 이름이나 인자 하나가 어긋나면 결과가 통째로 달라진다.
 *
 * ## mode 별 분기와 fallback
 * 마이그레이션 008 이 `search_dense_only` / `search_sparse_only` 를 나눠 놨다.
 * 그게 없는 환경(008 미적용)에서도 돌도록, 분리 RPC 가 실패하면 **조용히**
 * `search_hybrid_rrf` 로 내려간 뒤 응용 계층에서 rank 로 거른다.
 *
 * | mode | 1 순위 | 실패 시 |
 * |---|---|---|
 * | `dense` (벡터 있음) | `search_dense_only` | hybrid → `dense_rank != null` 필터 |
 * | `sparse` | `search_sparse_only` | hybrid → `sparse_rank != null` 필터 |
 * | `hybrid` | `search_hybrid_rrf` (벡터 없으면 sparse-only) | — |
 *
 * `usedSplitRpc` 가 false 일 때만 응용 필터가 걸린다는 게 계약이다. 분리 RPC 가 이미
 * 걸러 놨는데 또 거르면 결과가 줄어든다.
 *
 * ## top_k 는 우선순위가 있다
 * `doc_id` 지정(200) > mode ablation(100) > 기본(50). 응용 계층에서 더 걸러낼 것을
 * 감안해 미리 넉넉히 받아 두는 값이라, 낮추면 결과가 조용히 부족해진다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";
import type { RpcRow } from "./rrf.ts";
import type { SearchMode } from "./params.ts";

export const RRF_K = 60;
/** `doc_id` 응용 필터 시 부족 방지 — RPC 결과 중 일치만 통과하므로 4 배로 받는다. */
export const RPC_TOP_K_DOC_FILTER = 200;

export interface RpcTopKConfig {
  base: number;
  ablation: number;
}

export function resolveRpcTopK(
  docId: string | null,
  mode: SearchMode,
  cfg: RpcTopKConfig,
): number {
  if (docId !== null) return RPC_TOP_K_DOC_FILTER;
  if (mode === "dense" || mode === "sparse") return cfg.ablation;
  return cfg.base;
}

export interface RpcInput {
  mode: SearchMode;
  /** dense 벡터. 임베딩이 실패해 sparse-only 로 내려온 경우 null. */
  denseVec: number[] | null;
  /** PGroonga 질의 문자열 (`pgroonga.ts` 가 만든 것). */
  pgQuery: string;
  userId: string;
  topK: number;
  docId: string | null;
}

export interface RpcOutcome {
  rows: RpcRow[];
  /** 분리 RPC 를 실제로 썼는지 — 응용 필터 적용 여부를 가른다. */
  usedSplitRpc: boolean;
}

async function callRpc(
  client: SupabaseClient,
  fn: string,
  args: Record<string, unknown>,
): Promise<RpcRow[]> {
  const { data, error } = await client.rpc(fn, args);
  if (error) throw new Error(`${fn} 실패: ${error.message}`);
  return (data ?? []) as RpcRow[];
}

/**
 * mode 에 맞는 RPC 를 부르고 결과 행을 돌려준다.
 * 분리 RPC 가 없는 환경에서는 hybrid 로 내려간다(원본과 같은 graceful fallback).
 */
export async function runSearchRpc(
  client: SupabaseClient,
  input: RpcInput,
): Promise<RpcOutcome> {
  const { mode, denseVec, pgQuery, userId, topK } = input;

  if (mode === "dense" && denseVec !== null) {
    try {
      return {
        rows: await callRpc(client, "search_dense_only", {
          query_dense: denseVec,
          k_rrf: RRF_K,
          top_k: topK,
          user_id_arg: userId,
        }),
        usedSplitRpc: true,
      };
    } catch {
      // 008 미적용 — hybrid 로 내려간다.
    }
  } else if (mode === "sparse") {
    try {
      return {
        rows: await callRpc(client, "search_sparse_only", {
          query_text: pgQuery,
          k_rrf: RRF_K,
          top_k: topK,
          user_id_arg: userId,
        }),
        usedSplitRpc: true,
      };
    } catch {
      // 008 미적용 — hybrid 로 내려간다.
    }
  }

  // hybrid, 또는 위 분기의 fallback.
  if (denseVec !== null) {
    return {
      rows: await callRpc(client, "search_hybrid_rrf", {
        query_text: pgQuery,
        query_dense: denseVec,
        k_rrf: RRF_K,
        top_k: topK,
        user_id_arg: userId,
      }),
      usedSplitRpc: false,
    };
  }
  // 임베딩이 없으면 sparse 만으로라도 답한다.
  return {
    rows: await callRpc(client, "search_sparse_only", {
      query_text: pgQuery,
      k_rrf: RRF_K,
      top_k: topK,
      user_id_arg: userId,
    }),
    usedSplitRpc: false,
  };
}

/**
 * RPC 결과에 응용 계층 필터를 건다.
 *
 * `doc_id` 필터는 언제나, mode 필터는 **분리 RPC 를 안 썼을 때만**.
 */
export function applyRowFilters(
  rows: readonly RpcRow[],
  opts: { docId: string | null; mode: SearchMode; usedSplitRpc: boolean },
): RpcRow[] {
  let out = [...rows];
  if (opts.docId !== null) out = out.filter((r) => r.doc_id === opts.docId);
  if (!opts.usedSplitRpc) {
    if (opts.mode === "dense") {
      out = out.filter((r) => r.dense_rank !== null && r.dense_rank !== undefined);
    } else if (opts.mode === "sparse") {
      out = out.filter((r) => r.sparse_rank !== null && r.sparse_rank !== undefined);
    }
  }
  return out;
}

/** 응답의 `query_parsed` 에 실리는 히트 수. `null` 인 rank 는 그 경로가 안 걸린 것이다. */
export function countHits(rows: readonly RpcRow[]): { dense: number; sparse: number } {
  let dense = 0;
  let sparse = 0;
  for (const r of rows) {
    if (r.dense_rank !== null && r.dense_rank !== undefined) dense++;
    if (r.sparse_rank !== null && r.sparse_rank !== undefined) sparse++;
  }
  return { dense, sparse };
}
