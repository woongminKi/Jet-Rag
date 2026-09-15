/**
 * `~/.jetrag/config.json` — 에이전트의 유일한 설정 파일.
 *
 * ## 왜 검증을 따로 두는가
 * 설정이 잘못되면 상주 프로세스가 조용히 아무것도 안 하거나, 더 나쁘게는 잘못된 폴더를
 * 통째로 올린다. `validateConfig` 는 **항목별 사유를 전부 모아** 돌려준다 — 하나씩
 * 고치며 `init` 을 4번 다시 돌게 하지 않기 위해서다.
 *
 * ## 0600
 * `device_token` 은 그 자체로 업로드 권한이다. 맥·리눅스는 파일 권한으로 막고,
 * 윈도우는 `Deno.chmod` 가 없어 사용자 프로필 폴더의 기본 ACL 에 기댄다(README 에 명시).
 */

export interface AgentConfig {
  api_base: string;
  device_token: string;
  watch_dirs: string[];
  backlog_days: number;
}

export const DEFAULT_API_BASE = "https://jetrag-api.woong-s.com";
export const DEFAULT_BACKLOG_DAYS = 90;
const MAX_BACKLOG_DAYS = 3650;

/** 서버 `device_token.ts` 의 형식: `jrd_` + base64url 43자(32바이트). */
const TOKEN_RE = /^jrd_[A-Za-z0-9_-]{43}$/;
const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "::1"]);

interface EnvLike {
  get(key: string): string | undefined;
}

export function agentHome(env: EnvLike = Deno.env): string {
  const override = env.get("JETRAG_AGENT_HOME");
  if (override) return override.replace(/[\\/]+$/, "");
  const home = env.get("HOME") ?? env.get("USERPROFILE");
  if (!home) throw new Error("홈 디렉터리를 찾을 수 없습니다 (HOME/USERPROFILE 미설정).");
  return `${home.replace(/[\\/]+$/, "")}/.jetrag`;
}

export const configPath = (home: string = agentHome()) => `${home}/config.json`;
export const ledgerPath = (home: string = agentHome()) => `${home}/ledger.sqlite`;
export const logPath = (home: string = agentHome()) => `${home}/agent.log`;

/** 뒤 슬래시 제거. `/` 하나짜리 루트는 그대로 둔다. */
function trimTrailing(p: string): string {
  const t = p.replace(/[\\/]+$/, "");
  return t === "" ? p.slice(0, 1) : t;
}

function isAbsolute(p: string): boolean {
  return p.startsWith("/") || /^[A-Za-z]:[\\/]/.test(p) || p.startsWith("\\\\");
}

export function validateConfig(
  raw: unknown,
): { ok: true; config: AgentConfig } | { ok: false; errors: string[] } {
  const errors: string[] = [];
  const o = (raw ?? {}) as Record<string, unknown>;

  let apiBase = "";
  if (typeof o.api_base !== "string" || o.api_base.trim() === "") {
    errors.push("api_base: 문자열이어야 합니다.");
  } else {
    apiBase = trimTrailing(o.api_base.trim());
    let url: URL | null = null;
    try {
      url = new URL(apiBase);
    } catch {
      url = null;
    }
    if (!url || (url.protocol !== "https:" && url.protocol !== "http:")) {
      errors.push(`api_base: http(s) URL 이 아닙니다: ${o.api_base}`);
    } else if (url.protocol === "http:" && !LOCAL_HOSTS.has(url.hostname)) {
      // 기기 토큰이 평문으로 나간다. 로컬 테스트 외에는 막는다.
      errors.push("api_base: localhost 외에는 https 만 허용합니다.");
    }
  }

  const token = typeof o.device_token === "string" ? o.device_token.trim() : "";
  if (!TOKEN_RE.test(token)) {
    errors.push("device_token: `jrd_` + 43자 형식이어야 합니다. 설정 → 연결된 기기에서 발급하세요.");
  }

  let dirs: string[] = [];
  if (!Array.isArray(o.watch_dirs) || o.watch_dirs.length === 0) {
    errors.push("watch_dirs: 감시할 폴더가 최소 1개 필요합니다.");
  } else {
    const seen = new Set<string>();
    for (const d of o.watch_dirs) {
      if (typeof d !== "string" || d.trim() === "") {
        errors.push("watch_dirs: 항목은 비어 있지 않은 문자열이어야 합니다.");
        continue;
      }
      const norm = trimTrailing(d.trim());
      if (!isAbsolute(norm)) {
        errors.push(`watch_dirs: 절대 경로여야 합니다: ${d}`);
        continue;
      }
      if (seen.has(norm)) continue;
      seen.add(norm);
      dirs.push(norm);
    }
    if (dirs.length === 0 && errors.every((e) => !e.startsWith("watch_dirs"))) {
      errors.push("watch_dirs: 유효한 폴더가 없습니다.");
    }
    dirs = [...seen];
  }

  const days = o.backlog_days;
  if (typeof days !== "number" || !Number.isInteger(days) || days < 0 || days > MAX_BACKLOG_DAYS) {
    errors.push(`backlog_days: 0~${MAX_BACKLOG_DAYS} 의 정수여야 합니다.`);
  }

  if (errors.length > 0) return { ok: false, errors };
  return {
    ok: true,
    config: { api_base: apiBase, device_token: token, watch_dirs: dirs, backlog_days: days as number },
  };
}

export async function loadConfig(home: string = agentHome()): Promise<AgentConfig> {
  const path = configPath(home);
  let text: string;
  try {
    text = await Deno.readTextFile(path);
  } catch (e) {
    if (e instanceof Deno.errors.NotFound) {
      throw new Error(`설정이 없습니다: ${path}\n먼저 \`jetrag-agent init\` 을 실행하세요.`);
    }
    throw e;
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (e) {
    throw new Error(`config.json 을 읽을 수 없습니다 (${path}): ${(e as Error).message}`);
  }
  const r = validateConfig(parsed);
  if (!r.ok) throw new Error(`config.json 이 올바르지 않습니다:\n  - ${r.errors.join("\n  - ")}`);
  return r.config;
}

export async function saveConfig(cfg: AgentConfig, home: string = agentHome()): Promise<void> {
  const r = validateConfig(cfg);
  if (!r.ok) throw new Error(`저장 거부 — 설정이 올바르지 않습니다:\n  - ${r.errors.join("\n  - ")}`);
  await Deno.mkdir(home, { recursive: true });
  const path = configPath(home);
  const tmp = `${path}.tmp`;
  // 원자적 교체 — 쓰다 죽어도 기존 설정이 반쯤 덮이지 않는다.
  await Deno.writeTextFile(tmp, JSON.stringify(r.config, null, 2) + "\n");
  if (Deno.build.os !== "windows") await Deno.chmod(tmp, 0o600);
  await Deno.rename(tmp, path);
}
