/**
 * `chunk_filter` 의 카운트/판정 분리 계약.
 *
 * 창 분할 때문에 "문서 전체에서 3회 반복" 판정을 창 안에서 할 수 없다. 그래서
 * 카운트(`collectShortCounts`)와 판정(`headerFooterTexts`)을 떼어 놨다. 둘을 이어
 * 붙인 `detectHeaderFooterTexts` 가 **예전과 같아야** 한다 — 여기서 고정한다.
 */

import { assertEquals } from "@std/assert";
import {
  classifyChunk,
  collectShortCounts,
  detectHeaderFooterTexts,
  headerFooterTexts,
  runChunkFilterStage,
} from "./chunk_filter.ts";
import type { ChunkRecord } from "./chunk_records.ts";

function rec(text: string, idx = 0): ChunkRecord {
  return {
    doc_id: "d1",
    chunk_idx: idx,
    text,
    page: 1,
    section_title: null,
    bbox: null,
    char_range: [0, text.length],
    metadata: {},
  };
}

Deno.test("collectShortCounts — 빈 텍스트와 100자 이상은 안 센다", () => {
  const counts = collectShortCounts([
    rec("머리말"),
    rec("  머리말  "), // strip 후 같은 텍스트다
    rec("   "), // strip 하면 빈 문자열
    rec("가".repeat(100)), // 100자는 경계 밖 (`< 100`)
    rec("가".repeat(99)),
  ]);
  assertEquals(counts.get("머리말"), 2);
  assertEquals(counts.has(""), false);
  assertEquals(counts.has("가".repeat(100)), false);
  assertEquals(counts.get("가".repeat(99)), 1);
});

Deno.test("collectShortCounts — 여러 번 나눠 불러도 합계가 같다 (창 분할)", () => {
  const all = [rec("머리말"), rec("머리말"), rec("본문"), rec("머리말")];
  const whole = collectShortCounts(all);

  const acc = new Map<string, number>();
  collectShortCounts(all.slice(0, 2), acc);
  collectShortCounts(all.slice(2), acc);
  assertEquals([...acc].sort(), [...whole].sort());
});

Deno.test("headerFooterTexts — 3회 이상만 남는다", () => {
  const set = headerFooterTexts([["a", 2], ["b", 3], ["c", 4]]);
  assertEquals([...set].sort(), ["b", "c"]);
});

Deno.test("detectHeaderFooterTexts 는 둘을 이어 붙인 것과 같다", () => {
  const chunks = [rec("머리말"), rec("머리말"), rec("머리말"), rec("본문")];
  assertEquals(
    [...detectHeaderFooterTexts(chunks)],
    [...headerFooterTexts(collectShortCounts(chunks))],
  );
  assertEquals([...detectHeaderFooterTexts(chunks)], ["머리말"]);
});

Deno.test("runChunkFilterStage 는 그대로 — 마킹이지 삭제가 아니다", () => {
  const r = runChunkFilterStage([
    rec("머리말", 0),
    rec("머리말", 1),
    rec("머리말", 2),
    rec("이것은 충분히 긴 본문 문장입니다. 필터에 걸리지 않아야 합니다.", 3),
  ]);
  assertEquals(r.chunks.length, 4);
  assertEquals(r.chunks.map((c) => c.flags?.["filtered_reason"] ?? null), [
    "header_footer",
    "header_footer",
    "header_footer",
    null,
  ]);
  assertEquals(r.counts["header_footer"], 3);
});

Deno.test("classifyChunk 는 카운트 출처를 안 가린다 — Set 만 받는다", () => {
  const hf = headerFooterTexts([["머리말", 3]]);
  assertEquals(classifyChunk(rec("머리말"), hf), "header_footer");
  assertEquals(classifyChunk(rec("머리말"), new Set()), null);
});
