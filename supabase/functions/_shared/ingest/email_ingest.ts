/**
 * `services/email_ingest.py` 포팅 — 이메일 첨부를 문서로 받는다.
 *
 * Cloudflare Email Routing → Email Worker → `POST /ingest/email` 로 온다.
 * 인증은 JWT 가 아니라 **공유 secret** 이다(발신자가 사람이 아니라 Worker다).
 *
 * ## 거절은 조용히 한다
 * 잘못된 주소·모르는 토큰·발신자 불일치·Pro 아님·첨부 없음 → 전부 **200 + ignored** 다.
 * 4xx 를 내면 Worker 가 재시도하거나 발신자에게 반송 메일이 간다.
 * **secret 불일치만 401** — 그건 설정 오류라 시끄럽게 실패해야 발견된다.
 *
 * ## 업로드 경로의 의도적 미러다
 * 크기·매직바이트·중복 판정을 업로드와 같은 값으로 다시 쓴다. 원본 주석이 그렇게
 * 적어 뒀다 — 공용화는 hot path(업로드) 회귀 위험이 커서 보류한 것이다.
 * 다만 업로드에 있는 **"실패 문서 재시도" 분기는 여기 없다**(웹 UI 전용).
 */

import type { SupabaseClient } from "@supabase/supabase-js";

import { HEAD_BYTES, InputGateError, validateMagic } from "../documents/input_gate.ts";
import { buildUserPath } from "../documents/upload.ts";
import { flagsWithIngestMode } from "./ingest_mode.ts";
import { pyIsAlnum } from "../pychar.ts";
import { PY_SP } from "../search/pystr.ts";

/** 스펙 §2 — 이메일 첨부 허용 포맷. 업로드 화이트리스트의 **부분집합**이다. */
export const EMAIL_ALLOWED_EXTENSIONS: Record<string, string> = {
  ".pdf": "pdf",
  ".hwp": "hwp",
  ".hwpx": "hwpx",
  ".docx": "docx",
  ".jpg": "image",
  ".jpeg": "image",
  ".png": "image",
  ".heic": "image",
};

const MAX_SIZE_BYTES = 50 * 1024 * 1024;
const TOKEN_LEN = 8;

/**
 * 원본 `_EMAIL_RE`. `\s` 는 Python 유니코드 공백이라 그대로 옮긴다.
 * `Name <a@b.c>` 와 `a@b.c` 둘 다 받는다.
 */
const EMAIL_RE = new RegExp(
  `^(?:[^<]*<)?([A-Za-z0-9._%+\\-]+@[A-Za-z0-9.\\-]+)>?[${PY_SP}]*$`,
);

/** Python `str.strip()` 과 같은 공백 집합. */
const STRIP_RE = new RegExp(`^[${PY_SP}]+|[${PY_SP}]+$`, "g");
function pyStrip(s: string): string {
  return s.replace(STRIP_RE, "");
}

/** 원본 `_extract_email` — 실패하면 `null`. 소문자로 낮춘다. */
export function extractEmail(raw: string): string | null {
  const m = EMAIL_RE.exec(pyStrip(raw));
  return m ? m[1].toLowerCase() : null;
}

/** 원본 `parse_token` — `u-{token8}@…` 이 아니면 `null`. */
export function parseToken(toAddress: string): string | null {
  const email = extractEmail(toAddress);
  if (!email || !email.startsWith("u-")) return null;
  const local = email.split("@", 1)[0];
  const token = local.slice(2);
  // `len(token) != 8` 은 코드포인트 기준이지만 토큰은 ASCII 라 같다.
  if ([...token].length !== TOKEN_LEN) return null;
  // Python `str.isalnum()` — 빈 문자열은 False 다.
  if (token === "") return null;
  for (const ch of token) if (!pyIsAlnum(ch)) return null;
  return token;
}

/** 원본 `sender_allowed` — 가입 이메일과 일치해야 통과. 없으면 거절. */
export function senderAllowed(fromAddress: string, ownerEmail: string | null): boolean {
  if (!ownerEmail) return false;
  const sender = extractEmail(fromAddress);
  return sender !== null && sender === pyStrip(ownerEmail).toLowerCase();
}

export interface AddressRow {
  user_id: string;
  token: string;
  owner_email: string | null;
}

/** 원본 `lookup_by_token` — 조회 실패도 `null`(쓰기 경로라 fail-closed). */
export async function lookupByToken(
  client: SupabaseClient,
  token: string,
): Promise<AddressRow | null> {
  try {
    const { data, error } = await client
      .from("email_ingest_addresses")
      .select("user_id, token, owner_email")
      .eq("token", token)
      .limit(1);
    if (error) throw new Error(error.message);
    return ((data ?? [])[0] as AddressRow | undefined) ?? null;
  } catch (e) {
    console.warn(`email_ingest 주소 조회 실패 (token=${token.slice(0, 4)}...): ${e}`);
    return null;
  }
}

export type AttachmentResult = Record<string, unknown>;

export interface EmailIngestDeps {
  client: SupabaseClient;
  bucket: string;
}

/**
 * 원본 `ingest_email_attachment` — 첨부 1 건. **예외를 던지지 않는다.**
 *
 * webhook 은 첨부별 결과를 모아 항상 200 을 낸다.
 */
export async function ingestEmailAttachment(
  deps: EmailIngestDeps,
  opts: { userId: string; filename: string; contentType: string; raw: Uint8Array },
): Promise<AttachmentResult> {
  const { userId, filename, raw } = opts;
  const contentType = opts.contentType || "application/octet-stream";
  const dot = filename.lastIndexOf(".");
  const ext = dot >= 0 ? filename.slice(dot).toLowerCase() : "";
  const docType = EMAIL_ALLOWED_EXTENSIONS[ext];
  if (docType === undefined) {
    console.warn(`email_ingest skip — 비허용 확장자 ${ext} (user=${userId})`);
    return { status: "skipped", filename, reason: `비허용 확장자: ${ext || "(없음)"}` };
  }
  if (raw.length === 0) return { status: "skipped", filename, reason: "빈 첨부" };
  if (raw.length > MAX_SIZE_BYTES) {
    console.warn(`email_ingest skip — 50MB 초과 (user=${userId}, ${raw.length} bytes)`);
    return { status: "skipped", filename, reason: "50MB 초과" };
  }

  try {
    validateMagic(ext, raw.subarray(0, HEAD_BYTES));
  } catch (e) {
    const detail = e instanceof InputGateError ? e.message : String(e);
    console.warn(`email_ingest skip — magic bytes 불일치 (user=${userId}, ${filename}): ${detail}`);
    return { status: "skipped", filename, reason: "파일 형식 불일치" };
  }

  try {
    const digest = await crypto.subtle.digest("SHA-256", raw.slice().buffer as ArrayBuffer);
    const sha256 = [...new Uint8Array(digest)]
      .map((b) => b.toString(16).padStart(2, "0")).join("");

    // Tier 1 중복 — 업로드와 같은 판정이되 **실패 문서 재시도 분기는 없다**(웹 UI 전용).
    const { data: dup, error: dupErr } = await deps.client
      .from("documents")
      .select("id, flags")
      .eq("user_id", userId)
      .eq("sha256", sha256)
      .is("deleted_at", null)
      .limit(1);
    if (dupErr) throw new Error(dupErr.message);
    const existing = (dup ?? [])[0] as { id: string } | undefined;
    if (existing) return { status: "duplicated", filename, doc_id: existing.id };

    // 원본은 `pending/` 에 넣고 BG 가 올린다. Edge 는 업로드 경로와 같이 **먼저 올리고**
    // 최종 경로를 쓴다 — `extract` 가 `pending/` 을 재시도 대상으로 던지기 때문이다.
    const path = buildUserPath(userId, sha256, ext);
    const { error: upErr } = await deps.client.storage
      .from(deps.bucket).upload(path, raw, { contentType, upsert: true });
    if (upErr) throw new Error(`Storage 업로드 실패 (${path}): ${upErr.message}`);

    const base = filename.split("/").pop() ?? filename;
    const i = base.lastIndexOf(".");
    const stem = i <= 0 ? base : base.slice(0, i);
    const { data: docRow, error: insErr } = await deps.client
      .from("documents")
      .insert({
        user_id: userId,
        title: stem.normalize("NFC"),
        doc_type: docType,
        source_channel: "email",
        storage_path: path,
        sha256,
        size_bytes: raw.length,
        content_type: contentType,
        flags: flagsWithIngestMode({}, "default"),
      })
      .select("id")
      .single();
    if (insErr) throw new Error(insErr.message);
    const docId = (docRow as { id: string }).id;

    const { data: job, error: jobErr } = await deps.client
      .from("ingest_jobs").insert({ doc_id: docId, status: "queued" })
      .select("id").single();
    if (jobErr) throw new Error(jobErr.message);
    const jobId = (job as { id: string }).id;

    const { error: sendErr } = await deps.client.rpc("ingest_queue_send", {
      payload: { job_id: jobId, doc_id: docId, stage: "extract" },
    });
    if (sendErr) throw new Error(sendErr.message);

    return { status: "accepted", filename, doc_id: docId, job_id: jobId };
  } catch (e) {
    console.warn(`email_ingest skip — 내부 오류 (user=${userId}, ${filename}): ${e}`);
    return { status: "skipped", filename, reason: "내부 오류" };
  }
}

/** 원본 `_increment_docs_counter` — 실패해도 인제스트를 막지 않는다. */
export async function incrementDocsCounter(
  client: SupabaseClient,
  userId: string,
  nowMs: number,
): Promise<void> {
  try {
    const d = new Date(nowMs);
    const day = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}-${
      String(d.getUTCDate()).padStart(2, "0")
    }`;
    const { error } = await client.rpc("increment_usage_counter", {
      p_user_key: userId,
      p_metric: "docs",
      p_period_date: day,
    });
    if (error) throw new Error(error.message);
  } catch (e) {
    console.warn(`email_ingest docs 카운터 실패 (user=${userId}): ${e}`);
  }
}

/** 이메일 헤더 유래 값 로그용 — 개행 escape + 길이 제한(로그 인젝션 방어). */
export function safeForLog(value: string, limit = 128): string {
  return value.replace(/\r/g, "\\r").replace(/\n/g, "\\n").slice(0, limit);
}

/** `hmac.compare_digest` — 길이가 같을 때 상수 시간으로 비교한다. */
export function constantTimeEquals(a: string, b: string): boolean {
  const ab = new TextEncoder().encode(a);
  const bb = new TextEncoder().encode(b);
  if (ab.length !== bb.length) return false;
  let diff = 0;
  for (let i = 0; i < ab.length; i++) diff |= ab[i] ^ bb[i];
  return diff === 0;
}
