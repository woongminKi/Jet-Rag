/**
 * `ChunkRecord` → `chunks` 테이블 행 — `SupabasePgVectorStore._serialize_chunk` 포팅.
 *
 * ## 키를 넣느냐 마느냐가 계약이다
 * `bbox` · `dense_vec` · `char_range` · `id` 는 **값이 있을 때만** 넣는다. `null` 로
 * 명시하면 upsert 가 기존 값을 지운다 — `embed` 단계가 나중에 채우는 `dense_vec` 을
 * `load` 재실행이 날려 버리는 사고가 된다.
 *
 * 반대로 `sparse_json` · `metadata` · `flags` 는 **빈 객체라도 반드시** 넣는다.
 * 원본 주석 그대로 "직전 레코드 flags 가 잔존하지 않도록" 이다.
 *
 * ## `char_range` 는 문자열이다
 * `chunks.char_range` 는 `INT4RANGE` 다. PostgREST 로는 `"[start,end)"` 리터럴을
 * 보낸다 — 끝이 **열린 구간**이라 `]` 가 아니라 `)` 다.
 */

import type { ChunkRecord } from "./chunk_records.ts";

/** `chunks` 테이블 한 행. 선택 키는 값이 있을 때만 존재한다. */
export type ChunkRow = Record<string, unknown>;

export function chunkRecordToRow(chunk: ChunkRecord): ChunkRow {
  const row: ChunkRow = {
    doc_id: chunk.doc_id,
    chunk_idx: chunk.chunk_idx,
    text: chunk.text,
    page: chunk.page,
    section_title: chunk.section_title,
    // 빈 객체도 명시한다 — 생략하면 이전 값이 남는다.
    sparse_json: (chunk as { sparse_json?: Record<string, number> }).sparse_json ?? {},
    metadata: chunk.metadata ?? {},
    flags: (chunk as { flags?: Record<string, unknown> }).flags ?? {},
  };
  if (chunk.bbox !== null && chunk.bbox !== undefined) {
    row["bbox"] = [...chunk.bbox];
  }
  const dense = (chunk as { dense_vec?: number[] | null }).dense_vec;
  if (dense !== null && dense !== undefined) {
    row["dense_vec"] = dense;
  }
  if (chunk.char_range !== null && chunk.char_range !== undefined) {
    const [start, end] = chunk.char_range;
    row["char_range"] = `[${start},${end})`;
  }
  const id = (chunk as { chunk_id?: string | null }).chunk_id;
  if (id) row["id"] = id;
  return row;
}
