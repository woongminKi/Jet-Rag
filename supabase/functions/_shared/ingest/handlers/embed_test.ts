/**
 * `embed` 핸들러 계약.
 *
 * 여기서 틀리면 **일부 청크만 벡터가 있는** 상태가 되고, 검색은 되긴 되므로 조용하다.
 * 그래서 "어디까지 채웠나" 를 계약으로 고정한다.
 */

import { assertEquals, assertRejects } from "@std/assert";
import { makeEmbedHandler } from "./embed.ts";
import type { TaskPayload } from "../worker.ts";

const TASK: TaskPayload = { job_id: "j1", doc_id: "d1", stage: "embed" };

/** `chunks` 조회/갱신과 rpc 를 흉내낸다. `dense_vec` 을 채우면 다음 조회에서 빠진다. */
function fakeClient(texts: string[]) {
  const pending = texts.map((t, i) => ({ id: `c${i}`, text: t }));
  const filled = new Map<string, number[]>();
  const sends: Record<string, unknown>[] = [];
  let limitSeen: number | null = null;
  let orderedAsc: boolean | null = null;
  let isNullSeen = false;

  const jobUpdates: Record<string, unknown>[] = [];

  const client = {
    from(table: string) {
      // **테이블을 구분해야 한다.** 구분 안 하면 잡 마감 업데이트가
      // `dense_vec` 채움으로 잘못 세어져 개수 단언이 조용히 어긋난다.
      if (table === "ingest_jobs") {
        return {
          update(patch: Record<string, unknown>) {
            jobUpdates.push(patch);
            return { eq: () => Promise.resolve({ error: null }) };
          },
        };
      }
      const q = {
        _id: null as string | null,
        eq(col: string, v: unknown) {
          if (col === "id") q._id = v as string;
          return q;
        },
        is(col: string, v: unknown) {
          if (col === "dense_vec" && v === null) isNullSeen = true;
          return q;
        },
        order(_c: string, o?: { ascending?: boolean }) {
          orderedAsc = o?.ascending !== false;
          return q;
        },
        limit(n: number) {
          limitSeen = n;
          const rows = pending.filter((r) => !filled.has(r.id)).slice(0, n);
          return Promise.resolve({ data: rows, error: null });
        },
        update(patch: Record<string, unknown>) {
          return {
            eq(_c: string, id: string) {
              filled.set(id, patch.dense_vec as number[]);
              return Promise.resolve({ error: null });
            },
          };
        },
      };
      return { select: () => q, update: q.update };
    },
    rpc(name: string, args: Record<string, unknown>) {
      if (name === "ingest_queue_send") sends.push(args.payload as Record<string, unknown>);
      return Promise.resolve({ data: 1, error: null });
    },
  };
  return {
    jobUpdates,
    client,
    filled,
    sends,
    limitSeen: () => limitSeen,
    orderedAsc: () => orderedAsc,
    isNullSeen: () => isNullSeen,
  };
}

const vecFor = (texts: string[]) => texts.map((t) => [t.length, 0, 0]);

Deno.test("토큰이 없으면 던진다 — 조용히 건너뛰지 않는다", async () => {
  const { client } = fakeClient(["가"]);
  // deno-lint-ignore no-explicit-any
  const h = makeEmbedHandler({ client: client as any, token: "" });
  await assertRejects(() => h(TASK, {} as never), Error, "DEEPINFRA_API_TOKEN");
});

Deno.test("dense_vec 이 NULL 인 것만, chunk_idx 오름차순으로 집는다", async () => {
  const f = fakeClient(["가", "나"]);
  const h = makeEmbedHandler({
    // deno-lint-ignore no-explicit-any
    client: f.client as any,
    token: "T",
    embed: (t) => Promise.resolve(vecFor(t)),
  });
  await h(TASK, {} as never);
  assertEquals(f.isNullSeen(), true);
  assertEquals(f.orderedAsc(), true);
  assertEquals(f.filled.size, 2);
});

Deno.test("배치 크기만큼 나눠 부른다", async () => {
  const f = fakeClient(Array.from({ length: 7 }, (_, i) => `t${i}`));
  const calls: number[] = [];
  const h = makeEmbedHandler({
    // deno-lint-ignore no-explicit-any
    client: f.client as any,
    token: "T",
    batchSize: 3,
    chunksPerTask: 100,
    embed: (t) => (calls.push(t.length), Promise.resolve(vecFor(t))),
  });
  await h(TASK, {} as never);
  assertEquals(calls, [3, 3, 1]);
  assertEquals(f.filled.size, 7);
});

Deno.test("벡터가 해당 청크에 박힌다 — 순서가 밀리면 안 된다", async () => {
  const f = fakeClient(["가", "나나", "다다다"]);
  const h = makeEmbedHandler({
    // deno-lint-ignore no-explicit-any
    client: f.client as any,
    token: "T",
    embed: (t) => Promise.resolve(vecFor(t)),
  });
  await h(TASK, {} as never);
  // vecFor 는 길이를 첫 성분에 넣는다 — 짝이 맞는지 그대로 드러난다.
  assertEquals(f.filled.get("c0")![0], 1);
  assertEquals(f.filled.get("c1")![0], 2);
  assertEquals(f.filled.get("c2")![0], 3);
});

Deno.test("가득 채웠으면 다음 작업을 큐에 넣는다", async () => {
  const f = fakeClient(Array.from({ length: 10 }, (_, i) => `t${i}`));
  const h = makeEmbedHandler({
    // deno-lint-ignore no-explicit-any
    client: f.client as any,
    token: "T",
    chunksPerTask: 4,
    embed: (t) => Promise.resolve(vecFor(t)),
  });
  await h(TASK, {} as never);
  assertEquals(f.limitSeen(), 4);
  assertEquals(f.filled.size, 4);
  assertEquals(f.sends, [{ job_id: "j1", doc_id: "d1", stage: "embed" }]);
});

Deno.test("덜 찼으면 그게 마지막 — 아무것도 넣지 않는다", async () => {
  const f = fakeClient(["가", "나"]);
  const h = makeEmbedHandler({
    // deno-lint-ignore no-explicit-any
    client: f.client as any,
    token: "T",
    chunksPerTask: 4,
    embed: (t) => Promise.resolve(vecFor(t)),
  });
  await h(TASK, {} as never);
  assertEquals(f.sends, []);
});

Deno.test("남은 청크가 없으면 아무것도 하지 않는다 — 재시도해도 안전", async () => {
  const f = fakeClient([]);
  let called = 0;
  const h = makeEmbedHandler({
    // deno-lint-ignore no-explicit-any
    client: f.client as any,
    token: "T",
    embed: (t) => (called++, Promise.resolve(vecFor(t))),
  });
  await h(TASK, {} as never);
  assertEquals(called, 0);
  assertEquals(f.sends, []);
});

Deno.test("임베딩 개수가 안 맞으면 던진다", async () => {
  const f = fakeClient(["가", "나"]);
  const h = makeEmbedHandler({
    // deno-lint-ignore no-explicit-any
    client: f.client as any,
    token: "T",
    embed: () => Promise.resolve([[1, 2, 3]]), // 2개 요청에 1개 응답
  });
  await assertRejects(() => h(TASK, {} as never), Error, "개수 불일치");
});

Deno.test("남은 게 없으면 잡을 completed 로 마감한다 — 사슬의 끝이다", async () => {
  // 원본은 doc_embed·dedup 뒤에 마감한다. 그 둘이 아직 없어 embed 가 끝이다.
  // 마감을 안 하면 잡이 영원히 running 이라 `/documents/active` 가 계속 진행 중으로
  // 보여 주고 reingest 2 종이 항상 409 를 낸다(E2E 가 실제로 잡았다).
  const f = fakeClient([]);
  const h = makeEmbedHandler({
    // deno-lint-ignore no-explicit-any
    client: f.client as any,
    token: "T",
    embed: (t) => Promise.resolve(vecFor(t)),
    nowMs: () => 1_757_251_496_000,
  });
  await h(TASK, {} as never);
  assertEquals(f.jobUpdates, [{
    status: "completed",
    current_stage: "done",
    finished_at: "2025-09-07T13:24:56+00:00",
  }]);
});

Deno.test("아직 남았으면 마감하지 않고 다음 embed 를 넣는다", async () => {
  const f = fakeClient(Array.from({ length: 4 }, (_, i) => `t${i}`));
  const h = makeEmbedHandler({
    // deno-lint-ignore no-explicit-any
    client: f.client as any,
    token: "T",
    chunksPerTask: 4, // 가득 채웠다 = 더 남았을 수 있다
    embed: (t) => Promise.resolve(vecFor(t)),
  });
  await h(TASK, {} as never);
  assertEquals(f.jobUpdates, []);
  assertEquals(f.sends.length, 1);
});
