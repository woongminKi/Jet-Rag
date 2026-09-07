/**
 * `chunk` 핸들러 계약 — **extract 산출물을 전부, 순서대로** 모아야 한다.
 *
 * 여기서 순서가 틀리거나 일부가 빠지면 청크가 조용히 달라진다. 그런 실패는 나중에
 * 검색 품질 문제로만 보여서 원인을 못 찾는다. 그래서 계약으로 고정한다.
 */

import { assertEquals, assertRejects, assertStringIncludes } from "@std/assert";
import { injectSynonyms } from "../synonym_inject.ts";
import { toChunkRecords } from "../chunk_records.ts";
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
  scanRows: { seq: number; payload: unknown }[] = [],
  docFlags: Record<string, unknown> = {},
) {
  const flagUpdates: Record<string, unknown>[] = [];
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
    from(table: string) {
      // `content_gate` 가 문서 flags 를 읽어 머지한다 — 테이블을 구분해야 한다.
      if (table === "documents") {
        // deno-lint-ignore no-explicit-any
        const dq: any = {
          eq: () => dq,
          limit: () => Promise.resolve({ data: [{ flags: docFlags }], error: null }),
        };
        return {
          select: () => dq,
          update(row: Record<string, unknown>) {
            flagUpdates.push(row.flags as Record<string, unknown>);
            return { eq: () => Promise.resolve({ error: null }) };
          },
        };
      }
      // **stage 를 봐야 한다** — 핸들러가 extract·vision·scan 을 따로 긁는다.
      // 구분 없이 같은 행을 돌려주면 섹션이 두 번 들어가 테스트가 조용히 통과한다.
      let stage = "extract";
      const q = {
        eq(col: string, val: unknown) {
          if (col === "stage") stage = String(val);
          return q;
        },
        order(_c: string, o?: { ascending?: boolean }) {
          ordered = o?.ascending !== false;
          const src = stage === "vision" ? visionRows : stage === "scan" ? scanRows : rows;
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
  return { client, upserts, sends, calls, flagUpdates, wasOrdered: () => ordered };
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
  // 원본 순서: chunk_filter → content_gate → **tag_summarize** → load.
  assertEquals(sends[0], { job_id: "j1", doc_id: "d1", stage: "tag_summarize" });
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

Deno.test("chunk_filter — 표 노이즈·머리말이 flags 로 마킹된다(삭제가 아니다)", async () => {
  // 같은 짧은 텍스트가 3회 반복 → header_footer. 청크는 남고 flags 만 붙는다.
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
  const records = (upserts[0].row.payload as {
    records: { text: string; flags?: Record<string, unknown> }[];
  }).records;
  // 삭제되지 않는다 — 전부 남아 있다.
  assertEquals(records.length, 4);
  const reasons = records.map((r) => r.flags?.filtered_reason ?? null);
  assertEquals(reasons, ["header_footer", "header_footer", "header_footer", null]);
});

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
  });
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
