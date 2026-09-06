/**
 * 이메일 인제스트 주소 — `services/email_ingest.py` 중 `/me/*` 가 쓰는 부분 포팅.
 *
 * ## GET 인데 쓴다
 * `/me/email-ingest` 는 조회처럼 보이지만 **주소가 없으면 발급**하고, 저장된
 * `owner_email` 이 현재 이메일과 다르면 **갱신**한다. 원본 그대로다.
 *
 * ## rotate 는 파괴적이다
 * 토큰을 재발급하면 **구 주소가 즉시 무효**가 된다. 그래서 이식 검증에서 실제로 부르지
 * 않았고, 대신 토큰·시각을 주입할 수 있게 만들어 **요청 URL 만** 대조했다
 * (`verify_me_parity.py`). 난수를 그대로 쓰면 대조가 불가능하다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";
import { pyIsoUtc } from "../pytime.ts";

/** 원본과 같은 알파벳·길이. 주소에 들어가므로 소문자·숫자만 쓴다. */
const TOKEN_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789";
const TOKEN_LEN = 8; // 원본 `_TOKEN_LEN` — 16 으로 짐작했다가 대조에서 잡혔다.

export function generateToken(): string {
  const bytes = new Uint8Array(TOKEN_LEN);
  crypto.getRandomValues(bytes);
  // `secrets.choice` 와 같은 균등 분포를 위해 나머지 연산 편향을 피한다.
  let out = "";
  for (let i = 0; i < TOKEN_LEN; i++) {
    let b = bytes[i];
    // 36 의 배수가 아닌 구간(252~255)은 버리고 다시 뽑는다.
    while (b >= 252) {
      const one = new Uint8Array(1);
      crypto.getRandomValues(one);
      b = one[0];
    }
    out += TOKEN_ALPHABET[b % TOKEN_ALPHABET.length];
  }
  return out;
}

export function buildAddress(token: string, domain: string): string {
  return `u-${token}@${domain}`;
}

export interface AddressRow {
  user_id: string;
  token: string;
  owner_email: string | null;
}

/**
 * 주소 row 를 돌려준다 — 없으면 발급, `owner_email` 이 바뀌었으면 갱신.
 * 갱신은 **현재 이메일이 있을 때만** 한다(원본과 같다) — null 로 덮어쓰지 않는다.
 */
export async function getOrCreateAddress(
  client: SupabaseClient,
  userId: string,
  userEmail: string | null,
  token: string = generateToken(),
): Promise<AddressRow> {
  const { data, error } = await client
    .from("email_ingest_addresses")
    .select("user_id, token, owner_email")
    .eq("user_id", userId)
    .limit(1);
  if (error) throw new Error(`주소 조회 실패: ${error.message}`);
  const rows = (data ?? []) as AddressRow[];

  if (rows.length > 0) {
    const row = rows[0];
    if (userEmail && row.owner_email !== userEmail) {
      const { error: upErr } = await client
        .from("email_ingest_addresses")
        .update({ owner_email: userEmail })
        .eq("user_id", userId);
      if (upErr) throw new Error(`owner_email 갱신 실패: ${upErr.message}`);
      row.owner_email = userEmail;
    }
    return row;
  }

  const row: AddressRow = { user_id: userId, token, owner_email: userEmail };
  const { error: insErr } = await client.from("email_ingest_addresses").insert(row);
  if (insErr) throw new Error(`주소 발급 실패: ${insErr.message}`);
  return row;
}

/**
 * 토큰 재발급 — **구 주소가 즉시 무효**가 된다.
 * 토큰과 시각을 주입할 수 있게 열어 둔 건 검증 때문이다(위 docstring 참조).
 */
export function buildRotateRow(
  userId: string,
  userEmail: string | null,
  nowMs: number,
  token: string = generateToken(),
): Record<string, unknown> {
  return {
    user_id: userId,
    token,
    owner_email: userEmail,
    rotated_at: pyIsoUtc(nowMs),
  };
}

/**
 * upsert 쿼리를 **실행하지 않고** 돌려준다.
 *
 * 채점기가 이걸 직접 불러 요청 URL 만 대조한다 — rotate 는 파괴적이라 실행할 수 없다.
 * 처음엔 채점기가 조립을 복붙했는데, 그러면 이 함수를 고쳐도 안 잡힌다
 * (`on_conflict` 를 지워도 음성 대조가 0 건이었다).
 */
// deno-lint-ignore no-explicit-any
export function buildRotateQuery(
  client: SupabaseClient,
  row: Record<string, unknown>,
  // deno-lint-ignore no-explicit-any
): any {
  return client.from("email_ingest_addresses").upsert(row, { onConflict: "user_id" });
}

export async function rotateAddress(
  client: SupabaseClient,
  userId: string,
  userEmail: string | null,
  nowMs: number,
  token: string = generateToken(),
): Promise<AddressRow> {
  const row = buildRotateRow(userId, userEmail, nowMs, token);
  const { error } = await buildRotateQuery(client, row);
  if (error) throw new Error(`토큰 재발급 실패: ${error.message}`);
  return row as unknown as AddressRow;
}
