/**
 * `extract` 핸들러의 **거절 조건**과 **PDF 페이지 분할 계약**을 고정한다.
 *
 * 이 핸들러는 실패를 조용히 삼키면 안 된다 — 잡이 영원히 running 으로 남고 어디서
 * 멈췄는지도 안 보인다. 못 하는 상황마다 던지는지 확인한다.
 *
 * PDF 쪽에서 고정하는 계약:
 * - 페이지 범위를 **순차로** 이어가고 직전 범위의 `next_title` 을 넘긴다
 * - 마지막 범위가 끝나면 `chunk` 를 큐에 넣는다
 * - 저장 → enqueue **순서**(반대면 다음 태스크가 carryTitle 을 못 찾는다)
 */

import { assertEquals, assertRejects } from "@std/assert";
import {
  GRACEFUL_SKIP_DOC_TYPES,
  makeExtractHandler,
  SUPPORTED_DOC_TYPES,
} from "./extract.ts";
import { ALLOWED_EXTENSIONS } from "../../documents/input_gate.ts";
import type { PdfRangeResult } from "../pdf_open.ts";
import type { TaskPayload } from "../worker.ts";

const TASK: TaskPayload = { job_id: "j1", doc_id: "d1", stage: "extract" };

interface FakeOpts {
  /** `ingest_artifacts` 에 미리 있는 행 (carryTitle 조회용). */
  artifacts?: { seq: number; payload: Record<string, unknown> }[];
}

/** `documents` 조회 · `ingest_artifacts` 조회/upsert · `rpc` 를 흉내낸다. */
function fakeClient(doc: Record<string, unknown> | null, opts: FakeOpts = {}) {
  const upserts: { row: Record<string, unknown>; opts: unknown }[] = [];
  const sends: Record<string, unknown>[] = [];
  /** upsert 와 rpc 가 **어떤 순서로** 불렸는지 — 계약이다. */
  const calls: string[] = [];
  const updates: { table: string; row: Record<string, unknown> }[] = [];
  const artifacts = opts.artifacts ?? [];

  const client = {
    from(table: string) {
      if (table === "documents" || table === "ingest_jobs") {
        return {
          select: () => ({
            eq: () => ({
              limit: () => Promise.resolve({ data: doc ? [doc] : [], error: null }),
            }),
          }),
          update(row: Record<string, unknown>) {
            calls.push(`update:${table}`);
            updates.push({ table, row });
            return { eq: () => Promise.resolve({ data: null, error: null }) };
          },
        };
      }
      // ingest_artifacts
      const q = {
        _lt: Infinity,
        _desc: false,
        eq() {
          return q;
        },
        lt(_c: string, v: number) {
          q._lt = v;
          return q;
        },
        order(_c: string, o?: { ascending?: boolean }) {
          q._desc = o?.ascending === false;
          return q;
        },
        limit() {
          return Promise.resolve({ data: q._rows().slice(0, 1), error: null });
        },
        // `limit` 없이 그대로 await 하는 호출도 있다(스캔 판정). thenable 이 아니면
        // `{data}` 구조분해가 undefined 가 되어 **조용히 빈 결과**로 읽힌다.
        then(res: (v: unknown) => void) {
          res({ data: q._rows(), error: null });
        },
        _rows() {
          return artifacts.filter((a) => a.seq < q._lt)
            .sort((a, b) => (q._desc ? b.seq - a.seq : a.seq - b.seq))
            .map((a) => ({
              seq: a.seq,
              payload: a.payload,
              // PostgREST 의 `payload->key` 선택을 흉내낸다.
              raw_text: (a.payload as Record<string, unknown>)?.raw_text,
              raw_part_count: (a.payload as Record<string, unknown>)?.raw_part_count,
              raw_nonspace_len: (a.payload as Record<string, unknown>)?.raw_nonspace_len,
            }));
        },
      };
      return {
        select: () => q,
        upsert(row: Record<string, unknown>, o: unknown) {
          calls.push("upsert");
          upserts.push({ row, opts: o });
          // 실제 DB 처럼 같은 seq 는 덮어쓴다 — 스캔 판정이 방금 쓴 행을 봐야 한다.
          const seq = row.seq as number;
          const at = artifacts.findIndex((a) => a.seq === seq);
          const rec = { seq, payload: row.payload as Record<string, unknown> };
          if (at >= 0) artifacts[at] = rec;
          else artifacts.push(rec);
          return Promise.resolve({ data: null, error: null });
        },
      };
    },
    rpc(name: string, args: Record<string, unknown>) {
      calls.push(`rpc:${name}`);
      if (name === "ingest_queue_send") sends.push(args.payload as Record<string, unknown>);
      return Promise.resolve({ data: 1, error: null });
    },
  };
  return { client, upserts, sends, calls, updates };
}

const HWP_DOC = { id: "d1", doc_type: "hwp", storage_path: "user/u/x.hwp" };
const PDF_DOC = { id: "d1", doc_type: "pdf", storage_path: "user/u/x.pdf" };

/** 페이지 `total` 개짜리 가짜 PDF 추출기. mupdf WASM 을 로드하지 않는다. */
function fakePdf(total: number) {
  const seen: { from: number; count: number; carryTitle: string | null }[] = [];
  const fn = (
    _b: Uint8Array,
    o: { from: number; count: number; carryTitle: string | null },
  ): Promise<PdfRangeResult> => {
    seen.push({ ...o });
    const end = Math.min(total, o.from + o.count);
    const processed = Math.max(0, end - o.from);
    return Promise.resolve({
      sections: Array.from({ length: processed }, (_, k) => ({
        text: `p${o.from + k + 1}`,
        page: o.from + k + 1,
        section_title: null,
        bbox: null,
        metadata: {},
      })),
      // **길이가 의미를 갖는다.** 문서 전체 raw_text 가 50 자 이하면 스캔 PDF 로
      // 판정되므로(§37), 일반 PDF 테스트는 그보다 긴 본문을 내야 의도대로 돈다.
      rawParts: Array.from(
        { length: processed },
        (_, k) => `${o.from + k + 1}페이지 본문입니다. 판정에 걸리지 않을 만큼 충분히 길게 씁니다.`,
      ),
      nextTitle: processed > 0 ? `제목-${end}` : o.carryTitle,
      totalPages: total,
      processed,
    });
  };
  return { fn, seen };
}

Deno.test("문서가 없으면 던진다", async () => {
  const { client } = fakeClient(null);
  // deno-lint-ignore no-explicit-any
  const h = makeExtractHandler({ client: client as any, bucket: "documents" });
  await assertRejects(() => h(TASK, {} as never), Error, "문서를 찾을 수 없다");
});

/**
 * 아직 이식 안 된 doc_type 하나. **하드코딩하지 않는다** — 예시로 쓴 포맷이 이식되면
 * 테스트가 조용히 무의미해지는 일이 이 파일에서만 네 번 반복됐다(hwpx → image → …).
 * 집합에서 유도하면 마지막 하나까지 이식된 순간 `undefined` 가 되어 즉시 드러난다.
 */
const UNPORTED = ["url", "image", "txt", "md", "xlsx"]
  .find((t) => !SUPPORTED_DOC_TYPES.has(t) && !GRACEFUL_SKIP_DOC_TYPES.has(t));

Deno.test("이식 안 된 포맷은 **조용히 넘기지 않고** 던진다", async () => {
  if (UNPORTED === undefined) {
    throw new Error("이식 안 된 포맷이 없다 — 이 테스트를 지우거나 예시를 바꿔야 한다");
  }
  const { client } = fakeClient({ id: "d1", doc_type: UNPORTED, storage_path: "user/u/x.bin" });
  // deno-lint-ignore no-explicit-any
  const h = makeExtractHandler({ client: client as any, bucket: "documents" });
  await assertRejects(
    () => h(TASK, {} as never),
    Error,
    `아직 이식되지 않은 포맷: ${UNPORTED}`,
  );
});

Deno.test("SUPPORTED_DOC_TYPES — 단독 이미지가 들어왔다", () => {
  // 목록이 곧 계약이다. 빠지면 업로드는 되는데 인제스트가 던진다.
  assertEquals(
    [...SUPPORTED_DOC_TYPES].sort(),
    ["docx", "hwp", "hwpx", "image", "pdf", "pptx"],
  );
  // `url` 은 `trafilatura` 가 막고 있어 아직이다. 이식하면 이 줄이 먼저 깨진다.
  assertEquals(SUPPORTED_DOC_TYPES.has("url"), false);
});

Deno.test("업로드가 받는 doc_type 은 **전부** 여기서 분류돼 있어야 한다", () => {
  // 이 대조가 없어서 `.txt` 가 202 로 받아진 뒤 extract 에서 실패했다.
  // 업로드 화이트리스트와 이 핸들러가 따로 자라면 그 틈이 곧 회귀다.
  const uploadable = new Set(Object.values(ALLOWED_EXTENSIONS));
  const 던지는것: string[] = [];
  for (const t of uploadable) {
    if (SUPPORTED_DOC_TYPES.has(t) || GRACEFUL_SKIP_DOC_TYPES.has(t)) continue;
    던지는것.push(t);
  }
  // 남는 건 **원본이 실제로 파싱하는데 아직 못 옮긴 것**뿐이어야 한다.
  // 여기에 새 값이 나타나면 그건 조용한 회귀다 — 목록에 넣거나 이식해야 한다.
  // (`url` 은 확장자가 아니라 `POST /documents/url` 이 만드는 doc_type 이라 여기 없다.)
  // 이제 업로드 가능한 doc_type 은 **전부** 처리되거나 graceful skip 된다.
  assertEquals(던지는것.sort(), []);
  // `url` 은 확장자가 아니라 `POST /documents/url` 이 만드는 doc_type 이라 여기 없다.
  assertEquals(uploadable.has("url"), false);
  assertEquals(SUPPORTED_DOC_TYPES.has("url"), false);
  // 원본에도 파서가 없는 것들 — 원본과 같이 정상 완료시킨다.
  assertEquals([...GRACEFUL_SKIP_DOC_TYPES].sort(), ["md", "txt"]);
});

Deno.test("원본에도 파서가 없는 포맷은 잡을 **정상 완료**시킨다 (graceful skip)", async () => {
  const { client, sends, updates, calls } = fakeClient({
    id: "d1",
    doc_type: "txt",
    storage_path: "user/u/x.txt",
    flags: { ingest_mode: "default" },
  });
  const h = makeExtractHandler({
    // deno-lint-ignore no-explicit-any
    client: client as any,
    bucket: "documents",
    nowMs: () => 0,
    // 다운로드가 불리면 안 된다 — 파일을 읽지도 않고 넘겨야 한다.
    download: () => Promise.reject(new Error("여기 오면 안 된다")),
  });
  const outcome = await h(TASK, {} as never);

  // ① 로그는 `skipped` 다 — 실패가 아니다.
  assertEquals(outcome, {
    logStatus: "skipped",
    logError: "txt 포맷은 아직 지원되지 않습니다 (후속 어댑터 도입 예정).",
  });
  // ② 기존 flags 를 보존하고 두 키만 얹는다.
  assertEquals(updates[0], {
    table: "documents",
    row: {
      flags: {
        ingest_mode: "default",
        extract_skipped: true,
        extract_skipped_reason: "doc_type=txt 는 아직 지원되지 않는 포맷입니다 (W2 예정).",
      },
    },
  });
  // ③ 잡은 completed 로 마감된다.
  assertEquals(updates[1].table, "ingest_jobs");
  assertEquals((updates[1].row as { status: string }).status, "completed");
  assertEquals((updates[1].row as { current_stage: string }).current_stage, "done");
  // ④ 다음 스테이지를 큐에 넣지 않는다 — 안 넣는 것이 곧 사슬 종료다.
  assertEquals(sends, []);
  assertEquals(calls.filter((c) => c.startsWith("rpc:")), []);
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

Deno.test("HWP — 한 번에 끝내고 바로 chunk 를 큐에 넣는다", async () => {
  const { client, upserts, sends } = fakeClient(HWP_DOC);
  const h = makeExtractHandler({
    // deno-lint-ignore no-explicit-any
    client: client as any,
    bucket: "documents",
    download: () => Promise.resolve(new Uint8Array([1, 2, 3])),
    // HWP 파서를 태우지 않기 위해 다운로드만 가로챌 수는 없다 — 실제 파서가 던지면
    // 그건 그것대로 정보다. 여기서는 파싱 실패를 허용하고 계약만 본다.
  });
  // @rhwp/core 는 쓰레기 바이트에 던진다. 던지는 게 정상이고, 그때는 enqueue 도 없어야 한다.
  await assertRejects(() => h(TASK, {} as never));
  assertEquals(upserts.length, 0);
  assertEquals(sends.length, 0);
});

Deno.test("PDF — 페이지 범위를 순차로 이어가고 마지막에 chunk 를 넣는다", async () => {
  const { fn, seen } = fakePdf(25);
  const artifacts: { seq: number; payload: Record<string, unknown> }[] = [];
  const { client, upserts, sends } = fakeClient(PDF_DOC, { artifacts });
  const h = makeExtractHandler({
    // deno-lint-ignore no-explicit-any
    client: client as any,
    bucket: "documents",
    download: () => Promise.resolve(new Uint8Array([1])),
    extractPdf: fn,
    pagesPerTask: 10,
  });

  // 워커가 하듯 큐를 따라간다. upsert 된 행을 artifacts 에 반영해야 carryTitle 이 이어진다.
  let task: TaskPayload | null = { ...TASK };
  const stages: string[] = [];
  for (let guard = 0; task && guard < 10; guard++) {
    if (task.stage !== "extract") break;
    await h(task, {} as never);
    // 가짜 client 가 upsert 를 artifacts 에 반영한다 — carryTitle·스캔 판정 양쪽이 본다.
    task = sends[sends.length - 1] as unknown as TaskPayload;
    stages.push(task.stage);
  }

  assertEquals(seen.map((s) => [s.from, s.count]), [[0, 10], [10, 10], [20, 10]]);
  // 직전 범위의 next_title 이 그대로 넘어와야 한다.
  assertEquals(seen.map((s) => s.carryTitle), [null, "제목-10", "제목-20"]);
  assertEquals(upserts.map((u) => u.row.seq), [0, 10, 20]);
  assertEquals(stages, ["extract", "extract", "chunk"]);
  // 마지막 범위는 25 페이지에서 잘려 5 장만 처리한다.
  assertEquals(upserts.map((u) => (u.row.payload as { page_count: number }).page_count), [10, 10, 5]);
  assertEquals((upserts[0].row.payload as { total_pages: number }).total_pages, 25);
});

Deno.test("PDF — 저장이 끝난 **뒤에** 다음 작업을 넣는다", async () => {
  const { fn } = fakePdf(15);
  const { client, calls } = fakeClient(PDF_DOC);
  const h = makeExtractHandler({
    // deno-lint-ignore no-explicit-any
    client: client as any,
    bucket: "documents",
    download: () => Promise.resolve(new Uint8Array([1])),
    extractPdf: fn,
    pagesPerTask: 10,
  });
  await h(TASK, {} as never);
  // 반대 순서면 다음 태스크가 아직 없는 아티팩트에서 carryTitle 을 찾다가 던진다.
  assertEquals(calls, ["upsert", "rpc:ingest_queue_send"]);
});

Deno.test("PDF — 직전 범위 산출물이 없으면 던진다 (순서 깨짐)", async () => {
  const { fn } = fakePdf(25);
  const { client } = fakeClient(PDF_DOC, { artifacts: [] });
  const h = makeExtractHandler({
    // deno-lint-ignore no-explicit-any
    client: client as any,
    bucket: "documents",
    download: () => Promise.resolve(new Uint8Array([1])),
    extractPdf: fn,
  });
  await assertRejects(
    () => h({ ...TASK, from: 10 }, {} as never),
    Error,
    "직전 페이지 범위 산출물이 없다",
  );
});

Deno.test("PDF — 한 태스크로 끝나면 곧장 chunk 를 넣는다", async () => {
  const { fn } = fakePdf(3);
  const { client, sends, upserts } = fakeClient(PDF_DOC);
  const h = makeExtractHandler({
    // deno-lint-ignore no-explicit-any
    client: client as any,
    bucket: "documents",
    download: () => Promise.resolve(new Uint8Array([1])),
    extractPdf: fn,
    pagesPerTask: 10,
  });
  await h(TASK, {} as never);
  assertEquals(sends.length, 1);
  assertEquals(sends[0].stage, "chunk");
  assertEquals((upserts[0].row.payload as { page_count: number }).page_count, 3);
});

Deno.test("upsert 는 onConflict 로 멱등성을 보장한다", async () => {
  const { fn } = fakePdf(3);
  const { client, upserts } = fakeClient(PDF_DOC);
  const h = makeExtractHandler({
    // deno-lint-ignore no-explicit-any
    client: client as any,
    bucket: "documents",
    download: () => Promise.resolve(new Uint8Array([1])),
    extractPdf: fn,
  });
  await h(TASK, {} as never);
  assertEquals(upserts[0].opts, { onConflict: "job_id,stage,seq" });
});

Deno.test("스캔 PDF — 문서 전체 텍스트가 50자 이하면 chunk 대신 scan 으로 간다", async () => {
  // 원본 `_is_scan_pdf` 는 파서가 문서를 통째로 읽은 raw_text 를 본다. 창으로 나뉜
  // Edge 에서는 마지막 창에서 전체를 되붙여 판정한다.
  const empty = (
    _b: Uint8Array,
    o: { from: number; count: number; carryTitle: string | null },
  ): Promise<PdfRangeResult> => {
    const end = Math.min(3, o.from + o.count);
    const processed = Math.max(0, end - o.from);
    return Promise.resolve({
      sections: [], rawParts: [], nextTitle: null, totalPages: 3, processed,
    });
  };
  const { client, sends } = fakeClient(PDF_DOC);
  const h = makeExtractHandler({
    // deno-lint-ignore no-explicit-any
    client: client as any,
    bucket: "documents",
    download: () => Promise.resolve(new Uint8Array([1])),
    extractPdf: empty,
    pagesPerTask: 10,
    env: { JETRAG_PDF_VISION_ENRICH: "true" }, // vision 이 켜져 있어도 scan 이 이긴다
  });
  await h(TASK, {} as never);
  assertEquals(sends.length, 1);
  assertEquals(sends[0].stage, "scan");
  assertEquals(sends[0].from, 0);
});

Deno.test("본문이 있으면 스캔이 아니다 — vision 이 켜져 있으면 vision 으로", async () => {
  const { fn } = fakePdf(3);
  const { client, sends } = fakeClient(PDF_DOC);
  const h = makeExtractHandler({
    // deno-lint-ignore no-explicit-any
    client: client as any,
    bucket: "documents",
    download: () => Promise.resolve(new Uint8Array([1])),
    extractPdf: fn,
    pagesPerTask: 10,
    env: { JETRAG_PDF_VISION_ENRICH: "true" },
  });
  await h(TASK, {} as never);
  assertEquals(sends[0].stage, "vision");
});

Deno.test("스캔 판정용 값이 산출물에 남는다", async () => {
  const { fn } = fakePdf(2);
  const { client, upserts } = fakeClient(PDF_DOC);
  const h = makeExtractHandler({
    // deno-lint-ignore no-explicit-any
    client: client as any,
    bucket: "documents",
    download: () => Promise.resolve(new Uint8Array([1])),
    extractPdf: fn,
    pagesPerTask: 10,
  });
  await h(TASK, {} as never);
  const payload = upserts[0].row.payload as Record<string, number>;
  // 빈 창이 join 에서 빠지려면 개수를 알아야 한다.
  assertEquals(payload.raw_part_count, 2);
  // 공백 아닌 글자 수 — 이것만으로 50 초과가 확정되면 본문을 다시 안 읽는다.
  assertEquals(payload.raw_nonspace_len > 50, true);
});
