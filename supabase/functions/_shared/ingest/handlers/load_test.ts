/**
 * `load` 핸들러 계약 — part 하나씩 읽어 `chunks` 에 upsert 하고 이어간다.
 *
 * 여기서 고정하는 것:
 * - part 를 **하나만** 읽는다 (전부 읽으면 SK 최대 문서에서 13MB 를 든다)
 * - upsert 를 batch 로 쪼갠다 (Supabase statement_timeout)
 * - `on_conflict` 는 `doc_id,chunk_idx` — 원본 `upsert_chunks` 와 같아야 한다
 * - 마지막 part 에서는 `embed` 를 넣는다 (dense_vec 을 채워야 검색이 된다)
 * - 없는 part 는 **조용히 넘기지 않는다**
 */

import { assertEquals, assertRejects } from "@std/assert";
import { makeLoadHandler } from "./load.ts";
import type { TaskPayload } from "../worker.ts";

const TASK: TaskPayload = { job_id: "j1", doc_id: "d1", stage: "load", from: 0 };

function rec(idx: number, over: Record<string, unknown> = {}) {
  return {
    doc_id: "d1",
    chunk_idx: idx,
    text: `본문 ${idx}`,
    page: idx + 1,
    section_title: null,
    bbox: null,
    char_range: [0, 5],
    metadata: {},
    ...over,
  };
}

/** `ingest_artifacts` 단건 조회 · `chunks` upsert · rpc 를 흉내낸다. */
function fakeClient(parts: Record<number, unknown>) {
  const upserts: { rows: Record<string, unknown>[]; opts: unknown }[] = [];
  const sends: Record<string, unknown>[] = [];
  let askedSeq: number | null = null;

  const client = {
    from(table: string) {
      if (table === "ingest_artifacts") {
        const q = {
          _seq: null as number | null,
          eq(col: string, v: unknown) {
            if (col === "seq") {
              q._seq = v as number;
              askedSeq = v as number;
            }
            return q;
          },
          limit() {
            const p = q._seq !== null ? parts[q._seq] : undefined;
            return Promise.resolve({
              data: p === undefined ? [] : [{ seq: q._seq, payload: p }],
              error: null,
            });
          },
        };
        return { select: () => q };
      }
      // chunks
      return {
        upsert(rows: Record<string, unknown>[], opts: unknown) {
          upserts.push({ rows, opts });
          return Promise.resolve({ data: null, error: null });
        },
      };
    },
    rpc(name: string, args: Record<string, unknown>) {
      if (name === "ingest_queue_send") sends.push(args.payload as Record<string, unknown>);
      return Promise.resolve({ data: 1, error: null });
    },
  };
  return { client, upserts, sends, askedSeq: () => askedSeq };
}

Deno.test("part 가 없으면 던진다 — 청크를 조용히 잃지 않는다", async () => {
  const { client } = fakeClient({});
  // deno-lint-ignore no-explicit-any
  const h = makeLoadHandler({ client: client as any });
  await assertRejects(() => h(TASK, {} as never), Error, "chunk 산출물 part 0 이 없다");
});

Deno.test("요청받은 part 만 읽는다", async () => {
  const { client, askedSeq } = fakeClient({
    0: { part: 0, total_parts: 3, records: [rec(0)] },
    1: { part: 1, total_parts: 3, records: [rec(1)] },
    2: { part: 2, total_parts: 3, records: [rec(2)] },
  });
  // deno-lint-ignore no-explicit-any
  const h = makeLoadHandler({ client: client as any });
  await h({ ...TASK, from: 1 }, {} as never);
  assertEquals(askedSeq(), 1);
});

Deno.test("upsert 를 batch 로 쪼개고 on_conflict 를 준다", async () => {
  const records = Array.from({ length: 7 }, (_, i) => rec(i));
  const { client, upserts } = fakeClient({ 0: { part: 0, total_parts: 1, records } });
  // deno-lint-ignore no-explicit-any
  const h = makeLoadHandler({ client: client as any, batchSize: 3 });
  await h(TASK, {} as never);

  assertEquals(upserts.map((u) => u.rows.length), [3, 3, 1]);
  assertEquals(upserts[0].opts, { onConflict: "doc_id,chunk_idx" });
  // 순서가 유지돼야 한다.
  const all = upserts.flatMap((u) => u.rows) as { chunk_idx: number }[];
  assertEquals(all.map((r) => r.chunk_idx), [0, 1, 2, 3, 4, 5, 6]);
});

Deno.test("남은 part 가 있으면 다음을 큐에 넣는다", async () => {
  const { client, sends } = fakeClient({
    0: { part: 0, total_parts: 3, records: [rec(0)] },
  });
  // deno-lint-ignore no-explicit-any
  const h = makeLoadHandler({ client: client as any });
  await h(TASK, {} as never);
  assertEquals(sends, [{ job_id: "j1", doc_id: "d1", stage: "load", from: 1 }]);
});

Deno.test("마지막 part 면 embed 를 넣는다 — dense_vec 이 NULL 이면 검색이 반만 된다", async () => {
  const { client, sends } = fakeClient({
    2: { part: 2, total_parts: 3, records: [rec(9)] },
  });
  // deno-lint-ignore no-explicit-any
  const h = makeLoadHandler({ client: client as any });
  await h({ ...TASK, from: 2 }, {} as never);
  assertEquals(sends, [{ job_id: "j1", doc_id: "d1", stage: "embed" }]);
});

Deno.test("records 가 비어도 던지지 않는다 (빈 문서)", async () => {
  const { client, upserts, sends } = fakeClient({
    0: { part: 0, total_parts: 1, records: [] },
  });
  // deno-lint-ignore no-explicit-any
  const h = makeLoadHandler({ client: client as any });
  await h(TASK, {} as never);
  assertEquals(upserts.length, 0);
  // 빈 문서라도 embed 는 넣는다. embed 핸들러가 "남은 청크 없음" 으로 즉시 끝낸다 —
  // 여기서 안 넣으면 잡이 조용히 멈춘 것처럼 보인다.
  assertEquals(sends, [{ job_id: "j1", doc_id: "d1", stage: "embed" }]);
});

Deno.test("total_parts 가 없으면 1 로 본다 — 곧장 embed 로 넘어간다", async () => {
  const { client, sends } = fakeClient({ 0: { records: [rec(0)] } });
  // deno-lint-ignore no-explicit-any
  const h = makeLoadHandler({ client: client as any });
  await h(TASK, {} as never);
  assertEquals(sends, [{ job_id: "j1", doc_id: "d1", stage: "embed" }]);
});
