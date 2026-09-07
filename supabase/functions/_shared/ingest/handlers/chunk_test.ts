/**
 * `chunk` 핸들러 계약 — **extract 산출물을 전부, 순서대로** 모아야 한다.
 *
 * 여기서 순서가 틀리거나 일부가 빠지면 청크가 조용히 달라진다. 그런 실패는 나중에
 * 검색 품질 문제로만 보여서 원인을 못 찾는다. 그래서 계약으로 고정한다.
 */

import { assertEquals, assertRejects } from "@std/assert";
import { makeChunkHandler } from "./chunk.ts";
import type { TaskPayload } from "../worker.ts";

const TASK: TaskPayload = { job_id: "j1", doc_id: "d1", stage: "chunk" };
const ENV = {
  captionPrefixEnabled: false,
  synonymInjectionEnabled: false,
  synonymLlmEnabled: false,
};

function sec(text: string, page: number) {
  return { text, page, section_title: null, bbox: null, metadata: {} };
}

/**
 * `ingest_artifacts` 조회/upsert 흉내.
 * `rows` 는 **일부러 뒤섞어** 둔다 — 핸들러가 `order` 를 걸어야만 통과한다.
 */
function fakeClient(rows: { seq: number; payload: unknown }[]) {
  const upserts: { row: Record<string, unknown>; opts: unknown }[] = [];
  let ordered = false;
  const client = {
    from(_t: string) {
      const q = {
        eq: () => q,
        order(_c: string, o?: { ascending?: boolean }) {
          ordered = o?.ascending !== false;
          return Promise.resolve({
            data: [...rows].sort((a, b) => a.seq - b.seq),
            error: null,
          });
        },
      };
      return {
        select: () => q,
        upsert(row: Record<string, unknown>, opts: unknown) {
          upserts.push({ row, opts });
          return Promise.resolve({ data: null, error: null });
        },
      };
    },
  };
  return { client, upserts, wasOrdered: () => ordered };
}

Deno.test("extract 산출물이 없으면 던진다 — 빈 청크로 덮지 않는다", async () => {
  const { client } = fakeClient([]);
  // deno-lint-ignore no-explicit-any
  const h = makeChunkHandler({ client: client as any, env: ENV });
  await assertRejects(() => h(TASK, {} as never), Error, "extract 산출물이 없다");
});

Deno.test("seq 가 중복되면 던진다", async () => {
  const { client } = fakeClient([
    { seq: 0, payload: { sections: [sec("가", 1)] } },
    { seq: 0, payload: { sections: [sec("나", 2)] } },
  ]);
  // deno-lint-ignore no-explicit-any
  const h = makeChunkHandler({ client: client as any, env: ENV });
  await assertRejects(() => h(TASK, {} as never), Error, "seq 가 중복");
});

Deno.test("여러 extract 조각을 seq 순으로 이어붙인다", async () => {
  // 입력 순서를 뒤집어 둔다. 정렬을 안 하면 텍스트 순서가 뒤바뀐다.
  const { client, upserts, wasOrdered } = fakeClient([
    { seq: 10, payload: { sections: [sec("셋째 문장입니다.", 11)] } },
    { seq: 0, payload: { sections: [sec("첫째 문장입니다.", 1)] } },
    { seq: 5, payload: { sections: [sec("둘째 문장입니다.", 6)] } },
  ]);
  // deno-lint-ignore no-explicit-any
  const h = makeChunkHandler({ client: client as any, env: ENV });
  await h(TASK, {} as never);

  assertEquals(wasOrdered(), true);
  const payload = upserts[0].row.payload as {
    chunk_count: number;
    section_count: number;
    extract_parts: number;
    records: { text: string; chunk_idx: number; page: number }[];
  };
  assertEquals(payload.section_count, 3);
  assertEquals(payload.extract_parts, 3);
  assertEquals(payload.records.length, payload.chunk_count);
  // page 가 다르면 병합되지 않는다(`mergeShortSections` 의 3 조건 중 하나).
  // 그래서 청크 3 개가 그대로 남고 **순서**가 드러난다.
  assertEquals(payload.records.map((r) => r.text), [
    "첫째 문장입니다.",
    "둘째 문장입니다.",
    "셋째 문장입니다.",
  ]);
  assertEquals(payload.records.map((r) => r.page), [1, 6, 11]);
});

Deno.test("같은 page 의 짧은 섹션은 병합된다 — 이어붙인 순서가 텍스트에 드러난다", async () => {
  const { client, upserts } = fakeClient([
    { seq: 10, payload: { sections: [sec("셋째 문장입니다.", 1)] } },
    { seq: 0, payload: { sections: [sec("첫째 문장입니다.", 1)] } },
    { seq: 5, payload: { sections: [sec("둘째 문장입니다.", 1)] } },
  ]);
  // deno-lint-ignore no-explicit-any
  const h = makeChunkHandler({ client: client as any, env: ENV });
  await h(TASK, {} as never);
  const payload = upserts[0].row.payload as { records: { text: string }[] };
  assertEquals(payload.records.length, 1);
  assertEquals(
    payload.records[0].text,
    "첫째 문장입니다.\n\n둘째 문장입니다.\n\n셋째 문장입니다.",
  );
});

Deno.test("payload.sections 가 없는 조각은 건너뛴다", async () => {
  const { client, upserts } = fakeClient([
    { seq: 0, payload: { sections: [sec("본문", 1)] } },
    { seq: 1, payload: {} },
    { seq: 2, payload: null },
  ]);
  // deno-lint-ignore no-explicit-any
  const h = makeChunkHandler({ client: client as any, env: ENV });
  await h(TASK, {} as never);
  const payload = upserts[0].row.payload as { section_count: number; extract_parts: number };
  assertEquals(payload.section_count, 1);
  assertEquals(payload.extract_parts, 3);
});

Deno.test("chunk 산출물은 seq 0 에 onConflict 로 upsert 한다", async () => {
  const { client, upserts } = fakeClient([{ seq: 0, payload: { sections: [sec("본문", 1)] } }]);
  // deno-lint-ignore no-explicit-any
  const h = makeChunkHandler({ client: client as any, env: ENV });
  await h(TASK, {} as never);
  assertEquals(upserts[0].row.stage, "chunk");
  assertEquals(upserts[0].row.seq, 0);
  assertEquals(upserts[0].row.job_id, "j1");
  assertEquals(upserts[0].row.doc_id, "d1");
  assertEquals(upserts[0].opts, { onConflict: "job_id,stage,seq" });
});
