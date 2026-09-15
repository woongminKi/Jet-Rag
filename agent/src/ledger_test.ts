import { assertEquals } from "@std/assert";
import { Ledger } from "./ledger.ts";

const SHA = (n: number) => n.toString(16).padStart(64, "0");
const T0 = 1_700_000_000_000;

function mem(): Ledger {
  return Ledger.openMemory();
}

Deno.test("ledger — 신규 발견은 pending·precheck 미완료로 들어간다", () => {
  const l = mem();
  const state = l.discover({ sha256: SHA(1), path: "/a/x.pdf", size: 10, mtime: T0, queue: "live" }, T0);
  assertEquals(state, "pending");
  const row = l.get(SHA(1))!;
  assertEquals(row.state, "pending");
  assertEquals(row.precheck_done, 0);
  assertEquals(row.attempts, 0);
  assertEquals(row.queue, "live");
  l.close();
});

Deno.test("ledger — 같은 sha 를 다른 경로에서 다시 보면 경로만 갱신, 상태 유지", () => {
  const l = mem();
  l.discover({ sha256: SHA(1), path: "/a/x.pdf", size: 10, mtime: T0, queue: "live" }, T0);
  l.markDone(SHA(1), "doc-1", T0);
  const state = l.discover(
    { sha256: SHA(1), path: "/b/이름바꿈.pdf", size: 10, mtime: T0 + 5, queue: "live" },
    T0 + 5,
  );
  assertEquals(state, "done");
  const row = l.get(SHA(1))!;
  assertEquals(row.path, "/b/이름바꿈.pdf");
  assertEquals(row.mtime, T0 + 5);
  assertEquals(row.doc_id, "doc-1");
  l.close();
});

Deno.test("ledger — 백로그로 들어온 파일이 나중에 실시간으로 만져지면 live 로 승격", () => {
  const l = mem();
  l.discover({ sha256: SHA(2), path: "/a/y.pdf", size: 1, mtime: T0, queue: "backlog" }, T0);
  l.discover({ sha256: SHA(2), path: "/a/y.pdf", size: 1, mtime: T0, queue: "live" }, T0 + 1);
  assertEquals(l.get(SHA(2))!.queue, "live");
  // 반대 방향(live → backlog)은 강등하지 않는다.
  l.discover({ sha256: SHA(2), path: "/a/y.pdf", size: 1, mtime: T0, queue: "backlog" }, T0 + 2);
  assertEquals(l.get(SHA(2))!.queue, "live");
  l.close();
});

Deno.test("ledger — claimUnchecked 는 live 우선, 같은 큐면 최신순", () => {
  const l = mem();
  l.discover({ sha256: SHA(10), path: "/a/old.pdf", size: 1, mtime: T0 - 1000, queue: "backlog" }, T0);
  l.discover({ sha256: SHA(11), path: "/a/new.pdf", size: 1, mtime: T0, queue: "backlog" }, T0);
  l.discover({ sha256: SHA(12), path: "/a/live.pdf", size: 1, mtime: T0 - 9999, queue: "live" }, T0);
  const rows = l.claimUnchecked(10, T0);
  assertEquals(rows.map((r) => r.sha256), [SHA(12), SHA(11), SHA(10)]);
  l.close();
});

Deno.test("ledger — markPrechecked 뒤에야 업로드 대상이 된다", () => {
  const l = mem();
  l.discover({ sha256: SHA(3), path: "/a/z.pdf", size: 1, mtime: T0, queue: "live" }, T0);
  assertEquals(l.claimUpload("live", 5, T0).length, 0);
  l.markPrechecked([SHA(3)], T0);
  const claimed = l.claimUpload("live", 5, T0);
  assertEquals(claimed.length, 1);
  assertEquals(l.get(SHA(3))!.state, "uploading");
  // 이미 uploading 인 행은 다시 잡히지 않는다 — 중복 업로드 방지.
  assertEquals(l.claimUpload("live", 5, T0).length, 0);
  l.close();
});

Deno.test("ledger — 재시작하면 uploading 은 pending 으로 되돌아온다", () => {
  const l = mem();
  l.discover({ sha256: SHA(4), path: "/a/a.pdf", size: 1, mtime: T0, queue: "live" }, T0);
  l.markPrechecked([SHA(4)], T0);
  l.claimUpload("live", 5, T0);
  assertEquals(l.resetInFlight(T0 + 10), 1);
  assertEquals(l.get(SHA(4))!.state, "pending");
  // precheck 결과는 버리지 않는다 — 다시 물어볼 필요가 없다.
  assertEquals(l.get(SHA(4))!.precheck_done, 1);
  l.close();
});

Deno.test("ledger — 재시도는 attempts 를 올리고 next_attempt_at 을 민다", () => {
  const l = mem();
  l.discover({ sha256: SHA(5), path: "/a/b.pdf", size: 1, mtime: T0, queue: "live" }, T0);
  l.markPrechecked([SHA(5)], T0);
  l.claimUpload("live", 5, T0);
  l.markRetry(SHA(5), "429 분당 상한", T0 + 5000, T0);
  const row = l.get(SHA(5))!;
  assertEquals(row.state, "pending");
  assertEquals(row.attempts, 1);
  assertEquals(row.next_attempt_at, T0 + 5000);
  // 아직 시간이 안 됐으면 안 잡힌다.
  assertEquals(l.claimUpload("live", 5, T0 + 4999).length, 0);
  assertEquals(l.claimUpload("live", 5, T0 + 5000).length, 1);
  l.close();
});

Deno.test("ledger — 제외·한도대기·실패는 사유가 남는다", () => {
  const l = mem();
  l.discover({ sha256: SHA(6), path: "/a/c.exe", size: 1, mtime: T0, queue: "live" }, T0);
  l.markExcluded(SHA(6), "ext", "허용되지 않는 확장자: .exe", T0);
  l.discover({ sha256: SHA(7), path: "/a/d.pdf", size: 1, mtime: T0, queue: "live" }, T0);
  l.markQuotaWait(SHA(7), "저장 용량 초과 (900/1000)", T0 + 21_600_000, T0);
  l.discover({ sha256: SHA(8), path: "/a/e.pdf", size: 1, mtime: T0, queue: "live" }, T0);
  l.markFailed(SHA(8), "서버 처리 실패: 파서 오류", T0);

  assertEquals(l.counts(), {
    pending: 0,
    uploading: 0,
    registered: 0,
    done: 0,
    failed: 1,
    excluded: 1,
    quota_wait: 1,
  });
  const fails = l.recentProblems(5);
  assertEquals(fails.length, 3);
  assertEquals(fails.every((f) => (f.last_error ?? "").length > 0), true);
  // 한도대기는 시간이 되면 다시 업로드 대기로 돌아온다.
  assertEquals(l.dueQuotaWait(T0 + 21_600_000).length, 1);
  assertEquals(l.dueQuotaWait(T0 + 100).length, 0);
  l.close();
});

Deno.test("ledger — registered 는 폴링 대상, 완료되면 빠진다", () => {
  const l = mem();
  l.discover({ sha256: SHA(9), path: "/a/f.pdf", size: 1, mtime: T0, queue: "live" }, T0);
  l.markPrechecked([SHA(9)], T0);
  l.claimUpload("live", 5, T0);
  l.markRegistered(SHA(9), "doc-9", "job-9", T0 + 1000, T0);
  assertEquals(l.duePolls(50, T0 + 1000).map((r) => r.doc_id), ["doc-9"]);
  assertEquals(l.duePolls(50, T0 + 999).length, 0);
  l.markDone(SHA(9), "doc-9", T0 + 2000);
  assertEquals(l.duePolls(50, T0 + 9999).length, 0);
  l.close();
});

Deno.test("ledger — events 와 meta", () => {
  const l = mem();
  l.event("info", "시작", T0);
  l.event("error", "401", T0 + 1);
  assertEquals(l.recentEvents(10).map((e) => e.msg), ["401", "시작"]);
  assertEquals(l.getMeta("last_contact"), null);
  l.setMeta("last_contact", "2026-09-15T00:00:00Z");
  assertEquals(l.getMeta("last_contact"), "2026-09-15T00:00:00Z");
  l.close();
});

Deno.test("ledger — 손상된 파일은 .bak 으로 밀어내고 새로 연다", async () => {
  const dir = await Deno.makeTempDir({ prefix: "jetrag-led-" });
  const path = `${dir}/ledger.sqlite`;
  await Deno.writeTextFile(path, "이건 SQLite 파일이 아니다");
  const l = Ledger.open(path);
  assertEquals(l.counts().pending, 0);
  l.close();
  assertEquals((await Deno.stat(`${path}.bak`)).isFile, true);
});
