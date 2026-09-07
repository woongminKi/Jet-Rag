/**
 * `POST /documents/{id}/reingest` · `POST /documents/{id}/reingest-missing` 포팅.
 *
 * | 라우트 | 하는 일 |
 * |---|---|
 * | `reingest` | chunks 를 **전부 지우고** 같은 파일로 파이프라인 재실행 |
 * | `reingest-missing` | chunks 를 **그대로 두고** vision 이 안 닿은 페이지만 덧붙임 |
 *
 * ## 둘 다 IDOR 를 404 로 막는다
 * 남의 문서면 403 이 아니라 **404** 다. 403 은 "그 id 의 문서가 존재한다" 를 알려 준다.
 * reingest 는 chunks 를 지우고 vision 비용을 쓰므로 IDOR 이 곧 데이터 파괴이자 과금이다.
 *
 * ## 진행 중이면 409
 * 같은 문서에 잡이 둘 돌면 chunk_idx 가 겹치고 청크가 섞인다.
 *
 * ## 모드는 문서에 남긴다
 * `?mode=fast|default|precise`. 미지정이면 **기존 `flags.ingest_mode` 를 재사용**하고,
 * 그것도 없으면 `default` 다. 값은 `documents.flags` 에 쓴다 — 인제스트 단계들이
 * 거기서 page cap 을 다시 계산한다(`ingest_mode.ts` 참조).
 *
 * ## 큐로 넘긴다
 * 원본은 `BackgroundTasks` 로 파이프라인을 그 프로세스에서 돌린다. Edge 에는 그게 없고,
 * 이미 pgmq + pg_cron 사슬이 있으므로 **첫 태스크만 넣고 끝낸다.**
 * - `reingest` → `extract`(from 0) — 그 뒤는 기존 사슬
 * - `reingest-missing` → `vision_missing`(from 0) → `embed`
 */

import type { SupabaseClient } from "@supabase/supabase-js";

import { countPdfPages } from "../ingest/pdf_raster.ts";
import {
  asIngestMode,
  DEFAULT_INGEST_MODE,
  flagsWithIngestMode,
  type IngestMode,
  validateIngestMode,
} from "../ingest/ingest_mode.ts";
import { visionProcessedPages } from "../ingest/vision_incremental.ts";

export interface ReingestResult {
  status: number;
  body: Record<string, unknown>;
}

export interface ReingestDeps {
  client: SupabaseClient;
  bucket: string;
  /** 테스트 주입 — 실제 Storage 없이 바이트를 넣는다. */
  download?: (path: string) => Promise<Uint8Array>;
}

class HttpError extends Error {
  constructor(readonly status: number, readonly detail: string) {
    super(detail);
  }
}

/** 무효 모드는 400. 검증 자체는 `ingest_mode.ts` 가 하고 여기서는 오류로 바꾼다. */
function validatedMode(raw: string | null | undefined): IngestMode {
  const r = validateIngestMode(raw);
  if ("detail" in r) throw new HttpError(400, r.detail);
  return r.mode;
}

interface DocRow {
  id: string;
  user_id: string;
  doc_type?: string;
  storage_path?: string;
  flags?: Record<string, unknown> | null;
}

/** 본인 문서만. 없거나 남의 것이면 **404**(존재 위장). */
async function fetchOwnedDoc(
  client: SupabaseClient,
  docId: string,
  userId: string,
  columns: string,
): Promise<DocRow> {
  const { data, error } = await client
    .from("documents")
    .select(columns)
    .eq("id", docId)
    .is("deleted_at", null)
    .limit(1);
  if (error) throw new Error(`documents 조회 실패: ${error.message}`);
  const row = (data ?? [])[0] as unknown as DocRow | undefined;
  if (!row || row.user_id !== userId) {
    throw new HttpError(404, "문서를 찾을 수 없습니다.");
  }
  return row;
}

interface LatestJob {
  id: string;
  status: string;
}

/** 원본 `get_latest_job_for_doc` — `queued_at` 내림차순 첫 행. */
async function latestJob(
  client: SupabaseClient,
  docId: string,
): Promise<LatestJob | null> {
  const { data, error } = await client
    .from("ingest_jobs")
    .select("id, status")
    .eq("doc_id", docId)
    .order("queued_at", { ascending: false })
    .limit(1);
  if (error) throw new Error(`ingest_jobs 조회 실패: ${error.message}`);
  return ((data ?? [])[0] as LatestJob | undefined) ?? null;
}

async function assertNoRunningJob(
  client: SupabaseClient,
  docId: string,
  suffix: string,
): Promise<void> {
  const job = await latestJob(client, docId);
  if (job && (job.status === "queued" || job.status === "running")) {
    throw new HttpError(
      409,
      `진행 중인 작업이 있습니다 (job=${job.id}, status=${job.status}).${suffix}`,
    );
  }
}

async function createJob(client: SupabaseClient, docId: string): Promise<string> {
  const { data, error } = await client
    .from("ingest_jobs")
    .insert({ doc_id: docId, status: "queued" })
    .select("id")
    .single();
  if (error) throw new Error(`ingest_jobs 생성 실패: ${error.message}`);
  return (data as { id: string }).id;
}

async function enqueue(
  client: SupabaseClient,
  payload: Record<string, unknown>,
): Promise<void> {
  const { error } = await client.rpc("ingest_queue_send", { payload });
  if (error) throw new Error(`${payload.stage} enqueue 실패: ${error.message}`);
}

/**
 * 원본 `_reset_doc_for_reingest` — chunks 전부 삭제 + 재계산 대상 필드 reset.
 *
 * `flags.ingest_mode` **만** 살린다. 호출자가 새 모드로 덮어쓸 수 있도록 남기는 것이고,
 * scan·has_pii 같은 다른 시그널은 재인제스트가 다시 계산하므로 지운다.
 */
export async function resetDocForReingest(
  client: SupabaseClient,
  docId: string,
): Promise<number> {
  const { count, error: cErr } = await client
    .from("chunks")
    .select("id", { count: "exact", head: true })
    .eq("doc_id", docId);
  if (cErr) throw new Error(`chunks 수 조회 실패: ${cErr.message}`);
  const chunksDeleted = count ?? 0;
  if (chunksDeleted > 0) {
    const { error: dErr } = await client.from("chunks").delete().eq("doc_id", docId);
    if (dErr) throw new Error(`chunks 삭제 실패: ${dErr.message}`);
  }

  const { data, error: fErr } = await client
    .from("documents").select("flags").eq("id", docId).limit(1);
  if (fErr) throw new Error(`flags 조회 실패: ${fErr.message}`);
  const existing = ((data ?? [])[0] as { flags?: Record<string, unknown> } | undefined)
    ?.flags ?? {};
  const preserved: Record<string, unknown> = {};
  if ("ingest_mode" in existing) preserved["ingest_mode"] = existing["ingest_mode"];

  const { error: uErr } = await client.from("documents").update({
    tags: [],
    summary: null,
    flags: preserved,
    doc_embedding: null,
  }).eq("id", docId);
  if (uErr) throw new Error(`documents reset 실패: ${uErr.message}`);
  return chunksDeleted;
}

/** 미지정이면 기존 모드, 그것도 없으면 default. */
function resolveMode(
  raw: string | null,
  existingFlags: Record<string, unknown>,
): IngestMode {
  if (raw === null) {
    return asIngestMode(existingFlags["ingest_mode"]) ?? DEFAULT_INGEST_MODE;
  }
  return validatedMode(raw);
}

/** `POST /documents/{doc_id}/reingest` */
export async function reingestDocument(
  deps: ReingestDeps,
  userId: string,
  docId: string,
  params: URLSearchParams,
): Promise<ReingestResult> {
  try {
    const doc = await fetchOwnedDoc(deps.client, docId, userId, "id, flags, user_id");
    await assertNoRunningJob(deps.client, docId, " 완료 후 다시 시도하세요.");
    const mode = resolveMode(params.get("mode"), doc.flags ?? {});

    const chunksDeleted = await resetDocForReingest(deps.client, docId);

    // reset 이 flags 를 비운 뒤 새 모드를 명시한다 — 원본과 같이 **빈 dict 에서** 시작한다.
    const { error } = await deps.client
      .from("documents")
      .update({ flags: flagsWithIngestMode({}, mode) })
      .eq("id", docId);
    if (error) throw new Error(`flags 갱신 실패: ${error.message}`);

    const jobId = await createJob(deps.client, docId);
    await enqueue(deps.client, { job_id: jobId, doc_id: docId, stage: "extract" });

    return {
      status: 202,
      body: { doc_id: docId, job_id: jobId, chunks_deleted: chunksDeleted },
    };
  } catch (e) {
    if (e instanceof HttpError) return { status: e.status, body: { detail: e.detail } };
    throw e;
  }
}

/** 원본 `ReingestMissingResponse.note` 기본값. */
const MISSING_NOTE = "incremental vision reingest — 누락 페이지만 처리 후 백그라운드 진행. " +
  "결과는 GET /documents/{id}/status 로 폴링.";

/** `POST /documents/{doc_id}/reingest-missing` */
export async function reingestMissingVision(
  deps: ReingestDeps,
  userId: string,
  docId: string,
  params: URLSearchParams,
): Promise<ReingestResult> {
  try {
    const doc = await fetchOwnedDoc(
      deps.client,
      docId,
      userId,
      "id, doc_type, flags, user_id, storage_path",
    );
    // 원본 순서 그대로다 — PDF 검사가 409 보다 **먼저**다.
    if (doc.doc_type !== "pdf") {
      throw new HttpError(400, "incremental vision reingest 는 PDF 만 지원합니다.");
    }
    await assertNoRunningJob(deps.client, docId, "");
    const existingFlags = doc.flags ?? {};
    const mode = resolveMode(params.get("mode"), existingFlags);

    // 호출 시점의 누락 페이지를 응답에 즉시 담는다(원본과 같다).
    const path = doc.storage_path;
    if (!path) throw new Error(`storage_path 가 비었다: ${docId}`);
    const bytes = deps.download
      ? await deps.download(path)
      : await defaultDownload(deps.client, deps.bucket, path);
    const totalPages = await countPdfPages(bytes);
    const processed = await visionProcessedPages(deps.client, docId);
    const missing: number[] = [];
    for (let p = 1; p <= totalPages; p++) if (!processed.has(p)) missing.push(p);

    // 모드가 **바뀐 경우에만** 갱신한다 — 전체 reingest 와 달리 flags 를 보존한다.
    if (existingFlags["ingest_mode"] !== mode) {
      const { error } = await deps.client
        .from("documents")
        .update({ flags: flagsWithIngestMode(existingFlags, mode) })
        .eq("id", docId);
      if (error) throw new Error(`flags 갱신 실패: ${error.message}`);
    }

    const jobId = await createJob(deps.client, docId);
    await enqueue(deps.client, {
      job_id: jobId,
      doc_id: docId,
      stage: "vision_missing",
      from: 0,
    });

    return {
      status: 202,
      body: {
        doc_id: docId,
        job_id: jobId,
        total_pages: totalPages,
        missing_pages_before: missing,
        note: MISSING_NOTE,
      },
    };
  } catch (e) {
    if (e instanceof HttpError) return { status: e.status, body: { detail: e.detail } };
    throw e;
  }
}

async function defaultDownload(
  client: SupabaseClient,
  bucket: string,
  path: string,
): Promise<Uint8Array> {
  const { data, error } = await client.storage.from(bucket).download(path);
  if (error) throw new Error(`Storage 다운로드 실패 (${path}): ${error.message}`);
  if (!data) throw new Error(`Storage 응답이 비었다 (${path})`);
  return new Uint8Array(await data.arrayBuffer());
}
