/**
 * `routers/email_ingest.py` 포팅 — `POST /ingest/email`.
 *
 * ## 거절은 조용히, 설정 오류는 시끄럽게
 * | 상황 | 응답 |
 * |---|---|
 * | secret 미설정 | **503** — 기능이 꺼져 있다 |
 * | secret 불일치 | **401** — Worker 설정 오류라 드러나야 한다 |
 * | 그 밖의 거절 | **200 `ignored`** — Worker 재시도·반송 메일 회피 |
 *
 * Pro 플랜만 받는다. 플랜 조회가 실패해도 거절이다(쓰기 경로 — fail-closed).
 */

import type { SupabaseClient } from "@supabase/supabase-js";

import { getEffectivePlan } from "../me/quota.ts";
import {
  constantTimeEquals,
  incrementDocsCounter,
  ingestEmailAttachment,
  lookupByToken,
  parseToken,
  safeForLog,
  senderAllowed,
  type AttachmentResult,
} from "./email_ingest.ts";

export interface EmailRouteDeps {
  client: SupabaseClient;
  bucket: string;
  settings: { emailWebhookSecret: string };
  nowMs?: () => number;
}

export interface RouteResult {
  status: number;
  body: Record<string, unknown>;
}

/**
 * base64 — Python `b64decode(validate=True)` 판정을 그대로 옮긴다.
 *
 * `atob` 을 그냥 쓰면 **더 관대해서** 조용히 갈린다:
 * - 알파벳 밖 문자(`aGVsbG8!`, 공백)를 `atob` 은 상황에 따라 넘긴다 → 정규식으로 막는다.
 * - **패딩 누락**을 `atob` 은 받아준다. `atob("aGVsbG8")` → `"hello"` 지만
 *   Python 은 `binascii.Error` 다. 대조에서 이 한 건이 걸렸다 → 길이 4 배수를 강제한다.
 *
 * 첨부 1 건이 `skipped/base64 오류` 대신 통과하면 그 문서가 실제로 인제스트된다 —
 * 눈에 보이는 차이다.
 */
export function decodeBase64Strict(s: string): Uint8Array | null {
  if (s.length % 4 !== 0) return null;
  if (!/^[A-Za-z0-9+/]*={0,2}$/.test(s)) return null;
  try {
    const bin = atob(s);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  } catch {
    return null;
  }
}

export async function handleEmailWebhook(
  deps: EmailRouteDeps,
  req: Request,
): Promise<RouteResult> {
  const now = deps.nowMs ?? (() => Date.now());

  if (!deps.settings.emailWebhookSecret) {
    return {
      status: 503,
      body: { detail: "이메일 인제스트가 비활성 상태입니다 (JETRAG_EMAIL_WEBHOOK_SECRET 미설정)." },
    };
  }
  const header = req.headers.get("x-jetrag-webhook-secret") ?? "";
  if (!constantTimeEquals(header, deps.settings.emailWebhookSecret)) {
    return { status: 401, body: { detail: "webhook secret 불일치" } };
  }

  let payload: Record<string, unknown>;
  try {
    payload = await req.json() as Record<string, unknown>;
  } catch {
    return { status: 422, body: { detail: "JSON 본문이 필요합니다." } };
  }
  const to = typeof payload["to"] === "string" ? payload["to"] : null;
  const from = typeof payload["from"] === "string" ? payload["from"] : null;
  if (to === null || from === null) {
    // pydantic 은 필수 필드 누락을 422 로 낸다.
    return { status: 422, body: { detail: "`to` 와 `from` 이 필요합니다." } };
  }
  // pydantic 은 **처리 전에** 본문 전체를 검증한다 — 첨부 하나가 어긋나면 요청 전체가
  // 422 다. 여기서 관대하게 기본값을 채우면 거절돼야 할 메일이 인제스트된다.
  const rawAtt = payload["attachments"];
  if (rawAtt !== undefined && rawAtt !== null && !Array.isArray(rawAtt)) {
    return { status: 422, body: { detail: "`attachments` 는 배열이어야 합니다." } };
  }
  const attachments = Array.isArray(rawAtt) ? rawAtt : [];
  for (const a of attachments) {
    // `content_base64: str` 는 **기본값이 없다** — 없거나 문자열이 아니면 422.
    if (typeof (a as Record<string, unknown> | null)?.["content_base64"] !== "string") {
      return { status: 422, body: { detail: "`content_base64` 가 필요합니다." } };
    }
  }

  const token = parseToken(to);
  if (token === null) {
    console.warn(`email_ingest ignore — 잘못된 수신 주소: ${safeForLog(to)}`);
    return { status: 200, body: { status: "ignored", results: [] } };
  }
  const addr = await lookupByToken(deps.client, token);
  if (addr === null) {
    console.warn(`email_ingest ignore — 알 수 없는 토큰: ${token.slice(0, 4)}...`);
    return { status: 200, body: { status: "ignored", results: [] } };
  }
  if (!senderAllowed(from, addr.owner_email)) {
    console.warn(
      `email_ingest ignore — 발신자 불일치 (user=${addr.user_id}, from=${safeForLog(from)})`,
    );
    return { status: 200, body: { status: "ignored", results: [] } };
  }

  const plan = await getEffectivePlan(deps.client, String(addr.user_id));
  if (plan === null || plan.code !== "pro") {
    console.warn(
      `email_ingest ignore — Pro 아님 (user=${addr.user_id}, plan=${plan?.code ?? null})`,
    );
    return { status: 200, body: { status: "ignored", results: [] } };
  }
  if (attachments.length === 0) {
    console.warn(`email_ingest ignore — 첨부 없음 (user=${addr.user_id})`);
    return { status: 200, body: { status: "ignored", results: [] } };
  }

  const userId = String(addr.user_id);
  const results: AttachmentResult[] = [];
  for (const a of attachments) {
    const att = (a ?? {}) as Record<string, unknown>;
    // pydantic 기본값과 같다.
    const filename = typeof att["filename"] === "string" ? att["filename"] : "attachment";
    const contentType = typeof att["content_type"] === "string"
      ? att["content_type"]
      : "application/octet-stream";
    const b64 = typeof att["content_base64"] === "string" ? att["content_base64"] : "";
    const raw = decodeBase64Strict(b64);
    if (raw === null) {
      results.push({ status: "skipped", filename, reason: "base64 오류" });
      continue;
    }
    const r = await ingestEmailAttachment(
      { client: deps.client, bucket: deps.bucket },
      { userId, filename, contentType, raw },
    );
    if (r.status === "accepted") await incrementDocsCounter(deps.client, userId, now());
    results.push(r);
  }

  console.info(
    `email_ingest processed — user=${userId}, 첨부 ${results.length}건: ` +
      `[${results.map((r) => r.status).join(", ")}]`,
  );
  return { status: 200, body: { status: "processed", results } };
}
