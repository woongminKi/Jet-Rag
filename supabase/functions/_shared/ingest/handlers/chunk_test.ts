/**
 * `chunk` 핸들러 계약 — **소스 산출물을 창 단위로, 순서대로** 모아야 한다.
 *
 * 여기서 순서가 틀리거나 일부가 빠지면 청크가 조용히 달라진다. 그런 실패는 나중에
 * 검색 품질 문제로만 보여서 원인을 못 찾는다. 그래서 계약으로 고정한다.
 *
 * 창 분할 이후 추가로 고정하는 것:
 * - 창을 나눠 돌린 결과가 **한 창에 다 넣은 것과 identical** (records 전체 비교)
 * - 캐리 왕복(꼬리 섹션·chunk_idx·hfCounts·docFlags)이 아티팩트를 통해 실제로 도는지
 * - 마지막 창에서만 문서 flags 를 OR 로 머지하고, 잔존 part 를 지우고, tag_summarize
 * - 재배달해도 같은 결과(멱등)
 */

import { assertEquals, assertRejects, assertStringIncludes } from "@std/assert";
import { injectSynonyms } from "../synonym_inject.ts";
import { runChunkStage, toChunkRecords } from "../chunk_records.ts";
import { runContentGateStage } from "../content_gate.ts";
import type { ExtractedSection } from "../hwp_extract.ts";
import { makeChunkHandler } from "./chunk.ts";
import type { TaskPayload } from "../worker.ts";

const TASK: TaskPayload = { job_id: "j1", doc_id: "d1", stage: "chunk" };
const ENV = {
  captionPrefixEnabled: false,
  synonymInjectionEnabled: false,
  synonymLlmEnabled: false,
};

function sec(text: string, page: number | null) {
  return { text, page, section_title: null, bbox: null, metadata: {} };
}

interface Row {
  seq: number;
  payload: unknown;
}

interface Filters {
  eq: Record<string, unknown>;
  in: Record<string, unknown[]>;
  gte?: [string, unknown];
  range?: [number, number];
}

/**
 * `ingest_artifacts` 조회/upsert/삭제 · `documents` flags · rpc 를 흉내낸다.
 *
 * `rows` 는 **일부러 뒤섞어** 둔다 — 핸들러가 정렬해야만 통과한다.
 * upsert 한 `chunk` 행은 **되읽을 수 있게 보관한다** — 캐리 왕복이 진짜로 도는지
 * 보려면 가짜 클라이언트가 아니라 아티팩트를 거쳐야 한다.
 */
function fakeClient(
  rows: Row[],
  visionRows: Row[] = [],
  scanRows: Row[] = [],
  docFlags: Record<string, unknown> = {},
  seededChunks: Row[] = [],
) {
  const flagUpdates: Record<string, unknown>[] = [];
  const upserts: { row: Record<string, unknown>; opts: unknown }[] = [];
  const sends: Record<string, unknown>[] = [];
  const calls: string[] = [];
  const deletes: Filters[] = [];
  const chunkRows = new Map<number, unknown>();
  for (const r of seededChunks) chunkRows.set(r.seq, r.payload);

  const srcOf = (stage: string) => stage === "vision" ? visionRows : stage === "scan" ? scanRows : rows;

  // deno-lint-ignore no-explicit-any
  function resolve(f: Filters, select: string): any {
    const stage = f.eq["stage"] as string | undefined;
    // (C) 캐리 조회 — `stage='chunk'` 한 행.
    if (stage === "chunk") {
      const seq = f.eq["seq"] as number;
      if (!chunkRows.has(seq)) return { data: [], error: null };
      const payload = chunkRows.get(seq) as Record<string, unknown>;
      return { data: [{ seq, carry: payload?.["carry"] }], error: null };
    }
    // (A) 소스 플랜 — payload 없이 stage/seq 만.
    if (f.in["stage"]) {
      const out: { stage: string; seq: number }[] = [];
      for (const st of f.in["stage"] as string[]) {
        for (const r of srcOf(st)) out.push({ stage: st, seq: r.seq });
      }
      out.sort((a, b) => a.seq - b.seq);
      // PostgREST 의 `range` 를 흉내낸다 — 상한에 닿았는지 판정이 진짜로 돌아야 한다.
      if (!f.range) throw new Error("플랜 조회에 range 가 없다 — 조용히 잘릴 수 있다");
      return { data: out.slice(f.range[0], f.range[1] + 1), error: null };
    }
    // (B) 창 읽기 — 지정한 seq 만.
    if (stage !== undefined) {
      const wanted = new Set(f.in["seq"] ?? []);
      const data = srcOf(stage)
        .filter((r) => wanted.has(r.seq))
        .map((r) => ({
          stage,
          seq: r.seq,
          sections: (r.payload as { sections?: unknown } | null)?.sections,
          metadata: (r.payload as { metadata?: unknown } | null)?.metadata,
        }));
      // **순서를 일부러 뒤섞는다** — 핸들러가 플랜 순서로 재조립해야만 통과한다.
      data.reverse();
      return { data, error: null };
    }
    throw new Error(`가짜 클라이언트가 모르는 쿼리: select=${select} ${JSON.stringify(f)}`);
  }

  function query(select: string) {
    const f: Filters = { eq: {}, in: {} };
    // deno-lint-ignore no-explicit-any
    const q: any = {
      eq(col: string, v: unknown) {
        f.eq[col] = v;
        return q;
      },
      in(col: string, v: unknown[]) {
        f.in[col] = v;
        return q;
      },
      range(lo: number, hi: number) {
        f.range = [lo, hi];
        return q;
      },
      // `order` 는 체인 중간이다 — 여기서 resolve 하면 뒤에 붙는 `range` 를 놓친다.
      order() {
        return q;
      },
      limit() {
        return Promise.resolve(resolve(f, select));
      },
      // deno-lint-ignore no-explicit-any
      then(ok: any, err: any) {
        return Promise.resolve(resolve(f, select)).then(ok, err);
      },
    };
    return q;
  }

  const client = {
    rpc(name: string, args: Record<string, unknown>) {
      calls.push(`rpc:${name}`);
      if (name === "ingest_queue_send") sends.push(args.payload as Record<string, unknown>);
      return Promise.resolve({ data: 1, error: null });
    },
    from(table: string) {
      // `content_gate` 가 문서 flags 를 읽어 머지한다 — 테이블을 구분해야 한다.
      if (table === "documents") {
        // deno-lint-ignore no-explicit-any
        const dq: any = {
          eq: () => dq,
          limit: () => Promise.resolve({ data: [{ flags: { ...docFlags } }], error: null }),
        };
        return {
          select: () => dq,
          update(row: Record<string, unknown>) {
            const flags = row.flags as Record<string, unknown>;
            flagUpdates.push(flags);
            // **쓴 값이 남아야** 한다 — 마지막 창 재배달 판정이 이 값을 다시 읽는다.
            for (const k of Object.keys(docFlags)) delete docFlags[k];
            Object.assign(docFlags, flags);
            return { eq: () => Promise.resolve({ error: null }) };
          },
        };
      }
      return {
        select: (s: string) => query(s),
        upsert(row: Record<string, unknown>, opts: unknown) {
          calls.push("upsert");
          upserts.push({ row, opts });
          if (row.stage === "chunk") chunkRows.set(row.seq as number, row.payload);
          return Promise.resolve({ data: null, error: null });
        },
        delete() {
          const f: Filters = { eq: {}, in: {} };
          // deno-lint-ignore no-explicit-any
          const q: any = {
            eq(col: string, v: unknown) {
              f.eq[col] = v;
              return q;
            },
            gte(col: string, v: unknown) {
              f.gte = [col, v];
              calls.push("delete");
              deletes.push(f);
              for (const seq of [...chunkRows.keys()]) {
                if (seq >= (v as number)) chunkRows.delete(seq);
              }
              return Promise.resolve({ error: null });
            },
          };
          return q;
        },
      };
    },
  };
  return { client, upserts, sends, calls, flagUpdates, deletes, chunkRows };
}

/** payload 를 seq 순으로 모아 준다. */
// deno-lint-ignore no-explicit-any
function payloads(upserts: { row: Record<string, unknown> }[]): any[] {
  return [...upserts].sort((a, b) => (a.row.seq as number) - (b.row.seq as number))
    .map((u) => u.row.payload);
}

// ---------------------------------------------------------------------------
// 순서가 깨진 경우
// ---------------------------------------------------------------------------

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

Deno.test("직전 창의 캐리가 없으면 던진다 — 순서가 깨진 것이다", async () => {
  const { client } = fakeClient([
    { seq: 0, payload: { sections: [sec("첫째 문장입니다.", 1)] } },
    { seq: 1, payload: { sections: [sec("둘째 문장입니다.", 2)] } },
  ]);
  // deno-lint-ignore no-explicit-any
  const h = makeChunkHandler({ client: client as any, env: ENV, artifactsPerTask: 1 });
  await assertRejects(
    () => h({ ...TASK, from: 1, count: 1 }, {} as never),
    Error,
    "직전 chunk 창 산출물이 없다",
  );
});

Deno.test("범위 밖 창은 던진다 — 조용히 성공으로 끝내면 청크가 사라진다", async () => {
  const { client, upserts, sends } = fakeClient([
    { seq: 0, payload: { sections: [sec("본문 문장입니다.", 1)] } },
  ]);
  // deno-lint-ignore no-explicit-any
  const h = makeChunkHandler({ client: client as any, env: ENV, artifactsPerTask: 1 });
  await assertRejects(
    () => h({ ...TASK, from: 3, count: 1 }, {} as never),
    Error,
    "범위 밖",
  );
  assertEquals(upserts.length, 0);
  assertEquals(sends.length, 0);
});

Deno.test("창의 소스에서 sections 를 하나도 못 읽으면 던진다", async () => {
  // 플랜에는 있는데 payload 에서 배열이 안 나온 경우 — 별칭이 안 먹었거나 행이 사라졌다.
  // 그냥 두면 이 창이 청크 0 개를 만들고 잡은 **성공으로** 끝난다.
  const { client, upserts, sends } = fakeClient([
    { seq: 0, payload: { metadata: {} } },
    { seq: 1, payload: null },
  ]);
  // deno-lint-ignore no-explicit-any
  const h = makeChunkHandler({ client: client as any, env: ENV, artifactsPerTask: 2 });
  await assertRejects(
    () => h(TASK, {} as never),
    Error,
    "sections 를 하나도 못 읽었다",
  );
  assertEquals(upserts.length, 0);
  assertEquals(sends.length, 0);
});

Deno.test("소스 행이 플랜 상한에 닿으면 던진다 — 잘린 플랜으로 청킹하지 않는다", async () => {
  const rows = Array.from({ length: 5000 }, (_, i) => ({
    seq: i,
    payload: { sections: [sec(`문장 ${i} 입니다.`, i + 1)] },
  }));
  const { client } = fakeClient(rows);
  // deno-lint-ignore no-explicit-any
  const h = makeChunkHandler({ client: client as any, env: ENV, artifactsPerTask: 4 });
  await assertRejects(() => h(TASK, {} as never), Error, "플랜 상한");
});

// ---------------------------------------------------------------------------
// 창 하나 = 현행 계약 (전부 한 창에 들어가는 문서)
// ---------------------------------------------------------------------------

Deno.test("여러 extract 조각을 seq 순으로 이어붙인다", async () => {
  // 입력 순서를 뒤집어 둔다. 정렬을 안 하면 텍스트 순서가 뒤바뀐다.
  const { client, upserts } = fakeClient([
    { seq: 10, payload: { sections: [sec("셋째 문장입니다.", 11)] } },
    { seq: 0, payload: { sections: [sec("첫째 문장입니다.", 1)] } },
    { seq: 5, payload: { sections: [sec("둘째 문장입니다.", 6)] } },
  ]);
  // deno-lint-ignore no-explicit-any
  const h = makeChunkHandler({ client: client as any, env: ENV, artifactsPerTask: 10 });
  await h(TASK, {} as never);

  const payload = upserts[0].row.payload as {
    chunk_count: number;
    section_count: number;
    extract_parts: number;
    total_parts: number;
    records: { text: string; chunk_idx: number; page: number }[];
  };
  assertEquals(payload.total_parts, 1);
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
  const h = makeChunkHandler({ client: client as any, env: ENV, artifactsPerTask: 10 });
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
  const h = makeChunkHandler({ client: client as any, env: ENV, artifactsPerTask: 10 });
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
  // 그래도 tag_summarize 는 넣는다 — 안 넣으면 잡이 조용히 멈춘다.
  assertEquals(sends.length, 1);
});

Deno.test("저장이 **다 끝난 뒤에** 다음 단계를 넣는다", async () => {
  const secs = Array.from({ length: 5 }, (_, i) => sec(`문장 ${i} 입니다.`, i + 1));
  const { client, calls } = fakeClient([{ seq: 0, payload: { sections: secs } }]);
  // deno-lint-ignore no-explicit-any
  const h = makeChunkHandler({ client: client as any, env: ENV });
  await h(TASK, {} as never);
  // upsert → 잔존 정리 → enqueue.
  assertEquals(calls, ["upsert", "delete", "rpc:ingest_queue_send"]);
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
  const h = makeChunkHandler({ client: client as any, env: ENV, artifactsPerTask: 10 });
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

Deno.test("스캔 PDF — scan 산출물이 extract 를 **대체**한다", async () => {
  // 원본은 `result = _reroute_pdf_to_image(...)` 로 결과를 통째로 갈아끼운다.
  // 둘을 합치면 원본에 없는(거의 빈) extract 청크가 섞인다.
  const { client, upserts } = fakeClient(
    [{ seq: 0, payload: { sections: [sec("텍스트 레이어 찌꺼기입니다.", 1)] } }],
    [{ seq: 0, payload: { sections: [sec("비전 섹션입니다.", 1)] } }],
    [{ seq: 0, payload: { sections: [sec("스캔 OCR 본문입니다.", 1)] } }],
  );
  // deno-lint-ignore no-explicit-any
  const h = makeChunkHandler({ client: client as any, env: ENV });
  await h(TASK, {} as never);
  const payload = upserts[0].row.payload as Record<string, unknown>;
  assertEquals(payload.scan_parts, 1);
  assertEquals(payload.section_count, 1);
  const text = (payload.records as { text: string }[]).map((r) => r.text).join("\n");
  assertEquals(text.includes("스캔 OCR"), true, text);
  // 대체다 — extract·vision 은 안 들어간다.
  assertEquals(text.includes("찌꺼기"), false, text);
  assertEquals(text.includes("비전 섹션"), false, text);
});

// ---------------------------------------------------------------------------
// chunk_filter 이동 — 여기서 마킹하지 않는다
// ---------------------------------------------------------------------------

Deno.test("머리말 판정은 `load` 로 넘긴다 — 여기서는 flags 를 안 붙이고 목록만 남긴다", async () => {
  // 같은 짧은 텍스트가 3회 반복 → header_footer 후보. 마킹은 `load` 가 한다.
  const { client, upserts } = fakeClient([{
    seq: 0,
    payload: {
      sections: [
        sec("머리말", 1),
        sec("머리말", 2),
        sec("머리말", 3),
        sec("이것은 충분히 긴 본문 문장입니다. 필터에 걸리지 않아야 합니다.", 4),
      ],
    },
  }]);
  // deno-lint-ignore no-explicit-any
  const h = makeChunkHandler({ client: client as any, env: ENV });
  await h(TASK, {} as never);
  const payload = upserts[0].row.payload as {
    records: { flags?: Record<string, unknown> }[];
    header_footer_texts: string[];
  };
  assertEquals(payload.records.length, 4);
  // 이 단계에서는 아직 아무것도 마킹하지 않는다.
  assertEquals(payload.records.map((r) => r.flags?.filtered_reason ?? null), [
    null,
    null,
    null,
    null,
  ]);
  // 대신 문서 전체 판정 결과를 마지막 창이 남긴다.
  assertEquals(payload.header_footer_texts, ["머리말"]);
});

Deno.test("머리말 카운트는 **창을 넘어** 합산된다", async () => {
  // 창마다 1 회씩 → 창 안에서는 절대 3 회가 안 된다. 캐리로 합쳐야만 잡힌다.
  const { client, upserts } = fakeClient([
    { seq: 0, payload: { sections: [sec("머리말", 1)] } },
    { seq: 1, payload: { sections: [sec("머리말", 2)] } },
    { seq: 2, payload: { sections: [sec("머리말", 3)] } },
  ]);
  // deno-lint-ignore no-explicit-any
  const h = makeChunkHandler({ client: client as any, env: ENV, artifactsPerTask: 1 });
  await h({ ...TASK, from: 0, count: 1 }, {} as never);
  await h({ ...TASK, from: 1, count: 1 }, {} as never);
  await h({ ...TASK, from: 2, count: 1 }, {} as never);

  const last = payloads(upserts).at(-1);
  assertEquals(last.header_footer_texts, ["머리말"]);
  // 중간 창은 판정 결과를 안 남긴다(아직 모른다).
  assertEquals("header_footer_texts" in payloads(upserts)[0], false);
});

// ---------------------------------------------------------------------------
// content_gate
// ---------------------------------------------------------------------------

Deno.test("content_gate — PII·워터마크를 metadata 와 문서 flags 에 남긴다", async () => {
  const { client, upserts, flagUpdates } = fakeClient(
    [{
      seq: 0,
      payload: {
        sections: [
          sec("대외비 자료입니다. 주민번호 900101-1234567 이 포함된 긴 문장입니다.", 1),
        ],
      },
    }],
    [],
    [],
    { scan: true },
  ); // 기존 flags 는 보존돼야 한다
  // deno-lint-ignore no-explicit-any
  const h = makeChunkHandler({ client: client as any, env: ENV });
  await h(TASK, {} as never);

  const records = (upserts[0].row.payload as {
    records: { metadata: Record<string, unknown> }[];
  }).records;
  assertEquals(Array.isArray(records[0].metadata.pii_ranges), true);
  assertEquals(records[0].metadata.watermark_hits, ["대외비"]);

  assertEquals(flagUpdates.length, 1);
  assertEquals(flagUpdates[0], {
    scan: true, // 기존 flags 보존
    has_pii: true,
    has_watermark: true,
    third_party: false,
    watermark_hits: ["대외비"],
    chunk_finalized_job: "j1",
  });
});

Deno.test("content_gate — 아무것도 없으면 false 3개만 남긴다", async () => {
  const { client, flagUpdates } = fakeClient([{
    seq: 0,
    payload: { sections: [sec("평범한 본문 문장입니다. 특별한 것이 없습니다.", 1)] },
  }]);
  // deno-lint-ignore no-explicit-any
  const h = makeChunkHandler({ client: client as any, env: ENV });
  await h(TASK, {} as never);
  assertEquals(flagUpdates[0], {
    has_pii: false,
    has_watermark: false,
    third_party: false,
    chunk_finalized_job: "j1",
  });
});

Deno.test("content_gate — vision_type 은 첫 소스 행의 metadata 에서 온다", async () => {
  const { client, flagUpdates } = fakeClient([{
    seq: 0,
    payload: {
      sections: [sec("대화 내용입니다. 충분히 긴 문장으로 둔다.", 1)],
      metadata: { vision_type: "메신저대화" },
    },
  }]);
  // deno-lint-ignore no-explicit-any
  const h = makeChunkHandler({ client: client as any, env: ENV });
  await h(TASK, {} as never);
  assertEquals(flagUpdates[0]["third_party"], true);
});

// ---------------------------------------------------------------------------
// 창 분할 — 여기가 이 기능의 본체다
// ---------------------------------------------------------------------------

/** 창을 나눠도 같아야 하는 문서. 페이지가 전부 달라 컷 지점이 많다. */
const MULTI = [
  { seq: 0, payload: { sections: [sec("첫째 문장입니다.", 1), sec("둘째 문장입니다.", 2)] } },
  { seq: 10, payload: { sections: [sec("셋째 문장입니다.", 3), sec("넷째 문장입니다.", 4)] } },
  { seq: 20, payload: { sections: [sec("다섯째 문장입니다.", 5), sec("여섯째 문장입니다.", 6)] } },
];

Deno.test("창 3개로 나눠 돌린 결과가 한 창에 다 넣은 것과 identical", async () => {
  const { client, upserts, sends, chunkRows } = fakeClient(MULTI);
  // deno-lint-ignore no-explicit-any
  const h = makeChunkHandler({ client: client as any, env: ENV, artifactsPerTask: 1 });

  await h({ ...TASK, from: 0, count: 1 }, {} as never);
  assertEquals(sends[0], { job_id: "j1", doc_id: "d1", stage: "chunk", from: 1, count: 1 });
  await h({ ...TASK, from: 1, count: 1 }, {} as never);
  assertEquals(sends[1], { job_id: "j1", doc_id: "d1", stage: "chunk", from: 2, count: 1 });
  await h({ ...TASK, from: 2, count: 1 }, {} as never);
  assertEquals(sends[2], { job_id: "j1", doc_id: "d1", stage: "tag_summarize" });

  const ps = payloads(upserts);
  assertEquals(ps.map((p) => p.part), [0, 1, 2]);
  assertEquals(ps.map((p) => p.total_parts), [1, 2, 3].map(() => 3));

  // 창 경계는 page 가 바뀌는 지점이다 — 꼬리 한 페이지분이 다음 창으로 넘어간다.
  assertEquals(ps.map((p) => p.records.length), [1, 2, 3]);
  assertEquals(ps.map((p) => p.chunk_idx_from), [0, 1, 3]);
  assertEquals(ps.map((p) => p.next_chunk_idx), [1, 3, 6]);

  // 전체를 한 번에 처리한 것과 **byte-identical** 이어야 한다.
  const whole = runContentGateStage({
    chunks: runChunkStage({
      docId: "d1",
      sections: MULTI.flatMap((r) => r.payload.sections),
      env: ENV,
    }),
  }).chunks;
  const got = ps.flatMap((p) => p.records) as { text: string; chunk_idx: number }[];
  assertEquals(got.map((r) => r.chunk_idx), [0, 1, 2, 3, 4, 5]);
  assertEquals(JSON.stringify(got), JSON.stringify(whole));

  // 마지막 창이 문서 전체 청크 수를 적는다.
  assertEquals(ps[2].doc_chunk_count, 6);
  // 마지막 창은 캐리를 안 남긴다 — 읽을 다음 창이 없다.
  assertEquals("carry" in ps[2], false);
  assertEquals("carry" in ps[0], true);
  assertEquals(chunkRows.size, 3);
});

Deno.test("창 분할이 chunk_idx 연속성을 지킨다 — overlap 도 앞 창과 이어진다", async () => {
  const { client, upserts } = fakeClient(MULTI);
  // deno-lint-ignore no-explicit-any
  const h = makeChunkHandler({ client: client as any, env: ENV, artifactsPerTask: 1 });
  for (const from of [0, 1, 2]) await h({ ...TASK, from, count: 1 }, {} as never);

  const all = payloads(upserts).flatMap((p) => p.records) as {
    chunk_idx: number;
    metadata: Record<string, unknown>;
  }[];
  assertEquals(all.map((r) => r.chunk_idx), [0, 1, 2, 3, 4, 5]);
  assertEquals("overlap_with_prev_chunk_idx" in all[0].metadata, false);
  // 창 1 의 첫 청크(chunk_idx 1)도 앞 창의 마지막(0)과 이어진다.
  assertEquals(all[1].metadata["overlap_with_prev_chunk_idx"], 0);
  assertEquals(all[3].metadata["overlap_with_prev_chunk_idx"], 2);
});

Deno.test("문서 flags 는 창을 넘어 OR 로 누적된다 — 마지막 창이 한 번만 쓴다", async () => {
  const rows = [
    { seq: 0, payload: { sections: [sec("주민번호 900101-1234567 이 든 첫 장입니다.", 1)] } },
    { seq: 10, payload: { sections: [sec("평범한 가운데 장입니다. 아무것도 없습니다.", 2)] } },
    { seq: 20, payload: { sections: [sec("대외비 표시가 있는 마지막 장입니다.", 3)] } },
  ];
  const { client, flagUpdates } = fakeClient(rows);
  // deno-lint-ignore no-explicit-any
  const h = makeChunkHandler({ client: client as any, env: ENV, artifactsPerTask: 1 });
  for (const from of [0, 1, 2]) await h({ ...TASK, from, count: 1 }, {} as never);

  // 창마다 쓰면 마지막 창의 false 가 첫 창의 true 를 지운다 — 그래서 한 번만 쓴다.
  assertEquals(flagUpdates.length, 1);
  assertEquals(flagUpdates[0], {
    has_pii: true,
    has_watermark: true,
    third_party: false,
    watermark_hits: ["대외비"],
    chunk_finalized_job: "j1",
  });
});

Deno.test("마지막 창이 잔존 part 를 지운다 — 재인제스트로 창 수가 줄었을 때", async () => {
  // 옛 인제스트가 남긴 part 5. 안 지우면 `load` 가 이번에 안 만든 청크를 되살린다.
  const { client, deletes, chunkRows } = fakeClient(
    MULTI,
    [],
    [],
    {},
    [{ seq: 5, payload: { part: 5, total_parts: 6, records: [] } }],
  );
  // deno-lint-ignore no-explicit-any
  const h = makeChunkHandler({ client: client as any, env: ENV, artifactsPerTask: 1 });
  for (const from of [0, 1, 2]) await h({ ...TASK, from, count: 1 }, {} as never);

  assertEquals(deletes.length, 1);
  assertEquals(deletes[0].eq["stage"], "chunk");
  assertEquals(deletes[0].gte, ["seq", 3]);
  assertEquals(chunkRows.has(5), false);
  assertEquals([...chunkRows.keys()].sort(), [0, 1, 2]);
});

Deno.test("창을 재배달해도 결과가 같다 (멱등)", async () => {
  const { client, upserts } = fakeClient(MULTI);
  // deno-lint-ignore no-explicit-any
  const h = makeChunkHandler({ client: client as any, env: ENV, artifactsPerTask: 1 });
  await h({ ...TASK, from: 0, count: 1 }, {} as never);
  await h({ ...TASK, from: 1, count: 1 }, {} as never);
  const first = JSON.stringify(upserts.at(-1)!.row.payload);
  // 같은 창을 한 번 더 — 캐리(seq=0)는 그대로라 같은 offset·같은 출력이 나와야 한다.
  await h({ ...TASK, from: 1, count: 1 }, {} as never);
  assertEquals(JSON.stringify(upserts.at(-1)!.row.payload), first);
  // upsert 라 행은 늘지 않는다.
  assertEquals(new Set(upserts.map((u) => u.row.seq)).size, 2);
});

Deno.test("마지막 창을 재배달해도 tag_summarize 는 한 번만 들어간다", async () => {
  // 두 번 들어가면 LLM 2 회 + load 사슬 전체가 다시 돈다.
  const { client, sends, flagUpdates } = fakeClient(MULTI);
  // deno-lint-ignore no-explicit-any
  const h = makeChunkHandler({ client: client as any, env: ENV, artifactsPerTask: 1 });
  for (const from of [0, 1, 2]) await h({ ...TASK, from, count: 1 }, {} as never);
  assertEquals(sends.filter((x) => x.stage === "tag_summarize").length, 1);

  // 마지막 창만 한 번 더 — flags 에 남은 chunk_finalized_job 이 막아야 한다.
  await h({ ...TASK, from: 2, count: 1 }, {} as never);
  assertEquals(sends.filter((x) => x.stage === "tag_summarize").length, 1);
  // flags 는 다시 써도 같은 값이라 무해하다.
  assertEquals(flagUpdates.at(-1)!["chunk_finalized_job"], "j1");
});

Deno.test("한 창이 통째로 같은 page 면 청크 0 개로 넘긴다 — 잘라도 되는 곳이 없다", async () => {
  const rows = [
    { seq: 0, payload: { sections: [sec("가나다 첫 조각입니다.", 7)] } },
    { seq: 10, payload: { sections: [sec("라마바 둘째 조각입니다.", 7)] } },
    { seq: 20, payload: { sections: [sec("사아자 셋째 조각입니다.", 8)] } },
  ];
  const { client, upserts } = fakeClient(rows);
  // deno-lint-ignore no-explicit-any
  const h = makeChunkHandler({ client: client as any, env: ENV, artifactsPerTask: 1 });
  for (const from of [0, 1, 2]) await h({ ...TASK, from, count: 1 }, {} as never);

  const ps = payloads(upserts);
  // page 7 짜리 두 조각은 창 0·1 을 통과해 창 2 에서 병합된다.
  assertEquals(ps.map((p) => p.records.length), [0, 0, 2]);
  const texts = ps.flatMap((p) => p.records).map((r: { text: string }) => r.text);
  assertEquals(texts[0], "가나다 첫 조각입니다.\n\n라마바 둘째 조각입니다.");
  assertEquals(texts[1], "사아자 셋째 조각입니다.");
});

Deno.test("page 가 null 인 섹션끼리도 한 묶음으로 넘어간다", async () => {
  const rows = [
    { seq: 0, payload: { sections: [sec("널 첫째 조각입니다.", null)] } },
    { seq: 10, payload: { sections: [sec("널 둘째 조각입니다.", null)] } },
    { seq: 20, payload: { sections: [sec("페이지가 있는 조각입니다.", 1)] } },
  ];
  const { client, upserts } = fakeClient(rows);
  // deno-lint-ignore no-explicit-any
  const h = makeChunkHandler({ client: client as any, env: ENV, artifactsPerTask: 1 });
  for (const from of [0, 1, 2]) await h({ ...TASK, from, count: 1 }, {} as never);
  const texts = payloads(upserts).flatMap((p) => p.records).map((r: { text: string }) => r.text);
  assertEquals(texts, [
    "널 첫째 조각입니다.\n\n널 둘째 조각입니다.",
    "페이지가 있는 조각입니다.",
  ]);
});

/**
 * 실제 SK 사업보고서 픽스처로 도는 end-to-end 등가성.
 *
 * 위 `MULTI` 는 page 가 전부 달라 병합이 안 일어난다 — 창 경계 규칙을 진짜로 시험하려면
 * **창을 넘나드는 병합**이 있어야 한다. 픽스처는 1,903 섹션 → 170 병합이라 그렇다.
 */
Deno.test("픽스처 문서를 창으로 나눠 돌려도 전체 처리와 byte-identical", async () => {
  const fixture: ExtractedSection[] = JSON.parse(
    await Deno.readTextFile(
      new URL("../fixtures/chunk_sections_sample.json", import.meta.url),
    ),
  );
  // extract 아티팩트 흉내 — 150 섹션씩 13 조각(page 를 무시하고 자른다. 실제 extract 도
  // 페이지 수로 자르지 물어봐서 자르지 않는다).
  const rows = [];
  for (let i = 0, seq = 0; i < fixture.length; i += 150, seq += 10) {
    rows.push({ seq, payload: { sections: fixture.slice(i, i + 150) } });
  }
  const { client, upserts } = fakeClient(rows);
  // deno-lint-ignore no-explicit-any
  const h = makeChunkHandler({ client: client as any, env: ENV, artifactsPerTask: 2 });
  const total = Math.ceil(rows.length / 2);
  for (let w = 0; w < total; w++) await h({ ...TASK, from: w * 2, count: 2 }, {} as never);

  const got = payloads(upserts).flatMap((p) => p.records);
  const whole = runContentGateStage({
    chunks: runChunkStage({ docId: "d1", sections: fixture, env: ENV }),
  }).chunks;
  assertEquals(got.length, whole.length);
  assertEquals(JSON.stringify(got), JSON.stringify(whole));
});

Deno.test("창 크기는 메시지에 실려 온 count 가 우선이다", async () => {
  const { client, sends } = fakeClient(MULTI);
  // 핸들러 기본값 1 이어도 메시지가 3 이면 한 창이다.
  // deno-lint-ignore no-explicit-any
  const h = makeChunkHandler({ client: client as any, env: ENV, artifactsPerTask: 1 });
  await h({ ...TASK, from: 0, count: 3 }, {} as never);
  assertEquals(sends, [{ job_id: "j1", doc_id: "d1", stage: "tag_summarize" }]);
});

// ---------------------------------------------------------------------------
// 동의어 주입 배선 — 모듈이 아니라 **`toChunkRecords` 경유**를 본다.
//
// 예전에는 여기서 `notPortedInjector` 가 던졌다(조각 c2 미이관). 이제 실제 주입기가
// 물려 있는지, 그리고 ENV 가 꺼져 있을 때 **아무 일도 안 하는지**를 같이 고정한다.
// ---------------------------------------------------------------------------
Deno.test("동의어 ENV 가 켜지면 마커가 붙고 metadata 가 채워진다", () => {
  const records = toChunkRecords({
    docId: "d1",
    sections: [{
      text: "데이터센터 운영 지침",
      page: 1,
      section_title: null,
      bbox: null,
      metadata: {},
    }],
    env: { ...ENV, synonymInjectionEnabled: true },
    injectSynonyms,
  });
  assertEquals(records.length, 1);
  // 본문 뒤에 `\n\n[검색어: ...]` 한 줄.
  assertStringIncludes(records[0].text, "데이터센터 운영 지침\n\n[검색어: ");
  assertEquals(records[0].metadata["synonym_candidates"], ["DC", "전산센터", "data center"]);
  // doc-level LLM 후보를 안 넘겼으므로 `dict` 다.
  assertEquals(records[0].metadata["synonym_source"], "dict");
  // `char_range` 는 **마커까지 포함한** 길이다 — 원본도 주입 후에 잰다.
  assertEquals(records[0].char_range, [0, [...records[0].text].length]);
});

Deno.test("동의어 ENV 가 꺼져 있으면 텍스트·metadata 가 그대로다", () => {
  const records = toChunkRecords({
    docId: "d1",
    sections: [{
      text: "데이터센터 운영 지침",
      page: 1,
      section_title: null,
      bbox: null,
      metadata: {},
    }],
    env: { ...ENV, synonymInjectionEnabled: false },
    injectSynonyms,
  });
  assertEquals(records[0].text, "데이터센터 운영 지침");
  assertEquals("synonym_candidates" in records[0].metadata, false);
  assertEquals("synonym_source" in records[0].metadata, false);
});

Deno.test("사전에 안 걸리는 본문은 켜져 있어도 무변경", () => {
  const records = toChunkRecords({
    docId: "d1",
    sections: [{
      text: "오늘 점심은 김치찌개였다",
      page: 1,
      section_title: null,
      bbox: null,
      metadata: {},
    }],
    env: { ...ENV, synonymInjectionEnabled: true },
    injectSynonyms,
  });
  assertEquals(records[0].text, "오늘 점심은 김치찌개였다");
  assertEquals("synonym_candidates" in records[0].metadata, false);
});
