/**
 * `POST /documents/precheck` — sha256 목록을 보내면 이미 있는 것·실패한 것·새 것을 가른다.
 *
 * 에이전트가 파일 전체를 올리지 않고도 중복을 알기 위한 것이다(실측: 중복 3.2MB 판정에
 * 3~4초 전송이 들었다). `failed` 는 `flags.failed` 가 있는 행 — 재업로드 대상이다
 * (persist 의 재시도 분기와 같은 의미).
 *
 * ## 500 은 **원본 입력** 개수 상한이다
 * dedupe 전에 잰다. 중복을 잔뜩 보내 놓고 "합치면 몇 개 안 된다"로 상한을 우회하면
 * 파싱·정규화 비용은 이미 다 치른 뒤다. 입력 크기를 막는 게 목적이므로 순서를 지킨다.
 *
 * ## 상한 외에 별도 rate limit 은 걸지 않는다
 * 인증(로그인 필수) + 이 500 개 상한에 기댄다. 문서를 만들지 않는 조회라 업로드 쪽
 * 일일·버스트 상한과는 성격이 다르다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

export const PRECHECK_MAX = 500;
/**
 * 한 번의 `.in()` 에 넣는 해시 개수.
 *
 * **500 을 한 번에 보내면 414 다.** sha256 은 hex 64 자라 URL 인코딩 후 항목당 약 67B 를
 * 먹는다 — 실측 요청 라인 길이: n=100 → 6,826B, n=500 → 33,626B. Supabase 게이트웨이의
 * 헤더 버퍼가 8KB 라서 PostgREST 에 닿기도 전에 414 로 끊긴다. 100 이면 6.8KB 로 그 아래다.
 */
const IN_CHUNK = 100;
const HEX64 = /^[0-9a-f]{64}$/;

export type PrecheckState = "existing" | "failed" | "new";
export type PrecheckResults = Record<string, { state: PrecheckState; doc_id?: string }>;

interface PrecheckRow {
  id: string;
  sha256: string;
  flags?: Record<string, unknown> | null;
}

export function parsePrecheckBody(
  body: unknown,
): { ok: true; hashes: string[] } | { ok: false; detail: string } {
  const raw = (body as { hashes?: unknown } | null)?.hashes;
  if (!Array.isArray(raw)) return { ok: false, detail: "`hashes` 배열이 필요합니다." };
  if (raw.length === 0) return { ok: false, detail: "`hashes` 가 비어 있습니다." };
  // dedupe 전에 잰다 — 위 §"500 은 원본 입력 개수 상한이다".
  if (raw.length > PRECHECK_MAX) {
    return { ok: false, detail: `\`hashes\` 는 최대 ${PRECHECK_MAX}개입니다.` };
  }
  const hashes: string[] = [];
  for (const h of raw) {
    if (typeof h !== "string") {
      return { ok: false, detail: "`hashes` 항목은 문자열이어야 합니다." };
    }
    const lower = h.toLowerCase();
    if (!HEX64.test(lower)) {
      return { ok: false, detail: `sha256 hex 형식이 아닙니다: ${h.slice(0, 16)}` };
    }
    hashes.push(lower);
  }
  return { ok: true, hashes: [...new Set(hashes)] };
}

export async function precheckHashes(
  client: SupabaseClient,
  userId: string,
  hashes: string[],
): Promise<PrecheckResults> {
  const rows: PrecheckRow[] = [];
  // 100 개씩 끊어 조회한다 — 위 `IN_CHUNK` 주석의 414 때문이다.
  for (let i = 0; i < hashes.length; i += IN_CHUNK) {
    const { data, error } = await client
      .from("documents")
      .select("id, sha256, flags")
      .eq("user_id", userId)
      .is("deleted_at", null)
      .in("sha256", hashes.slice(i, i + IN_CHUNK));
    // 한 덩이라도 실패하면 전체를 버린다. 일부만 성공한 결과를 돌려주면 에이전트가
    // 없는 파일을 "새 것"으로 오인해 통째로 다시 올린다.
    if (error) throw new Error(`precheck 조회 실패: ${error.message}`);
    rows.push(...((data ?? []) as PrecheckRow[]));
  }
  const out: PrecheckResults = {};
  for (const h of hashes) out[h] = { state: "new" };
  for (const row of rows) {
    out[row.sha256] = row.flags?.["failed"]
      ? { state: "failed", doc_id: row.id }
      : { state: "existing", doc_id: row.id };
  }
  return out;
}
