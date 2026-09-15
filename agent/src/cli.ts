#!/usr/bin/env -S deno run --allow-read --allow-write --allow-net --allow-env --allow-run --allow-sys
/**
 * `jetrag-agent` CLI — `init` `run` `status` `logs` `install` `uninstall`.
 *
 * ## 카톡 폴더를 **가정하지 않는다**
 * 문서에 적힌 기본 경로를 그대로 쓰면 틀린 폴더를 감시하고 사용자는 "왜 안 올라가지"만
 * 남는다. 후보 중 **실제로 존재하는 것만** 보여주고, 하나도 없으면 직접 입력받는다.
 */

import { ApiClient } from "./client.ts";
import {
  type AgentConfig,
  agentHome,
  configPath,
  DEFAULT_API_BASE,
  DEFAULT_BACKLOG_DAYS,
  ledgerPath,
  loadConfig,
  logPath,
  saveConfig,
} from "./config.ts";
import { type FileState, Ledger } from "./ledger.ts";
import { FileLogger, tailLines } from "./log.ts";
import { install, uninstall } from "./install.ts";
import { formatBytes, scanBacklog, summarize } from "./scanner.ts";
import { sha256File } from "./hash.ts";
import { FatalAuthError, runAgent } from "./run.ts";
import { VERSION } from "./version.ts";

export interface ParsedArgs {
  command: string;
  flags: Record<string, string | boolean>;
  positionals: string[];
}

export function parseArgs(argv: string[]): ParsedArgs {
  const flags: Record<string, string | boolean> = {};
  const positionals: string[] = [];
  let command = "";
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "-y") {
      flags.yes = true;
    } else if (a.startsWith("--")) {
      const eq = a.indexOf("=");
      if (eq > 0) {
        flags[a.slice(2, eq)] = a.slice(eq + 1);
      } else {
        const key = a.slice(2);
        const next = argv[i + 1];
        if (next !== undefined && !next.startsWith("-")) {
          flags[key] = next;
          i++;
        } else {
          flags[key] = true;
        }
      }
    } else if (command === "") {
      command = a;
    } else {
      positionals.push(a);
    }
  }
  if (command === "") command = "help";
  return { command, flags, positionals };
}

/** 카톡 저장 폴더 후보 — **존재하는 것만** 돌려준다. 경로를 가정하지 않기 위한 장치다. */
export function kakaoCandidates(
  os: string,
  homeDir: string,
  exists: (p: string) => boolean = defaultExists,
): string[] {
  const list = os === "darwin"
    ? [
      `${homeDir}/Documents/카카오톡 받은 파일`,
      `${homeDir}/Downloads/KakaoTalk`,
      `${homeDir}/Library/Containers/com.kakao.KakaoTalkMac/Data/Documents/KakaoTalk Downloads`,
    ]
    : os === "windows"
    ? [
      `${homeDir}\\Documents\\카카오톡 받은 파일`,
      `${homeDir}\\Downloads\\KakaoTalk`,
    ]
    : [];
  return list.filter(exists);
}

function defaultExists(p: string): boolean {
  try {
    return Deno.statSync(p).isDirectory;
  } catch {
    return false;
  }
}

export interface StatusView {
  counts: Record<FileState, number>;
  problems: { path: string; state: string; last_error: string | null; updated_at: number }[];
  lastContact: string | null;
  watchDirs: string[];
}

export function renderStatus(v: StatusView): string {
  const c = v.counts;
  const lines = [
    `Jet-Rag 에이전트 ${VERSION}`,
    ``,
    `감시 폴더: ${v.watchDirs.length === 0 ? "(없음)" : v.watchDirs.join(", ")}`,
    `마지막 서버 통신: ${v.lastContact ?? "없음"}`,
    ``,
    `대기 ${c.pending}  업로드중 ${c.uploading}  처리중 ${c.registered}  완료 ${c.done}`,
    `실패 ${c.failed}  제외 ${c.excluded}  한도대기 ${c.quota_wait}`,
  ];
  if (v.problems.length > 0) {
    lines.push("", "최근 문제:");
    for (const p of v.problems) {
      lines.push(`  [${p.state}] ${p.path}`);
      lines.push(`      ${p.last_error ?? "(사유 없음)"}`);
    }
  }
  return lines.join("\n");
}

const HELP = `Jet-Rag PC 에이전트 ${VERSION}

사용법:
  jetrag-agent init [--yes]        설정 만들기 (토큰·폴더·백로그)
  jetrag-agent run                 감시 시작 (포그라운드)
  jetrag-agent status              상태·최근 문제
  jetrag-agent logs [--lines 50]   로그 보기
  jetrag-agent install             로그인 시 자동 시작 등록
  jetrag-agent uninstall           자동 시작 해제
`;

async function cmdInit(flags: Record<string, string | boolean>): Promise<number> {
  const home = agentHome();
  const homeDir = Deno.env.get("HOME") ?? Deno.env.get("USERPROFILE") ?? "";
  const yes = flags.yes === true;

  console.log("Jet-Rag 에이전트 설정을 만듭니다.\n");
  const token = typeof flags.token === "string"
    ? flags.token
    : (prompt("기기 토큰 (웹 설정 → 연결된 기기에서 발급, jrd_...):") ?? "").trim();
  const apiBase = typeof flags["api-base"] === "string" ? flags["api-base"] : DEFAULT_API_BASE;

  const downloads = Deno.build.os === "windows" ? `${homeDir}\\Downloads` : `${homeDir}/Downloads`;
  const dirs = [downloads, ...kakaoCandidates(Deno.build.os, homeDir)];
  console.log("\n감시할 폴더 후보:");
  for (const d of dirs) console.log(`  - ${d}`);
  if (kakaoCandidates(Deno.build.os, homeDir).length === 0) {
    console.log(
      "  (카카오톡 저장 폴더를 찾지 못했습니다. 카카오톡 → 설정 → 파일 저장 경로를 확인해 직접 넣으세요.)",
    );
  }
  const extra = yes ? "" : (prompt("추가할 폴더 (쉼표로 구분, 없으면 Enter):") ?? "").trim();
  const watchDirs = [...dirs, ...extra.split(",").map((s) => s.trim()).filter((s) => s !== "")];

  const daysRaw = yes
    ? String(DEFAULT_BACKLOG_DAYS)
    : (prompt(`백로그 기간(일, 0이면 안 함) [${DEFAULT_BACKLOG_DAYS}]:`) ?? "").trim();
  const backlogDays = daysRaw === "" ? DEFAULT_BACKLOG_DAYS : Number(daysRaw);

  const cfg: AgentConfig = {
    api_base: apiBase,
    device_token: token,
    watch_dirs: watchDirs,
    backlog_days: Number.isFinite(backlogDays) ? backlogDays : DEFAULT_BACKLOG_DAYS,
  };
  try {
    await saveConfig(cfg, home);
  } catch (e) {
    console.error(`\n${(e as Error).message}`);
    return 1;
  }
  console.log(`\n설정 저장: ${configPath(home)}`);

  if (cfg.backlog_days > 0) {
    const found = await scanBacklog(cfg.watch_dirs, { days: cfg.backlog_days });
    const s = summarize(found);
    console.log(`\n최근 ${cfg.backlog_days}일 백로그: 대상 ${s.count}개, ${formatBytes(s.bytes)}.`);
    const go = yes || (confirm("지금 원장에 넣을까요? (후순위로 천천히 올라갑니다)") === true);
    if (go) {
      const ledger = Ledger.open(ledgerPath(home));
      let n = 0;
      for (const c of found) {
        const sha = await sha256File(c.path);
        ledger.discover(
          { sha256: sha, path: c.path, size: c.size, mtime: c.mtime, queue: "backlog" },
          Date.now(),
        );
        n++;
      }
      ledger.setMeta("backlog_scanned_at", String(Date.now()));
      ledger.close();
      console.log(`${n}개를 백로그 큐에 넣었습니다.`);
    }
  }
  console.log(
    "\n다음: `jetrag-agent install` 로 자동 시작을 등록하거나 `jetrag-agent run` 으로 바로 시작하세요.",
  );
  return 0;
}

async function cmdRun(): Promise<number> {
  const home = agentHome();
  const cfg = await loadConfig(home);
  const ledger = Ledger.open(ledgerPath(home));
  const logger = new FileLogger(logPath(home));
  const client = new ApiClient({ apiBase: cfg.api_base, token: cfg.device_token });
  const ac = new AbortController();
  for (const sig of ["SIGINT", "SIGTERM"] as const) {
    try {
      Deno.addSignalListener(sig, () => ac.abort());
    } catch {
      // 윈도우는 SIGTERM 이 없다.
    }
  }
  try {
    await runAgent({ cfg, ledger, client, logger, signal: ac.signal, skipBacklog: false });
    return 0;
  } catch (e) {
    if (e instanceof FatalAuthError) {
      console.error(`중단: ${e.message}`);
      return 2;
    }
    throw e;
  } finally {
    ledger.close();
    await logger.close();
  }
}

async function cmdStatus(): Promise<number> {
  const home = agentHome();
  const cfg = await loadConfig(home);
  const ledger = Ledger.open(ledgerPath(home));
  try {
    console.log(renderStatus({
      counts: ledger.counts(),
      problems: ledger.recentProblems(5).map((r) => ({
        path: r.path,
        state: r.state,
        last_error: r.last_error,
        updated_at: r.updated_at,
      })),
      lastContact: ledger.getMeta("last_contact"),
      watchDirs: cfg.watch_dirs,
    }));
    return 0;
  } finally {
    ledger.close();
  }
}

async function cmdLogs(flags: Record<string, string | boolean>): Promise<number> {
  const n = typeof flags.lines === "string" ? Number(flags.lines) : 50;
  const lines = await tailLines(logPath(agentHome()), Number.isFinite(n) ? n : 50);
  console.log(lines.length === 0 ? "(로그 없음)" : lines.join("\n"));
  return 0;
}

async function cmdInstall(remove: boolean): Promise<number> {
  const home = agentHome();
  const homeDir = Deno.env.get("HOME") ?? Deno.env.get("USERPROFILE") ?? "";
  const env = { os: Deno.build.os, homeDir, jetragHome: home, binPath: Deno.execPath() };
  try {
    const log = remove ? await uninstall(env) : await install(env);
    for (const l of log) console.log(l);
    return 0;
  } catch (e) {
    console.error((e as Error).message);
    return 1;
  }
}

export async function main(argv: string[]): Promise<number> {
  const { command, flags } = parseArgs(argv);
  // `--version` 은 CLI 관습이다. 플래그로 들어와도 version 명령과 같이 동작해야 한다
  // (안 그러면 도움말만 나와서 버전을 못 읽는다).
  if (command === "help" && flags.version === true) {
    console.log(VERSION);
    return 0;
  }
  switch (command) {
    case "init":
      return await cmdInit(flags);
    case "run":
      return await cmdRun();
    case "status":
      return await cmdStatus();
    case "logs":
      return await cmdLogs(flags);
    case "install":
      return await cmdInstall(false);
    case "uninstall":
      return await cmdInstall(true);
    case "version":
      console.log(VERSION);
      return 0;
    default:
      console.log(HELP);
      return command === "help" ? 0 : 1;
  }
}

if (import.meta.main) {
  try {
    Deno.exit(await main(Deno.args));
  } catch (e) {
    console.error((e as Error).message);
    Deno.exit(1);
  }
}
