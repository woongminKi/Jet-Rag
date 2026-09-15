/**
 * 자동 시작 등록 (스펙 §5.4).
 *
 * | OS | 방법 |
 * |---|---|
 * | 맥 | `~/Library/LaunchAgents/com.jetrag.agent.plist` + `launchctl bootstrap gui/<uid>` |
 * | 윈도우 | `schtasks /Create /SC ONLOGON` |
 *
 * ## 문자열 생성과 실행을 나눈다
 * plist·명령 인자는 순수 함수라 테스트할 수 있고, 실행은 얇게 감싼다. plist 를 눈으로
 * 검수하지 않고 launchd 에 넘겼다가 XML 이 깨져 조용히 안 뜨는 게 가장 흔한 실패다.
 */

export const LAUNCHD_LABEL = "com.jetrag.agent";
export const SCHTASKS_NAME = "JetRagAgent";

export function xmlEscape(s: string): string {
  return s
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

export function launchdPlistPath(homeDir: string): string {
  return `${homeDir}/Library/LaunchAgents/${LAUNCHD_LABEL}.plist`;
}

export function launchdPlist(binPath: string, jetragHome: string): string {
  return `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LAUNCHD_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${xmlEscape(binPath)}</string>
    <string>run</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ProcessType</key>
  <string>Background</string>
  <key>StandardOutPath</key>
  <string>${xmlEscape(jetragHome)}/launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>${xmlEscape(jetragHome)}/launchd.err.log</string>
</dict>
</plist>
`;
}

export function schtasksCreateArgs(binPath: string): string[] {
  // `/TR` 은 한 문자열이다. 경로에 공백이 있으므로 따옴표를 직접 넣는다.
  return [
    "/Create",
    "/TN",
    SCHTASKS_NAME,
    "/TR",
    `"${binPath}" run`,
    "/SC",
    "ONLOGON",
    "/RL",
    "LIMITED",
    "/F",
  ];
}

export function schtasksDeleteArgs(): string[] {
  return ["/Delete", "/TN", SCHTASKS_NAME, "/F"];
}

async function run(cmd: string, args: string[]): Promise<{ code: number; out: string }> {
  const p = new Deno.Command(cmd, { args, stdout: "piped", stderr: "piped" });
  const r = await p.output();
  const out = new TextDecoder().decode(r.stdout) + new TextDecoder().decode(r.stderr);
  return { code: r.code, out: out.trim() };
}

export interface InstallEnv {
  os: string;
  homeDir: string;
  jetragHome: string;
  binPath: string;
}

export async function install(env: InstallEnv): Promise<string[]> {
  const log: string[] = [];
  if (env.os === "darwin") {
    const path = launchdPlistPath(env.homeDir);
    await Deno.mkdir(path.slice(0, path.lastIndexOf("/")), { recursive: true });
    await Deno.writeTextFile(path, launchdPlist(env.binPath, env.jetragHome));
    log.push(`plist 작성: ${path}`);
    const uid = Deno.uid() ?? 501;
    // 이미 등록돼 있으면 bootstrap 이 실패한다 — 먼저 걷어낸다.
    await run("launchctl", ["bootout", `gui/${uid}/${LAUNCHD_LABEL}`]);
    const r = await run("launchctl", ["bootstrap", `gui/${uid}`, path]);
    if (r.code !== 0) throw new Error(`launchctl bootstrap 실패 (${r.code}): ${r.out}`);
    log.push(`launchd 등록 완료 (gui/${uid}/${LAUNCHD_LABEL})`);
    log.push("맥은 첫 실행 때 ~/Downloads 접근 권한(TCC)을 물을 수 있습니다. 허용해야 감시가 됩니다.");
    return log;
  }
  if (env.os === "windows") {
    const r = await run("schtasks", schtasksCreateArgs(env.binPath));
    if (r.code !== 0) throw new Error(`schtasks /Create 실패 (${r.code}): ${r.out}`);
    log.push(`작업 스케줄러 등록 완료 (${SCHTASKS_NAME}, 로그온 시)`);
    return log;
  }
  throw new Error(
    `자동 시작 등록은 맥·윈도우만 지원합니다 (현재: ${env.os}). \`jetrag-agent run\` 을 직접 실행하세요.`,
  );
}

export async function uninstall(env: InstallEnv): Promise<string[]> {
  const log: string[] = [];
  if (env.os === "darwin") {
    const uid = Deno.uid() ?? 501;
    await run("launchctl", ["bootout", `gui/${uid}/${LAUNCHD_LABEL}`]);
    const path = launchdPlistPath(env.homeDir);
    try {
      await Deno.remove(path);
      log.push(`plist 삭제: ${path}`);
    } catch {
      log.push(`plist 없음: ${path}`);
    }
    log.push("launchd 등록 해제 완료");
    return log;
  }
  if (env.os === "windows") {
    const r = await run("schtasks", schtasksDeleteArgs());
    log.push(r.code === 0 ? "작업 스케줄러 등록 해제 완료" : `이미 없거나 해제 실패: ${r.out}`);
    return log;
  }
  throw new Error(`맥·윈도우만 지원합니다 (현재: ${env.os}).`);
}
