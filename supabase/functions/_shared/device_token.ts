/**
 * 기기 토큰 — 무인 클라이언트(PC 에이전트·단축어·앱)용 긴 수명 Bearer.
 *
 * 형식 `jrd_` + 43자 base64url(32바이트 = 256비트). 서버는 sha256 hex 만 저장한다 —
 * 고엔트로피라 느린 해시(bcrypt)가 필요 없고, 조회가 인덱스 한 번이다.
 *
 * ## 스코프는 라우트 화이트리스트다
 * `ingest` 는 문서를 **넣고 상태를 보는** 4개 라우트만. 검색·삭제·결제·기기 관리는 막힌다.
 * 토큰이 새어도 문서를 읽거나 지우지 못한다(스펙 §4 S3).
 */

import type { SupabaseClient } from "@supabase/supabase-js";

export const DEVICE_TOKEN_PREFIX = "jrd_";
const TOKEN_BODY_LEN = 43;
const PREFIX_DISPLAY_LEN = 8;

export interface GeneratedDeviceToken {
  token: string;
  hash: string;
  prefix: string;
}

function base64url(bytes: Uint8Array): string {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export async function hashDeviceToken(token: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(token));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function generateDeviceToken(): Promise<GeneratedDeviceToken> {
  const raw = new Uint8Array(32);
  crypto.getRandomValues(raw);
  const token = `${DEVICE_TOKEN_PREFIX}${base64url(raw)}`;
  return { token, hash: await hashDeviceToken(token), prefix: token.slice(0, PREFIX_DISPLAY_LEN) };
}

export function isDeviceTokenFormat(s: string): boolean {
  return s.startsWith(DEVICE_TOKEN_PREFIX) &&
    s.length === DEVICE_TOKEN_PREFIX.length + TOKEN_BODY_LEN &&
    /^[A-Za-z0-9_-]+$/.test(s.slice(DEVICE_TOKEN_PREFIX.length));
}

export interface DeviceTokenRow {
  id: string;
  user_id: string;
  name: string;
  scopes: string[];
  last_used_at: string | null;
  revoked_at: string | null;
}

/** 해시로 조회. 폐기된 토큰은 `null` (폐기 여부는 호출자가 구분할 필요 없다 — 둘 다 401). */
export async function lookupDeviceToken(
  client: SupabaseClient,
  token: string,
): Promise<DeviceTokenRow | null> {
  const hash = await hashDeviceToken(token);
  const { data, error } = await client
    .from("device_tokens")
    .select("id, user_id, name, scopes, last_used_at, revoked_at")
    .eq("token_hash", hash)
    .limit(1);
  if (error) throw new Error(`device_tokens 조회 실패: ${error.message}`);
  const row = (data ?? [])[0] as DeviceTokenRow | undefined;
  if (!row || row.revoked_at) return null;
  return row;
}

const LAST_USED_THROTTLE_MS = 60_000;

/**
 * `last_used_at` 을 분당 1회만 갱신한다 — 요청마다 쓰면 업로드 폭주 때 쓰기가 두 배다.
 *
 * 실패해도 요청을 깨지 않는다(표시용 값이다). 다만 **조용히 넘기지는 않는다** —
 * PostgREST 는 실패를 throw 가 아니라 `{ error }` 로 돌려주므로, try/catch 만 두면
 * 모든 갱신이 실패해도 로그가 한 줄도 안 남는다. 두 경로를 다 본다.
 */
export async function touchDeviceToken(
  client: SupabaseClient,
  row: Pick<DeviceTokenRow, "id" | "last_used_at">,
  nowMs: number,
): Promise<void> {
  const last = row.last_used_at ? Date.parse(row.last_used_at) : 0;
  if (nowMs - last < LAST_USED_THROTTLE_MS) return;
  try {
    const { error } = await client.from("device_tokens")
      .update({ last_used_at: new Date(nowMs).toISOString() })
      .eq("id", row.id);
    if (error) {
      console.warn(`device_tokens last_used_at 갱신 실패 (id=${row.id}): ${error.message}`);
    }
  } catch (e) {
    console.warn(`device_tokens last_used_at 갱신 예외 (id=${row.id}):`, e);
  }
}

/** `ingest` 스코프가 허용하는 라우트 — 메서드 + 경로 정규식. */
const INGEST_ROUTES: Array<[string, RegExp]> = [
  ["POST", /^\/documents$/],
  ["POST", /^\/documents\/precheck$/],
  ["GET", /^\/documents\/[^/]+\/status$/],
  ["GET", /^\/documents\/batch-status$/],
];

export function deviceScopeAllows(scopes: string[], method: string, path: string): boolean {
  if (!scopes.includes("ingest")) return false;
  return INGEST_ROUTES.some(([m, re]) => m === method && re.test(path));
}
