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
 * ## 저장은 업로드와 같은 함수를 쓴다
 * 2026-09-15 부터 크기·매직바이트·중복 판정·Storage·큐 투입이 전부
 * `documents/persist.ts` 하나다(이전엔 같은 로직을 각자 들고 있었다). 여기 남은 것은
 * **이메일 고유의 관문**뿐이다 — 주소 토큰, 발신자 일치, 첨부별 결과 모으기.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

import { persistDocument, type StorageCheck } from "../documents/persist.ts";
import { pyIsAlnum } from "../pychar.ts";
import { PY_SP } from "../search/pystr.ts";

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
  checkStorage?: (sizeBytes: number) => Promise<StorageCheck | null>;
}

/**
 * 원본 `ingest_email_attachment` — 첨부 1 건. **예외를 던지지 않는다.**
 *
 * webhook 은 첨부별 결과를 모아 항상 200 을 낸다. 저장은 `persistDocument` 가 한다 —
 * 확장자 표도 업로드와 같은 `ALLOWED_EXTENSIONS` 다(2026-09-15 통합, 이전엔 부분집합).
 * 이전 구현에 없던 "실패 흔적 재시도" 분기가 persist 에는 있다 → 같은 첨부를 다시 보내면
 * 실패했던 문서가 재시도된다(의도된 변화).
 */
export async function ingestEmailAttachment(
  deps: EmailIngestDeps,
  opts: { userId: string; filename: string; contentType: string; raw: Uint8Array },
): Promise<AttachmentResult> {
  const { userId, filename, raw } = opts;
  try {
    const r = await persistDocument(
      {
        bytes: raw,
        fileName: filename,
        sourceChannel: "email",
        ingestMode: "default",
        contentType: opts.contentType || "application/octet-stream",
      },
      { client: deps.client, bucket: deps.bucket, userId, checkStorage: deps.checkStorage },
    );
    if (!r.ok) {
      // 이전 구현의 사유 문구를 유지한다 — Worker 로그·사용자 안내가 이 문구를 본다.
      const reason = r.status === 413
        ? "50MB 초과"
        : r.status === 402
        ? "저장 용량 한도 초과"
        : r.detail.startsWith("지원되지 않는 확장자")
        ? `비허용 확장자: ${extOfName(filename) || "(없음)"}`
        : r.detail === "빈 파일입니다."
        ? "빈 첨부"
        : "파일 형식 불일치";
      console.warn(`email_ingest skip — ${reason} (user=${userId}, ${filename})`);
      return { status: "skipped", filename, reason };
    }
    if (r.outcome === "duplicated") return { status: "duplicated", filename, doc_id: r.docId };
    return { status: "accepted", filename, doc_id: r.docId, job_id: r.jobId };
  } catch (e) {
    console.warn(`email_ingest skip — 내부 오류 (user=${userId}, ${filename}): ${e}`);
    return { status: "skipped", filename, reason: "내부 오류" };
  }
}

/** 로그 사유 문구용 확장자 — persist 가 이미 거절한 뒤라 판정에는 쓰지 않는다. */
function extOfName(filename: string): string {
  const dot = filename.lastIndexOf(".");
  return dot >= 0 ? filename.slice(dot).toLowerCase() : "";
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
