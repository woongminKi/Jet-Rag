/**
 * RPC row → doc 단위 그룹·dedupe → 정렬 — `search.py` 의 3) · 5) 단계 포팅.
 *
 * RRF 융합 자체는 Postgres RPC(`search_hybrid_rrf`) 안에 있다. 여기서 하는 일은
 * 그 결과 row 를 문서 단위로 접고 순서를 정하는 것이다.
 *
 * ## chunk_id dedupe 가 핵심이다
 * dense path 와 sparse path 가 **같은 chunk_id 를 별개 row 로** 돌려줄 수 있다.
 * 리스트로 쌓으면 `matched_chunk_count` 가 부풀려지므로 `chunk_id → max(score)` 로
 * 접는다. 문서 점수는 그 문서에 속한 청크 점수의 **최댓값**이다(합이 아니다).
 *
 * ## 동점일 때의 순서도 계약이다
 * 정렬은 점수 내림차순 하나뿐이라 **동점이 흔하다**. Python `sorted(reverse=True)` 는
 * 안정 정렬이라 동점이면 `docs_meta` 의 삽입 순서 — 즉 PostgREST 응답 행 순서 — 가
 * 그대로 남는다. JS `Array.sort` 도 ES2019 부터 안정 정렬이라 같은 결과가 나오지만,
 * **입력 순서가 같아야** 성립한다. 그래서 호출부는 documents 응답 순서를 흐트러뜨리면
 * 안 된다(`Object.keys` 나 `Set` 을 거치면서 순서가 바뀌면 동점 문서의 순위가 갈린다).
 *
 * ## 여기서 안 하는 것
 * 엔티티 boost·vision 인접 boost·doc-level embedding RRF 가산은 운영에서 전부 꺼져 있어
 * 옮기지 않았다(플랜 §3). 켜지면 조용히 다른 순위가 나오므로 `unsupported.ts` 가
 * 기동 시 막는다.
 */

import { applyGuards, type GuardMeta } from "./guards.ts";

/** RPC 가 돌려주는 행. `dense_rank`/`sparse_rank` 는 그 경로에서 안 걸리면 null 이다. */
export interface RpcRow {
  chunk_id: string;
  doc_id: string;
  rrf_score: number | null;
  dense_rank?: number | null;
  sparse_rank?: number | null;
}

export interface GroupOptions {
  /** 청크별 가드 판정 재료. 없는 청크는 가드가 걸리지 않는다. */
  guardMeta: Map<string, GuardMeta>;
  /** reranker 가 점수를 이미 다시 매겼으면 가드를 건너뛴다. */
  coverGuardSkip: boolean;
  tocEnabled: boolean;
  queryWantsToc: boolean;
}

export interface GroupResult {
  /** doc_id → 그 문서 최고 청크 점수. 삽입 순서 = rpc_rows 에서 처음 나온 순서. */
  docScore: Map<string, number>;
  /** doc_id → (chunk_id → 점수). 중복 chunk_id 는 최댓값 하나로 접힌다. */
  docChunkScores: Map<string, Map<string, number>>;
  /** `docScore` 의 키 순서 그대로. */
  candidateDocIds: string[];
}

/**
 * `float(r["rrf_score"])` 대응. 원본은 값이 없거나 숫자가 아니면 `TypeError`/`ValueError`
 * 로 500 이 난다 — 여기서는 그 예외를 흉내내지 않고 숫자만 받는다. 실제 RPC 행은 항상
 * 숫자라 운영 경로에서는 차이가 없다.
 */
function toScore(v: unknown): number {
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : 0;
}

/** RPC row 를 문서 단위로 접는다. 가드 penalty 는 이 안에서 곱해진다. */
export function groupByDoc(rows: readonly RpcRow[], opts: GroupOptions): GroupResult {
  const docScore = new Map<string, number>();
  const docChunkScores = new Map<string, Map<string, number>>();

  for (const r of rows) {
    const docId = r.doc_id;
    const chunkId = r.chunk_id;
    const score = applyGuards(toScore(r.rrf_score), opts.guardMeta.get(chunkId), {
      skip: opts.coverGuardSkip,
      tocEnabled: opts.tocEnabled,
      queryWantsToc: opts.queryWantsToc,
    });

    // 원본이 `max(doc_score.get(doc_id, 0.0), score)` 라 첫 값도 0 과 비교된다 —
    // 음수 점수는 0 으로 바닥이 깔린다. RRF 점수는 양수라 실제로는 안 걸린다.
    docScore.set(docId, Math.max(docScore.get(docId) ?? 0.0, score));

    let chunks = docChunkScores.get(docId);
    if (chunks === undefined) {
      chunks = new Map<string, number>();
      docChunkScores.set(docId, chunks);
    }
    const existing = chunks.get(chunkId);
    if (existing === undefined || score > existing) chunks.set(chunkId, score);
  }

  return { docScore, docChunkScores, candidateDocIds: [...docScore.keys()] };
}

/**
 * 점수 내림차순 정렬. **동점은 입력 순서를 유지한다**(안정 정렬) — 원본의
 * `sorted(..., reverse=True)` 와 같다. `reverse=True` 는 동점을 뒤집지 않는다.
 */
export function sortDocIds(docIds: Iterable<string>, docScore: Map<string, number>): string[] {
  return [...docIds].sort((a, b) => (docScore.get(b) ?? 0) - (docScore.get(a) ?? 0));
}

/** 정렬된 목록에서 한 페이지를 떼어낸다. `total` 은 페이지가 아니라 전체 수다. */
export function paginate<T>(items: readonly T[], offset: number, limit: number): T[] {
  return items.slice(offset, offset + limit);
}
