/**
 * `POST /documents` — 파일 업로드. `routers/documents.upload_document` 포팅.
 *
 * ## Railway 와 흐름이 다르다 — BackgroundTasks 가 없다
 * 원본은 `documents` 행을 **`pending/…` placeholder** 로 먼저 넣고 응답한 뒤,
 * BG task 가 Storage 업로드 + 경로 갱신 + 8 단계 파이프라인을 돌린다.
 *
 * Edge 에는 그런 BG 가 없다. 대신 큐가 있다. 그래서:
 * 1. Storage 에 **final path 로 바로** 올린다(`user/<uid>/<sha256><ext>`)
 * 2. `documents` insert — 처음부터 실제 경로다
 * 3. `ingest_jobs` insert
 * 4. 큐에 `extract` 투입 → pg_cron 이 이어받는다
 *
 * `pending/` 을 안 쓰는 게 오히려 낫다. extract 핸들러가 "storage_path 가 아직
 * pending" 이면 던지고 재시도하는데, 그 경합 자체가 사라진다.
 *
 * ## dedup 은 원본 그대로 3 갈래다
 * - 같은 sha256 이 있고 **실패 흔적(`flags.failed`)** → 재시도로 본다. 잡을 새로 만든다.
 * - 같은 sha256 이 정상 → **새 행을 만들지 않고** `duplicated: true` 로 즉시 응답.
 * - 없으면 새로 만든다.
 *
 * ## 파일명은 NFC 로 통일한다
 * macOS Finder 가 NFD 로 보낸다. 그대로 두면 검색 쿼리(NFC)와 바이트가 안 맞는다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";
import {
  flagsWithIngestMode,
  validateIngestMode,
} from "../ingest/ingest_mode.ts";

import {
  ALLOWED_EXTENSIONS,
  HEAD_BYTES,
  InputGateError,
  MAX_SIZE_BYTES,
  validateMagic,
} from "./input_gate.ts";

/** 원본 `_SourceChannel`. */
const SOURCE_CHANNELS = new Set([
  "drag-drop",
  "os-share",
  "clipboard",
  "url",
  "camera",
  "api",
]);

export interface UploadResult {
  status: number;
  body: Record<string, unknown>;
}

export interface UploadDeps {
  client: SupabaseClient;
  bucket: string;
  userId: string;
  /** 테스트 주입 — Storage 업로드를 가로챈다. */
  upload?: (path: string, bytes: Uint8Array, contentType: string) => Promise<void>;
}

/** `PurePosixPath(name).suffix.lower()` 와 같다. 마지막 점 이후만, 점이 없으면 빈 문자열. */
export function extOf(fileName: string): string {
  const base = fileName.split("/").pop() ?? fileName;
  const i = base.lastIndexOf(".");
  // Python `suffix` 는 이름이 점으로 시작하면(숨김 파일) 확장자로 치지 않는다.
  if (i <= 0) return "";
  return base.slice(i).toLowerCase();
}

/** `PurePosixPath(name).stem`. */
function stemOf(fileName: string): string {
  const base = fileName.split("/").pop() ?? fileName;
  const i = base.lastIndexOf(".");
  return i <= 0 ? base : base.slice(0, i);
}

async function sha256Hex(bytes: Uint8Array): Promise<string> {
  // `bytes.buffer` 가 ArrayBufferLike 라 그대로는 BufferSource 로 안 받는다.
  const h = await crypto.subtle.digest("SHA-256", bytes.slice().buffer as ArrayBuffer);
  return [...new Uint8Array(h)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** `SupabaseBlobStorage.build_user_path` — `user/<uid>/<sha256><ext>`. */
export function buildUserPath(userId: string, sha256: string, ext: string): string {
  return `user/${userId}/${sha256}${ext}`;
}

export async function handleUpload(
  form: FormData,
  deps: UploadDeps,
): Promise<UploadResult> {
  const file = form.get("file");
  if (!(file instanceof File)) {
    return { status: 422, body: { detail: "file 필드가 필요합니다." } };
  }
  const sourceChannel = String(form.get("source_channel") ?? "api");
  if (!SOURCE_CHANNELS.has(sourceChannel)) {
    return { status: 422, body: { detail: `source_channel 이 올바르지 않습니다: ${sourceChannel}` } };
  }
  const titleField = form.get("title");
  const fileName = file.name || "untitled";

  // ---- 운영 모드 (S2 D3) — 원본이 **확장자 검증 직전**에 본다 ----
  // 이 값은 `documents.flags.ingest_mode` 로 남아 vision page cap 을 정한다.
  // 안 쓰면 재인제스트가 모드를 못 이어받고 항상 default 로 떨어진다.
  const modeField = form.get("mode");
  const modeResult = validateIngestMode(
    typeof modeField === "string" ? modeField : null,
  );
  if ("detail" in modeResult) {
    return { status: 400, body: { detail: modeResult.detail } };
  }
  const ingestMode = modeResult.mode;

  // ---- 게이트 ①: 확장자 화이트리스트 ----
  const ext = extOf(fileName);
  const docType = ALLOWED_EXTENSIONS[ext];
  if (docType === undefined) {
    return { status: 400, body: { detail: `지원되지 않는 확장자입니다: ${ext || "(없음)"}` } };
  }

  // ---- 크기 ----
  // 원본은 chunk 스트리밍으로 세면서 넘으면 즉시 끊는다. 여기서는 `File.size` 를
  // 먼저 보고 끊는다 — 이미 메모리에 올라온 뒤라 스트리밍의 이점은 없지만
  // **50MB 를 넘는 것을 읽지 않고 거절**하는 효과는 같다.
  if (file.size > MAX_SIZE_BYTES) {
    return { status: 413, body: { detail: "파일 크기 상한(50MB) 초과" } };
  }
  const bytes = new Uint8Array(await file.arrayBuffer());
  if (bytes.length > MAX_SIZE_BYTES) {
    return { status: 413, body: { detail: "파일 크기 상한(50MB) 초과" } };
  }
  if (bytes.length === 0) {
    return { status: 400, body: { detail: "빈 파일입니다." } };
  }

  // ---- 게이트 ②: 매직바이트 ----
  try {
    validateMagic(ext, bytes.subarray(0, HEAD_BYTES));
  } catch (e) {
    if (e instanceof InputGateError) return { status: e.status, body: { detail: e.message } };
    throw e;
  }

  const sha256 = await sha256Hex(bytes);
  const contentType = file.type || "application/octet-stream";

  // ---- Tier 1 dedup ----
  const { data: existing, error: exErr } = await deps.client
    .from("documents")
    .select("id, flags")
    .eq("user_id", deps.userId)
    .eq("sha256", sha256)
    .is("deleted_at", null)
    .limit(1);
  if (exErr) throw new Error(`documents 조회 실패: ${exErr.message}`);

  const dup = (existing ?? [])[0] as { id: string; flags?: Record<string, unknown> } | undefined;
  if (dup && !dup.flags?.["failed"]) {
    // 정상 중복 — 새 행을 만들지 않는다.
    return { status: 202, body: { doc_id: dup.id, job_id: null, duplicated: true } };
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
  if (dup) {
    // 실패 흔적이 있는 행 — 재업로드는 재시도 의도다. flags 를 비우고 다시 돌린다.
    docId = dup.id;
    const { error } = await deps.client
      .from("documents")
      .update({
        storage_path: path,
        // 원본도 재시도 때 flags 를 비우고 새 모드만 남긴다.
        flags: flagsWithIngestMode({}, ingestMode),
        deleted_at: null,
      })
      .eq("id", docId);
    if (error) throw new Error(`documents 갱신 실패: ${error.message}`);
  } else {
    // macOS Finder 가 NFD 로 파일명을 보낸다. 검색 쿼리(NFC)와 바이트를 맞춘다.
    const title = (typeof titleField === "string" && titleField ? titleField : stemOf(fileName))
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
    if (error) throw new Error(`documents 생성 실패: ${error.message}`);
    docId = data.id as string;
  }

  // ---- 잡 + 큐 투입 ----
  const { data: job, error: jobErr } = await deps.client
    .from("ingest_jobs")
    .insert({ doc_id: docId, status: "queued" })
    .select("id")
    .single();
  if (jobErr) throw new Error(`ingest_jobs 생성 실패: ${jobErr.message}`);

  const { error: sendErr } = await deps.client.rpc("ingest_queue_send", {
    payload: { job_id: job.id, doc_id: docId, stage: "extract" },
  });
  if (sendErr) throw new Error(`extract enqueue 실패: ${sendErr.message}`);

  return { status: 202, body: { doc_id: docId, job_id: job.id, duplicated: false } };
}
