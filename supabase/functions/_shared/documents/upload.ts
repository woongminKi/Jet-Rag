/**
 * `POST /documents` — multipart 를 풀어 `persistDocument` 에 넘긴다.
 *
 * 저장 로직은 2026-09-15 부터 `persist.ts` 에 있다. 여기 남은 것은 **HTTP 모양**뿐이다:
 * 폼 필드 검증(422/400)과 결과 → 상태코드·본문 매핑. 응답 모양은 원본과 같다.
 *
 * ## dedup 응답 모양
 * 정상 중복: 202 `{doc_id, job_id: null, duplicated: true}`. 신규·재시도: 202 `{…, duplicated: false}`.
 */

import type { SupabaseClient } from "@supabase/supabase-js";
import { validateIngestMode } from "../ingest/ingest_mode.ts";
import { MAX_SIZE_BYTES } from "./input_gate.ts";
import { persistDocument, type StorageCheck } from "./persist.ts";

/** 원본 `_SourceChannel` + 자동 수집 채널 3종(2026-09-15 스펙 §4 S1). */
export const SOURCE_CHANNELS = new Set([
  "drag-drop",
  "os-share",
  "clipboard",
  "url",
  "camera",
  "api",
  "pc-agent",
  "ios-shortcut",
  "android-agent",
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
  checkStorage?: (sizeBytes: number) => Promise<StorageCheck | null>;
}

export async function handleUpload(form: FormData, deps: UploadDeps): Promise<UploadResult> {
  const file = form.get("file");
  if (!(file instanceof File)) {
    return { status: 422, body: { detail: "file 필드가 필요합니다 (multipart/form-data)." } };
  }
  const sourceChannel = String(form.get("source_channel") ?? "api");
  if (!SOURCE_CHANNELS.has(sourceChannel)) {
    return { status: 422, body: { detail: `source_channel 이 올바르지 않습니다: ${sourceChannel}` } };
  }
  const titleField = form.get("title");
  const fileName = file.name || "untitled";

  // 운영 모드 (S2 D3) — 원본이 **확장자 검증 직전**에 본다.
  const modeField = form.get("mode");
  const modeResult = validateIngestMode(typeof modeField === "string" ? modeField : null);
  if ("detail" in modeResult) return { status: 400, body: { detail: modeResult.detail } };

  // 50MB 를 넘는 것은 읽지 않고 거절한다 — `File.size` 를 먼저 본다.
  if (file.size > MAX_SIZE_BYTES) {
    return { status: 413, body: { detail: "파일 크기 상한(50MB) 초과" } };
  }
  const bytes = new Uint8Array(await file.arrayBuffer());

  const r = await persistDocument(
    {
      bytes,
      fileName,
      sourceChannel,
      ingestMode: modeResult.mode,
      title: typeof titleField === "string" ? titleField : null,
      contentType: file.type || null,
    },
    deps,
  );
  if (!r.ok) {
    // `code` 는 **언제나** 싣는다. 에이전트가 거절 사유로 분기하려면 한국어 `detail` 을
    // 문자열 매칭하는 수밖에 없고, 그러면 문구를 다듬는 순간 조용히 갈린다.
    const body: Record<string, unknown> = { detail: r.detail, code: r.code };
    if (r.ext !== undefined) body.ext = r.ext;
    if (r.reason) {
      body.reason = r.reason;
      body.used = r.used;
      body.limit = r.limit;
    }
    // status 는 persist 가 정한 값을 그대로 쓴다 — `channel` 은 503(서버 설정 문제라
    // 재시도해야 한다), 나머지 거절은 4xx(영구)다.
    return { status: r.status, body };
  }
  return {
    status: 202,
    body: { doc_id: r.docId, job_id: r.jobId, duplicated: r.outcome === "duplicated" },
  };
}
