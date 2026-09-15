import { assertEquals } from "@std/assert";
import { formatBytes, scanBacklog, summarize } from "./scanner.ts";

const DAY = 86_400_000;

async function fixture(): Promise<{ root: string; now: number }> {
  const root = await Deno.makeTempDir({ prefix: "jetrag-scan-" });
  const now = Date.now();
  const mk = async (rel: string, body: string, ageDays: number) => {
    const path = `${root}/${rel}`;
    await Deno.mkdir(path.slice(0, path.lastIndexOf("/")), { recursive: true });
    await Deno.writeTextFile(path, body);
    const t = new Date(now - ageDays * DAY);
    await Deno.utime(path, t, t);
  };
  await mk("최신.pdf", "%PDF-1.7\n___", 1);
  await mk("중간.docx", "PK___", 30);
  await mk("하위/깊은.txt", "본문", 10);
  await mk("오래된.pdf", "%PDF-1.7\n___", 200); // 90일 밖
  await mk("무시.exe", "MZ", 1); // 확장자 밖
  await mk("받는중.pdf.crdownload", "___", 1); // 임시
  await mk(".숨김.pdf", "%PDF", 1); // 숨김
  await mk(".캐시폴더/안에.pdf", "%PDF", 1); // 숨김 폴더
  return { root, now };
}

Deno.test("scanner — N일 이내 · 게이트 통과 · 최신순", async () => {
  const { root, now } = await fixture();
  const found = await scanBacklog([root], { days: 90, now });
  assertEquals(found.map((f) => f.path.slice(root.length + 1)), [
    "최신.pdf",
    "하위/깊은.txt",
    "중간.docx",
  ]);
});

Deno.test("scanner — days=0 이면 아무것도 안 본다 (백로그 해제)", async () => {
  const { root, now } = await fixture();
  assertEquals(await scanBacklog([root], { days: 0, now }), []);
});

Deno.test("scanner — 50MB 초과는 제외 사유와 함께 건너뛴다", async () => {
  const root = await Deno.makeTempDir({ prefix: "jetrag-scan-big-" });
  const path = `${root}/큰파일.pdf`;
  await Deno.writeTextFile(path, "%PDF-1.7\n");
  // sparse 파일 — 51MB 를 실제로 쓰지 않는다.
  await Deno.truncate(path, 51 * 1024 * 1024);
  const skipped: { path: string; reason: string }[] = [];
  const found = await scanBacklog([root], {
    days: 90,
    now: Date.now(),
    onSkip: (p, r) => skipped.push({ path: p, reason: r }),
  });
  assertEquals(found, []);
  assertEquals(skipped.length, 1);
  assertEquals(skipped[0].reason, "too_large");
});

Deno.test("scanner — 없는 폴더는 건너뛰고 나머지를 계속 본다", async () => {
  const { root, now } = await fixture();
  const found = await scanBacklog([`${root}/없음`, root], { days: 90, now });
  assertEquals(found.length, 3);
});

Deno.test("scanner — 집계와 사람이 읽는 크기", () => {
  const s = summarize([
    { path: "/a", size: 1024 * 1024, mtime: 0 },
    { path: "/b", size: 3 * 1024 * 1024, mtime: 0 },
  ]);
  assertEquals(s.count, 2);
  assertEquals(s.bytes, 4 * 1024 * 1024);
  assertEquals(formatBytes(4 * 1024 * 1024), "4.0MB");
  assertEquals(formatBytes(512), "512B");
  assertEquals(formatBytes(2 * 1024 * 1024 * 1024), "2.0GB");
});
