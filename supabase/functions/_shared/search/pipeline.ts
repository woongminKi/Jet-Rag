/**
 * `/search` 파이프라인 — `search.py` 의 `search()` 흐름 조립.
 *
 * HTTP 를 모른다. 요청 파싱·인증·CORS 는 `api-search/index.ts` 가 하고, 여기는
 * **검증된 파라미터와 user_id 를 받아 응답 객체를 만든다.** 그래야 패리티 검사기가
 * 토큰 없이 Python 쪽 `search()` 와 나란히 in-process 로 돌려 비교할 수 있다.
 *
 * ## 단계 지도 (원본 주석 번호 그대로)
 * | # | 하는 일 | 모듈 |
 * |---|---|---|
 * | 0 | 질의 의도 신호 | `intent.ts` |
 * | 0-a | 메타 필터 fast path | `meta_fast_path.ts` |
 * | 1 | dense 임베딩 | `embed.ts` |
 * | 2 | mode 별 RPC + 행 필터 | `rpc.ts`, `pgroonga.ts` |
 * | 2-b | 후보 청크 본문 fetch | `chunks.ts` |
 * | 3 | doc 그룹·dedupe + 가드 | `rrf.ts`, `guards.ts` |
 * | 4 | documents fetch + 메타 필터 | `filters.ts` |
 * | 5 | 정렬 + MMR + 페이지네이션 | `rrf.ts`, `mmr.ts` |
 * | 6·7 | 청크 cap + 응답 조립 | `assemble.ts` |
 * | — | 지표 기록 | `metrics.ts` |
 *
 * ## meta fast path 는 항상 켜진 분기다
 * ENV 토글이 아니라 질의 모양으로 갈린다. `doc_id` 미지정 + `mode=hybrid` 일 때만 보고,
 * 결과가 0 행이면 **버리고 RAG 로 계속 간다**(헤더 `meta_fast_fallback`).
 * 이 경로는 지표를 기록하지 않는다 — 원본도 그렇다.
 *
 * ## 조기 반환이 두 군데다
 * RPC 결과가 0 행일 때와, 페이지에 문서가 하나도 없을 때. 둘 다 `total` 값이 다르고
 * (`0` vs 필터 후 전체 수) 지표도 각각 기록한다 — 합치면 응답이 달라진다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

import type { SearchParams } from "./params.ts";
import { buildPgroongaQuery } from "./pgroonga.ts";
import { isCrossDocClassQuery, isCrossDocQuery, route } from "./intent.ts";
import { embedQuery, isTransientEmbedError } from "./embed.ts";
import { applyRowFilters, countHits, resolveRpcTopK, RPC_TOP_K_DOC_FILTER, runSearchRpc } from "./rpc.ts";
import { candidateChunkIds, fetchCandidateChunks } from "./chunks.ts";
import { groupByDoc, paginate, sortDocIds } from "./rrf.ts";
import { queryWantsToc, tocGuardEnabled } from "./guards.ts";
import { buildDocumentsQuery } from "./filters.ts";
import { coerceEmbedding, isDisabled as mmrDisabled, rerank, resolveLambda } from "./mmr.ts";
import { buildItems, chunkCapFor, type ChunkOrder, type DocMetaRow } from "./assemble.ts";
import { recordSearch } from "./metrics.ts";
import { findEnabledUnsupported, unsupportedDetail } from "./unsupported.ts";
import { isMetaOnly, runFastPath } from "./meta_fast_path.ts";

/** 원본 기본값. `doc_id` 지정(200) · mode ablation(100) · 기본(50). */
const RPC_TOP_K = 50;
const RPC_TOP_K_ABLATION = 100;
const RERANKER_PATH_DISABLED = "disabled";
const RETRY_AFTER_SECONDS = "60";

export interface QueryParsedInfo {
  has_dense: boolean;
  has_sparse: boolean;
  dense_hits: number;
  sparse_hits: number;
  fused: number;
  fallback_reason: string | null;
  reranker_used: boolean;
  reranker_fallback_reason: string | null;
  reranker_path: string;
  doc_embedding_rrf_used: boolean;
  doc_embedding_hits: number;
  hyde_used: boolean;
  hyde_fallback_reason: string | null;
}

export interface SearchResponseBody {
  query: string;
  total: number;
  limit: number;
  offset: number;
  items: unknown[];
  took_ms: number;
  query_parsed: QueryParsedInfo;
  meta: Record<string, unknown>;
}

export interface PipelineResult {
  body: SearchResponseBody;
  headers: Record<string, string>;
}

/** 파이프라인이 HTTP 상태를 정해야 하는 실패. */
export class SearchHttpError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
    readonly headers: Record<string, string> = {},
  ) {
    super(detail);
    this.name = "SearchHttpError";
  }
}

export interface PipelineDeps {
  client: SupabaseClient;
  read: (k: string) => string | undefined;
  waitUntil?: (p: Promise<unknown>) => void;
  /** 경과 시간 측정 — 테스트에서 고정하려고 열어 둔다. */
  monotonic?: () => number;
}

/** 미이식 기능(전부 꺼짐)에 해당하는 고정값. 켜져 있으면 애초에 진입 못 한다. */
function inactiveQueryParsed(): Pick<
  QueryParsedInfo,
  | "reranker_used"
  | "reranker_fallback_reason"
  | "reranker_path"
  | "doc_embedding_rrf_used"
  | "doc_embedding_hits"
  | "hyde_used"
  | "hyde_fallback_reason"
> {
  return {
    reranker_used: false,
    reranker_fallback_reason: null,
    reranker_path: RERANKER_PATH_DISABLED,
    doc_embedding_rrf_used: false,
    doc_embedding_hits: 0,
    hyde_used: false,
    hyde_fallback_reason: null,
  };
}

/** decomposition·cross-doc scoped 는 미이식(=꺼짐) 이라 항상 이 모양이다. */
function inactiveMeta(): Record<string, unknown> {
  return {
    decomposition_fired: false,
    decomposed_subqueries: [],
    decomposition_cost_usd: 0.0,
    decomposition_cached: false,
    cross_doc_scoped_applied: false,
    cross_doc_candidate_doc_ids: [],
    cross_doc_candidate_top_n: 0,
  };
}

export async function runSearch(
  params: SearchParams,
  userId: string,
  deps: PipelineDeps,
): Promise<PipelineResult> {
  const now = deps.monotonic ?? (() => performance.now());
  const startT = now();
  const elapsedMs = () => Math.trunc(now() - startT);

  // 미이식 토글이 켜져 있으면 조용히 다른 순위를 내지 않고 여기서 멈춘다.
  const enabled = findEnabledUnsupported(deps.read);
  if (enabled.length > 0) throw new SearchHttpError(500, unsupportedDetail(enabled));

  const { cleanQ, mode, limit, offset, docId } = params;

  // 0) 의도 신호 — MMR 적용 여부와 청크 cap 을 가른다.
  const decision = route(cleanQ);
  const wantsToc = queryWantsToc(cleanQ);
  const tocOn = tocGuardEnabled(deps.read);

  // 0-a) 메타 필터 fast path — 임베딩·RPC 없이 documents 만 보고 답한다.
  //   `doc_id` 지정이나 mode ablation 은 의도가 명확하므로 RAG 를 강제한다.
  //   0 행이면 fast path 를 **버리고** RAG 로 계속 간다(`meta_fast_fallback`) —
  //   "SK 사업보고서 매출" 처럼 제목 ILIKE 가 0 건이 되는 질의를 빈 결과로 돌려주지
  //   않으려는 설계다. 이 경로는 지표를 기록하지 않는다(원본도 그렇다).
  let metaFastFallback = false;
  if (docId === null && mode === "hybrid") {
    const plan = isMetaOnly(cleanQ);
    if (plan !== null) {
      const rows = await runFastPath(deps.client, plan, userId);
      if (rows.length > 0) {
        const paged = rows.slice(offset, offset + limit);
        return {
          body: {
            query: cleanQ,
            total: rows.length,
            limit,
            offset,
            items: paged.map((r) => ({
              doc_id: r.id,
              doc_title: r.title ?? "",
              doc_type: r.doc_type ?? "",
              tags: r.tags ?? [],
              summary: r.summary ?? null,
              created_at: r.created_at ?? "",
              // 메타 매칭은 boolean 이라 전부 같은 점수다.
              relevance: 1.0,
              matched_chunk_count: 0,
              matched_chunks: [],
            })),
            took_ms: elapsedMs(),
            query_parsed: {
              has_dense: false,
              has_sparse: false,
              dense_hits: 0,
              sparse_hits: 0,
              fused: rows.length,
              fallback_reason: null,
              ...inactiveQueryParsed(),
            },
            meta: {
              path: "meta_fast",
              matched_kind: plan.matchedKind,
              tags: plan.tags,
              title_ilike: plan.titleIlike,
              date_range: plan.dateRange,
            },
          },
          headers: {
            "X-Search-Path": "meta_fast",
            "X-Reranker-Path": RERANKER_PATH_DISABLED,
          },
        };
      }
      metaFastFallback = true;
    }
  }

  // 1) dense 임베딩. transient 면 sparse-only 로 낮추고, 영구 실패면 503 이다.
  let denseVec: number[] | null = null;
  let fallbackReason: string | null = null;
  let embedCacheHit = false;
  try {
    const r = await embedQuery(cleanQ, {
      read: deps.read,
      client: deps.client,
      waitUntil: deps.waitUntil,
    });
    denseVec = r.vector;
    embedCacheHit = r.cacheHit;
  } catch (e) {
    if (isTransientEmbedError(e)) {
      fallbackReason = "transient_5xx";
    } else {
      // 운영자가 알아채야 하는 실패 — 지표에 남기고 503 을 던진다.
      recordSearch({
        tookMs: elapsedMs(),
        denseHits: 0,
        sparseHits: 0,
        fused: 0,
        hasDense: false,
        fallbackReason: "permanent_4xx",
        embedCacheHit: false,
        mode,
        queryText: cleanQ,
      }, { read: deps.read, client: deps.client, waitUntil: deps.waitUntil });
      throw new SearchHttpError(
        503,
        "검색 일시 오류 — 임베딩 서비스에 연결할 수 없습니다. 잠시 후 다시 시도해주세요.",
        { "Retry-After": RETRY_AFTER_SECONDS },
      );
    }
  }

  // 2) RPC.
  const topK = resolveRpcTopK(docId, mode, { base: RPC_TOP_K, ablation: RPC_TOP_K_ABLATION });
  const pgQuery = buildPgroongaQuery(cleanQ);
  const outcome = await runSearchRpc(deps.client, {
    mode,
    denseVec,
    pgQuery,
    userId,
    topK,
    docId,
  });
  const rpcRows = applyRowFilters(outcome.rows, {
    docId,
    mode,
    usedSplitRpc: outcome.usedSplitRpc,
  });
  const hits = countHits(rpcRows);

  const queryParsed: QueryParsedInfo = {
    has_dense: denseVec !== null,
    has_sparse: hits.sparse > 0,
    dense_hits: hits.dense,
    sparse_hits: hits.sparse,
    fused: rpcRows.length,
    fallback_reason: fallbackReason,
    ...inactiveQueryParsed(),
  };
  const headers = {
    "X-Search-Path": metaFastFallback ? "meta_fast_fallback" : "rag",
    "X-Reranker-Path": RERANKER_PATH_DISABLED,
  };

  const record = (tookMs: number, fused: number) =>
    recordSearch({
      tookMs,
      denseHits: hits.dense,
      sparseHits: hits.sparse,
      fused,
      hasDense: denseVec !== null,
      fallbackReason,
      embedCacheHit,
      mode,
      queryText: cleanQ,
    }, { read: deps.read, client: deps.client, waitUntil: deps.waitUntil });

  // 조기 반환 ① — RPC 가 아무것도 못 찾음. `total` 은 0 이다.
  if (rpcRows.length === 0) {
    const tookMs = elapsedMs();
    record(tookMs, 0);
    return {
      body: {
        query: cleanQ,
        total: 0,
        limit,
        offset,
        items: [],
        took_ms: tookMs,
        query_parsed: queryParsed,
        meta: inactiveMeta(),
      },
      headers,
    };
  }

  // 2-b) 후보 청크 본문 — 가드 판정과 응답 조립이 같은 fetch 를 쓴다.
  const candIds = candidateChunkIds(rpcRows);
  const { chunksById, guardMeta } = await fetchCandidateChunks(deps.client, candIds);

  // 3) doc 그룹 + 가드 penalty.
  const grouped = groupByDoc(rpcRows, {
    guardMeta,
    coverGuardSkip: false, // reranker 미이식 → 언제나 가드를 적용한다.
    tocEnabled: tocOn,
    queryWantsToc: wantsToc,
  });

  // 4) documents fetch + 메타 필터 4종. **응답 행 순서를 그대로 보존한다** —
  //    동점 문서의 순위가 이 순서에 달려 있다.
  const docsQuery = buildDocumentsQuery(deps.client, {
    userId,
    candidateDocIds: grouped.candidateDocIds,
    docType: params.docType,
    tags: params.tags,
    fromDate: params.fromDate,
    toDate: params.toDate,
  });
  const { data: docRows, error: docErr } = await docsQuery;
  if (docErr) throw new Error(`documents 조회 실패: ${docErr.message}`);
  const docsMeta = new Map<string, DocMetaRow & { doc_embedding?: unknown }>();
  for (const d of (docRows ?? []) as ({ id: string } & DocMetaRow)[]) {
    docsMeta.set(d.id, d);
  }

  // 5) 정렬 → MMR → 페이지네이션.
  let sortedDocIds = sortDocIds(docsMeta.keys(), grouped.docScore);

  // ⚠️ 원본에서 **MMR 은 도달 불가능한 코드**다. 그대로 재현한다.
  //
  // `search.py` 3) 단계의 `for r in rpc_rows: doc_id = r["doc_id"]` 가 **함수 파라미터
  // `doc_id` 를 덮어쓴다**(1286 행). 그 뒤 5) 단계의 MMR 게이트가 `doc_id is None` 을
  // 보므로(1458 행), rpc_rows 가 비지 않는 한 — 즉 여기 도달한 모든 경우 — 항상 False 다.
  // 두 행 사이에 `doc_id` 를 다시 대입하는 곳은 없다(전 구간 확인).
  //
  // 실측(2026-09-05): T1 이 뜨고 문서가 5~8 건인 질의 7 개에서 `mmr.rerank` 호출 **0 회**.
  //
  // 파라미터 `docId` 를 그냥 쓰면 Edge 에서만 MMR 이 되살아나 cross-doc 질의의 순위가
  // 조용히 바뀐다. 그래서 덮어쓰기까지 재현한다 — 나중에 원본의 shadowing 이 고쳐지면
  // 패리티 검사기가 그 차이를 잡는다.
  const shadowedDocId: string | null = rpcRows.length > 0 ? rpcRows[rpcRows.length - 1].doc_id : docId;
  const crossDoc = isCrossDocQuery(cleanQ, decision);
  if (
    !mmrDisabled(deps.read) && sortedDocIds.length > 1 && shadowedDocId === null && crossDoc
  ) {
    const embeddings = new Map<string, number[]>();
    for (const did of sortedDocIds) {
      const vec = coerceEmbedding(
        (docsMeta.get(did) as { doc_embedding?: unknown } | undefined)?.doc_embedding,
      );
      if (vec !== null) embeddings.set(did, vec);
    }
    sortedDocIds = rerank(sortedDocIds, {
      relevance: grouped.docScore,
      embeddingsById: embeddings,
      topK: sortedDocIds.length, // 전체 재정렬 — 페이지네이션은 그 뒤에.
      lambda: resolveLambda(deps.read),
    });
  }

  const totalDocs = sortedDocIds.length;
  const pageDocIds = paginate(sortedDocIds, offset, limit);

  // 조기 반환 ② — 필터·페이지 밖. `total` 은 필터 후 전체 수다.
  if (pageDocIds.length === 0) {
    const tookMs = elapsedMs();
    record(tookMs, rpcRows.length);
    return {
      body: {
        query: cleanQ,
        total: totalDocs,
        limit,
        offset,
        items: [],
        took_ms: tookMs,
        query_parsed: queryParsed,
        meta: inactiveMeta(),
      },
      headers,
    };
  }

  // 6) 청크 cap + chunk_id → 점수.
  // 원본과 같은 판정 — 함수 진입 시 정해진 top_k 가 doc 필터 값이면 doc 스코프다.
  const isDocScope = topK === RPC_TOP_K_DOC_FILTER;
  const isCrossDocResp = !isDocScope && isCrossDocClassQuery(cleanQ, decision);
  const chunkCap = chunkCapFor({ isDocScope, isCrossDoc: isCrossDocResp });
  const order: ChunkOrder = isDocScope || isCrossDocResp ? "score" : "chunk_idx";

  const chunkRrf = new Map<string, number>();
  for (const did of pageDocIds) {
    for (const [cid, sc] of grouped.docChunkScores.get(did) ?? []) chunkRrf.set(cid, sc);
  }

  // 7) 응답 조립.
  const items = buildItems({
    pageDocIds,
    docsMeta,
    docScore: grouped.docScore,
    docChunkScores: grouped.docChunkScores,
    chunksById,
    chunkRrf,
    cleanQ,
    chunkCap,
    order,
    topScore: sortedDocIds.length > 0 ? grouped.docScore.get(sortedDocIds[0]) : undefined,
  });

  const tookMs = elapsedMs();
  record(tookMs, rpcRows.length);
  return {
    body: {
      query: cleanQ,
      total: totalDocs,
      limit,
      offset,
      items,
      took_ms: tookMs,
      query_parsed: queryParsed,
      meta: inactiveMeta(),
    },
    headers,
  };
}
