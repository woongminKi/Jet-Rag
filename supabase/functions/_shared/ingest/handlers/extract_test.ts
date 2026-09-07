/**
 * `extract` 핸들러의 **거절 조건**을 고정한다.
 *
 * 이 핸들러는 실패를 조용히 삼키면 안 된다 — 잡이 영원히 running 으로 남고 어디서
 * 멈췄는지도 안 보인다. 못 하는 상황마다 던지는지 확인한다.
 */

import { assertEquals, assertRejects } from "@std/assert";
import { makeExtractHandler } from "./extract.ts";
import type { TaskPayload } from "../worker.ts";

const TASK: TaskPayload = { job_id: "j1", doc_id: "d1", stage: "extract" };

/** `from().select().eq().limit()` 과 `from().upsert()` 만 흉내낸다. */
function fakeClient(doc: Record<string, unknown> | null) {
  const upserts: { row: Record<string, unknown>; opts: unknown }[] = [];
  const client = {
    from(_t: string) {
      return {
        select: () => ({
          eq: () => ({ limit: () => Promise.resolve({ data: doc ? [doc] : [], error: null }) }),
        }),
        upsert(row: Record<string, unknown>, opts: unknown) {
          upserts.push({ row, opts });
          return Promise.resolve({ data: null, error: null });
        },
      };
    },
  };
  return { client, upserts };
}

Deno.test("문서가 없으면 던진다", async () => {
  const { client } = fakeClient(null);
  // deno-lint-ignore no-explicit-any
  const h = makeExtractHandler({ client: client as any, bucket: "documents" });
  await assertRejects(() => h(TASK, {} as never), Error, "문서를 찾을 수 없다");
});

Deno.test("이식 안 된 포맷은 **조용히 넘기지 않고** 던진다", async () => {
  const { client } = fakeClient({ id: "d1", doc_type: "pdf", storage_path: "user/u/x.pdf" });
  // deno-lint-ignore no-explicit-any
  const h = makeExtractHandler({ client: client as any, bucket: "documents" });
  await assertRejects(() => h(TASK, {} as never), Error, "아직 이식되지 않은 포맷: pdf");
});

Deno.test("storage_path 가 pending 이면 던진다 (재시도 대상)", async () => {
  const { client } = fakeClient({ id: "d1", doc_type: "hwp", storage_path: "pending/u/x.hwp" });
  // deno-lint-ignore no-explicit-any
  const h = makeExtractHandler({ client: client as any, bucket: "documents" });
  await assertRejects(() => h(TASK, {} as never), Error, "아직 pending");
});

Deno.test("storage_path 가 비면 던진다", async () => {
  const { client } = fakeClient({ id: "d1", doc_type: "hwp", storage_path: "" });
  // deno-lint-ignore no-explicit-any
  const h = makeExtractHandler({ client: client as any, bucket: "documents" });
  await assertRejects(() => h(TASK, {} as never), Error, "storage_path 가 비었다");
});

Deno.test("정상 경로 — 산출물을 upsert 하고 onConflict 를 준다", async () => {
  const { client, upserts } = fakeClient({
    id: "d1",
    doc_type: "hwp",
    storage_path: "user/u/abc.hwp",
  });
  const hwpBytes = await Deno.readFile(
    new URL("../../../../../assets/public/law_sample1.hwp", import.meta.url),
  );
  const h = makeExtractHandler({
    // deno-lint-ignore no-explicit-any
    client: client as any,
    bucket: "documents",
    download: (p) => {
      assertEquals(p, "user/u/abc.hwp"); // 조회한 경로를 그대로 써야 한다
      return Promise.resolve(hwpBytes);
    },
  });
  await h(TASK, {} as never);

  assertEquals(upserts.length, 1);
  const { row, opts } = upserts[0];
  assertEquals(row.job_id, "j1");
  assertEquals(row.doc_id, "d1");
  assertEquals(row.stage, "extract");
  assertEquals(row.seq, 0);
  // **UNIQUE 제약 위에 upsert 해야 멱등이다.** 빠지면 재배달 시 행이 둘이 된다.
  assertEquals(opts, { onConflict: "job_id,stage,seq" });
  const payload = row.payload as { source_type: string; sections: unknown[] };
  assertEquals(payload.source_type, "hwp");
  assertEquals(payload.sections.length, 36); // 기준선과 같은 섹션 수
});

Deno.test("task.from 이 있으면 seq 로 쓴다 (PDF 페이지 분할 대비)", async () => {
  const { client, upserts } = fakeClient({
    id: "d1",
    doc_type: "hwp",
    storage_path: "user/u/abc.hwp",
  });
  const hwpBytes = await Deno.readFile(
    new URL("../../../../../assets/public/law_sample1.hwp", import.meta.url),
  );
  const h = makeExtractHandler({
    // deno-lint-ignore no-explicit-any
    client: client as any,
    bucket: "documents",
    download: () => Promise.resolve(hwpBytes),
  });
  await h({ ...TASK, from: 7 }, {} as never);
  assertEquals(upserts[0].row.seq, 7);
});
