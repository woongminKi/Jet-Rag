/**
 * 드레인 루프 계약 고정.
 *
 * 큐 소비는 **분기마다 결과가 다르다** — 성공은 delete, 실패는 남겨서 재시도, 한도 초과나
 * 모르는 stage 는 archive. 하나라도 뒤집히면 메시지가 영원히 돌거나(독약) 조용히 사라진다.
 * 실제 DB 없이 그 분기를 전부 태운다.
 */

import { assertEquals } from "@std/assert";
import { drainOnce, MAX_ATTEMPTS, type QueueMessage } from "./worker.ts";

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
