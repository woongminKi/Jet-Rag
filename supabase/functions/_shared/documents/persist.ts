/**
 * `persistDocument` — 바이트를 문서로 만드는 **유일한** 경로.
 *
 * 웹 업로드(`upload.ts`)·이메일(`email_ingest.ts`)·PC 에이전트(같은 `POST /documents`)가
 * 전부 여기를 지난다. 2026-09-15 이전에는 업로드와 이메일이 같은 로직을 각자 들고 있었다
 * (원본 주석: "hot path 회귀 위험이 커서 공용화 보류"). 세 번째 경로가 생기는 시점에 합쳤다.
 *
 * 순서: 확장자 → 빈 파일 → 크기(50MB) → 매직바이트 → sha256 → dedup 3갈래
 *       → (신규·재시도만) 용량 검사 → Storage → documents → ingest_jobs → 큐 `extract`.
 *
 * ## dedup 3갈래
 * - 같은 sha256 이 정상 → `duplicated` (아무것도 만들지 않는다)
 * - 같은 sha256 에 `flags.failed` → `retried` (같은 행, 새 잡)
 * - 없음 → `created`
 *
 * ## UNIQUE(user_id, sha256) 충돌은 duplicated 다
 * 에이전트가 같은 파일을 두 번 보내는 경합이 실제로 생긴다(watchFs 이벤트 중복).
 * 500 으로 터뜨리면 에이전트가 재시도하고 또 충돌한다. 재조회해서 `duplicated` 로 닫는다.
 *
 * ## 용량 검사는 dedup 뒤다
 * 중복 파일은 용량을 더 쓰지 않는다. 한도가 찬 사용자도 이미 있는 파일은 "있다"고
 * 답을 들어야 에이전트 원장이 정리된다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

import { flagsWithIngestMode, type IngestMode } from "../ingest/ingest_mode.ts";
import {
  ALLOWED_EXTENSIONS,
  HEAD_BYTES,
  InputGateError,
  MAX_SIZE_BYTES,
  validateMagic,
} from "./input_gate.ts";

export interface PersistInput {
  bytes: Uint8Array;
  fileName: string;
  sourceChannel: string;
  ingestMode: IngestMode;
  /** 비면 파일명 stem 을 쓴다. */
  title?: string | null;
  contentType?: string | null;
}

export interface StorageCheck {
  allowed: boolean;
  usedBytes: number;
  limitBytes: number;
}

export interface PersistDeps {
  client: SupabaseClient;
  bucket: string;
  userId: string;
  /** 테스트 주입 — Storage 업로드를 가로챈다. */
  upload?: (path: string, bytes: Uint8Array, contentType: string) => Promise<void>;
  /** S4 용량 한도. 없으면 검사하지 않는다. `null` 을 돌려주면 fail-open. */
  checkStorage?: (sizeBytes: number) => Promise<StorageCheck | null>;
}

export type PersistOutcome = "created" | "duplicated" | "retried";

/**
 * 거절 사유의 **판별자**. 호출자(이메일 Worker 로그 등)가 사유 문구를 만들 때
 * `detail` 한국어 산문을 문자열 매칭하지 않도록 두었다 — 문구를 다듬는 순간
 * 조용히 갈리는 종류의 결합이다.
 */
export type PersistFailureCode =
  | "ext"
  | "empty"
  | "too_large"
  | "magic"
  | "storage_limit"
  | "channel";

export type PersistResult =
  | { ok: true; outcome: PersistOutcome; docId: string; jobId: string | null }
  | {
    ok: false;
    status: 400 | 402 | 413 | 422;
    code: PersistFailureCode;
    detail: string;
    /** `code: "ext"` 일 때만 채운다 — 호출자가 사유 문구에 확장자를 넣는다. */
    ext?: string;
    reason?: "storage_limit";
    used?: number;
    limit?: number;
  };

/** `PurePosixPath(name).suffix.lower()` 와 같다. 마지막 점 이후만, 점이 없으면 빈 문자열. */
export function extOf(name: string): string {
  const base = name.split("/").pop() ?? name;
  const i = base.lastIndexOf(".");
  // Python `suffix` 는 이름이 점으로 시작하면(숨김 파일) 확장자로 치지 않는다.
  if (i <= 0) return "";
  return base.slice(i).toLowerCase();
}

/** `PurePosixPath(name).stem`. */
export function stemOf(name: string): string {
  const base = name.split("/").pop() ?? name;
  const i = base.lastIndexOf(".");
  return i <= 0 ? base : base.slice(0, i);
}

export async function sha256Hex(bytes: Uint8Array): Promise<string> {
  // 뷰를 그대로 넘긴다 — `.slice()` 를 끼우면 최대 50MB 를 통째로 한 번 더 복사한다.
  // 캐스팅은 `Uint8Array<ArrayBufferLike>` 가 `BufferSource` 와 명목상 안 맞아서지,
  // 런타임에서는 TypedArray 뷰가 그대로 허용된다.
  const digest = await crypto.subtle.digest("SHA-256", bytes as BufferSource);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** `SupabaseBlobStorage.build_user_path` — `user/<uid>/<sha256><ext>`. */
export function buildUserPath(userId: string, sha256: string, ext: string): string {
  return `user/${userId}/${sha256}${ext}`;
}

interface ExistingRow {
  id: string;
  flags?: Record<string, unknown> | null;
}

async function findBySha(
  client: SupabaseClient,
  userId: string,
  sha256: string,
): Promise<ExistingRow | undefined> {
  const { data, error } = await client
    .from("documents")
    .select("id, flags")
    .eq("user_id", userId)
    .eq("sha256", sha256)
    .is("deleted_at", null)
    .limit(1);
  if (error) throw new Error(`documents 조회 실패: ${error.message}`);
  return (data ?? [])[0] as ExistingRow | undefined;
}

export async function persistDocument(
  input: PersistInput,
  deps: PersistDeps,
): Promise<PersistResult> {
  const { bytes, fileName, sourceChannel, ingestMode } = input;
  const contentType = input.contentType || "application/octet-stream";

  // ---- 게이트 ①: 확장자 화이트리스트 ----
  const ext = extOf(fileName);
  const docType = ALLOWED_EXTENSIONS[ext];
  if (docType === undefined) {
    return {
      ok: false,
      status: 400,
      code: "ext",
      ext,
      detail: `지원되지 않는 확장자입니다: ${ext || "(없음)"}`,
    };
  }
  if (bytes.length === 0) {
    return { ok: false, status: 400, code: "empty", detail: "빈 파일입니다." };
  }
  if (bytes.length > MAX_SIZE_BYTES) {
    return { ok: false, status: 413, code: "too_large", detail: "파일 크기 상한(50MB) 초과" };
  }

  // ---- 게이트 ②: 매직바이트 ----
  try {
    validateMagic(ext, bytes.subarray(0, HEAD_BYTES));
  } catch (e) {
    if (e instanceof InputGateError) {
      return { ok: false, status: 400, code: "magic", detail: e.message };
    }
    throw e;
  }

  const sha256 = await sha256Hex(bytes);

  // ---- Tier 1 dedup ----
  const dup = await findBySha(deps.client, deps.userId, sha256);
  if (dup && !dup.flags?.["failed"]) {
    return { ok: true, outcome: "duplicated", docId: dup.id, jobId: null };
  }

  // ---- S4 용량 한도 — 신규·재시도만 ----
  if (deps.checkStorage) {
    const check = await deps.checkStorage(bytes.length);
    if (check && !check.allowed) {
      return {
        ok: false,
        status: 402,
        code: "storage_limit",
        reason: "storage_limit",
        used: check.usedBytes,
        limit: check.limitBytes,
        // **문서 삭제 엔드포인트가 없다.** 지우라고 안내하면 할 수 없는 일을 시키는 것이다.
        detail: `저장 용량 한도를 초과했습니다 (${formatBytes(check.usedBytes)} / ${
          formatBytes(check.limitBytes)
        }). Pro 로 업그레이드하면 더 큰 용량을 이용할 수 있습니다.`,
      };
    }
  }

  // ---- Storage 업로드 (응답 전에 끝낸다) ----
  const path = buildUserPath(deps.userId, sha256, ext);
  if (deps.upload) {
    await deps.upload(path, bytes, contentType);
  } else {
    const { error: upErr } = await deps.client.storage
      .from(deps.bucket)
      .upload(path, bytes, { contentType, upsert: true });
    if (upErr) throw new Error(`Storage 업로드 실패 (${path}): ${upErr.message}`);
  }

  let docId: string;
  let outcome: PersistOutcome;
  if (dup) {
    // 실패 흔적이 있는 행 — 재업로드는 재시도 의도다. flags 를 비우고 다시 돌린다.
    docId = dup.id;
    outcome = "retried";
    // 같은 바이트가 **다른 허용 확장자**로 다시 올 수 있다(.hwpx→.docx 는 둘 다 ZIP 매직이라
    // 게이트를 통과한다). 경로·타입·크기를 같이 갱신하지 않으면 doc_type 이 낡은 채 남아
    // extract 가 엉뚱한 파서를 고른다.
    // `deleted_at: null` 은 쓰지 않는다 — findBySha 가 이미 살아있는 행만 돌려주므로 도달 불가다.
    // 삭제된 문서를 되살리려면 UNIQUE(user_id, sha256) 를 부분 인덱스로 바꾸는 게 먼저다.
    const { error } = await deps.client
      .from("documents")
      .update({
        storage_path: path,
        doc_type: docType,
        content_type: contentType,
        size_bytes: bytes.length,
        flags: flagsWithIngestMode({}, ingestMode),
      })
      .eq("id", docId);
    if (error) throw new Error(`documents 갱신 실패: ${error.message}`);
  } else {
    // macOS Finder 가 NFD 로 파일명을 보낸다. 검색 쿼리(NFC)와 바이트를 맞춘다.
    const title = (input.title && input.title.length > 0 ? input.title : stemOf(fileName))
      .normalize("NFC");
    const { data, error } = await deps.client
      .from("documents")
      .insert({
        user_id: deps.userId,
        title,
        doc_type: docType,
        source_channel: sourceChannel,
        storage_path: path,
        sha256,
        size_bytes: bytes.length,
        content_type: contentType,
        flags: flagsWithIngestMode({}, ingestMode),
      })
      .select("id")
      .single();
    if (error) {
      // UNIQUE(user_id, sha256) — 같은 파일이 동시에 들어온 경합. 먼저 온 쪽이 이겼다.
      const pgCode = (error as { code?: string }).code;
      if (pgCode === "23505") {
        const raced = await findBySha(deps.client, deps.userId, sha256);
        if (raced) return { ok: true, outcome: "duplicated", docId: raced.id, jobId: null };
      }
      // CHECK(source_channel) — 마이그 031 전까지 `pc-agent` 계열은 DB 가 거절한다.
      // 그대로 던지면 500 이라 에이전트가 무한 재시도한다. 422 로 닫아 "보내지 마라"를 알린다.
      if (pgCode === "23514") {
        return {
          ok: false,
          status: 422,
          code: "channel",
          detail: `source_channel 이 아직 서버에서 허용되지 않습니다: ${sourceChannel}`,
        };
      }
      throw new Error(`documents 생성 실패: ${error.message}`);
    }
    docId = (data as { id: string }).id;
    outcome = "created";
  }

  // ---- 잡 + 큐 투입 ----
  const { data: job, error: jobErr } = await deps.client
    .from("ingest_jobs")
    .insert({ doc_id: docId, status: "queued" })
    .select("id")
    .single();
  if (jobErr) throw new Error(`ingest_jobs 생성 실패: ${jobErr.message}`);
  const jobId = (job as { id: string }).id;

  const { error: sendErr } = await deps.client.rpc("ingest_queue_send", {
    payload: { job_id: jobId, doc_id: docId, stage: "extract" },
  });
  if (sendErr) throw new Error(`extract enqueue 실패: ${sendErr.message}`);

  return { ok: true, outcome, docId, jobId };
}

export function formatBytes(n: number): string {
  if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(1)}GB`;
  if (n >= 1024 ** 2) return `${(n / 1024 ** 2).toFixed(0)}MB`;
  return `${Math.ceil(n / 1024)}KB`;
}
