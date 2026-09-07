/**
 * 업로드 핸들러 계약.
 *
 * 여기서 고정하는 것:
 * - 게이트 순서 — 확장자 → 크기 → 매직바이트. **거절이면 Storage 를 건드리지 않는다.**
 * - dedup 3 갈래 (정상 중복 / 실패 흔적 / 신규)
 * - 큐 투입까지 끝나야 202
 */

import { assertEquals } from "@std/assert";
import { buildUserPath, extOf, handleUpload } from "./upload.ts";

const PDF = new Uint8Array([...new TextEncoder().encode("%PDF-1.7\n"), ...new Uint8Array(400)]);
const PNG = new Uint8Array([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, ...new Uint8Array(400)]);

function formOf(name: string, bytes: Uint8Array, extra: Record<string, string> = {}): FormData {
  const f = new FormData();
  f.append("file", new File([bytes.slice()], name, { type: "application/pdf" }));
  for (const [k, v] of Object.entries(extra)) f.append(k, v);
  return f;
}

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
        single: () => Promise.resolve({ data: { id: `${table}-new` }, error: null }),
        insert(row: Record<string, unknown>) {
          inserts.push({ table, row });
          return {
            select: () => ({ single: () => Promise.resolve({ data: { id: `${table}-new` }, error: null }) }),
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

Deno.test("허용 안 된 확장자는 400 — Storage 를 건드리지 않는다", async () => {
  const f = fakeClient();
  const r = await handleUpload(formOf("bad.exe", PDF), f.deps);
  assertEquals(r.status, 400);
  assertEquals(f.uploads.length, 0);
  assertEquals(f.inserts.length, 0);
});

Deno.test("빈 파일은 400", async () => {
  const f = fakeClient();
  const r = await handleUpload(formOf("a.pdf", new Uint8Array(0)), f.deps);
  assertEquals(r.status, 400);
  assertEquals(f.uploads.length, 0);
});

Deno.test("매직바이트 불일치는 400 — Storage 를 건드리지 않는다", async () => {
  const f = fakeClient();
  // 확장자는 .pdf 인데 내용은 PNG
  const r = await handleUpload(formOf("fake.pdf", PNG), f.deps);
  assertEquals(r.status, 400);
  assertEquals(String(r.body.detail).includes("일치하지 않습니다"), true);
  assertEquals(f.uploads.length, 0);
});

Deno.test("file 필드가 없으면 422", async () => {
  const f = fakeClient();
  const r = await handleUpload(new FormData(), f.deps);
  assertEquals(r.status, 422);
});

Deno.test("source_channel 이 목록 밖이면 422", async () => {
  const f = fakeClient();
  const r = await handleUpload(formOf("a.pdf", PDF, { source_channel: "해킹" }), f.deps);
  assertEquals(r.status, 422);
  assertEquals(f.uploads.length, 0);
});

Deno.test("신규 업로드 — Storage · documents · job · 큐까지 간다", async () => {
  const f = fakeClient();
  const r = await handleUpload(formOf("계약서.pdf", PDF, { source_channel: "drag-drop" }), f.deps);
  assertEquals(r.status, 202);
  assertEquals(r.body.duplicated, false);
  assertEquals(f.uploads.length, 1);
  assertEquals(f.uploads[0].path.startsWith("user/u1/"), true);
  assertEquals(f.uploads[0].path.endsWith(".pdf"), true);

  const doc = f.inserts.find((i) => i.table === "documents")!.row;
  assertEquals(doc.user_id, "u1");
  assertEquals(doc.doc_type, "pdf");
  assertEquals(doc.source_channel, "drag-drop");
  assertEquals(doc.title, "계약서");
  assertEquals(doc.size_bytes, PDF.length);
  // storage_path 가 처음부터 실제 경로여야 한다 — pending placeholder 를 안 쓴다.
  assertEquals(String(doc.storage_path).includes("pending"), false);

  assertEquals(f.inserts.some((i) => i.table === "ingest_jobs"), true);
  assertEquals(f.sends.length, 1);
  assertEquals(f.sends[0].stage, "extract");
});

Deno.test("title 을 주면 그걸 쓴다 (NFC 정규화)", async () => {
  const f = fakeClient();
  // NFD 로 조합된 '한' — macOS Finder 가 이렇게 보낸다.
  const nfd = "한";
  await handleUpload(formOf("a.pdf", PDF, { title: nfd }), f.deps);
  const doc = f.inserts.find((i) => i.table === "documents")!.row;
  assertEquals(doc.title, "한");
});

Deno.test("파일명도 NFC 로 통일한다", async () => {
  const f = fakeClient();
  await handleUpload(formOf("한.pdf", PDF), f.deps);
  const doc = f.inserts.find((i) => i.table === "documents")!.row;
  assertEquals(doc.title, "한");
});

Deno.test("정상 중복 — 새 행도 잡도 만들지 않는다", async () => {
  const f = fakeClient({ existing: [{ id: "old-doc", flags: {} }] });
  const r = await handleUpload(formOf("a.pdf", PDF), f.deps);
  assertEquals(r.status, 202);
  assertEquals(r.body, { doc_id: "old-doc", job_id: null, duplicated: true });
  assertEquals(f.uploads.length, 0);
  assertEquals(f.inserts.length, 0);
  assertEquals(f.sends.length, 0);
});

Deno.test("실패 흔적이 있으면 재시도로 본다 — 같은 행에 잡을 새로 건다", async () => {
  const f = fakeClient({ existing: [{ id: "failed-doc", flags: { failed: true } }] });
  const r = await handleUpload(formOf("a.pdf", PDF), f.deps);
  assertEquals(r.status, 202);
  assertEquals(r.body.doc_id, "failed-doc");
  assertEquals(r.body.duplicated, false);
  // 새 documents 행은 안 만든다.
  assertEquals(f.inserts.some((i) => i.table === "documents"), false);
  // flags 를 비우고 **새 모드만** 남긴다(원본 `_flags_with_ingest_mode({}, mode)`).
  assertEquals(f.updates[0].flags, { ingest_mode: "default" });
  assertEquals(f.uploads.length, 1);
  assertEquals(f.sends.length, 1);
});

Deno.test("같은 내용이면 같은 경로 — sha256 기반", async () => {
  const a = fakeClient();
  const b = fakeClient();
  await handleUpload(formOf("x.pdf", PDF), a.deps);
  await handleUpload(formOf("전혀다른이름.pdf", PDF), b.deps);
  assertEquals(a.uploads[0].path, b.uploads[0].path);
});

Deno.test("mode 를 flags.ingest_mode 로 남긴다 — 재인제스트가 이걸 이어받는다", async () => {
  const f = fakeClient();
  const r = await handleUpload(formOf("a.pdf", PDF, { mode: "fast" }), f.deps);
  assertEquals(r.status, 202);
  const doc = f.inserts.find((i) => i.table === "documents");
  assertEquals(doc?.row.flags, { ingest_mode: "fast" });
});

Deno.test("mode 미지정이면 default", async () => {
  const f = fakeClient();
  await handleUpload(formOf("a.pdf", PDF), f.deps);
  const doc = f.inserts.find((i) => i.table === "documents");
  assertEquals(doc?.row.flags, { ingest_mode: "default" });
});

Deno.test("무효 mode 는 400 — 확장자 검증보다 먼저다", async () => {
  const f = fakeClient();
  // 확장자가 무효여도 mode 오류가 먼저 나와야 원본과 같다.
  const r = await handleUpload(formOf("a.zip", PDF, { mode: "turbo" }), f.deps);
  assertEquals(r.status, 400);
  assertEquals(
    r.body.detail,
    "지원되지 않는 모드입니다: 'turbo' (허용: fast, default, precise)",
  );
  assertEquals(f.uploads.length, 0);
});
