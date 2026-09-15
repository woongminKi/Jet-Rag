/**
 * `persistDocument` 계약 — 업로드·이메일·에이전트가 모두 지나는 단 하나의 저장 경로.
 *
 * 여기서 고정하는 것:
 * - 게이트 순서(확장자 → 빈 파일 → 크기 → 매직)와 **거절이면 Storage 를 안 건드린다**
 * - dedup 3 갈래(created / duplicated / retried)
 * - UNIQUE(user_id, sha256) 경합은 500 이 아니라 duplicated
 * - 용량 검사 훅은 dedup **뒤**에 온다
 */

import { assertEquals } from "@std/assert";
import { buildUserPath, extOf, persistDocument } from "./persist.ts";

const PDF = new Uint8Array([...new TextEncoder().encode("%PDF-1.7\n"), ...new Uint8Array(400)]);
const PNG = new Uint8Array([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, ...new Uint8Array(400)]);
/** OOXML·HWPX 공통 ZIP 매직 — 확장자만 다른 같은 바이트를 만드는 데 쓴다. */
const ZIP = new Uint8Array([0x50, 0x4B, 0x03, 0x04, ...new Uint8Array(400)]);

interface FakeOpts {
  existing?: { id: string; flags?: Record<string, unknown> }[];
  /** documents insert 가 UNIQUE 충돌(23505)을 내게 한다. */
  insertConflict?: boolean;
  /** documents insert 가 CHECK 위반(23514)을 내게 한다 — 마이그 031 전의 새 채널. */
  insertCheckViolation?: boolean;
}

function fakeClient(opts: FakeOpts = {}) {
  const inserts: { table: string; row: Record<string, unknown> }[] = [];
  const updates: Record<string, unknown>[] = [];
  const sends: Record<string, unknown>[] = [];
  const uploads: { path: string; size: number }[] = [];
  let selectCalls = 0;
  const client = {
    from(table: string) {
      const api = {
        select: () => api,
        eq: () => api,
        is: () => api,
        limit: () => {
          selectCalls++;
          // UNIQUE 충돌 뒤 재조회는 존재하는 행을 돌려준다.
          if (opts.insertConflict && selectCalls >= 2) {
            return Promise.resolve({ data: [{ id: "raced-doc", flags: {} }], error: null });
          }
          return Promise.resolve({ data: opts.existing ?? [], error: null });
        },
        insert(row: Record<string, unknown>) {
          inserts.push({ table, row });
          if (table === "documents" && opts.insertCheckViolation) {
            return {
              select: () => ({
                single: () =>
                  Promise.resolve({
                    data: null,
                    error: {
                      code: "23514",
                      message: 'new row violates check constraint "documents_source_channel_check"',
                    },
                  }),
              }),
            };
          }
          if (table === "documents" && opts.insertConflict) {
            return {
              select: () => ({
                single: () =>
                  Promise.resolve({
                    data: null,
                    error: {
                      code: "23505",
                      message: "duplicate key value violates unique constraint",
                    },
                  }),
              }),
            };
          }
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
    storage: { from: () => ({ upload: () => Promise.resolve({ error: null }) }) },
  };
  const deps = {
    // deno-lint-ignore no-explicit-any
    client: client as any,
    bucket: "documents",
    userId: "u1",
    upload: (path: string, bytes: Uint8Array) => {
      uploads.push({ path, size: bytes.length });
      return Promise.resolve();
    },
  };
  return { deps, inserts, updates, sends, uploads };
}

const base = { fileName: "계약서.pdf", sourceChannel: "drag-drop", ingestMode: "default" as const };

Deno.test("persist — 게이트 순서: 확장자 → 빈 파일 → 크기 → 매직. 거절이면 Storage 안 건드림", async () => {
  const f = fakeClient();
  const r1 = await persistDocument({ ...base, fileName: "bad.exe", bytes: PDF }, f.deps);
  assertEquals(r1.ok, false);
  if (!r1.ok) assertEquals(r1.status, 400);
  const r2 = await persistDocument({ ...base, bytes: new Uint8Array(0) }, f.deps);
  assertEquals(r2.ok, false);
  if (!r2.ok) assertEquals(r2.status, 400);
  const r3 = await persistDocument({ ...base, fileName: "fake.pdf", bytes: PNG }, f.deps);
  assertEquals(r3.ok, false);
  if (!r3.ok) assertEquals(r3.detail.includes("일치하지 않습니다"), true);
  assertEquals(f.uploads.length, 0);
  assertEquals(f.inserts.length, 0);
});

Deno.test("persist — 신규: Storage · documents · job · 큐", async () => {
  const f = fakeClient();
  const r = await persistDocument({ ...base, bytes: PDF }, f.deps);
  assertEquals(r.ok, true);
  if (!r.ok) return;
  assertEquals(r.outcome, "created");
  assertEquals(r.docId, "documents-new");
  assertEquals(r.jobId, "ingest_jobs-new");
  const doc = f.inserts.find((i) => i.table === "documents")!.row;
  assertEquals(doc.title, "계약서");
  assertEquals(doc.source_channel, "drag-drop");
  assertEquals(doc.flags, { ingest_mode: "default" });
  assertEquals(f.sends[0].stage, "extract");
});

Deno.test("persist — 정상 중복은 duplicated, 아무것도 안 만든다", async () => {
  const f = fakeClient({ existing: [{ id: "old", flags: {} }] });
  const r = await persistDocument({ ...base, bytes: PDF }, f.deps);
  assertEquals(r.ok, true);
  if (!r.ok) return;
  assertEquals(r.outcome, "duplicated");
  assertEquals(r.docId, "old");
  assertEquals(r.jobId, null);
  assertEquals(f.uploads.length, 0);
});

Deno.test("persist — 실패 흔적은 retried: 같은 행에 잡을 새로 건다", async () => {
  const f = fakeClient({ existing: [{ id: "failed", flags: { failed: true } }] });
  const r = await persistDocument({ ...base, bytes: PDF, ingestMode: "fast" }, f.deps);
  assertEquals(r.ok, true);
  if (!r.ok) return;
  assertEquals(r.outcome, "retried");
  assertEquals(r.docId, "failed");
  assertEquals(f.updates[0].flags, { ingest_mode: "fast" });
  assertEquals(f.uploads.length, 1);
  assertEquals(f.sends.length, 1);
});

Deno.test("persist — UNIQUE 충돌(경합)은 500 이 아니라 duplicated", async () => {
  const f = fakeClient({ insertConflict: true });
  const r = await persistDocument({ ...base, bytes: PDF }, f.deps);
  assertEquals(r.ok, true);
  if (!r.ok) return;
  assertEquals(r.outcome, "duplicated");
  assertEquals(r.docId, "raced-doc");
  assertEquals(f.sends.length, 0);
});

Deno.test("persist — 용량 검사가 거부하면 402, Storage 안 건드림", async () => {
  const f = fakeClient();
  const r = await persistDocument({ ...base, bytes: PDF }, {
    ...f.deps,
    checkStorage: () => Promise.resolve({ allowed: false, usedBytes: 900, limitBytes: 1000 }),
  });
  assertEquals(r.ok, false);
  if (r.ok) return;
  assertEquals(r.status, 402);
  assertEquals(r.reason, "storage_limit");
  assertEquals(r.used, 900);
  assertEquals(r.limit, 1000);
  assertEquals(f.uploads.length, 0);
});

Deno.test("persist — 용량 검사는 dedup 뒤에 온다: 중복이면 검사 없이 duplicated", async () => {
  const f = fakeClient({ existing: [{ id: "old", flags: {} }] });
  let called = 0;
  const r = await persistDocument({ ...base, bytes: PDF }, {
    ...f.deps,
    checkStorage: () => {
      called++;
      return Promise.resolve({ allowed: false, usedBytes: 1, limitBytes: 1 });
    },
  });
  assertEquals(r.ok, true);
  assertEquals(called, 0);
});

Deno.test("persist — CHECK 위반(23514)은 500 도 4xx 도 아닌 503: 마이그가 밀린 서버다", async () => {
  const f = fakeClient({ insertCheckViolation: true });
  const r = await persistDocument(
    { ...base, bytes: PDF, sourceChannel: "pc-agent" },
    f.deps,
  );
  assertEquals(r.ok, false);
  if (r.ok) return;
  // 4xx 면 에이전트가 이 파일을 "영구 실패" 로 보고 원장에서 지운다(스펙 §5.2).
  // 파일 잘못이 아니라 서버 설정이므로 재시도 가능한 503 이어야 한다.
  assertEquals(r.status, 503);
  assertEquals(r.code, "channel");
  assertEquals(r.detail.includes("pc-agent"), true);
  // 잡·큐까지 가지 않는다 — documents 가 없으니 만들 것도 없다.
  assertEquals(f.inserts.some((i) => i.table === "ingest_jobs"), false);
  assertEquals(f.sends.length, 0);
});

Deno.test("persist — 거절 코드는 산문이 아니라 code 로 갈린다", async () => {
  const f = fakeClient();
  const ext = await persistDocument({ ...base, fileName: "bad.exe", bytes: PDF }, f.deps);
  assertEquals(ext.ok, false);
  if (!ext.ok) {
    assertEquals(ext.code, "ext");
    assertEquals(ext.ext, ".exe"); // 호출자가 사유 문구에 쓴다
  }
  const empty = await persistDocument({ ...base, bytes: new Uint8Array(0) }, f.deps);
  if (!empty.ok) assertEquals(empty.code, "empty");
  const magic = await persistDocument({ ...base, fileName: "fake.pdf", bytes: PNG }, f.deps);
  if (!magic.ok) assertEquals(magic.code, "magic");
});

Deno.test("persist — 재시도는 doc_type·크기까지 갱신한다 (.hwpx→.docx 는 같은 ZIP 매직)", async () => {
  const f = fakeClient({ existing: [{ id: "failed", flags: { failed: true } }] });
  const r = await persistDocument(
    { ...base, fileName: "계약서.docx", bytes: ZIP, contentType: "application/zip" },
    f.deps,
  );
  assertEquals(r.ok, true);
  const up = f.updates[0];
  assertEquals(up.doc_type, "docx"); // 낡은 채로 두면 extract 가 엉뚱한 파서를 고른다
  assertEquals(up.size_bytes, ZIP.length);
  assertEquals(up.content_type, "application/zip");
  // 삭제 복구는 아직 없다 — findBySha 가 살아있는 행만 주므로 도달 불가다.
  assertEquals("deleted_at" in up, false);
});

Deno.test("extOf — Python PurePosixPath.suffix 와 같다", () => {
  assertEquals(extOf("a.PDF"), ".pdf");
  assertEquals(extOf("a.tar.gz"), ".gz");
  assertEquals(extOf("noext"), "");
  assertEquals(extOf(".hidden"), ""); // 숨김 파일은 확장자로 치지 않는다
  assertEquals(extOf("dir/x.png"), ".png");
});

Deno.test("buildUserPath — user/<uid>/<sha256><ext>", () => {
  assertEquals(buildUserPath("u1", "abc", ".pdf"), "user/u1/abc.pdf");
});
