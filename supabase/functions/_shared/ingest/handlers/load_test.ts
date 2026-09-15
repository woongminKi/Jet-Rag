/**
 * `load` 핸들러 계약 — part 하나씩 읽어 `chunks` 에 upsert 하고 이어간다.
 *
 * 여기서 고정하는 것:
 * - part 를 **하나만**, 그것도 **쓰는 필드만** 읽는다 (`payload` 를 통째로 읽으면
 *   `carry` 가 따라온다 — SK 실측 0.64MB, 이 단계에서는 한 번도 안 보는 값이다)
 * - `chunk_filter` 마킹이 **여기서** 일어난다 (문서 전체 반복 횟수를 알아야 해서
 *   `chunk` 창 단위 처리에서 옮겨 왔다)
 * - 머리말 목록은 `total_parts - 1` 로 **지목**해서 읽는다 (`order desc` 로 찾으면
 *   재인제스트 잔존 행이 잡혀 마킹이 조용히 빠진다)
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

/**
 * `ingest_artifacts` 단건 조회 · 머리말 목록 조회 · `chunks` upsert/count · rpc 흉내.
 *
 * `header_footer_texts` 는 **그 seq 의 행에만** 둔다 — 마지막 창 지목이 정확한지
 * 보려면 seq 마다 달라야 한다. 없으면 그 행에는 키가 아예 없는 것이다.
 */
interface Part {
  records?: unknown[];
  total_parts?: number;
  header_footer_texts?: string[];
}

function fakeClient(parts: Record<number, Part>) {
  const upserts: { rows: Record<string, unknown>[]; opts: unknown }[] = [];
  const sends: Record<string, unknown>[] = [];
  let askedSeq: number | null = null;
  const hfSeqs: number[] = [];

  const client = {
    from(table: string) {
      if (table === "ingest_artifacts") {
        // deno-lint-ignore no-explicit-any
        const make = (select: string): any => {
          const isHf = select.includes("header_footer_texts");
          let seq: number | null = null;
          // deno-lint-ignore no-explicit-any
          const q: any = {
            eq(col: string, v: unknown) {
              if (col === "seq") {
                seq = v as number;
                if (isHf) hfSeqs.push(seq);
                else askedSeq = seq;
              }
              return q;
            },
            order() {
              throw new Error("머리말 목록을 order desc 로 찾으면 안 된다 — 잔존 행이 잡힌다");
            },
            limit() {
              const p = seq !== null ? parts[seq] : undefined;
              if (p === undefined) return Promise.resolve({ data: [], error: null });
              return Promise.resolve({
                data: [
                  isHf
                    ? { seq, texts: p.header_footer_texts }
                    : { seq, records: p.records, total_parts: p.total_parts },
                ],
                error: null,
              });
            },
          };
          return q;
        };
        return { select: (sel: string) => make(sel) };
      }
      // chunks
      return {
        // 마지막 part 의 비율 경고가 세는 곳. 진단용이라 값은 대충이어도 된다.
        select() {
          // deno-lint-ignore no-explicit-any
          const cq: any = {
            eq: () => cq,
            not: () => cq,
            // deno-lint-ignore no-explicit-any
            then: (ok: any, err: any) => Promise.resolve({ count: 0, error: null }).then(ok, err),
          };
          return cq;
        },
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
  return {
    client,
    upserts,
    sends,
    askedSeq: () => askedSeq,
    hfAsked: () => hfSeqs.length,
    hfSeqs: () => hfSeqs,
  };
}

Deno.test("part 가 없으면 던진다 — 청크를 조용히 잃지 않는다", async () => {
  const { client } = fakeClient({});
  // deno-lint-ignore no-explicit-any
  const h = makeLoadHandler({ client: client as any });
  await assertRejects(() => h(TASK, {} as never), Error, "chunk 산출물 part 0 이 없다");
});

Deno.test("요청받은 part 만 읽는다", async () => {
  const { client, askedSeq } = fakeClient({
    0: { total_parts: 3, records: [rec(0)] },
    1: { total_parts: 3, records: [rec(1)] },
    2: { total_parts: 3, records: [rec(2)], header_footer_texts: [] },
  });
  // deno-lint-ignore no-explicit-any
  const h = makeLoadHandler({ client: client as any });
  await h({ ...TASK, from: 1 }, {} as never);
  assertEquals(askedSeq(), 1);
});

Deno.test("upsert 를 batch 로 쪼개고 on_conflict 를 준다", async () => {
  const records = Array.from({ length: 7 }, (_, i) => rec(i));
  const { client, upserts } = fakeClient({
    0: { total_parts: 1, records, header_footer_texts: [] },
  });
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
    0: { total_parts: 3, records: [rec(0)] },
    2: { total_parts: 3, records: [], header_footer_texts: [] },
  });
  // deno-lint-ignore no-explicit-any
  const h = makeLoadHandler({ client: client as any });
  await h(TASK, {} as never);
  assertEquals(sends, [{ job_id: "j1", doc_id: "d1", stage: "load", from: 1 }]);
});

Deno.test("마지막 part 면 embed 를 넣는다 — dense_vec 이 NULL 이면 검색이 반만 된다", async () => {
  const { client, sends } = fakeClient({
    2: { total_parts: 3, records: [rec(9)], header_footer_texts: [] },
  });
  // deno-lint-ignore no-explicit-any
  const h = makeLoadHandler({ client: client as any });
  await h({ ...TASK, from: 2 }, {} as never);
  assertEquals(sends, [{ job_id: "j1", doc_id: "d1", stage: "embed" }]);
});

Deno.test("records 가 비어도 던지지 않는다 (빈 문서)", async () => {
  const { client, upserts, sends, hfAsked } = fakeClient({
    0: { total_parts: 1, records: [] },
  });
  // deno-lint-ignore no-explicit-any
  const h = makeLoadHandler({ client: client as any });
  await h(TASK, {} as never);
  assertEquals(upserts.length, 0);
  // 청크가 없으면 머리말 목록도 안 읽는다 — 쓸데없는 왕복을 안 한다.
  assertEquals(hfAsked(), 0);
  // 빈 문서라도 embed 는 넣는다. embed 핸들러가 "남은 청크 없음" 으로 즉시 끝낸다 —
  // 여기서 안 넣으면 잡이 조용히 멈춘 것처럼 보인다.
  assertEquals(sends, [{ job_id: "j1", doc_id: "d1", stage: "embed" }]);
});

Deno.test("total_parts 가 없으면 1 로 본다 — 곧장 embed 로 넘어간다", async () => {
  const { client, sends, hfSeqs } = fakeClient({
    0: { records: [rec(0)], header_footer_texts: [] },
  });
  // deno-lint-ignore no-explicit-any
  const h = makeLoadHandler({ client: client as any });
  await h(TASK, {} as never);
  assertEquals(hfSeqs(), [0]); // 1 - 1
  assertEquals(sends, [{ job_id: "j1", doc_id: "d1", stage: "embed" }]);
});

// ---------------------------------------------------------------------------
// chunk_filter 마킹 — chunk 에서 옮겨 온 부분
// ---------------------------------------------------------------------------

Deno.test("마지막 창이 남긴 목록으로 header_footer 를 마킹한다", async () => {
  // part 안에는 "머리말" 이 **한 번**뿐이다 — part 만 보면 3회 규칙에 못 걸린다.
  const records = [
    rec(0, { text: "머리말" }),
    rec(1, { text: "이것은 충분히 긴 본문 문장입니다. 걸리지 않아야 합니다." }),
  ];
  const { client, upserts, hfAsked, hfSeqs } = fakeClient({
    1: { total_parts: 3, records },
    // 목록은 **마지막 창**(seq = total_parts - 1)에만 있다.
    2: { total_parts: 3, records: [], header_footer_texts: ["머리말"] },
  });
  // deno-lint-ignore no-explicit-any
  const h = makeLoadHandler({ client: client as any });
  await h({ ...TASK, from: 1 }, {} as never);

  assertEquals(hfAsked(), 1);
  assertEquals(hfSeqs(), [2]);
  const rows = upserts.flatMap((u) => u.rows) as { flags: Record<string, unknown> }[];
  assertEquals(rows[0].flags["filtered_reason"], "header_footer");
  assertEquals("filtered_reason" in rows[1].flags, false);
});

Deno.test("잔존 상위 seq 가 머리말 목록을 가리지 않는다", async () => {
  // 재인제스트로 창 수가 줄어 옛 part 7 이 남아 있는 상황. `order desc limit 1` 로
  // 찾으면 목록이 없는 그 행이 잡혀 **머리말 마킹이 통째로 조용히 빠진다**.
  const { client, upserts, hfSeqs } = fakeClient({
    0: {
      total_parts: 1,
      records: [rec(0, { text: "머리말" })],
      header_footer_texts: ["머리말"],
    },
    7: { total_parts: 8, records: [] }, // 잔존 — 목록 없음
  });
  // deno-lint-ignore no-explicit-any
  const h = makeLoadHandler({ client: client as any });
  await h(TASK, {} as never);
  assertEquals(hfSeqs(), [0]);
  const rows = upserts.flatMap((u) => u.rows) as { flags: Record<string, unknown> }[];
  assertEquals(rows[0].flags["filtered_reason"], "header_footer");
});

Deno.test("목록에 없으면 마킹하지 않는다 — 판정 출처는 문서 전체다", async () => {
  const { client, upserts } = fakeClient({
    0: { total_parts: 1, records: [rec(0, { text: "머리말" })], header_footer_texts: [] },
  });
  // deno-lint-ignore no-explicit-any
  const h = makeLoadHandler({ client: client as any });
  await h(TASK, {} as never);
  const rows = upserts.flatMap((u) => u.rows) as { flags: Record<string, unknown> }[];
  assertEquals("filtered_reason" in rows[0].flags, false);
});

Deno.test("나머지 사유는 part 만 보고도 판정된다 — 순서가 규칙이다", async () => {
  const records = [
    rec(0, { text: "   " }), // empty
    rec(1, { text: "2,800" }), // extreme_short (글자가 없다)
    rec(2, { text: "머리말" }), // header_footer 가 table_noise 보다 먼저다
    rec(3, { text: "1 | 2\n".repeat(20) }), // table_noise
  ];
  const { client, upserts } = fakeClient({
    0: { total_parts: 1, records, header_footer_texts: ["머리말"] },
  });
  // deno-lint-ignore no-explicit-any
  const h = makeLoadHandler({ client: client as any });
  await h(TASK, {} as never);
  const rows = upserts.flatMap((u) => u.rows) as { flags: Record<string, unknown> }[];
  assertEquals(rows.map((r) => r.flags["filtered_reason"] ?? null), [
    "empty",
    "extreme_short",
    "header_footer",
    "table_noise",
  ]);
});

Deno.test("header_footer_texts 가 아예 없으면 머리말만 건너뛰고 나머지는 돈다", async () => {
  // 창 분할 배포 직전에 chunk 를 끝낸 잡 — 그 산출물은 이미 마킹돼 있다.
  const records = [
    rec(0, { text: "머리말", flags: { filtered_reason: "header_footer" } }),
    rec(1, { text: "   " }),
  ];
  const { client, upserts } = fakeClient({ 0: { total_parts: 1, records } });
  // deno-lint-ignore no-explicit-any
  const h = makeLoadHandler({ client: client as any });
  await h(TASK, {} as never);
  const rows = upserts.flatMap((u) => u.rows) as { flags: Record<string, unknown> }[];
  // 이미 붙어 있던 flags 는 보존된다.
  assertEquals(rows[0].flags["filtered_reason"], "header_footer");
  assertEquals(rows[1].flags["filtered_reason"], "empty");
});

Deno.test("기존 flags 는 지우지 않는다 — filtered_reason 만 덧쓴다", async () => {
  const { client, upserts } = fakeClient({
    0: {
      total_parts: 1,
      records: [rec(0, { text: "머리말", flags: { keep: 1 } })],
      header_footer_texts: ["머리말"],
    },
  });
  // deno-lint-ignore no-explicit-any
  const h = makeLoadHandler({ client: client as any });
  await h(TASK, {} as never);
  const rows = upserts.flatMap((u) => u.rows) as { flags: Record<string, unknown> }[];
  assertEquals(rows[0].flags, { keep: 1, filtered_reason: "header_footer" });
});
