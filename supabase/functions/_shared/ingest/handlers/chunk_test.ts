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
function fakeClient(
  rows: { seq: number; payload: unknown }[],
  visionRows: { seq: number; payload: unknown }[] = [],
) {
  const upserts: { row: Record<string, unknown>; opts: unknown }[] = [];
  const sends: Record<string, unknown>[] = [];
  const calls: string[] = [];
  let ordered = false;
  const client = {
    rpc(name: string, args: Record<string, unknown>) {
      calls.push(`rpc:${name}`);
      if (name === "ingest_queue_send") sends.push(args.payload as Record<string, unknown>);
      return Promise.resolve({ data: 1, error: null });
    },
    from(_t: string) {
      // **stage 를 봐야 한다** — 핸들러가 extract 와 vision 을 따로 긁는다.
      // 구분 없이 같은 행을 돌려주면 섹션이 두 번 들어가 테스트가 조용히 통과한다.
      let stage = "extract";
      const q = {
        eq(col: string, val: unknown) {
          if (col === "stage") stage = String(val);
          return q;
        },
        order(_c: string, o?: { ascending?: boolean }) {
          ordered = o?.ascending !== false;
          const src = stage === "vision" ? visionRows : rows;
          return Promise.resolve({
            data: [...src].sort((a, b) => a.seq - b.seq),
            error: null,
          });
        },
      };
      return {
        select: () => q,
        upsert(row: Record<string, unknown>, opts: unknown) {
          calls.push("upsert");
          upserts.push({ row, opts });
          return Promise.resolve({ data: null, error: null });
        },
      };
    },
  };
  return { client, upserts, sends, calls, wasOrdered: () => ordered };
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
    total_parts: number;
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

Deno.test("레코드를 CHUNKS_PER_ARTIFACT 개씩 쪼개 저장한다", async () => {
  // page 를 전부 다르게 줘서 병합을 막는다 → 섹션 수 = 청크 수.
  const secs = Array.from({ length: 7 }, (_, i) => sec(`문장 ${i} 입니다.`, i + 1));
  const { client, upserts, sends } = fakeClient([{ seq: 0, payload: { sections: secs } }]);
  const h = makeChunkHandler({
    // deno-lint-ignore no-explicit-any
    client: client as any,
    env: ENV,
    chunksPerArtifact: 3,
  });
  await h(TASK, {} as never);

  // 7 청크 / 3 = 3 part (3, 3, 1)
  assertEquals(upserts.length, 3);
  assertEquals(upserts.map((u) => u.row.seq), [0, 1, 2]);
  const parts = upserts.map((u) =>
    u.row.payload as { part: number; total_parts: number; records: unknown[] }
  );
  assertEquals(parts.map((p) => p.records.length), [3, 3, 1]);
  assertEquals(parts.map((p) => p.part), [0, 1, 2]);
  // 모든 part 가 total_parts 를 안다 — load 가 어디서 멈출지 판단하는 근거다.
  assertEquals(parts.map((p) => p.total_parts), [3, 3, 3]);
  // chunk_count 는 **전체** 수다(그 part 의 수가 아니다).
  assertEquals(parts.map((p) => (p as unknown as { chunk_count: number }).chunk_count), [7, 7, 7]);

  // 쪼개도 순서는 이어진다.
  const all = parts.flatMap((p) => p.records) as { chunk_idx: number }[];
  assertEquals(all.map((r) => r.chunk_idx), [0, 1, 2, 3, 4, 5, 6]);

  assertEquals(sends.length, 1);
  assertEquals(sends[0], { job_id: "j1", doc_id: "d1", stage: "load", from: 0 });
});

Deno.test("청크가 0 개여도 part 를 하나는 남긴다", async () => {
  // 빈 텍스트만 있으면 섹션이 걸러져 청크가 안 나온다.
  const { client, upserts, sends } = fakeClient([{ seq: 0, payload: { sections: [] } }]);
  // deno-lint-ignore no-explicit-any
  const h = makeChunkHandler({ client: client as any, env: ENV });
  await h(TASK, {} as never);
  assertEquals(upserts.length, 1);
  const p = upserts[0].row.payload as { total_parts: number; records: unknown[] };
  assertEquals(p.total_parts, 1);
  assertEquals(p.records.length, 0);
  // 그래도 load 는 넣는다 — 안 넣으면 잡이 조용히 멈춘다.
  assertEquals(sends.length, 1);
});

Deno.test("저장이 **다 끝난 뒤에** load 를 넣는다", async () => {
  const secs = Array.from({ length: 5 }, (_, i) => sec(`문장 ${i} 입니다.`, i + 1));
  const { client, calls } = fakeClient([{ seq: 0, payload: { sections: secs } }]);
  const h = makeChunkHandler({
    // deno-lint-ignore no-explicit-any
    client: client as any,
    env: ENV,
    chunksPerArtifact: 2,
  });
  await h(TASK, {} as never);
  // upsert 3 회(2/2/1) 뒤에 enqueue 1 회.
  assertEquals(calls, ["upsert", "upsert", "upsert", "rpc:ingest_queue_send"]);
});

Deno.test("vision 섹션은 extract 섹션 **전부 뒤에** 붙는다", async () => {
  // 원본 `_enrich_pdf_with_vision` 이 `sections = list(base.sections)` 로 시작해
  // 페이지 루프에서 append 하기 때문이다. seq 를 섞어 둬 순서 규칙만으로 통과하게 한다.
  const { client, upserts } = fakeClient(
    [
      { seq: 5, payload: { sections: [sec("텍스트 둘째 문장입니다.", 6)] } },
      { seq: 0, payload: { sections: [sec("텍스트 첫째 문장입니다.", 1)] } },
    ],
    [
      { seq: 4, payload: { sections: [sec("비전 둘째 문장입니다.", 5)] } },
      { seq: 0, payload: { sections: [sec("비전 첫째 문장입니다.", 1)] } },
    ],
  );
  // deno-lint-ignore no-explicit-any
  const h = makeChunkHandler({ client: client as any, env: ENV });
  await h(TASK, {} as never);

  const payload = upserts[0].row.payload as Record<string, unknown>;
  assertEquals(payload.section_count, 4);
  assertEquals(payload.extract_parts, 2);
  assertEquals(payload.vision_parts, 2);
  const text = (payload.records as { text: string }[]).map((r) => r.text).join("\n");
  const order = ["텍스트 첫째", "텍스트 둘째", "비전 첫째", "비전 둘째"]
    .map((k) => text.indexOf(k));
  assertEquals(order.every((v, i) => v >= 0 && (i === 0 || v > order[i - 1])), true, text);
});

Deno.test("vision 산출물이 없어도 extract 만으로 돈다", async () => {
  const { client, upserts } = fakeClient([
    { seq: 0, payload: { sections: [sec("본문 문장입니다.", 1)] } },
  ]);
  // deno-lint-ignore no-explicit-any
  const h = makeChunkHandler({ client: client as any, env: ENV });
  await h(TASK, {} as never);
  const payload = upserts[0].row.payload as Record<string, unknown>;
  assertEquals(payload.vision_parts, 0);
  assertEquals(payload.section_count, 1);
});
