/**
 * 드레인 루프 계약 고정.
 *
 * 큐 소비는 **분기마다 결과가 다르다** — 성공은 delete, 실패는 남겨서 재시도, 한도 초과나
 * 모르는 stage 는 archive. 하나라도 뒤집히면 메시지가 영원히 돌거나(독약) 조용히 사라진다.
 * 실제 DB 없이 그 분기를 전부 태운다.
 */

import { assertEquals } from "@std/assert";
import { drainLoop, drainOnce, MAX_ATTEMPTS, type QueueMessage, type TaskPayload } from "./worker.ts";

interface Call {
  fn: string;
  args: Record<string, unknown>;
}

/** `rpc` 와 `from().update().eq()` 만 흉내내는 최소 가짜. 호출을 전부 기록한다. */
function fakeClient(messages: QueueMessage[]) {
  const calls: Call[] = [];
  const updates: Record<string, unknown>[] = [];
  const client = {
    rpc(fn: string, args: Record<string, unknown>) {
      calls.push({ fn, args });
      if (fn === "ingest_queue_read") return Promise.resolve({ data: messages, error: null });
      return Promise.resolve({ data: true, error: null });
    },
    from(_t: string) {
      return {
        update(patch: Record<string, unknown>) {
          updates.push(patch);
          return { eq: () => Promise.resolve({ data: null, error: null }) };
        },
      };
    },
  };
  return { client, calls, updates };
}

function msg(over: Partial<QueueMessage> = {}): QueueMessage {
  return {
    msg_id: 1,
    read_ct: 1,
    enqueued_at: "2026-09-07T00:00:00Z",
    vt: "2026-09-07T00:10:00Z",
    message: { job_id: "j1", doc_id: "d1", stage: "extract" },
    ...over,
  };
}

Deno.test("성공하면 delete 하고 ok 로 센다", async () => {
  const { client, calls } = fakeClient([msg()]);
  let ran = 0;
  const r = await drainOnce({
    // deno-lint-ignore no-explicit-any
    client: client as any,
    handlers: {
      extract: () => {
        ran++;
        return Promise.resolve();
      },
    },
  });
  assertEquals(ran, 1);
  assertEquals([r.read, r.ok, r.retried, r.archived], [1, 1, 0, 0]);
  assertEquals(calls.filter((c) => c.fn === "ingest_queue_delete").length, 1);
  assertEquals(calls.filter((c) => c.fn === "ingest_queue_archive").length, 0);
});

Deno.test("핸들러가 던지면 **지우지 않는다** — vt 후 재시도", async () => {
  const { client, calls } = fakeClient([msg()]);
  const r = await drainOnce({
    // deno-lint-ignore no-explicit-any
    client: client as any,
    handlers: { extract: () => Promise.reject(new Error("일부러")) },
  });
  assertEquals([r.ok, r.retried, r.archived], [0, 1, 0]);
  // 지우거나 보관하면 작업이 사라진다.
  assertEquals(calls.filter((c) => c.fn === "ingest_queue_delete").length, 0);
  assertEquals(calls.filter((c) => c.fn === "ingest_queue_archive").length, 0);
  assertEquals(r.errors[0].error, "일부러");
});

Deno.test("모르는 stage 는 즉시 보관한다 (독약 메시지 방지)", async () => {
  const { client, calls, updates } = fakeClient([
    msg({ message: { job_id: "j1", doc_id: "d1", stage: "없는단계" } }),
  ]);
  // deno-lint-ignore no-explicit-any
  const r = await drainOnce({ client: client as any, handlers: {} });
  assertEquals([r.ok, r.retried, r.archived], [0, 0, 1]);
  assertEquals(calls.filter((c) => c.fn === "ingest_queue_archive").length, 1);
  // 큐에 남기면 vt 마다 되살아나 영원히 돈다.
  assertEquals(calls.filter((c) => c.fn === "ingest_queue_delete").length, 0);
  assertEquals(updates.some((u) => u.status === "failed"), true);
});

Deno.test("재시도 한도를 넘으면 보관하고 잡을 failed 로", async () => {
  const { client, calls, updates } = fakeClient([msg({ read_ct: MAX_ATTEMPTS + 1 })]);
  let ran = 0;
  const r = await drainOnce({
    // deno-lint-ignore no-explicit-any
    client: client as any,
    handlers: {
      extract: () => {
        ran++;
        return Promise.resolve();
      },
    },
  });
  // 한도를 넘었으면 **핸들러를 부르지 않는다** — 또 실패할 뿐이다.
  assertEquals(ran, 0);
  assertEquals([r.ok, r.archived], [0, 1]);
  assertEquals(calls.filter((c) => c.fn === "ingest_queue_archive").length, 1);
  assertEquals(updates.some((u) => u.status === "failed"), true);
});

Deno.test("한도 경계 — read_ct == MAX_ATTEMPTS 는 아직 돌린다", async () => {
  const { client } = fakeClient([msg({ read_ct: MAX_ATTEMPTS })]);
  let ran = 0;
  const r = await drainOnce({
    // deno-lint-ignore no-explicit-any
    client: client as any,
    handlers: {
      extract: () => {
        ran++;
        return Promise.resolve();
      },
    },
  });
  assertEquals(ran, 1);
  assertEquals([r.ok, r.archived], [1, 0]);
});

Deno.test("빈 큐면 아무 것도 안 한다", async () => {
  const { client, calls } = fakeClient([]);
  // deno-lint-ignore no-explicit-any
  const r = await drainOnce({ client: client as any, handlers: {} });
  assertEquals([r.read, r.ok, r.retried, r.archived], [0, 0, 0, 0]);
  assertEquals(calls.length, 1); // read 한 번뿐
});

Deno.test("여러 건을 각각 독립 처리한다 (하나 실패해도 나머지 진행)", async () => {
  const { client, calls } = fakeClient([
    msg({ msg_id: 1 }),
    msg({ msg_id: 2, message: { job_id: "j2", doc_id: "d2", stage: "extract" } }),
    msg({ msg_id: 3, message: { job_id: "j3", doc_id: "d3", stage: "없는단계" } }),
  ]);
  const r = await drainOnce({
    // deno-lint-ignore no-explicit-any
    client: client as any,
    handlers: {
      extract: (t) => t.job_id === "j2" ? Promise.reject(new Error("두번째만 실패")) : Promise.resolve(),
    },
  });
  assertEquals([r.read, r.ok, r.retried, r.archived], [3, 1, 1, 1]);
});

// ---------------------------------------------------------------------------
// drainLoop — 예산 안에서 여러 판을 돈다
// ---------------------------------------------------------------------------

/** `ingest_queue_read` 가 대본대로 응답하는 클라이언트. */
function scriptedClient(rounds: (TaskPayload | null)[]) {
  let i = 0;
  const seen: string[] = [];
  const client = {
    rpc(name: string, _args: Record<string, unknown>) {
      if (name === "ingest_queue_read") {
        const t = rounds[i++] ?? null;
        return Promise.resolve({
          data: t ? [{ msg_id: i, read_ct: 1, enqueued_at: "", vt: "", message: t }] : [],
          error: null,
        });
      }
      return Promise.resolve({ data: true, error: null });
    },
    from() {
      return { update: () => ({ eq: () => Promise.resolve({ error: null }) }) };
    },
  };
  return { client, seen };
}

const T = (stage: string): TaskPayload => ({ job_id: "j", doc_id: "d", stage });

Deno.test("drainLoop — 큐가 비면 멈춘다", async () => {
  const { client } = scriptedClient([T("a"), T("a"), null]);
  const handled: string[] = [];
  const r = await drainLoop({
    // deno-lint-ignore no-explicit-any
    client: client as any,
    handlers: { a: () => (handled.push("a"), Promise.resolve()) },
  });
  assertEquals(handled.length, 2);
  assertEquals(r.ok, 2);
  // 3번째 판에서 read 0 을 보고 멈춘다.
  assertEquals(r.rounds, 3);
});

Deno.test("drainLoop — 예산을 넘기면 멈춘다", async () => {
  // 100 판 분량을 줘도 예산이 끊는다.
  const { client } = scriptedClient(Array.from({ length: 100 }, () => T("slow")));
  let n = 0;
  const r = await drainLoop({
    // deno-lint-ignore no-explicit-any
    client: client as any,
    handlers: {
      slow: async () => {
        n++;
        await new Promise((res) => setTimeout(res, 12));
      },
    },
    budgetMs: 40,
  });
  // 판당 약 12ms → 40ms 예산이면 서너 판.
  assertEquals(n === r.ok, true);
  assertEquals(r.rounds >= 2 && r.rounds <= 8, true, `rounds=${r.rounds}`);
  assertEquals(r.elapsedMs >= 40, true, `elapsed=${r.elapsedMs}`);
});

Deno.test("drainLoop — 판 도중에는 끊지 않는다", async () => {
  // 예산 0 이어도 **첫 판은 끝까지** 돈다. 중간에 자르면 산출물이 반만 남는다.
  const { client } = scriptedClient([T("a"), T("a")]);
  let done = 0;
  const r = await drainLoop({
    // deno-lint-ignore no-explicit-any
    client: client as any,
    handlers: {
      a: async () => {
        await new Promise((res) => setTimeout(res, 5));
        done++;
      },
    },
    budgetMs: 0,
  });
  assertEquals(done, 1);
  assertEquals(r.rounds, 1);
  assertEquals(r.ok, 1);
});

Deno.test("drainLoop — maxRounds 가 무한 루프를 막는다", async () => {
  const { client } = scriptedClient(Array.from({ length: 1000 }, () => T("fast")));
  const r = await drainLoop({
    // deno-lint-ignore no-explicit-any
    client: client as any,
    handlers: { fast: () => Promise.resolve() },
    budgetMs: 60_000,
    maxRounds: 7,
  });
  assertEquals(r.rounds, 7);
});

Deno.test("drainLoop — 판별 집계를 합산한다", async () => {
  const { client } = scriptedClient([T("a"), T("모르는단계"), T("a"), null]);
  const r = await drainLoop({
    // deno-lint-ignore no-explicit-any
    client: client as any,
    handlers: { a: () => Promise.resolve() },
  });
  assertEquals(r.read, 3);
  assertEquals(r.ok, 2);
  assertEquals(r.archived, 1); // 핸들러 없는 stage 는 보관
  assertEquals(r.errors.length, 1);
});
