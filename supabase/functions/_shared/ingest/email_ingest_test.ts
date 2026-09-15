/**
 * `ingestEmailAttachment` 계약 — 저장은 `persistDocument` 가 하고, 여기서 고정하는 건
 * **persist 결과 → 이메일 결과 매핑**이다.
 *
 * - 거절 사유 문구는 통합(2026-09-15) 이전 구현 그대로여야 한다. Worker 로그·사용자
 *   안내가 이 문자열을 본다.
 * - `.pptx`/`.txt`/`.md` 는 이제 받는다 — 확장자 표를 업로드와 통일한 결과다.
 * - 실패 흔적이 있는 문서는 재시도된다 — 이전 이메일 경로에는 없던 분기다.
 */

import { assertEquals } from "@std/assert";
import { ingestEmailAttachment } from "./email_ingest.ts";

const PDF = new Uint8Array([...new TextEncoder().encode("%PDF-1.7\n"), ...new Uint8Array(400)]);
/** OOXML(.pptx/.docx/.hwpx) 공통 ZIP 매직. */
const ZIP = new Uint8Array([0x50, 0x4B, 0x03, 0x04, ...new Uint8Array(400)]);

interface FakeOpts {
  existing?: { id: string; flags?: Record<string, unknown> }[];
}

function fakeClient(opts: FakeOpts = {}) {
  const inserts: { table: string; row: Record<string, unknown> }[] = [];
  const updates: Record<string, unknown>[] = [];
  const sends: Record<string, unknown>[] = [];
  const uploads: { path: string; size: number }[] = [];
  const client = {
    from(table: string) {
      const api = {
        select: () => api,
        eq: () => api,
        is: () => api,
        limit: () => Promise.resolve({ data: opts.existing ?? [], error: null }),
        insert(row: Record<string, unknown>) {
          inserts.push({ table, row });
          return {
            select: () => ({
              single: () => Promise.resolve({ data: { id: `${table}-new` }, error: null }),
            }),
          };
        },
        update(row: Record<string, unknown>) {
          updates.push(row);
          return { eq: () => Promise.resolve({ error: null }) };
        },
      };
      return api;
    },
    rpc(name: string, args: Record<string, unknown>) {
      if (name === "ingest_queue_send") sends.push(args.payload as Record<string, unknown>);
      return Promise.resolve({ data: 1, error: null });
    },
    storage: {
      from: () => ({
        upload: (path: string, bytes: Uint8Array) => {
          uploads.push({ path, size: bytes.length });
          return Promise.resolve({ error: null });
        },
      }),
    },
  };
  // deno-lint-ignore no-explicit-any
  const deps = { client: client as any, bucket: "documents" };
  return { deps, inserts, updates, sends, uploads };
}

Deno.test("email — 비허용 확장자는 skipped, 사유에 확장자가 그대로 실린다", async () => {
  const f = fakeClient();
  const r = await ingestEmailAttachment(f.deps, {
    userId: "u1",
    filename: "bad.exe",
    contentType: "application/octet-stream",
    raw: PDF,
  });
  assertEquals(r, { status: "skipped", filename: "bad.exe", reason: "비허용 확장자: .exe" });
  assertEquals(f.uploads.length, 0);
  assertEquals(f.inserts.length, 0);
});

Deno.test("email — 빈 첨부는 skipped", async () => {
  const f = fakeClient();
  const r = await ingestEmailAttachment(f.deps, {
    userId: "u1",
    filename: "a.pdf",
    contentType: "application/pdf",
    raw: new Uint8Array(0),
  });
  assertEquals(r.status, "skipped");
  assertEquals(r.reason, "빈 첨부");
  assertEquals(f.uploads.length, 0);
});

Deno.test("email — 실패 흔적이 있으면 재시도한다 (통합 전에는 duplicated 였다)", async () => {
  const f = fakeClient({ existing: [{ id: "failed-doc", flags: { failed: true } }] });
  const r = await ingestEmailAttachment(f.deps, {
    userId: "u1",
    filename: "a.pdf",
    contentType: "application/pdf",
    raw: PDF,
  });
  assertEquals(r.status, "accepted");
  assertEquals(r.doc_id, "failed-doc");
  assertEquals(r.job_id, "ingest_jobs-new");
  // 새 documents 행은 안 만든다 — 같은 행에 잡만 새로 건다.
  assertEquals(f.inserts.some((i) => i.table === "documents"), false);
  assertEquals(f.sends.length, 1);
});

Deno.test("email — 정상 중복은 duplicated, 아무것도 안 만든다", async () => {
  const f = fakeClient({ existing: [{ id: "old-doc", flags: {} }] });
  const r = await ingestEmailAttachment(f.deps, {
    userId: "u1",
    filename: "a.pdf",
    contentType: "application/pdf",
    raw: PDF,
  });
  assertEquals(r, { status: "duplicated", filename: "a.pdf", doc_id: "old-doc" });
  assertEquals(f.uploads.length, 0);
});

Deno.test("email — .pptx 를 받는다: 확장자 표가 업로드와 같아졌다", async () => {
  const f = fakeClient();
  const r = await ingestEmailAttachment(f.deps, {
    userId: "u1",
    filename: "발표자료.pptx",
    contentType: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    raw: ZIP,
  });
  assertEquals(r.status, "accepted");
  const doc = f.inserts.find((i) => i.table === "documents")!.row;
  assertEquals(doc.doc_type, "pptx");
  assertEquals(doc.source_channel, "email");
  assertEquals(doc.flags, { ingest_mode: "default" });
  assertEquals(f.uploads.length, 1);
  assertEquals(f.sends[0].stage, "extract");
});
