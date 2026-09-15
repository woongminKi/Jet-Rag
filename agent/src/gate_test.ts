import { assertEquals } from "@std/assert";
import { ALLOWED_EXTENSIONS } from "../../supabase/functions/_shared/documents/input_gate.ts";
import { extOf, gateEntry, isStable, isTempName, MAX_SIZE_BYTES } from "./gate.ts";
import { sha256File, sha256Hex } from "./hash.ts";

Deno.test("gate — 확장자 표는 서버 것을 그대로 쓴다 (11종)", () => {
  // 복사본이 아니라 import 다. 서버가 표를 바꾸면 이 테스트가 먼저 깨진다.
  assertEquals(Object.keys(ALLOWED_EXTENSIONS).sort(), [
    ".docx",
    ".heic",
    ".hwp",
    ".hwpx",
    ".jpeg",
    ".jpg",
    ".md",
    ".pdf",
    ".png",
    ".pptx",
    ".txt",
  ]);
  assertEquals(MAX_SIZE_BYTES, 50 * 1024 * 1024);
});

Deno.test("gate — extOf 는 마지막 점 이후, 소문자", () => {
  assertEquals(extOf("보고서.PDF"), ".pdf");
  assertEquals(extOf("/a/b/c.tar.gz"), ".gz");
  assertEquals(extOf("no-ext"), "");
  assertEquals(extOf(".gitignore"), "");
});

Deno.test("gate — 임시·시스템 파일 패턴", () => {
  const temp = [
    "보고서.pdf.crdownload",
    "a.part",
    "b.tmp",
    "c.download",
    "d.partial",
    "~$문서.docx",
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
    ".숨김.pdf",
  ];
  for (const n of temp) assertEquals(isTempName(n), true, `임시로 봐야 한다: ${n}`);
  for (const n of ["보고서.pdf", "a.docx"]) assertEquals(isTempName(n), false, n);
});

Deno.test("gate — 판정 4갈래", () => {
  assertEquals(gateEntry({ path: "/a/보고서.pdf", size: 1024 }), { ok: true, docType: "pdf" });
  assertEquals(gateEntry({ path: "/a/x.crdownload", size: 1 }), {
    ok: false,
    reason: "temp",
    detail: "임시 파일",
  });
  assertEquals(gateEntry({ path: "/a/x.exe", size: 1 }).ok, false);
  assertEquals((gateEntry({ path: "/a/x.exe", size: 1 }) as { reason: string }).reason, "ext");
  assertEquals((gateEntry({ path: "/a/x.pdf", size: 0 }) as { reason: string }).reason, "empty");
  // 50MB 초과는 서버가 413 으로 돌려주므로 보내지 않고 여기서 제외한다(스펙: 실측 51MB 21초).
  assertEquals(
    (gateEntry({ path: "/a/x.pdf", size: MAX_SIZE_BYTES + 1 }) as { reason: string }).reason,
    "too_large",
  );
});

Deno.test("gate — isStable: 두 번 연속 같은 크기·mtime 이어야 통과", async () => {
  const sizes = [100, 200, 200, 200];
  let i = 0;
  const stat = () => Promise.resolve({ size: sizes[Math.min(i++, sizes.length - 1)], mtime: new Date(0) });
  const sleep = () => Promise.resolve();
  assertEquals(await isStable("/a/x.pdf", { checks: 2, stat, sleep }), false); // 100 → 200
  assertEquals(await isStable("/a/x.pdf", { checks: 2, stat, sleep }), true); // 200 → 200
});

Deno.test("gate — isStable: 파일이 사라지면 false", async () => {
  const stat = () => Promise.reject(new Deno.errors.NotFound("없음"));
  assertEquals(await isStable("/a/x.pdf", { checks: 2, stat, sleep: () => Promise.resolve() }), false);
});

Deno.test("hash — 알려진 sha256", async () => {
  assertEquals(
    await sha256Hex(new TextEncoder().encode("abc")),
    "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
  );
  const dir = await Deno.makeTempDir({ prefix: "jetrag-hash-" });
  await Deno.writeTextFile(`${dir}/a.txt`, "abc");
  assertEquals(
    await sha256File(`${dir}/a.txt`),
    "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
  );
});
