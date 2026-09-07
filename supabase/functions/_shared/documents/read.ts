/**
 * `/documents` 읽기 라우트 3종 — `routers/documents.py` 포팅.
 *
 * | 라우트 | 원본 |
 * |---|---|
 * | `GET /documents` | `list_documents` |
 * | `GET /documents/{id}` | `get_document` |
 * | `GET /documents/{id}/status` | `get_document_status` |
 *
 * ## 소유권은 **404 로** 막는다
 * 원본 주석 그대로 "타인 doc 은 404(존재 위장)". 403 을 주면 그 id 가 존재한다는 사실이
 * 새어 나간다. 목록은 애초에 `user_id` 로 걸러 조회한다.
 *
 * ## 익명 방문자도 읽는다 — 그게 설계다
 * 토큰이 없으면 `getCurrentUser` 가 owner 컨텍스트(`isAuthenticated: false`)를 준다.
 * 수익화 W1 의 "데모 병행" — 로그인 없이 owner 문서를 read-only 로 보여준다.
 * 그래서 여기에는 `requireAuthenticatedUser` 를 걸지 않는다(쓰기 라우트에만 건다).
 *
 * ## `failed` 필터를 Python 쪽에서 한다
 * 원본이 그렇게 한다 — PostgREST 의 jsonb 연산자로 "`flags.failed` 가 true 가 아닌 것"
 * 을 표현하려면 NULL 인 row 까지 걸리는 OR 조합이 까다롭다. 페이지네이션도 그래서
 * 조회 뒤에 자른다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

/** `ingest_jobs` 한 행 → 응답의 `JobStatus`. */
export interface JobStatus {
  job_id: string;
  status: string;
  current_stage: string | null;
  attempts: number;
  error_msg: string | null;
  queued_at: string;
  started_at: string | null;
  finished_at: string | null;
  /**
   * 아래 둘은 이 라우트들에서 **항상 `null`** 이다. 원본이 `JobStatus(...)` 를 만들 때
   * 안 넘겨서 pydantic 기본값이 들어간다 — 실제 값은 `/documents/active` 만 채운다.
   * 빠뜨렸다가 응답 대조에서 걸렸다. 키가 없는 것과 `null` 인 것은 다르다.
   */
  estimated_remaining_ms: number | null;
  stage_progress: Record<string, unknown> | null;
}

function toJobStatus(row: Record<string, unknown> | null): JobStatus | null {
  if (!row) return null;
  return {
    job_id: row["id"] as string,
    status: row["status"] as string,
    current_stage: (row["current_stage"] as string) ?? null,
    attempts: (row["attempts"] as number) ?? 0,
    error_msg: (row["error_msg"] as string) ?? null,
    queued_at: row["queued_at"] as string,
    started_at: (row["started_at"] as string) ?? null,
    finished_at: (row["finished_at"] as string) ?? null,
    estimated_remaining_ms: null,
    stage_progress: null,
  };
}

/** `get_latest_job_for_doc` — queued_at 최신 1건. */
async function latestJob(
  client: SupabaseClient,
  docId: string,
): Promise<Record<string, unknown> | null> {
  const { data, error } = await client
    .from("ingest_jobs")
    .select("*")
    .eq("doc_id", docId)
    .order("queued_at", { ascending: false })
    .limit(1);
  if (error) throw new Error(`ingest_jobs 조회 실패: ${error.message}`);
  return (data ?? [])[0] ?? null;
}

async function chunkCount(client: SupabaseClient, docId: string): Promise<number> {
  const { count, error } = await client
    .from("chunks")
    .select("id", { count: "exact", head: true })
    .eq("doc_id", docId);
  if (error) throw new Error(`chunks 조회 실패: ${error.message}`);
  return count ?? 0;
}

export interface ReadResult {
  status: number;
  body: Record<string, unknown>;
}

const NOT_FOUND: ReadResult = { status: 404, body: { detail: "문서를 찾을 수 없습니다." } };

/**
 * FastAPI(pydantic v2) 의 422 본문을 그대로 낸다.
 *
 * 문구·필드를 지어내면 안 된다 — 프런트가 `detail[].loc` 이나 `type` 을 보고 분기할 수
 * 있다. Railway 응답을 실측해 `fixtures/documents_422_measured.json` 에 떠 뒀고
 * `verify_documents_read_parity.py` 가 그걸로 대조한다.
 *
 * **오류가 여러 개면 전부 담는다** — `?limit=0&offset=-1` 은 항목 2 개다(실측).
 */
export interface ValidationItem {
  type: string;
  loc: [string, string];
  msg: string;
  input: string;
  ctx?: Record<string, number>;
}

function intError(name: string, raw: string): ValidationItem {
  return {
    type: "int_parsing",
    loc: ["query", name],
    msg: "Input should be a valid integer, unable to parse string as an integer",
    input: raw,
  };
}

/** 쿼리 정수 파라미터. 실패하면 `ValidationItem` 을 모아 준다. */
export function parseIntParam(
  raw: string | null,
  dflt: number,
  bounds: { ge?: number; le?: number },
  name: string,
  errors: ValidationItem[],
): number {
  if (raw === null) return dflt;
  // pydantic 은 "3.5" 도 int 로 못 읽는다(실측). 부호 붙은 정수만 받는다.
  if (!/^[+-]?\d+$/.test(raw.trim()) || raw.trim() === "") {
    errors.push(intError(name, raw));
    return dflt;
  }
  const n = Number(raw);
  if (bounds.ge !== undefined && n < bounds.ge) {
    errors.push({
      type: "greater_than_equal",
      loc: ["query", name],
      msg: `Input should be greater than or equal to ${bounds.ge}`,
      input: raw,
      ctx: { ge: bounds.ge },
    });
    return dflt;
  }
  if (bounds.le !== undefined && n > bounds.le) {
    errors.push({
      type: "less_than_equal",
      loc: ["query", name],
      msg: `Input should be less than or equal to ${bounds.le}`,
      input: raw,
      ctx: { le: bounds.le },
    });
    return dflt;
  }
  return n;
}

/**
 * FastAPI `bool` 쿼리 파싱.
 *
 * 유효값을 실측했다(Railway 200/422): `true/false · 1/0 · yes/no · on/off · y/n · t/f`
 * (대소문자 무관). 그 밖은 **422** 다 — 빈 문자열과 `2` 도 무효다.
 * 처음엔 "아니면 false" 로 뒀다가 대조에서 걸렸다.
 */
const BOOL_TRUE = new Set(["true", "1", "yes", "on", "y", "t"]);
const BOOL_FALSE = new Set(["false", "0", "no", "off", "n", "f"]);

export function parseBoolParam(
  raw: string | null,
  name: string,
  errors: ValidationItem[],
): boolean {
  if (raw === null) return false;
  const v = raw.toLowerCase();
  if (BOOL_TRUE.has(v)) return true;
  if (BOOL_FALSE.has(v)) return false;
  errors.push({
    type: "bool_parsing",
    loc: ["query", name],
    msg: "Input should be a valid boolean, unable to interpret input",
    input: raw,
  });
  return false;
}

/** `GET /documents` — 최신순 목록. 항목마다 청크 수와 최신 잡 상태를 붙인다. */
export async function listDocuments(
  client: SupabaseClient,
  userId: string,
  params: URLSearchParams,
): Promise<ReadResult> {
  // 원본은 **선언 순서대로** 검증하고 오류를 모아서 한 번에 낸다.
  const errors: ValidationItem[] = [];
  const limit = parseIntParam(params.get("limit"), 20, { ge: 1, le: 100 }, "limit", errors);
  const offset = parseIntParam(params.get("offset"), 0, { ge: 0 }, "offset", errors);
  const includeFailed = parseBoolParam(params.get("include_failed"), "include_failed", errors);
  if (errors.length > 0) return { status: 422, body: { detail: errors } };

  const { data, error } = await client
    .from("documents")
    .select(
      "id, title, doc_type, source_channel, size_bytes, content_type, " +
        "tags, summary, flags, created_at",
    )
    .eq("user_id", userId)
    .is("deleted_at", null)
    .order("created_at", { ascending: false });
  if (error) throw new Error(`documents 조회 실패: ${error.message}`);

  const all = (data ?? []) as unknown as Record<string, unknown>[];
  // 원본과 같이 **조회 뒤에** 거른다. jsonb 부정 조건이 NULL row 까지 걸리기 때문이다.
  const filtered = includeFailed
    ? all
    : all.filter((d) => !(d["flags"] as Record<string, unknown> | null)?.["failed"]);

  const total = filtered.length;
  const page = filtered.slice(offset, offset + limit);

  const items = [];
  for (const doc of page) {
    const docId = doc["id"] as string;
    const job = await latestJob(client, docId);
    items.push({
      id: docId,
      title: doc["title"],
      doc_type: doc["doc_type"],
      source_channel: doc["source_channel"],
      size_bytes: doc["size_bytes"],
      content_type: doc["content_type"],
      tags: (doc["tags"] as unknown[]) ?? [],
      summary: doc["summary"] ?? null,
      flags: (doc["flags"] as Record<string, unknown>) ?? {},
      chunks_count: await chunkCount(client, docId),
      latest_job_status: job ? job["status"] : null,
      latest_job_stage: job ? (job["current_stage"] ?? null) : null,
      created_at: doc["created_at"],
    });
  }

  return { status: 200, body: { total, limit, offset, items } };
}

/** `GET /documents/{id}` — 단건 종합. 타인 문서는 **404**(존재 위장). */
export async function getDocument(
  client: SupabaseClient,
  userId: string,
  docId: string,
): Promise<ReadResult> {
  const { data, error } = await client
    .from("documents")
    .select(
      "id, title, doc_type, source_channel, size_bytes, content_type, " +
        "tags, summary, flags, created_at, received_ms, user_id",
    )
    .eq("id", docId)
    .is("deleted_at", null)
    .limit(1);
  if (error) throw new Error(`documents 조회 실패: ${error.message}`);

  const doc = (data ?? [])[0] as unknown as Record<string, unknown> | undefined;
  if (!doc || doc["user_id"] !== userId) return NOT_FOUND;

  const flags = (doc["flags"] as Record<string, unknown>) ?? {};
  const rawUrl = flags["source_url"];
  return {
    status: 200,
    body: {
      id: doc["id"],
      title: doc["title"],
      doc_type: doc["doc_type"],
      source_channel: doc["source_channel"],
      size_bytes: doc["size_bytes"],
      content_type: doc["content_type"],
      tags: (doc["tags"] as unknown[]) ?? [],
      summary: doc["summary"] ?? null,
      flags,
      chunks_count: await chunkCount(client, docId),
      latest_job: toJobStatus(await latestJob(client, docId)),
      created_at: doc["created_at"],
      received_ms: doc["received_ms"] ?? null,
      source_url: typeof rawUrl === "string" ? rawUrl : null,
    },
  };
}

/**
 * `GET /documents/{id}/status` — 잡 상태(+선택 로그).
 *
 * **`deleted_at` 을 보지 않는다.** 원본이 그렇다 — 삭제된 문서의 진행 상태도 조회된다.
 * 상세(`get_document`)와 다른 점이라 그대로 옮긴다.
 */
export async function getDocumentStatus(
  client: SupabaseClient,
  userId: string,
  docId: string,
  includeLogs: boolean,
): Promise<ReadResult> {
  const { data, error } = await client
    .from("documents")
    .select("id, user_id")
    .eq("id", docId)
    .limit(1);
  if (error) throw new Error(`documents 조회 실패: ${error.message}`);

  const doc = (data ?? [])[0] as unknown as Record<string, unknown> | undefined;
  if (!doc || doc["user_id"] !== userId) return NOT_FOUND;

  const job = await latestJob(client, docId);
  let logs: unknown[] | null = null;
  if (includeLogs && job) {
    const { data: rows, error: logErr } = await client
      .from("ingest_logs")
      .select("*")
      .eq("job_id", job["id"] as string)
      .order("started_at", { ascending: true });
    if (logErr) throw new Error(`ingest_logs 조회 실패: ${logErr.message}`);
    logs = rows ?? [];
  }

  return { status: 200, body: { doc_id: docId, job: toJobStatus(job), logs } };
}
