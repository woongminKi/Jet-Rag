/**
 * `embed` 핸들러 계약.
 *
 * 여기서 틀리면 **일부 청크만 벡터가 있는** 상태가 되고, 검색은 되긴 되므로 조용하다.
 * 그래서 "어디까지 채웠나" 를 계약으로 고정한다.
 */

import { assertEquals, assertRejects } from "@std/assert";
import { makeEmbedHandler, roundForHalfvec } from "./embed.ts";
import type { TaskPayload } from "../worker.ts";

const TASK: TaskPayload = { job_id: "j1", doc_id: "d1", stage: "embed" };

/** 청크 하나. `reason` 이 있으면 `flags.filtered_reason` 이 붙은 행이다. */
type Row = string | { text: string; reason: string };

/**
 * `chunks` 조회와 rpc 를 흉내낸다. `chunks_set_dense_vec` 로 채운 행은 다음 조회에서 빠진다.
 * 단건 `update()` 는 **일부러 없다** — 핸들러가 그 경로로 돌아가면 여기서 TypeError 로 드러난다(사고 4).
 *
 * `is()` 로 건 필터를 **그대로 적용한다** — 필터를 세기만 하고 행은 다 돌려주면
 * "필터를 걸었다" 는 단언은 통과하면서 실제로 걸러지는지는 아무도 안 본다.
 */
function fakeClient(rows: Row[]) {
  const pending = rows.map((r, i) =>
    typeof r === "string"
      ? { id: `c${i}`, text: r, reason: null as string | null }
      : { id: `c${i}`, text: r.text, reason: r.reason }
  );
  const filled = new Map<string, number[]>();
  const sends: Record<string, unknown>[] = [];
  /** `chunks_set_dense_vec` 호출 횟수 — ceil(행수 / EMBED_WRITE_SLICE) 회여야 한다. */
  let setDenseCalls = 0;
  /** 테스트 주입: rpc 가 돌려줄 갱신 행수를 강제한다(불일치 검출용). */
  let forceWritten: number | null = null;
  let limitSeen: number | null = null;
  let orderedAsc: boolean | null = null;
  let isNullSeen = false;
  /** `.is(col, null)` 로 걸린 컬럼들 — 호출 순서 그대로. */
  const isFilters: string[] = [];

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
          if (v === null) isFilters.push(col);
          if (col === "dense_vec" && v === null) isNullSeen = true;
          return q;
        },
        order(_c: string, o?: { ascending?: boolean }) {
          orderedAsc = o?.ascending !== false;
          return q;
        },
        limit(n: number) {
          limitSeen = n;
          const skipFiltered = isFilters.includes("flags->>filtered_reason");
          const out = pending
            .filter((r) => !filled.has(r.id))
            .filter((r) => !skipFiltered || r.reason === null)
            .map((r) => ({ id: r.id, text: r.text }))
            .slice(0, n);
          return Promise.resolve({ data: out, error: null });
        },
      };
      return { select: () => q };
    },
    rpc(name: string, args: Record<string, unknown>) {
      if (name === "ingest_queue_send") sends.push(args.payload as Record<string, unknown>);
      if (name === "chunks_set_dense_vec") {
        setDenseCalls++;
        const rows = args.p_rows as { id: string; vec: number[] }[];
        for (const r of rows) filled.set(r.id, r.vec);
        return Promise.resolve({ data: forceWritten ?? rows.length, error: null });
      }
      return Promise.resolve({ data: 1, error: null });
    },
  };
  return {
    jobUpdates,
    client,
    filled,
    sends,
    setDenseCalls: () => setDenseCalls,
    forceWritten: (n: number) => {
      forceWritten = n;
    },
    limitSeen: () => limitSeen,
    orderedAsc: () => orderedAsc,
    isNullSeen: () => isNullSeen,
    isFilters: () => isFilters,
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

Deno.test("덜 찼으면 그게 마지막 — doc_embed 로 넘긴다", async () => {
  const f = fakeClient(["가", "나"]);
  const h = makeEmbedHandler({
    // deno-lint-ignore no-explicit-any
    client: f.client as any,
    token: "T",
    chunksPerTask: 4,
    embed: (t) => Promise.resolve(vecFor(t)),
  });
  await h(TASK, {} as never);
  assertEquals(f.sends, [{ job_id: "j1", doc_id: "d1", stage: "doc_embed" }]);
});

Deno.test("남은 청크가 없으면 임베딩은 안 부른다 — 재시도해도 안전", async () => {
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
  // 다시 배달돼도 임베딩은 안 부른다. 다음 단계 enqueue 는 멱등이다(같은 메시지가
  // 두 번 들어가도 doc_embed 가 같은 값을 다시 쓸 뿐이다).
  assertEquals(f.sends, [{ job_id: "j1", doc_id: "d1", stage: "doc_embed" }]);
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

Deno.test("남은 게 없으면 doc_embed 로 넘긴다 — 여기서 마감하지 않는다", async () => {
  // 사슬의 끝은 dedup 이다. embed 가 마감하면 doc_embed·dedup 이 돌기도 전에
  // 잡이 completed 가 되어 reingest 가 그 사이에 끼어들 수 있다.
  const f = fakeClient([]);
  const h = makeEmbedHandler({
    // deno-lint-ignore no-explicit-any
    client: f.client as any,
    token: "T",
    embed: (t) => Promise.resolve(vecFor(t)),
  });
  await h(TASK, {} as never);
  assertEquals(f.jobUpdates, []);
  assertEquals(f.sends, [{ job_id: "j1", doc_id: "d1", stage: "doc_embed" }]);
});

Deno.test("아직 남았으면 embed 를 다시 넣는다", async () => {
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
  assertEquals(f.sends, [{ job_id: "j1", doc_id: "d1", stage: "embed" }]);
});

Deno.test("filtered_reason 이 붙은 청크는 조회에서 뺀다 — 제공자에 안 간다", async () => {
  // 검색 RPC 가 `(flags->>'filtered_reason') IS NULL` 만 보므로 마킹된 청크의
  // `dense_vec` 은 아무도 안 읽는다. 채우면 DeepInfra 비용과 HNSW 인덱스만 는다.
  const f = fakeClient([
    { text: "머리말", reason: "header_footer" },
    "본문 가",
    { text: "2,800", reason: "extreme_short" },
    "본문 나",
  ]);
  const sent: string[][] = [];
  const h = makeEmbedHandler({
    // deno-lint-ignore no-explicit-any
    client: f.client as any,
    token: "T",
    embed: (t) => (sent.push(t), Promise.resolve(vecFor(t))),
  });
  await h(TASK, {} as never);

  // 조회에 두 필터가 다 걸렸다.
  assertEquals(f.isFilters(), ["dense_vec", "flags->>filtered_reason"]);
  // 제공자에 간 텍스트에 마킹된 것이 하나도 없다.
  assertEquals(sent, [["본문 가", "본문 나"]]);
  // 마킹된 청크의 dense_vec 은 NULL 로 남는다.
  assertEquals([...f.filled.keys()].sort(), ["c1", "c3"]);
});

Deno.test("남은 게 전부 filtered 면 루프가 끝난다 — doc_embed 로 넘어간다", async () => {
  // 필터 없이 세면 `dense_vec IS NULL` 이 영원히 참이라 embed 가 자기를 무한히
  // 다시 큐에 넣는다. 종료 조건이 필터와 같아야 한다.
  const f = fakeClient([
    { text: "머리말", reason: "header_footer" },
    { text: "꼬리말", reason: "header_footer" },
  ]);
  let called = 0;
  const h = makeEmbedHandler({
    // deno-lint-ignore no-explicit-any
    client: f.client as any,
    token: "T",
    chunksPerTask: 2,
    embed: (t) => (called++, Promise.resolve(vecFor(t))),
  });
  await h(TASK, {} as never);
  assertEquals(called, 0);
  assertEquals(f.filled.size, 0);
  assertEquals(f.sends, [{ job_id: "j1", doc_id: "d1", stage: "doc_embed" }]);
});

Deno.test("perTask 가 안 찬 건 남은 unfiltered 기준이다 — 필터된 행이 자리를 안 먹는다", async () => {
  // 마킹된 행이 `limit` 을 채우면 "가득 찼다" 로 오판해 빈 embed 태스크가 한 번 더 돈다.
  const f = fakeClient([
    { text: "머리말", reason: "header_footer" },
    "본문 가",
    { text: "꼬리말", reason: "header_footer" },
  ]);
  const h = makeEmbedHandler({
    // deno-lint-ignore no-explicit-any
    client: f.client as any,
    token: "T",
    chunksPerTask: 3,
    embed: (t) => Promise.resolve(vecFor(t)),
  });
  await h(TASK, {} as never);
  assertEquals(f.filled.size, 1);
  assertEquals(f.sends, [{ job_id: "j1", doc_id: "d1", stage: "doc_embed" }]);
});

Deno.test("저장은 32행씩 rpc 로 묶는다 — 단건 UPDATE 로 돌아가지 않는다 (사고 4)", async () => {
  // 청크 64개(태스크 한도), 제공자 배치 16 → 임베딩 호출 4번, 저장은 32행씩 2번.
  const f = fakeClient(Array.from({ length: 64 }, (_, i) => `t${i}`));
  const h = makeEmbedHandler({
    // deno-lint-ignore no-explicit-any
    client: f.client as any,
    token: "T",
    embed: (t) => Promise.resolve(vecFor(t)),
  });
  await h(TASK, {} as never);
  assertEquals(f.setDenseCalls(), 2);
  assertEquals(f.filled.size, 64);
  assertEquals(f.filled.get("c9"), [2, 0, 0]);
  // 10개면 1번.
  const g = fakeClient(Array.from({ length: 10 }, (_, i) => `t${i}`));
  // deno-lint-ignore no-explicit-any
  await makeEmbedHandler({ client: g.client as any, token: "T", embed: (t) => Promise.resolve(vecFor(t)) })(
    TASK,
    {} as never,
  );
  assertEquals(g.setDenseCalls(), 1);
});

Deno.test("벡터는 소수 6자리로 잘라 보낸다 — halfvec 가 버리는 자릿수는 본문 낭비다", () => {
  assertEquals(roundForHalfvec([0.123456789, -1.9999999, 2]), [0.123457, -2, 2]);
});

Deno.test("rpc 갱신 행수가 보낸 개수와 다르면 던진다 — 조용히 넘기지 않는다", async () => {
  const f = fakeClient(["가", "나", "다"]);
  f.forceWritten(2);
  const h = makeEmbedHandler({
    // deno-lint-ignore no-explicit-any
    client: f.client as any,
    token: "T",
    embed: (t) => Promise.resolve(vecFor(t)),
  });
  await assertRejects(() => h(TASK, {} as never), Error, "wrote=2, expect=3");
  // 실패한 태스크는 다음 단계를 걸지 않는다.
  assertEquals(f.sends.length, 0);
});
