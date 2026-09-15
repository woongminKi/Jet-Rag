/**
 * E2E — 임시 폴더에 샘플을 넣고 서버 문서 상태까지 확인한다 (스펙 §7 E2E, §8 7단계).
 *
 *   deno run -A agent/scripts/e2e_samples.ts <session_access_token> [--keep]
 *
 * 하는 일:
 *   ① 세션으로 기기 토큰 발급 (`POST /me/devices`)
 *   ② 임시 감시 폴더에 허용 11종 + 제외 대조군 2종을 넣는다
 *   ③ 에이전트를 틱 루프로 돌린다 (백로그 아님 — 전부 실시간 큐)
 *   ④ 원장 상태와 서버 응답을 대조해 찍는다
 *   ⑤ 기기 토큰 폐기 후 업로드가 `auth` 로 막히는지 확인한다
 *
 * **왜 원장과 서버를 둘 다 보는가**: 독립된 두 신호가 일치할 때만 판정한다(워크스페이스 규칙).
 * 원장만 보면 "에이전트가 그렇게 믿는다"일 뿐이다.
 *
 * ## 플랜 원안에서 고친 것 (2026-09-15, 전부 서버 소스를 읽고 확정 — 네트워크 호출 0)
 *
 * 1. **파일을 감시 시작 *뒤에* 넣는다.** 원안은 `skipBacklog: true` + `backlog_days: 0` 인데
 *    파일을 `runAgent` 앞에서 만들었다. 그러면 백로그 스캔도 안 돌고 watchFs 이벤트도 안 나서
 *    원장이 끝까지 비어 있다. 실시간 큐로 넣으려면 감시가 붙은 **뒤에** 써야 한다.
 * 2. **`/documents/active` 는 `completed` 를 뺀다** (`active.ts`: "completed/cancelled 가 latest 면
 *    자연 제외된다"). 그래서 전부 성공하면 `items` 가 0건이고, 원안대로면 그게 실패처럼 보인다.
 *    원장이 아는 `doc_id` 로 `batch-status` 를 한 번 더 물어 **완료까지 보이는 신호**를 만든다.
 * 3. **`items[].title` 이 아니라 `file_name`** 이다(`active.ts`). 원안대로면 제목이 늘 빈칸이다.
 * 4. **제외 대조군은 원장에 `excluded` 로 안 남는다.** `run.ts#ingest` 는 게이트 탈락 시
 *    로그만 남기고 원장에 넣지 않는다. 원안 기대표의 "무시.exe → excluded" 는 틀렸다.
 *    둘 다 "원장에 행 없음"이 정답이고, `.exe` 만 `제외(ext)` 로그가 남는다.
 * 5. **다 끝나면 예산을 안 기다리고 끊는다.** 원안은 25분을 꽉 채운다.
 */

import { ApiClient, ApiError } from "../src/client.ts";
import { sha256File } from "../src/hash.ts";
import { type FileState, Ledger } from "../src/ledger.ts";
import { FileLogger, tailLines } from "../src/log.ts";
import { FatalAuthError, runAgent } from "../src/run.ts";

const [session, ...rest] = Deno.args;
if (!session) {
  console.error("사용법: deno run -A agent/scripts/e2e_samples.ts <session_access_token> [--keep]");
  Deno.exit(1);
}
const KEEP = rest.includes("--keep");
const API = Deno.env.get("JETRAG_API_BASE") ?? "https://jetrag-api.woong-s.com";
const SH = { Authorization: `Bearer ${session}`, "User-Agent": "JetRag-E2E/0.1" };
// `.pathname` 은 퍼센트 인코딩된다 — 레포가 공백 있는 경로에 있으면 파일을 못 찾는다.
const REPO = decodeURIComponent(new URL("../../", import.meta.url).pathname);

// ── ① 기기 토큰 발급 ─────────────────────────────────────────────
const devRes = await fetch(`${API}/me/devices`, {
  method: "POST",
  headers: { ...SH, "content-type": "application/json" },
  body: JSON.stringify({ name: `e2e-${new Date().toISOString().slice(0, 19)}` }),
});
if (devRes.status !== 201) {
  console.error(`기기 토큰 발급 실패 ${devRes.status}: ${await devRes.text()}`);
  Deno.exit(1);
}
const device = await devRes.json() as { id: string; token: string; token_prefix: string };
console.log(`기기 토큰 발급: ${device.token_prefix}… (id ${device.id})`);

// ── ② 샘플 준비 ──────────────────────────────────────────────────
const watchDir = Deno.env.get("E2E_WATCH_DIR") ??
  await Deno.makeTempDir({ prefix: "jetrag-e2e-watch-" });
await Deno.mkdir(watchDir, { recursive: true });
const home = await Deno.makeTempDir({ prefix: "jetrag-e2e-home-" });

/** 레포에 실물이 있는 것들. 없으면 그 확장자는 "미검증"으로 남는다. */
const FROM_REPO: [string, string][] = [
  ["law sample3.pdf", "샘플.pdf"],
  ["law sample2.hwp", "샘플.hwp"],
  ["직제_규정(2024.4.30.개정).hwpx", "샘플.hwpx"],
  ["승인글 템플릿1.docx", "샘플.docx"],
  ["브랜딩_스튜디오앤드오어.pptx", "샘플.pptx"],
  ["web/public/icon-192.png", "샘플.png"],
];

/** 제외 대조군 — 둘 다 원장에 행이 없어야 한다(§4 참고). */
const CONTROLS = ["무시.exe", "받는중.pdf.crdownload"];

const placed: string[] = [];
const missing: string[] = [];

/** 감시가 붙은 뒤에 실제로 파일을 만든다 — 이게 실시간 큐로 들어가는 유일한 경로다. */
async function placeSamples(): Promise<void> {
  for (const [src, dst] of FROM_REPO) {
    try {
      await Deno.copyFile(`${REPO}${src}`, `${watchDir}/${dst}`);
      placed.push(dst);
    } catch {
      missing.push(src);
    }
  }
  await Deno.writeTextFile(`${watchDir}/샘플.txt`, "계약 기간은 2026년 9월 15일부터 1년간으로 한다.\n");
  await Deno.writeTextFile(`${watchDir}/샘플.md`, "# 회의록\n\n- 결정: 에이전트 E2E 진행\n");
  placed.push("샘플.txt", "샘플.md");

  // jpg·jpeg·heic 는 맥의 sips 로 png 에서 변환한다. 다른 OS 면 미검증으로 남긴다.
  if (Deno.build.os === "darwin" && placed.includes("샘플.png")) {
    const conv: [string, string][] = [["jpeg", "샘플.jpg"], ["jpeg", "샘플2.jpeg"], ["heic", "샘플.heic"]];
    for (const [fmt, name] of conv) {
      const r = await new Deno.Command("sips", {
        args: ["-s", "format", fmt, `${watchDir}/샘플.png`, "--out", `${watchDir}/${name}`],
        stdout: "null",
        stderr: "null",
      }).output();
      if (r.code === 0) placed.push(name);
      else missing.push(name);
    }
  } else {
    missing.push("샘플.jpg", "샘플2.jpeg", "샘플.heic (sips 없음)");
  }

  await Deno.writeTextFile(`${watchDir}/무시.exe`, "MZ  ");
  await Deno.writeTextFile(`${watchDir}/받는중.pdf.crdownload`, "%PDF-1.7\n");
}

// ── ③ 에이전트 실행 ──────────────────────────────────────────────
const cfg = { api_base: API, device_token: device.token, watch_dirs: [watchDir], backlog_days: 0 };
const ledger = Ledger.open(`${home}/ledger.sqlite`);
const logger = new FileLogger(`${home}/agent.log`);
const client = new ApiClient({ apiBase: API, token: device.token });
const ac = new AbortController();
// 서버 큐가 시간당 ~5건이라 13건이면 최악 2.5시간이다. 기본 25분에서 끊고 중간 상태를 찍는다.
const budgetMs = Number(Deno.env.get("E2E_BUDGET_MS") ?? 25 * 60 * 1000);
const timer = setTimeout(() => ac.abort(), budgetMs);
const started = Date.now();

const ACTIVE_STATES: FileState[] = ["pending", "uploading", "registered"];
/** 다 끝났으면 예산을 기다리지 않고 끊는다. 아무것도 발견 못 했으면 그건 그것대로 신호다. */
const settleWatch = setInterval(() => {
  const c = ledger.counts();
  const discovered = Object.values(c).reduce((n, v) => n + v, 0);
  const active = ACTIVE_STATES.reduce((n, s) => n + c[s], 0);
  if (discovered > 0 && active === 0) ac.abort();
}, 5000);

// 감시가 붙을 시간을 준 뒤에 파일을 만든다. 순서를 뒤집으면 이벤트가 안 나서 원장이 빈 채로 끝난다.
const placing = (async () => {
  await new Promise((r) => setTimeout(r, 2000));
  await placeSamples();
  console.log(`\n투입: ${placed.length}개 → ${watchDir}`);
  if (missing.length > 0) console.log(`미검증(파일 없음): ${missing.join(", ")}`);
  console.log(`제외 대조군: ${CONTROLS.join(", ")} (둘 다 원장에 행이 없어야 정상)`);
})();

let fatal: string | null = null;
try {
  await runAgent({ cfg, ledger, client, logger, signal: ac.signal, tickMs: 3000, skipBacklog: true });
} catch (e) {
  fatal = e instanceof FatalAuthError ? `FatalAuthError: ${e.message}` : `${(e as Error).message}`;
  console.error(`\n루프 중단: ${fatal}`);
}
clearTimeout(timer);
clearInterval(settleWatch);
await placing;

// ── ④ 원장 ↔ 서버 대조 ───────────────────────────────────────────
const elapsed = ((Date.now() - started) / 1000).toFixed(0);
console.log(`\n원장 상태 (${elapsed}초 경과):`);
console.log(ledger.counts());

/** 파일별 원장 행 — 기대표와 1:1 로 대조하려면 counts 만으로는 부족하다. */
const rows: { name: string; state: string; docId: string | null; error: string | null }[] = [];
for (const name of placed) {
  let sha: string;
  try {
    sha = await sha256File(`${watchDir}/${name}`);
  } catch {
    rows.push({ name, state: "(파일 없음)", docId: null, error: null });
    continue;
  }
  const row = ledger.get(sha);
  rows.push({
    name,
    state: row?.state ?? "(원장에 없음)",
    docId: row?.doc_id ?? null,
    error: row?.last_error ?? null,
  });
}

// 서버 잡 상태를 doc_id 로 한 번 더 — active 는 completed 를 빼기 때문에 이게 있어야 완료가 보인다.
const docIds = rows.map((r) => r.docId).filter((d): d is string => d !== null && d !== "");
const jobs = new Map<string, string>();
if (docIds.length > 0) {
  try {
    for (const [docId, job] of await client.batchStatus(docIds)) {
      jobs.set(docId, job === null ? "(잡 없음)" : `${job.status}/${job.current_stage ?? "-"}`);
    }
  } catch (e) {
    console.error(`batch-status 조회 실패: ${e instanceof ApiError ? e.kind : ""} ${(e as Error).message}`);
  }
}

console.log(`\n파일별 대조 (원장 ↔ 서버):`);
console.log(`  ${"파일".padEnd(16)} ${"원장".padEnd(12)} ${"doc_id".padEnd(10)} 서버 잡`);
for (const r of rows) {
  const dv = r.docId === null || r.docId === "" ? "-" : r.docId.slice(0, 8);
  const jv = r.docId === null ? "-" : (jobs.get(r.docId) ?? "(응답에 없음)");
  console.log(
    `  ${r.name.padEnd(16)} ${r.state.padEnd(12)} ${dv.padEnd(10)} ${jv}${r.error ? ` — ${r.error}` : ""}`,
  );
}

// 제외 대조군 — 원장에 행이 없고, .exe 만 제외 로그가 남는 게 정답이다.
const logLines = await tailLines(`${home}/agent.log`, 500);
console.log(`\n제외 대조군:`);
for (const name of CONTROLS) {
  let inLedger = false;
  try {
    inLedger = ledger.get(await sha256File(`${watchDir}/${name}`)) !== null;
  } catch { /* 파일이 없으면 원장에도 없다 */ }
  const logged = logLines.some((l) => l.includes("제외(") && l.includes(name));
  console.log(
    `  ${name.padEnd(24)} 원장행 ${inLedger ? "있음 ← 비정상" : "없음 ✓"}  제외로그 ${
      logged ? "있음" : "없음"
    }`,
  );
}

for (const r of ledger.recentProblems(100)) {
  console.log(`  [${r.state}] ${r.path.slice(watchDir.length + 1)} — ${r.last_error ?? ""}`);
}

const active = await fetch(`${API}/documents/active?hours=1`, { headers: SH }).then((r) => r.json()) as {
  items: { doc_id: string; file_name?: string; job?: { status: string; current_stage: string | null } }[];
};
// 여기 0건인 게 실패가 아니다 — active 는 진행 중·실패만 담고 completed 는 빼기 때문이다.
console.log(`\n서버 active(진행 중·실패만, completed 제외): ${active.items.length}건`);
for (const it of active.items) {
  console.log(
    `  ${it.doc_id.slice(0, 8)} ${it.job?.status ?? "-"} ${it.job?.current_stage ?? ""} ${
      it.file_name ?? ""
    }`,
  );
}

// ── ⑤ 폐기 후 401 확인 ───────────────────────────────────────────
const del = await fetch(`${API}/me/devices/${device.id}`, { method: "DELETE", headers: SH });
console.log(`\n기기 폐기: ${del.status}`);
const after = await client.upload({ name: "폐기후.txt", bytes: new TextEncoder().encode("x") });
console.log(`폐기 후 업로드 결과: ${after.kind} (auth 여야 정상)`);

ledger.close();
await logger.close();
if (KEEP) {
  console.log(`\n남김: ${watchDir}, ${home} (로그: ${home}/agent.log)`);
} else {
  await Deno.remove(watchDir, { recursive: true });
  await Deno.remove(home, { recursive: true });
  console.log("\n임시 폴더 정리 완료 (--keep 으로 남길 수 있습니다).");
}
if (fatal !== null) Deno.exit(2);
