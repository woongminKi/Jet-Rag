/**
 * `/me/devices` — 기기 토큰 발급·목록·폐기. 세션 인증만(기기 토큰으로 기기를 못 만든다).
 */

import type { SupabaseClient } from "@supabase/supabase-js";
import { generateDeviceToken } from "../device_token.ts";
import { MeHttpError } from "./pipeline.ts";

const NAME_MAX = 60;
const DEVICES_MAX = 20;

export interface DeviceView {
  id: string;
  name: string;
  token_prefix: string;
  scopes: string[];
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

const COLUMNS = "id, name, token_prefix, scopes, created_at, last_used_at, revoked_at";

export async function listDevices(client: SupabaseClient, userId: string): Promise<DeviceView[]> {
  const { data, error } = await client
    .from("device_tokens")
    .select(COLUMNS)
    .eq("user_id", userId)
    .order("created_at", { ascending: false });
  if (error) throw new Error(`device_tokens 목록 실패: ${error.message}`);
  return (data ?? []) as DeviceView[];
}

/** 발급. 응답의 `token` 은 **이 한 번**만 나간다. */
export async function createDevice(
  client: SupabaseClient,
  userId: string,
  rawName: unknown,
): Promise<DeviceView & { token: string }> {
  const name = typeof rawName === "string" ? rawName.trim() : "";
  if (!name || name.length > NAME_MAX) {
    throw new MeHttpError(422, `name 은 1~${NAME_MAX}자여야 합니다.`);
  }
  const active = (await listDevices(client, userId)).filter((d) => !d.revoked_at);
  if (active.length >= DEVICES_MAX) {
    throw new MeHttpError(409, `기기는 최대 ${DEVICES_MAX}개까지 연결할 수 있습니다.`);
  }
  const t = await generateDeviceToken();
  const { data, error } = await client
    .from("device_tokens")
    .insert({ user_id: userId, name, token_hash: t.hash, token_prefix: t.prefix })
    .select(COLUMNS)
    .single();
  if (error) throw new Error(`device_tokens 생성 실패: ${error.message}`);
  return { ...(data as DeviceView), token: t.token };
}

/** 폐기. 남의 기기나 없는 id 는 404 (존재 위장). 이미 폐기됐어도 200. */
export async function revokeDevice(
  client: SupabaseClient,
  userId: string,
  deviceId: string,
): Promise<DeviceView> {
  const { data, error } = await client
    .from("device_tokens")
    .update({ revoked_at: new Date().toISOString() })
    .eq("id", deviceId)
    .eq("user_id", userId)
    .is("revoked_at", null)
    .select(COLUMNS);
  if (error) throw new Error(`device_tokens 폐기 실패: ${error.message}`);
  const rows = (data ?? []) as DeviceView[];
  if (rows.length > 0) return rows[0];
  // 이미 폐기된 본인 기기면 그 행을, 아니면 404.
  const { data: existing } = await client
    .from("device_tokens").select(COLUMNS).eq("id", deviceId).eq("user_id", userId).limit(1);
  const row = ((existing ?? []) as DeviceView[])[0];
  if (!row) throw new MeHttpError(404, "Not Found");
  return row;
}
