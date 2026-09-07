/**
 * `/documents/active` · `/documents/batch-status` — `routers/documents.py` 포팅.
 *
 * 둘 다 프런트 폴러가 주기적으로 부른다. `/ingest` 페이지의 진행 카드와
 * 문서 목록 상태 갱신이 여기에 달려 있다.
 *
 * ## 공통 구조 — "doc_id 별 latest job"
 * 두 라우트 다 `ingest_jobs` 를 `queued_at desc` 로 한 번에 긁고 **doc_id 별 첫 행**만
 * 남긴다. SQL 에서 status 로 거르지 않는 게 핵심이다 — 원본 주석대로 같은 doc 에
 * "어제 failed + 오늘 completed" 가 있으면 failed 만 뽑혀 **완료된 문서가 계속 진행
 * 중으로 보인다.**
 *
 * ## `stage_progress` fallback 은 옮기지 않았다
 * 원본에는 그 컬럼이 없는 환경(마이그 010 미적용)을 위한 1 회 재시도 fallback 이 있다.
 * **운영 DB 에 컬럼이 있는 것을 실측했다**(2026-09-07). 죽은 경로를 옮기면 그게 도는지
 * 아무도 확인하지 못한다. 컬럼이 사라지면 조용히 넘기지 말고 500 으로 드러나는 편이 낫다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";
import { isUuid } from "./uuid_guard.ts";

import { computeRemainingMs, type StageProgress } from "./eta.ts";
import { parseIntParam, type ReadResult, type ValidationItem } from "./read.ts";

/** 원본 상수. */
const ACTIVE_DOC_DEFAULT_HOURS = 24;
const ACTIVE_DOC_MAX_HOURS = 168; // 7일
const ACTIVE_DOC_STATUSES = new Set(["queued", "running", "failed"]);
const BATCH_STATUS_MAX_IDS = 50;

/** `_INGEST_JOBS_BASE_COLUMNS` + stage_progress. */
const JOB_COLUMNS =
  "id, doc_id, status, current_stage, attempts, error_msg, queued_at, started_at, finished_at, stage_progress";

interface JobRow {
  id: string;
  doc_id: string;
  status: string;
  current_stage: string | null;
  attempts: number | null;
  error_msg: string | null;
  queued_at: string;
  started_at: string | null;
  finished_at: string | null;
  stage_progress: StageProgress | null;
}

/** `queued_at desc` 로 정렬된 행에서 doc_id 별 **첫** 행만 남긴다. */
function latestByDoc(rows: JobRow[]): Map<string, JobRow> {
  const out = new Map<string, JobRow>();
  for (const row of rows) if (!out.has(row.doc_id)) out.set(row.doc_id, row);
  return out;
}

async function toJobStatus(
  client: SupabaseClient,
  row: JobRow,
): Promise<Record<string, unknown>> {
  return {
    job_id: row.id,
    status: row.status,
    current_stage: row.current_stage ?? null,
    attempts: row.attempts ?? 0,
    error_msg: row.error_msg ?? null,
    queued_at: row.queued_at,
    started_at: row.started_at ?? null,
    finished_at: row.finished_at ?? null,
    estimated_remaining_ms: await computeRemainingMs(client, {
      jobStatus: row.status,
      currentStage: row.current_stage ?? null,
      stageProgress: row.stage_progress,
    }),
    stage_progress: row.stage_progress ?? null,
  };
}

/**
 * `GET /documents/active` — 최근 N 시간 내 **진행 중·실패** 문서.
 *
 * `completed`/`cancelled` 가 latest 면 자연 제외된다.
 */
export async function listActiveDocuments(
  client: SupabaseClient,
  userId: string,
  params: URLSearchParams,
): Promise<ReadResult> {
  const errors: ValidationItem[] = [];
  const hours = parseIntParam(
    params.get("hours"),
    ACTIVE_DOC_DEFAULT_HOURS,
    { ge: 1, le: ACTIVE_DOC_MAX_HOURS },
    "hours",
    errors,
  );
  if (errors.length > 0) return { status: 422, body: { detail: errors } };

  const cutoff = new Date(Date.now() - hours * 3600_000).toISOString();

  const { data, error } = await client
    .from("ingest_jobs")
    .select(JOB_COLUMNS)
    .gte("queued_at", cutoff)
    .order("queued_at", { ascending: false });
  if (error) throw new Error(`ingest_jobs 조회 실패: ${error.message}`);

  // **status 를 SQL 에서 거르지 않는다.** 거르면 "어제 failed + 오늘 completed" 인
  // 문서가 계속 진행 중으로 보인다(원본 주석).
  const latest = latestByDoc((data ?? []) as unknown as JobRow[]);
  for (const [docId, row] of latest) {
    if (!ACTIVE_DOC_STATUSES.has(row.status)) latest.delete(docId);
  }
  if (latest.size === 0) return { status: 200, body: { items: [] } };

  const { data: docs, error: docErr } = await client
    .from("documents")
    .select("id, title, size_bytes")
    .in("id", [...latest.keys()])
    .eq("user_id", userId);
  if (docErr) throw new Error(`documents 조회 실패: ${docErr.message}`);

  const meta = new Map<string, { title?: string; size_bytes?: number }>();
  for (const d of (docs ?? []) as unknown as Record<string, unknown>[]) {
    meta.set(d["id"] as string, d as { title?: string; size_bytes?: number });
  }

  const items = [];
  for (const [docId, row] of latest) {
    // 타인 문서는 여기서 자연히 빠진다 — `documents` 조회에 user_id 를 걸었다.
    const m = meta.get(docId);
    if (!m) continue;
    items.push({
      doc_id: docId,
      file_name: m.title || docId,
      size_bytes: m.size_bytes || 0,
      job: await toJobStatus(client, row),
    });
  }
  return { status: 200, body: { items } };
}

/**
 * `GET /documents/batch-status?ids=a,b,c` — 여러 문서의 latest job 을 한 번에.
 *
 * 미소유 id 는 **404 가 아니라 결과에서 빠진다** — 배치라 부분 응답이 자연스럽다.
 * 프런트 폴러가 `doc_id` 키로 매핑하므로 누락은 `undefined` 가 된다(원본 주석).
 */
export async function batchStatus(
  client: SupabaseClient,
  userId: string,
  params: URLSearchParams,
): Promise<ReadResult> {
  const raw = params.get("ids");
  if (raw === null) {
    // 원본은 `Query(...)` 필수라 누락 시 pydantic 422 다.
    return {
      status: 422,
      body: {
        detail: [{
          type: "missing",
          loc: ["query", "ids"],
          msg: "Field required",
          input: null,
        }],
      },
    };
  }

  const docIds = raw.split(",").map((s) => s.trim()).filter((s) => s);
  if (docIds.length === 0) {
    return { status: 400, body: { detail: "ids 가 비어있습니다." } };
  }
  if (docIds.length > BATCH_STATUS_MAX_IDS) {
    return {
      status: 400,
      body: { detail: `한 번에 최대 ${BATCH_STATUS_MAX_IDS}개 (요청: ${docIds.length})` },
    };
  }

  // UUID 가 아닌 id 는 여기서 뺀다 — 넣으면 Postgres 가 통째로 500 을 낸다.
  // 존재할 수 없는 id 이므로 **없는 문서와 같은 취급**이다(응답에서 빠진다).
  const queryIds = docIds.filter(isUuid);
  if (queryIds.length === 0) return { status: 200, body: { items: [] } };

  // 본인 소유만 — IDOR 차단. **입력 순서를 보존한다.**
  const { data: owned, error } = await client
    .from("documents")
    .select("id")
    .in("id", queryIds)
    .eq("user_id", userId);
  if (error) throw new Error(`documents 조회 실패: ${error.message}`);
  const ownedIds = new Set((owned ?? []).map((r) => (r as { id: string }).id));
  const allowed = docIds.filter((d) => ownedIds.has(d));
  if (allowed.length === 0) return { status: 200, body: { items: [] } };

  const { data, error: jobErr } = await client
    .from("ingest_jobs")
    .select(JOB_COLUMNS)
    .in("doc_id", allowed)
    .order("queued_at", { ascending: false });
  if (jobErr) throw new Error(`ingest_jobs 조회 실패: ${jobErr.message}`);

  const latest = latestByDoc((data ?? []) as unknown as JobRow[]);
  const items = [];
  for (const docId of allowed) {
    const row = latest.get(docId);
    items.push({
      doc_id: docId,
      job: row ? await toJobStatus(client, row) : null,
    });
  }
  return { status: 200, body: { items } };
}
