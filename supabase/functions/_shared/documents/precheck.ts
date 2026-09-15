/**
 * `POST /documents/precheck` — sha256 목록을 보내면 이미 있는 것·실패한 것·새 것을 가른다.
 *
 * 에이전트가 파일 전체를 올리지 않고도 중복을 알기 위한 것이다(실측: 중복 3.2MB 판정에
 * 3~4초 전송이 들었다). `failed` 는 `flags.failed` 가 있는 행 — 재업로드 대상이다
 * (persist 의 재시도 분기와 같은 의미).
 */

import type { SupabaseClient } from "@supabase/supabase-js";

export const PRECHECK_MAX = 500;
const HEX64 = /^[0-9a-f]{64}$/;

export type PrecheckState = "existing" | "failed" | "new";
export type PrecheckResults = Record<string, { state: PrecheckState; doc_id?: string }>;

export function parsePrecheckBody(
  body: unknown,
): { ok: true; hashes: string[] } | { ok: false; detail: string } {
  const raw = (body as { hashes?: unknown } | null)?.hashes;
  if (!Array.isArray(raw)) return { ok: false, detail: "`hashes` 배열이 필요합니다." };
  if (raw.length === 0) return { ok: false, detail: "`hashes` 가 비어 있습니다." };
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
  const { data, error } = await client
    .from("documents")
    .select("id, sha256, flags")
    .eq("user_id", userId)
    .is("deleted_at", null)
    .in("sha256", hashes);
  if (error) throw new Error(`precheck 조회 실패: ${error.message}`);
  const out: PrecheckResults = {};
  for (const h of hashes) out[h] = { state: "new" };
  const rows = (data ?? []) as {
    id: string;
    sha256: string;
    flags?: Record<string, unknown> | null;
  }[];
  for (const row of rows) {
    out[row.sha256] = row.flags?.["failed"]
      ? { state: "failed", doc_id: row.id }
      : { state: "existing", doc_id: row.id };
  }
  return out;
}
