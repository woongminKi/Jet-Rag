/**
 * 로컬 게이트 — 서버에 보내기 전에 거를 수 있는 것만 거른다.
 *
 * ## 서버 표를 복사하지 않는다
 * `ALLOWED_EXTENSIONS` 와 `MAX_SIZE_BYTES` 는 `_shared/documents/input_gate.ts` 를
 * **직접 import** 한다. 그 파일의 내부 import 는 `hwpml_sniff.ts` 하나뿐이고 그 파일은
 * import 가 0개라, agent 의 deno.json 만으로 해결된다(2026-09-15 실측).
 * 복사본을 두면 서버가 확장자를 추가했을 때 에이전트만 조용히 뒤처진다.
 *
 * ## 매직바이트는 여기서 안 본다
 * 판정 주체는 서버 하나여야 한다. 클라이언트가 먼저 거절하면 서버 규칙이 바뀔 때
 * 두 곳이 어긋난다. 매직 불일치는 400 `code magic` 으로 돌아오고 `excluded` 가 된다.
 *
 * ## 완성 판정 (스펙 §5.2)
 * 다운로드 중인 파일을 올리면 잘린 파일이 문서가 된다. 이벤트 후 3초 간격 2회
 * 크기·mtime 이 같을 때만 진행한다.
 */

import { ALLOWED_EXTENSIONS, MAX_SIZE_BYTES } from "../../supabase/functions/_shared/documents/input_gate.ts";

export { ALLOWED_EXTENSIONS, MAX_SIZE_BYTES };

export type GateReject = "temp" | "ext" | "empty" | "too_large";

export interface GateEntryInput {
  path: string;
  size: number;
}

export type GateResult =
  | { ok: true; docType: string }
  | { ok: false; reason: GateReject; detail: string };

/** 임시 파일 접미사 — 다운로드·오피스 락 파일. */
const TEMP_SUFFIXES = [".crdownload", ".part", ".partial", ".tmp", ".download"];
/** 시스템 파일. */
const SYSTEM_NAMES = new Set(["thumbs.db", "desktop.ini", ".ds_store"]);

export function baseName(path: string): string {
  const parts = path.split(/[\\/]/);
  return parts[parts.length - 1] ?? path;
}

/** `persist.ts` 의 `extOf` 와 같은 규칙: 마지막 점 이후만, 앞에 이름이 있어야 한다. */
export function extOf(name: string): string {
  const base = baseName(name);
  const i = base.lastIndexOf(".");
  if (i <= 0) return "";
  return base.slice(i).toLowerCase();
}

export function isTempName(name: string): boolean {
  const base = baseName(name).toLowerCase();
  if (SYSTEM_NAMES.has(base)) return true;
  // `~$문서.docx` — Word/Excel 락 파일.
  if (base.startsWith("~$")) return true;
  // 숨김 파일 전부. 사용자가 의도적으로 넣는 문서가 점으로 시작하는 경우는 없다.
  if (base.startsWith(".")) return true;
  return TEMP_SUFFIXES.some((s) => base.endsWith(s));
}

export function gateEntry(input: GateEntryInput): GateResult {
  const name = baseName(input.path);
  if (isTempName(name)) return { ok: false, reason: "temp", detail: "임시 파일" };
  const ext = extOf(name);
  const docType = ALLOWED_EXTENSIONS[ext];
  if (docType === undefined) {
    return { ok: false, reason: "ext", detail: `허용되지 않는 확장자: ${ext === "" ? "(없음)" : ext}` };
  }
  if (input.size <= 0) return { ok: false, reason: "empty", detail: "빈 파일" };
  if (input.size > MAX_SIZE_BYTES) {
    const mb = (input.size / 1024 / 1024).toFixed(1);
    return { ok: false, reason: "too_large", detail: `50MB 초과 (${mb}MB)` };
  }
  return { ok: true, docType };
}

export interface StableOptions {
  intervalMs?: number;
  /** 같은 값을 몇 번 연속 봐야 통과인가. 2 = 3초 간격으로 두 번. */
  checks?: number;
  stat?: (path: string) => Promise<{ size: number; mtime: Date | null }>;
  sleep?: (ms: number) => Promise<void>;
}

const defaultStat = async (path: string) => {
  const st = await Deno.stat(path);
  return { size: st.size, mtime: st.mtime };
};
const defaultSleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** 크기·mtime 이 `checks` 번 연속 같으면 true. 중간에 사라지거나 바뀌면 false. */
export async function isStable(path: string, opts: StableOptions = {}): Promise<boolean> {
  const interval = opts.intervalMs ?? 3000;
  const checks = opts.checks ?? 2;
  const stat = opts.stat ?? defaultStat;
  const sleep = opts.sleep ?? defaultSleep;
  let prev: { size: number; mtime: number } | null = null;
  for (let i = 0; i < checks; i++) {
    let cur: { size: number; mtime: number };
    try {
      const st = await stat(path);
      cur = { size: st.size, mtime: st.mtime?.getTime() ?? 0 };
    } catch {
      return false; // 사라졌거나 권한이 없다.
    }
    if (prev !== null && (prev.size !== cur.size || prev.mtime !== cur.mtime)) return false;
    prev = cur;
    if (i < checks - 1) await sleep(interval);
  }
  return true;
}

/** `isStable` 을 크기가 안정될 때까지 최대 `maxWaitMs` 재시도. 다운로드가 긴 파일용. */
export async function waitStable(
  path: string,
  maxWaitMs = 120_000,
  opts: StableOptions = {},
): Promise<boolean> {
  const interval = opts.intervalMs ?? 3000;
  const deadline = Date.now() + maxWaitMs;
  while (Date.now() < deadline) {
    if (await isStable(path, opts)) return true;
    await (opts.sleep ?? defaultSleep)(interval);
  }
  return false;
}
