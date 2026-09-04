/**
 * 응답 조립 — `search.py` 7) 단계 포팅.
 *
 * 정렬된 doc 목록과 청크 본문을 받아 `SearchResponse.items` 를 만든다.
 *
 * ## relevance 는 결과 집합 내 정규화다
 * `round(min(1.0, doc_score / top_score), 4)` — 절대값이 아니라 **그 응답의 1 위 대비**
 * 비율이다. 같은 문서라도 함께 나온 문서가 달라지면 값이 달라진다.
 *
 * ## 반올림이 언어마다 다르다
 * Python `round()` 는 **은행가 반올림**(정확히 절반이면 짝수 쪽)이고, JS 의
 * `toFixed`/`Math.round` 는 절반을 위로 올린다. 소수 4 자리에서 정확히 절반이 되는 값은
 * `m/32`(m 홀수) 꼴 16 개뿐이고 그중 8 개에서 결과가 갈린다(예: `0.03125` → Python
 * `0.0312`, `toFixed` `0.0313`). RRF 점수 비율이 정확히 이 값이 될 일은 사실상 없지만,
 * "사실상 없다" 를 코드에 남기면 나중에 못 찾는다. `pyRound` 로 정확히 맞췄다.
 *
 * ## `1.0` 과 `1` — 응답 바이트가 다르다 (실측, 의도한 차이)
 * Python(pydantic)은 `"relevance":1.0` 으로, `JSON.stringify` 는 `"relevance":1` 로 쓴다.
 * **1 위 문서의 relevance 는 항상 1.0** 이라 모든 응답에 나타난다. 어떤 JSON 파서로 읽어도
 * 같은 수라 프론트 동작은 같고, 굳이 맞추려면 표준 직렬화를 버리고 문자열을 손으로 짜야
 * 한다. 맞추지 않기로 했다 — 대신 **엔드투엔드 대조는 바이트가 아니라 파싱한 값으로** 한다.
 *
 * ## 청크 표시 순서가 모드마다 다르다
 * | 모드 | 순서 | cap |
 * |---|---|---|
 * | doc 스코프 (`doc_id` 지정) | 점수 내림차순 | 200 |
 * | cross-doc 질의 | 점수 내림차순 | 8 |
 * | 목록 (기본) | **`chunk_idx` 오름차순** | 3 |
 *
 * 목록 모드만 순서가 다르다 — 미리보기는 본문 등장 순서로 보여주는 게 자연스러워서다.
 * 어느 쪽이든 **먼저 점수 상위 `cap` 개를 고른 뒤** 표시 순서를 정한다. 순서를 먼저
 * 정하고 자르면 다른 청크가 뽑힌다.
 */

import { makeSnippetWithHighlights, stripSynonymMarker } from "./snippet.ts";

export const MAX_MATCHED_CHUNKS_PER_DOC = 3;
export const MAX_MATCHED_CHUNKS_PER_DOC_CROSS_DOC = 8;
export const MAX_MATCHED_CHUNKS_DOC_SCOPE = 200;

/**
 * Python `round(x, nd)` — 은행가 반올림. 이진 표현을 정확히 꺼내 `BigInt` 로 비교하므로
 * 부동소수 오차 없이 원본과 같은 값이 나온다.
 *
 * `10^nd` 로 곱한 값이 `2^53` 을 넘으면 마지막 나눗셈에서 정밀도를 잃는다 — relevance 는
 * 0~1 에 `nd=4` 라 해당 없다.
 */
export function pyRound(x: number, nd: number): number {
  if (!Number.isFinite(x) || x === 0) return x;
  const neg = x < 0;
  const a = Math.abs(x);

  const view = new DataView(new ArrayBuffer(8));
  view.setFloat64(0, a);
  const bits = view.getBigUint64(0);
  const expBits = Number((bits >> 52n) & 0x7ffn);
  const mantBits = bits & 0xf_ffff_ffff_ffffn;
  // a = m × 2^e 로 정확히 분해한다 (지수부 0 은 비정규화 수).
  const m = expBits === 0 ? mantBits : mantBits | (1n << 52n);
  const e = expBits === 0 ? -1074 : expBits - 1075;

  const p = 10n ** BigInt(nd);
  let q: bigint;
  if (e >= 0) {
    q = m * p * (1n << BigInt(e)); // 이미 정수 — 반올림할 것이 없다
  } else {
    const k = BigInt(-e);
    const n = m * p;
    q = n >> k;
    const r = n - (q << k);
    const half = 1n << (k - 1n);
    // 정확히 절반이면 짝수 쪽으로 — 이게 Python 과 JS 가 갈리는 지점이다.
    if (r > half || (r === half && (q & 1n) === 1n)) q += 1n;
  }
  const out = Number(q) / Number(p);
  return neg ? -out : out;
}

/** 결과 집합 내 정규화 분모. 1 위 점수가 0 이하면 1 로 둔다(0 나눗셈 회피). */
export function normalizeBase(topScore: number | undefined): number {
  if (topScore === undefined) return 1.0;
  return topScore > 0 ? topScore : 1.0;
}

/** `round(min(1.0, score / normalize), 4)`. */
export function relevanceOf(score: number, normalize: number): number {
  return pyRound(Math.min(1.0, score / normalize), 4);
}

export interface ChunkRow {
  id: string;
  chunk_idx: number;
  text?: string | null;
  page?: number | null;
  section_title?: string | null;
  metadata?: Record<string, unknown> | null;
}

export interface DocMetaRow {
  title?: string | null;
  doc_type?: string | null;
  tags?: string[] | null;
  summary?: string | null;
  created_at?: string | null;
}

export interface MatchedChunk {
  chunk_id: string;
  chunk_idx: number;
  text: string;
  page: number | null;
  section_title: string | null;
  highlight: [number, number][];
  rrf_score: number | null;
  metadata: Record<string, unknown> | null;
}

export interface SearchHit {
  doc_id: string;
  doc_title: string;
  doc_type: string;
  tags: string[];
  summary: string | null;
  created_at: string;
  relevance: number;
  matched_chunk_count: number;
  matched_chunks: MatchedChunk[];
}

/** doc 스코프 / cross-doc 은 점수 내림차순, 목록 모드는 `chunk_idx` 오름차순. */
export type ChunkOrder = "score" | "chunk_idx";

export function chunkCapFor(mode: { isDocScope: boolean; isCrossDoc: boolean }): number {
  if (mode.isDocScope) return MAX_MATCHED_CHUNKS_DOC_SCOPE;
  if (mode.isCrossDoc) return MAX_MATCHED_CHUNKS_PER_DOC_CROSS_DOC;
  return MAX_MATCHED_CHUNKS_PER_DOC;
}

/**
 * 점수 상위 `cap` 개 청크 id. 동점은 입력 순서를 유지한다(`rrf.ts` 와 같은 이유 —
 * 원본이 dict 순서에 기대는 안정 정렬이다).
 */
export function selectTopChunkIds(chunkScores: Map<string, number>, cap: number): string[] {
  return [...chunkScores.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, cap)
    .map(([cid]) => cid);
}

/** 표시 순서를 정한다. 본문이 없는 청크는 원본처럼 조용히 빠진다. */
export function orderChunks(
  topIds: readonly string[],
  chunksById: Map<string, ChunkRow>,
  order: ChunkOrder,
): ChunkRow[] {
  const rows = topIds.map((cid) => chunksById.get(cid)).filter((c): c is ChunkRow => c !== undefined);
  if (order === "score") return rows;
  return rows.sort((a, b) => a.chunk_idx - b.chunk_idx);
}

export function buildMatchedChunk(
  c: ChunkRow,
  cleanQ: string,
  chunkRrf: Map<string, number>,
): MatchedChunk {
  const { text, highlights } = makeSnippetWithHighlights(
    stripSynonymMarker(c.text ?? ""),
    cleanQ,
  );
  const meta = c.metadata ?? null;
  return {
    chunk_id: c.id,
    chunk_idx: c.chunk_idx,
    text,
    page: c.page ?? null,
    section_title: c.section_title ?? null,
    highlight: highlights,
    rrf_score: chunkRrf.get(c.id) ?? null,
    // 원본이 `chunk_meta if chunk_meta else None` 이라 **빈 dict 도 null 이 된다**.
    metadata: meta && Object.keys(meta).length > 0 ? meta : null,
  };
}

export interface BuildItemsInput {
  pageDocIds: readonly string[];
  docsMeta: Map<string, DocMetaRow>;
  docScore: Map<string, number>;
  docChunkScores: Map<string, Map<string, number>>;
  chunksById: Map<string, ChunkRow>;
  chunkRrf: Map<string, number>;
  cleanQ: string;
  chunkCap: number;
  order: ChunkOrder;
  /** 1 위 문서 점수. `sorted_doc_ids` 가 비었으면 `undefined`. */
  topScore: number | undefined;
}

export function buildItems(input: BuildItemsInput): SearchHit[] {
  const normalize = normalizeBase(input.topScore);
  const items: SearchHit[] = [];

  for (const docId of input.pageDocIds) {
    const meta = input.docsMeta.get(docId) ?? {};
    const allMatches = input.docChunkScores.get(docId) ?? new Map<string, number>();
    // dedupe 된 unique 청크 수 — 표시 개수(cap 적용 후)가 아니다.
    const matchedCount = allMatches.size;

    const topIds = selectTopChunkIds(allMatches, input.chunkCap);
    const topChunks = orderChunks(topIds, input.chunksById, input.order);

    items.push({
      doc_id: docId,
      doc_title: meta.title ?? "",
      doc_type: meta.doc_type ?? "",
      tags: meta.tags ?? [],
      summary: meta.summary ?? null,
      created_at: meta.created_at ?? "",
      relevance: relevanceOf(input.docScore.get(docId) ?? 0, normalize),
      matched_chunk_count: matchedCount,
      matched_chunks: topChunks.map((c) => buildMatchedChunk(c, input.cleanQ, input.chunkRrf)),
    });
  }
  return items;
}
