import { assertEquals } from "@std/assert";
import { kakaoCandidates, parseArgs, renderStatus } from "./cli.ts";

Deno.test("cli — 인자 파싱", () => {
  assertEquals(parseArgs(["run"]), { command: "run", flags: {}, positionals: [] });
  assertEquals(parseArgs(["init", "--yes"]), { command: "init", flags: { yes: true }, positionals: [] });
  assertEquals(parseArgs(["logs", "--lines", "50"]), {
    command: "logs",
    flags: { lines: "50" },
    positionals: [],
  });
  assertEquals(parseArgs(["logs", "--lines=50"]), {
    command: "logs",
    flags: { lines: "50" },
    positionals: [],
  });
  assertEquals(parseArgs([]), { command: "help", flags: {}, positionals: [] });
  assertEquals(parseArgs(["--help"]), { command: "help", flags: { help: true }, positionals: [] });
  assertEquals(parseArgs(["init", "-y"]), { command: "init", flags: { yes: true }, positionals: [] });
});

Deno.test("cli — 카톡 폴더는 '있는 것만' 후보로 올린다 (경로를 가정하지 않는다)", () => {
  // 문서값을 믿지 않는다. 존재하는 폴더만 후보가 되고, 하나도 없으면 사용자가 직접 넣는다.
  const exists = (p: string) => p.endsWith("카카오톡 받은 파일");
  const mac = kakaoCandidates("darwin", "/Users/x", exists);
  assertEquals(mac, ["/Users/x/Documents/카카오톡 받은 파일"]);
  assertEquals(kakaoCandidates("darwin", "/Users/x", () => false), []);
  const win = kakaoCandidates("windows", "C:\\Users\\x", (p) => p.includes("카카오톡"));
  assertEquals(win, ["C:\\Users\\x\\Documents\\카카오톡 받은 파일"]);
});

Deno.test("cli — status 출력에 상태별 개수와 최근 실패 사유가 있다", () => {
  const out = renderStatus({
    counts: { pending: 2, uploading: 1, registered: 3, done: 120, failed: 1, excluded: 4, quota_wait: 0 },
    problems: [
      {
        path: "/a/큰파일.pdf",
        state: "excluded",
        last_error: "[too_large] 50MB 초과 (61.2MB)",
        updated_at: 1_700_000_000_000,
      },
      {
        path: "/a/깨진.hwp",
        state: "failed",
        last_error: "서버 처리 실패(extract): 파서 오류",
        updated_at: 1_700_000_000_000,
      },
    ],
    lastContact: "2026-09-15T01:02:03.000Z",
    watchDirs: ["/Users/x/Downloads"],
  });
  assertEquals(out.includes("완료 120"), true);
  assertEquals(out.includes("제외 4"), true);
  assertEquals(out.includes("50MB 초과"), true);
  assertEquals(out.includes("파서 오류"), true);
  assertEquals(out.includes("/Users/x/Downloads"), true);
  assertEquals(out.includes("2026-09-15T01:02:03.000Z"), true);
});

Deno.test("cli — 서버 통신 기록이 없으면 '없음'", () => {
  const out = renderStatus({
    counts: { pending: 0, uploading: 0, registered: 0, done: 0, failed: 0, excluded: 0, quota_wait: 0 },
    problems: [],
    lastContact: null,
    watchDirs: [],
  });
  assertEquals(out.includes("없음"), true);
});
