/**
 * `toChunkRecords` / `runChunkStage` 의 `idxOffset` 계약.
 *
 * 창 분할(`handlers/chunk.ts`)이 문서를 여러 태스크로 나눠 처리하므로 `chunk_idx` 는
 * **창마다 이어져야** 한다. offset 이 없으면 창마다 0 부터 다시 시작해
 * `chunks(doc_id, chunk_idx)` upsert 가 앞 창을 덮어쓴다 — 청크가 조용히 사라진다.
 *
 * offset 0 이 현행과 **한 글자도 다르지 않아야** 한다는 것도 같이 고정한다.
 */

import { assertEquals } from "@std/assert";
import { runChunkStage, toChunkRecords } from "./chunk_records.ts";
import type { ExtractedSection } from "./hwp_extract.ts";

const ENV = {
  captionPrefixEnabled: false,
  synonymInjectionEnabled: false,
  synonymLlmEnabled: false,
};

function sec(text: string, page: number): ExtractedSection {
  return { text, page, section_title: null, bbox: null, metadata: {} };
}

/** page 를 전부 다르게 줘서 병합을 막는다 → 섹션 수 = 청크 수. */
const SECTIONS = [
  sec("첫째 문장입니다.", 1),
  sec("둘째 문장입니다.", 2),
  sec("셋째 문장입니다.", 3),
];

Deno.test("idxOffset 을 안 주면 현행과 동일하다 — 0 부터, 첫 청크는 overlap 없음", () => {
  const records = toChunkRecords({ docId: "d1", sections: SECTIONS, env: ENV });
  assertEquals(records.map((r) => r.chunk_idx), [0, 1, 2]);
  assertEquals("overlap_with_prev_chunk_idx" in records[0].metadata, false);
  assertEquals(records[1].metadata["overlap_with_prev_chunk_idx"], 0);
  assertEquals(records[2].metadata["overlap_with_prev_chunk_idx"], 1);
});

Deno.test("idxOffset=0 은 생략과 byte-identical 하다", () => {
  const a = toChunkRecords({ docId: "d1", sections: SECTIONS, env: ENV });
  const b = toChunkRecords({ docId: "d1", sections: SECTIONS, env: ENV, idxOffset: 0 });
  assertEquals(JSON.stringify(b), JSON.stringify(a));
});

Deno.test("idxOffset=100 이면 chunk_idx 가 100 부터고 첫 청크 overlap 은 99 다", () => {
  const records = toChunkRecords({
    docId: "d1",
    sections: SECTIONS,
    env: ENV,
    idxOffset: 100,
  });
  assertEquals(records.map((r) => r.chunk_idx), [100, 101, 102]);
  // 창 경계의 첫 청크는 **앞 창의 마지막 청크**와 이어진다 — 그래서 overlap 이 붙는다.
  assertEquals(records[0].metadata["overlap_with_prev_chunk_idx"], 99);
  assertEquals(records[1].metadata["overlap_with_prev_chunk_idx"], 100);
});

Deno.test("idxOffset 은 chunk_idx·overlap 말고는 아무것도 안 바꾼다", () => {
  const a = toChunkRecords({ docId: "d1", sections: SECTIONS, env: ENV });
  const b = toChunkRecords({ docId: "d1", sections: SECTIONS, env: ENV, idxOffset: 7 });
  for (let i = 0; i < a.length; i++) {
    assertEquals(b[i].text, a[i].text);
    assertEquals(b[i].page, a[i].page);
    assertEquals(b[i].char_range, a[i].char_range);
    assertEquals(b[i].section_title, a[i].section_title);
  }
});

Deno.test("runChunkStage 도 idxOffset 을 그대로 흘린다", () => {
  const records = runChunkStage({
    docId: "d1",
    sections: SECTIONS,
    env: ENV,
    idxOffset: 5,
  });
  assertEquals(records.map((r) => r.chunk_idx), [5, 6, 7]);
  assertEquals(records[0].metadata["overlap_with_prev_chunk_idx"], 4);
});
