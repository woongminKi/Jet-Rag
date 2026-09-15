import { assertEquals } from "@std/assert";
import { Debouncer, fsEventPaths, watchLoop } from "./watcher.ts";

Deno.test("watcher — 관심 있는 이벤트만, 임시 파일은 버린다", () => {
  assertEquals(fsEventPaths({ kind: "create", paths: ["/a/x.pdf"] }), ["/a/x.pdf"]);
  assertEquals(fsEventPaths({ kind: "modify", paths: ["/a/x.pdf"] }), ["/a/x.pdf"]);
  assertEquals(fsEventPaths({ kind: "rename", paths: ["/a/x.crdownload", "/a/x.pdf"] }), ["/a/x.pdf"]);
  // 삭제·접근은 볼 일이 없다 (삭제 동기화는 범위 밖 — 스펙 §1).
  assertEquals(fsEventPaths({ kind: "remove", paths: ["/a/x.pdf"] }), []);
  assertEquals(fsEventPaths({ kind: "access", paths: ["/a/x.pdf"] }), []);
  assertEquals(fsEventPaths({ kind: "create", paths: ["/a/.DS_Store"] }), []);
});

Deno.test("watcher — 디바운스: 연타는 마지막 1회로 합친다", async () => {
  const fired: string[] = [];
  const d = new Debouncer(30, (p) => fired.push(p));
  d.push("/a/x.pdf");
  d.push("/a/x.pdf");
  d.push("/a/y.pdf");
  d.push("/a/x.pdf");
  assertEquals(fired, []);
  await new Promise((r) => setTimeout(r, 90));
  assertEquals(fired.sort(), ["/a/x.pdf", "/a/y.pdf"]);
  d.dispose();
});

Deno.test("watcher — dispose 하면 예약된 flush 가 취소된다 (종료 시 누수 방지)", async () => {
  const fired: string[] = [];
  const d = new Debouncer(30, (p) => fired.push(p));
  d.push("/a/x.pdf");
  d.dispose();
  await new Promise((r) => setTimeout(r, 90));
  assertEquals(fired, []);
});

Deno.test("watcher — 실제 watchFs: 새 파일이 콜백으로 온다", async () => {
  const root = await Deno.makeTempDir({ prefix: "jetrag-watch-" });
  const ac = new AbortController();
  const seen: string[] = [];
  let resolve: () => void = () => {};
  const got = new Promise<void>((r) => (resolve = r));
  const loop = watchLoop([root], (p) => {
    seen.push(p);
    resolve();
  }, { debounceMs: 30, signal: ac.signal });

  // watchFs 가 붙을 시간을 준다.
  await new Promise((r) => setTimeout(r, 200));
  await Deno.writeTextFile(`${root}/새문서.pdf`, "%PDF-1.7\n");
  await Promise.race([got, new Promise((r) => setTimeout(r, 5000))]);
  ac.abort();
  await loop;
  assertEquals(seen.some((p) => p.endsWith("새문서.pdf")), true);
});
