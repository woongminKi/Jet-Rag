# 서버 근본 수정 (S1~S4) 구현 플랜 — 자동 수집 ① 서버 편

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 웹 업로드·이메일·PC 에이전트(그리고 뒤이어 아이폰·안드로이드)가 같은 저장 함수·같은 인증(기기 토큰)·같은 계량(용량 + 월 Vision 페이지) 위에서 동작하게 서버를 고친다.

**Architecture:** Supabase Edge Functions(Deno)의 `api-documents`·`api-account`·`api-ingest-worker` 와 `_shared/` 모듈을 수정한다. 저장 로직은 `_shared/documents/persist.ts` 하나로 모으고, 기기 토큰은 `current_user.ts` 의 3-way 분기에 네 번째 갈래로 붙이며, 계량은 `plans` 컬럼 교체 + 워커 앞단의 게이트로 구현한다. DB 변경은 마이그 030·031 두 개.

**Tech Stack:** Deno 2.x / TypeScript, supabase-js, pgmq + pg_cron, PostgreSQL(RLS), Next.js 16 웹(설정 화면). 테스트는 `deno test --allow-env --allow-net --allow-read` (CI 와 같은 명령), 웹은 `pnpm exec tsc --noEmit && pnpm lint`.

**스펙:** `docs/superpowers/specs/2026-09-15-pc-agent-auto-ingest-design.md` §4·§6·§7·§8 (1~5단계). 에이전트(6~9단계)는 별도 플랜.

---

## 파일 구조

| 파일 | 책임 | 작업 |
|---|---|---|
| `supabase/functions/_shared/documents/persist.ts` | **신규.** 바이트 → 게이트 → dedup → Storage → documents → job → 큐. 세 경로 공용 | Task 1 |
| `supabase/functions/_shared/documents/persist_test.ts` | 신규. persist 계약 | Task 1 |
| `supabase/functions/_shared/documents/upload.ts` | multipart 파싱 + `SOURCE_CHANNELS` 만 남김. persist 호출 | Task 1 |
| `supabase/functions/_shared/ingest/email_ingest.ts` | 토큰·발신자 검사만 남김. persist 호출. `EMAIL_ALLOWED_EXTENSIONS` 제거 | Task 1 |
| `supabase/functions/_shared/device_token.ts` | 신규. 토큰 생성·해시·형식 판정·조회·스코프 판정 | Task 2 |
| `supabase/functions/_shared/current_user.ts` | Bearer `jrd_` 분기 → `authKind: "device"` | Task 2 |
| `supabase/functions/_shared/me/devices.ts` | 신규. 발급·목록·폐기 | Task 2 |
| `supabase/functions/api-account/index.ts` | `/me/devices` 라우트 | Task 2 |
| `supabase/functions/api-documents/index.ts` | 기기 토큰 스코프 게이트, precheck 라우트, 상한 교체 | Task 2·3·4 |
| `api/migrations/030_device_tokens.sql` | 신규 | Task 2 |
| `web/src/lib/api/client.ts` | `apiPostJson`·`apiDelete` 추가 | Task 2 |
| `web/src/components/jet-rag/devices-section.tsx` | 신규. 연결된 기기 UI | Task 2 |
| `supabase/functions/_shared/documents/precheck.ts` | 신규. sha256 목록 분류 | Task 3 |
| `api/migrations/031_metering_v2.sql` | 신규. plans 컬럼·CHECK 확장·RPC 3개·cron | Task 4 |
| `supabase/functions/_shared/me/quota.ts` | `PlanLimits` 교체, `storageUsedBytes`·`visionPagesUsedMonth`·`makeStorageCheck` | Task 4 |
| `supabase/functions/_shared/rate_limit.ts` | `METRIC_DOCS` 제거, `enforceUploadBurst` 추가 | Task 4 |
| `supabase/functions/_shared/ingest/quota_gate.ts` | 신규. Vision 단계 진입 전 월 페이지 게이트 | Task 4 |
| `supabase/functions/_shared/ingest/worker.ts` | `gate` 주입점 + `deferred_quota` 전이 | Task 4 |
| `supabase/functions/api-ingest-worker/index.ts` | 게이트 결선 | Task 4 |
| `supabase/functions/_shared/me/pipeline.ts` | `/me/plan` 새 모양 | Task 4 |
| `supabase/functions/_shared/config.ts` | `rateLimitDocsPerDay` 제거 | Task 4 |
| `web/src/app/settings/page.tsx`, `subscription-section.tsx` | 계량 표시 | Task 4 |
| `api/scripts/measure_queue_throughput.ts` | 신규. 재인제스트 배치로 시간당 건수 실측 | Task 5 |

각 Task 끝에 커밋한다. 커밋 메시지는 한국어, Claude 서명 없음(워크스페이스 규칙).

---

## Task 1: S1 — `persist.ts` 로 저장 로직 통합

**Files:**
- Create: `supabase/functions/_shared/documents/persist.ts`
- Create: `supabase/functions/_shared/documents/persist_test.ts`
- Modify: `supabase/functions/_shared/documents/upload.ts` (전체 재작성, 225줄 → 약 90줄)
- Modify: `supabase/functions/_shared/ingest/email_ingest.ts:20-40, 100-216`
- Test: 기존 `upload_test.ts`, `email_route_test.ts` 는 **그대로 통과해야 한다** (동작 보존 리팩터)

- [ ] **Step 1: persist 테스트 작성 (실패 확인용)**

`supabase/functions/_shared/documents/persist_test.ts`:

```ts
import { assertEquals } from "@std/assert";
import { persistDocument } from "./persist.ts";

const PDF = new Uint8Array([...new TextEncoder().encode("%PDF-1.7\n"), ...new Uint8Array(400)]);
const PNG = new Uint8Array([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, ...new Uint8Array(400)]);

interface FakeOpts {
  existing?: { id: string; flags?: Record<string, unknown> }[];
  /** documents insert 가 UNIQUE 충돌(23505)을 내게 한다. */
  insertConflict?: boolean;
}

function fakeClient(opts: FakeOpts = {}) {
  const inserts: { table: string; row: Record<string, unknown> }[] = [];
  const updates: Record<string, unknown>[] = [];
  const sends: Record<string, unknown>[] = [];
  const uploads: { path: string; size: number }[] = [];
  let selectCalls = 0;
  const client = {
    from(table: string) {
      const api = {
        select: () => api,
        eq: () => api,
        is: () => api,
        limit: () => {
          selectCalls++;
          // UNIQUE 충돌 뒤 재조회는 존재하는 행을 돌려준다.
          if (opts.insertConflict && selectCalls >= 2) {
            return Promise.resolve({ data: [{ id: "raced-doc", flags: {} }], error: null });
          }
          return Promise.resolve({ data: opts.existing ?? [], error: null });
        },
        insert(row: Record<string, unknown>) {
          inserts.push({ table, row });
          if (table === "documents" && opts.insertConflict) {
            return {
              select: () => ({
                single: () =>
                  Promise.resolve({
                    data: null,
                    error: { code: "23505", message: "duplicate key value violates unique constraint" },
                  }),
              }),
            };
          }
          return {
            select: () => ({ single: () => Promise.resolve({ data: { id: `${table}-new` }, error: null }) }),
          };
        },
        update(row: Record<string, unknown>) {
          updates.push(row);
          return { eq: () => Promise.resolve({ error: null }) };
        },
      };
      return api;
    },
    rpc(name: string, args: Record<string, unknown>) {
      if (name === "ingest_queue_send") sends.push(args.payload as Record<string, unknown>);
      return Promise.resolve({ data: 1, error: null });
    },
    storage: { from: () => ({ upload: () => Promise.resolve({ error: null }) }) },
  };
  const deps = {
    // deno-lint-ignore no-explicit-any
    client: client as any,
    bucket: "documents",
    userId: "u1",
    upload: (path: string, bytes: Uint8Array) => {
      uploads.push({ path, size: bytes.length });
      return Promise.resolve();
    },
  };
  return { deps, inserts, updates, sends, uploads };
}

const base = { fileName: "계약서.pdf", sourceChannel: "drag-drop", ingestMode: "default" as const };

Deno.test("persist — 게이트 순서: 확장자 → 빈 파일 → 크기 → 매직. 거절이면 Storage 안 건드림", async () => {
  const f = fakeClient();
  const r1 = await persistDocument({ ...base, fileName: "bad.exe", bytes: PDF }, f.deps);
  assertEquals(r1.ok, false);
  if (!r1.ok) assertEquals(r1.status, 400);
  const r2 = await persistDocument({ ...base, bytes: new Uint8Array(0) }, f.deps);
  assertEquals(r2.ok, false);
  if (!r2.ok) assertEquals(r2.status, 400);
  const r3 = await persistDocument({ ...base, fileName: "fake.pdf", bytes: PNG }, f.deps);
  assertEquals(r3.ok, false);
  if (!r3.ok) assertEquals(r3.detail.includes("일치하지 않습니다"), true);
  assertEquals(f.uploads.length, 0);
  assertEquals(f.inserts.length, 0);
});

Deno.test("persist — 신규: Storage · documents · job · 큐", async () => {
  const f = fakeClient();
  const r = await persistDocument({ ...base, bytes: PDF }, f.deps);
  assertEquals(r.ok, true);
  if (!r.ok) return;
  assertEquals(r.outcome, "created");
  assertEquals(r.docId, "documents-new");
  assertEquals(r.jobId, "ingest_jobs-new");
  const doc = f.inserts.find((i) => i.table === "documents")!.row;
  assertEquals(doc.title, "계약서");
  assertEquals(doc.source_channel, "drag-drop");
  assertEquals(doc.flags, { ingest_mode: "default" });
  assertEquals(f.sends[0].stage, "extract");
});

Deno.test("persist — 정상 중복은 duplicated, 아무것도 안 만든다", async () => {
  const f = fakeClient({ existing: [{ id: "old", flags: {} }] });
  const r = await persistDocument({ ...base, bytes: PDF }, f.deps);
  assertEquals(r.ok, true);
  if (!r.ok) return;
  assertEquals(r.outcome, "duplicated");
  assertEquals(r.docId, "old");
  assertEquals(r.jobId, null);
  assertEquals(f.uploads.length, 0);
});

Deno.test("persist — 실패 흔적은 retried: 같은 행에 잡을 새로 건다", async () => {
  const f = fakeClient({ existing: [{ id: "failed", flags: { failed: true } }] });
  const r = await persistDocument({ ...base, bytes: PDF, ingestMode: "fast" }, f.deps);
  assertEquals(r.ok, true);
  if (!r.ok) return;
  assertEquals(r.outcome, "retried");
  assertEquals(r.docId, "failed");
  assertEquals(f.updates[0].flags, { ingest_mode: "fast" });
  assertEquals(f.uploads.length, 1);
  assertEquals(f.sends.length, 1);
});

Deno.test("persist — UNIQUE 충돌(경합)은 500 이 아니라 duplicated", async () => {
  const f = fakeClient({ insertConflict: true });
  const r = await persistDocument({ ...base, bytes: PDF }, f.deps);
  assertEquals(r.ok, true);
  if (!r.ok) return;
  assertEquals(r.outcome, "duplicated");
  assertEquals(r.docId, "raced-doc");
  assertEquals(f.sends.length, 0);
});

Deno.test("persist — 용량 검사가 거부하면 402, Storage 안 건드림", async () => {
  const f = fakeClient();
  const r = await persistDocument({ ...base, bytes: PDF }, {
    ...f.deps,
    checkStorage: () => Promise.resolve({ allowed: false, usedBytes: 900, limitBytes: 1000 }),
  });
  assertEquals(r.ok, false);
  if (r.ok) return;
  assertEquals(r.status, 402);
  assertEquals(r.reason, "storage_limit");
  assertEquals(r.used, 900);
  assertEquals(r.limit, 1000);
  assertEquals(f.uploads.length, 0);
});

Deno.test("persist — 용량 검사는 dedup 뒤에 온다: 중복이면 검사 없이 duplicated", async () => {
  const f = fakeClient({ existing: [{ id: "old", flags: {} }] });
  let called = 0;
  const r = await persistDocument({ ...base, bytes: PDF }, {
    ...f.deps,
    checkStorage: () => {
      called++;
      return Promise.resolve({ allowed: false, usedBytes: 1, limitBytes: 1 });
    },
  });
  assertEquals(r.ok, true);
  assertEquals(called, 0);
});
```

- [ ] **Step 2: 실패 확인**

Run: `cd supabase/functions && deno test --allow-env --allow-net --allow-read _shared/documents/persist_test.ts`
Expected: FAIL — `Module not found "./persist.ts"`

- [ ] **Step 3: `persist.ts` 작성**

```ts
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

export type PersistResult =
  | { ok: true; outcome: PersistOutcome; docId: string; jobId: string | null }
  | {
    ok: false;
    status: 400 | 402 | 413;
    detail: string;
    reason?: "storage_limit";
    used?: number;
    limit?: number;
  };

/** `PurePosixPath(name).suffix.lower()` 와 같다. 마지막 점 이후만, 점이 없으면 빈 문자열. */
export function extOf(name: string): string {
  const base = name.split("/").pop() ?? name;
  const i = base.lastIndexOf(".");
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
  const digest = await crypto.subtle.digest("SHA-256", bytes.slice().buffer as ArrayBuffer);
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
    return { ok: false, status: 400, detail: `지원되지 않는 확장자입니다: ${ext || "(없음)"}` };
  }
  if (bytes.length === 0) return { ok: false, status: 400, detail: "빈 파일입니다." };
  if (bytes.length > MAX_SIZE_BYTES) {
    return { ok: false, status: 413, detail: "파일 크기 상한(50MB) 초과" };
  }

  // ---- 게이트 ②: 매직바이트 ----
  try {
    validateMagic(ext, bytes.subarray(0, HEAD_BYTES));
  } catch (e) {
    if (e instanceof InputGateError) return { ok: false, status: 400, detail: e.message };
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
        reason: "storage_limit",
        used: check.usedBytes,
        limit: check.limitBytes,
        detail: `저장 용량 한도를 초과했습니다 (${formatBytes(check.usedBytes)} / ${
          formatBytes(check.limitBytes)
        }). 문서를 삭제하거나 Pro 로 업그레이드해 주세요.`,
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
    const { error } = await deps.client
      .from("documents")
      .update({
        storage_path: path,
        flags: flagsWithIngestMode({}, ingestMode),
        deleted_at: null,
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
      if ((error as { code?: string }).code === "23505") {
        const raced = await findBySha(deps.client, deps.userId, sha256);
        if (raced) return { ok: true, outcome: "duplicated", docId: raced.id, jobId: null };
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
```

`IngestMode` 타입이 `ingest_mode.ts` 에 export 돼 있는지 확인한다: `grep -n "export type IngestMode\|export.*IngestMode" supabase/functions/_shared/ingest/ingest_mode.ts`. 없으면 `validateIngestMode` 의 반환 타입에서 `mode` 의 타입 이름을 쓰고, 그것도 없으면 `ingest_mode.ts` 에 `export type IngestMode = "fast" | "default" | "precise";` 를 추가하고 기존 함수 시그니처가 그 타입을 쓰게 한다.

- [ ] **Step 4: persist 테스트 통과 확인**

Run: `cd supabase/functions && deno test --allow-env --allow-net --allow-read _shared/documents/persist_test.ts`
Expected: `ok | 7 passed | 0 failed`

- [ ] **Step 5: `upload.ts` 를 persist 위의 얇은 껍데기로 재작성**

파일 전체를 아래로 교체한다. `extOf`·`buildUserPath` 는 다른 모듈이 import 하므로 re-export 를 남긴다.

```ts
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
import { persistDocument, type StorageCheck } from "./persist.ts";

export { buildUserPath, extOf, stemOf } from "./persist.ts";

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
  if (file.size > 50 * 1024 * 1024) {
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
    const body: Record<string, unknown> = { detail: r.detail };
    if (r.reason) {
      body.reason = r.reason;
      body.used = r.used;
      body.limit = r.limit;
    }
    return { status: r.status, body };
  }
  return {
    status: 202,
    body: { doc_id: r.docId, job_id: r.jobId, duplicated: r.outcome === "duplicated" },
  };
}
```

기존 `upload.ts` 에 있던 `MAX_SIZE_BYTES` import 가 `input_gate.ts` 의 export 인지 확인하고, 위 코드의 `50 * 1024 * 1024` 를 `MAX_SIZE_BYTES` import 로 바꾼다 (`import { MAX_SIZE_BYTES } from "./input_gate.ts";`).

- [ ] **Step 6: 기존 upload 테스트 통과 확인**

Run: `cd supabase/functions && deno test --allow-env --allow-net --allow-read _shared/documents/`
Expected: 모두 PASS. `upload_test.ts` 의 "실패 흔적이 있으면 재시도" 케이스는 `duplicated: false` 를 기대하는데 `outcome === "retried"` 도 false 이므로 그대로 통과한다.

- [ ] **Step 7: `email_ingest.ts` 를 persist 위로 옮긴다**

`ingestEmailAttachment` 함수 본문과 `EMAIL_ALLOWED_EXTENSIONS`·`MAX_SIZE_BYTES` 상수를 지우고 아래로 교체한다. import 도 정리한다 (`HEAD_BYTES`·`InputGateError`·`validateMagic`·`buildUserPath`·`flagsWithIngestMode` import 제거, `persistDocument`·`StorageCheck` import 추가).

```ts
import { persistDocument, type StorageCheck } from "../documents/persist.ts";

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

function extOfName(filename: string): string {
  const dot = filename.lastIndexOf(".");
  return dot >= 0 ? filename.slice(dot).toLowerCase() : "";
}
```

`incrementDocsCounter` 는 Task 4 에서 지운다(지금은 남긴다).

- [ ] **Step 8: 이메일 테스트 통과 확인**

Run: `cd supabase/functions && deno test --allow-env --allow-net --allow-read _shared/ingest/email_route_test.ts`
Expected: PASS. `EMAIL_ALLOWED_EXTENSIONS` 를 import 하는 테스트가 있으면 그 테스트 케이스를 `ALLOWED_EXTENSIONS`(`../documents/input_gate.ts`) 로 바꾼다. `.pptx`/`.txt`/`.md` 첨부가 "비허용" 이길 기대하는 케이스가 있으면 **기대값을 accepted 로 바꾼다** (통합의 의도된 결과).

- [ ] **Step 9: 전체 테스트·fmt·lint**

Run: `cd supabase/functions && deno fmt --check && deno lint && deno test --allow-env --allow-net --allow-read`
Expected: fmt 0 오류, lint 0 오류, 전부 PASS (기존 246 + persist 7).

- [ ] **Step 10: 커밋**

```bash
git add supabase/functions/_shared/documents/persist.ts supabase/functions/_shared/documents/persist_test.ts supabase/functions/_shared/documents/upload.ts supabase/functions/_shared/ingest/email_ingest.ts
git commit -m "refactor(edge): 저장 로직을 persist.ts 하나로 통합 — 업로드·이메일이 같은 함수를 쓴다

- persistDocument: 게이트→dedup→(용량 검사 훅)→Storage→documents→job→큐. UNIQUE 충돌은 duplicated.
- upload.ts 는 multipart 파싱과 응답 매핑만. source_channel 에 pc-agent·ios-shortcut·android-agent 추가.
- email_ingest.ts 는 토큰·발신자 검사만 남기고 persist 호출. 확장자 표를 ALLOWED_EXTENSIONS 로 통일.
- 스펙 2026-09-15 §4 S1."
```

---

## Task 2: S3 — 기기 토큰 (마이그 030 + 인증 분기 + `/me/devices` + 설정 UI)

**Files:**
- Create: `api/migrations/030_device_tokens.sql`
- Create: `supabase/functions/_shared/device_token.ts`, `device_token_test.ts`
- Create: `supabase/functions/_shared/me/devices.ts`
- Modify: `supabase/functions/_shared/current_user.ts:44-52, 165-190`
- Modify: `supabase/functions/api-account/index.ts:150-195` (ME_ROUTES)
- Modify: `supabase/functions/api-documents/index.ts:56-60, 146-150`
- Modify: `web/src/lib/api/client.ts` (apiPostJson·apiDelete 추가)
- Create: `web/src/components/jet-rag/devices-section.tsx`
- Modify: `web/src/app/settings/page.tsx` (섹션 삽입)

- [ ] **Step 1: 마이그 030 작성**

`api/migrations/030_device_tokens.sql`:

```sql
-- ============================================================
-- 030_device_tokens.sql — 기기 토큰 (자동 수집 ① 스펙 §4 S3)
-- ============================================================
-- 배경
--   PC 에이전트·아이폰 단축어·안드로이드 앱은 무인으로 돈다. 브라우저 세션(1시간 JWT +
--   회전 refresh)은 갱신을 놓치면 조용히 로그아웃된다. 긴 수명·기기 단위 폐기·좁은 스코프의
--   토큰이 필요하다.
--
-- 설계
--   - 토큰 원문은 저장하지 않는다. sha256 hex 만. 원문은 발급 응답에 한 번 실린다.
--   - token_prefix: 표시용 앞 8자(`jrd_xxxx`). 사용자가 어느 기기인지 알아보는 용도.
--   - scopes: 기본 {ingest} = POST /documents, POST /documents/precheck,
--     GET /documents/{id}/status, GET /documents/batch-status 만.
--   - revoked_at 이 채워지면 401. 행은 지우지 않는다(감사 흔적).
--
-- RLS
--   본인 행 SELECT 만. 쓰기는 service_role(Edge) 만.
--
-- 적용 절차
--   Supabase Studio → SQL Editor → 본 파일 paste → Run.
--
-- 검증 SQL
--   SELECT column_name FROM information_schema.columns WHERE table_name='device_tokens';
--   SELECT polname FROM pg_policy WHERE polrelid = 'device_tokens'::regclass;
-- ============================================================

CREATE TABLE IF NOT EXISTS device_tokens (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    token_hash    TEXT NOT NULL UNIQUE,
    token_prefix  TEXT NOT NULL,
    scopes        TEXT[] NOT NULL DEFAULT '{ingest}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at  TIMESTAMPTZ,
    revoked_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_device_tokens_user ON device_tokens (user_id, created_at DESC);

ALTER TABLE device_tokens ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS device_tokens_select_own ON device_tokens;
CREATE POLICY device_tokens_select_own
    ON device_tokens FOR SELECT
    TO authenticated
    USING (user_id = auth.uid());

DROP POLICY IF EXISTS device_tokens_service_role_all ON device_tokens;
CREATE POLICY device_tokens_service_role_all
    ON device_tokens FOR ALL
    TO service_role
    USING (TRUE) WITH CHECK (TRUE);

-- ============================================================
-- 롤백
--   DROP TABLE IF EXISTS device_tokens;
-- ============================================================
```

- [ ] **Step 2: device_token 테스트 작성**

`supabase/functions/_shared/device_token_test.ts`:

```ts
import { assertEquals, assertMatch } from "@std/assert";
import {
  DEVICE_TOKEN_PREFIX,
  deviceScopeAllows,
  generateDeviceToken,
  hashDeviceToken,
  isDeviceTokenFormat,
} from "./device_token.ts";

Deno.test("generateDeviceToken — jrd_ + 43자 base64url, prefix 8자, hash 는 sha256 hex", async () => {
  const t = await generateDeviceToken();
  assertMatch(t.token, /^jrd_[A-Za-z0-9_-]{43}$/);
  assertEquals(t.prefix, t.token.slice(0, 8));
  assertEquals(t.hash, await hashDeviceToken(t.token));
  assertMatch(t.hash, /^[0-9a-f]{64}$/);
});

Deno.test("두 번 생성하면 다르다", async () => {
  const a = await generateDeviceToken();
  const b = await generateDeviceToken();
  assertEquals(a.token === b.token, false);
});

Deno.test("isDeviceTokenFormat — 접두어와 길이", () => {
  assertEquals(isDeviceTokenFormat(`${DEVICE_TOKEN_PREFIX}${"a".repeat(43)}`), true);
  assertEquals(isDeviceTokenFormat("eyJhbGciOi..."), false);
  assertEquals(isDeviceTokenFormat("jrd_short"), false);
});

Deno.test("deviceScopeAllows — ingest 는 4개 라우트만", () => {
  const s = ["ingest"];
  assertEquals(deviceScopeAllows(s, "POST", "/documents"), true);
  assertEquals(deviceScopeAllows(s, "POST", "/documents/precheck"), true);
  assertEquals(deviceScopeAllows(s, "GET", "/documents/abc/status"), true);
  assertEquals(deviceScopeAllows(s, "GET", "/documents/batch-status"), true);
  assertEquals(deviceScopeAllows(s, "GET", "/documents"), false);
  assertEquals(deviceScopeAllows(s, "GET", "/documents/abc"), false);
  assertEquals(deviceScopeAllows(s, "POST", "/documents/abc/reingest"), false);
  assertEquals(deviceScopeAllows(s, "GET", "/search"), false);
  assertEquals(deviceScopeAllows([], "POST", "/documents"), false);
});
```

- [ ] **Step 3: 실패 확인**

Run: `cd supabase/functions && deno test --allow-env --allow-net --allow-read _shared/device_token_test.ts`
Expected: FAIL — module not found

- [ ] **Step 4: `device_token.ts` 작성**

```ts
/**
 * 기기 토큰 — 무인 클라이언트(PC 에이전트·단축어·앱)용 긴 수명 Bearer.
 *
 * 형식 `jrd_` + 43자 base64url(32바이트 = 256비트). 서버는 sha256 hex 만 저장한다 —
 * 고엔트로피라 느린 해시(bcrypt)가 필요 없고, 조회가 인덱스 한 번이다.
 *
 * ## 스코프는 라우트 화이트리스트다
 * `ingest` 는 문서를 **넣고 상태를 보는** 4개 라우트만. 검색·삭제·결제·기기 관리는 막힌다.
 * 토큰이 새어도 문서를 읽거나 지우지 못한다(스펙 §4 S3).
 */

import type { SupabaseClient } from "@supabase/supabase-js";

export const DEVICE_TOKEN_PREFIX = "jrd_";
const TOKEN_BODY_LEN = 43;
const PREFIX_DISPLAY_LEN = 8;

export interface GeneratedDeviceToken {
  token: string;
  hash: string;
  prefix: string;
}

function base64url(bytes: Uint8Array): string {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export async function hashDeviceToken(token: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(token));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function generateDeviceToken(): Promise<GeneratedDeviceToken> {
  const raw = new Uint8Array(32);
  crypto.getRandomValues(raw);
  const token = `${DEVICE_TOKEN_PREFIX}${base64url(raw)}`;
  return { token, hash: await hashDeviceToken(token), prefix: token.slice(0, PREFIX_DISPLAY_LEN) };
}

export function isDeviceTokenFormat(s: string): boolean {
  return s.startsWith(DEVICE_TOKEN_PREFIX) &&
    s.length === DEVICE_TOKEN_PREFIX.length + TOKEN_BODY_LEN &&
    /^[A-Za-z0-9_-]+$/.test(s.slice(DEVICE_TOKEN_PREFIX.length));
}

export interface DeviceTokenRow {
  id: string;
  user_id: string;
  name: string;
  scopes: string[];
  last_used_at: string | null;
  revoked_at: string | null;
}

/** 해시로 조회. 폐기된 토큰은 `null` (폐기 여부는 호출자가 구분할 필요 없다 — 둘 다 401). */
export async function lookupDeviceToken(
  client: SupabaseClient,
  token: string,
): Promise<DeviceTokenRow | null> {
  const hash = await hashDeviceToken(token);
  const { data, error } = await client
    .from("device_tokens")
    .select("id, user_id, name, scopes, last_used_at, revoked_at")
    .eq("token_hash", hash)
    .limit(1);
  if (error) throw new Error(`device_tokens 조회 실패: ${error.message}`);
  const row = (data ?? [])[0] as DeviceTokenRow | undefined;
  if (!row || row.revoked_at) return null;
  return row;
}

const LAST_USED_THROTTLE_MS = 60_000;

/** `last_used_at` 을 분당 1회만 갱신한다 — 요청마다 쓰면 업로드 폭주 때 쓰기가 두 배다. */
export async function touchDeviceToken(
  client: SupabaseClient,
  row: DeviceTokenRow,
  nowMs: number,
): Promise<void> {
  const last = row.last_used_at ? Date.parse(row.last_used_at) : 0;
  if (nowMs - last < LAST_USED_THROTTLE_MS) return;
  try {
    await client.from("device_tokens")
      .update({ last_used_at: new Date(nowMs).toISOString() })
      .eq("id", row.id);
  } catch (e) {
    console.warn(`device_tokens last_used_at 갱신 실패 (id=${row.id}):`, e);
  }
}

/** `ingest` 스코프가 허용하는 라우트 — 메서드 + 경로 정규식. */
const INGEST_ROUTES: Array<[string, RegExp]> = [
  ["POST", /^\/documents$/],
  ["POST", /^\/documents\/precheck$/],
  ["GET", /^\/documents\/[^/]+\/status$/],
  ["GET", /^\/documents\/batch-status$/],
];

export function deviceScopeAllows(scopes: string[], method: string, path: string): boolean {
  if (!scopes.includes("ingest")) return false;
  return INGEST_ROUTES.some(([m, re]) => m === method && re.test(path));
}
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd supabase/functions && deno test --allow-env --allow-net --allow-read _shared/device_token_test.ts`
Expected: `4 passed`

- [ ] **Step 6: `current_user.ts` 에 기기 토큰 분기 추가**

`CurrentUser` 인터페이스에 필드 추가:

```ts
export interface CurrentUser {
  userId: string;
  email: string | null;
  isAuthenticated: boolean;
  /** `session`(JWT·쿠키) 또는 `device`(기기 토큰). 익명은 `session`. 기본값은 session. */
  authKind?: "session" | "device";
  /** 기기 토큰일 때만. */
  scopes?: string[];
  deviceId?: string;
}
```

`getCurrentUser` 시그니처와 본문 교체:

```ts
import { isDeviceTokenFormat, lookupDeviceToken, touchDeviceToken } from "./device_token.ts";
import type { SupabaseClient } from "@supabase/supabase-js";

export interface CurrentUserOpts {
  /** 기기 토큰을 받는 함수만 넘긴다. 안 넘기면 `jrd_` 토큰은 무효 JWT 로 취급돼 401 이다. */
  deviceClient?: SupabaseClient;
  now?: () => number;
}

export async function getCurrentUser(
  req: Request,
  settings: AuthSettings,
  opts: CurrentUserOpts = {},
): Promise<CurrentUser> {
  if (!settings.authEnabled) {
    return { userId: settings.defaultUserId, email: null, isAuthenticated: true, authKind: "session" };
  }

  const token = extractBearerToken(req) ?? extractCookieToken(req, settings);
  if (token === null) {
    return {
      userId: settings.ownerUserId || settings.defaultUserId,
      email: null,
      isAuthenticated: false,
      authKind: "session",
    };
  }

  // ---- 기기 토큰 (2026-09-15) — JWT 검증보다 먼저, 형식으로 가른다 ----
  if (opts.deviceClient && isDeviceTokenFormat(token)) {
    const row = await lookupDeviceToken(opts.deviceClient, token);
    if (row === null) {
      throw new AuthError(401, "인증이 필요합니다.", { "WWW-Authenticate": "Bearer" });
    }
    await touchDeviceToken(opts.deviceClient, row, (opts.now ?? Date.now)());
    return {
      userId: row.user_id,
      email: null,
      isAuthenticated: true,
      authKind: "device",
      scopes: row.scopes,
      deviceId: row.id,
    };
  }

  try {
    const verified = await verifyJwt(token, settings);
    return { userId: verified.userId, email: verified.email, isAuthenticated: true, authKind: "session" };
  } catch (e) {
    if (e instanceof JWTValidationError) {
      throw new AuthError(401, "인증이 필요합니다.", { "WWW-Authenticate": "Bearer" });
    }
    throw e;
  }
}

/** 기기 토큰 호출자를 막는 게이트 — 기기 관리·결제처럼 세션만 허용하는 곳. */
export function requireSessionUser(user: CurrentUser): CurrentUser {
  if (user.authKind === "device") {
    throw new AuthError(403, "기기 토큰으로는 이 작업을 할 수 없습니다.");
  }
  return user;
}
```

`current_user_test.ts` 가 `getCurrentUser(req, settings)` 2인자로 부르므로 그대로 통과한다. 테스트 하나를 추가한다:

```ts
Deno.test("기기 토큰 — deviceClient 없이는 401 (JWT 로 취급)", async () => {
  const req = new Request("http://x/documents", {
    headers: { Authorization: `Bearer jrd_${"a".repeat(43)}` },
  });
  let status = 0;
  try {
    await getCurrentUser(req, { ...SETTINGS_ENABLED });
  } catch (e) {
    status = (e as { status: number }).status;
  }
  assertEquals(status, 401);
});
```

`SETTINGS_ENABLED` 는 `current_user_test.ts` 안에서 이미 쓰는 `authEnabled: true` 설정 객체 이름으로 맞춘다 (`grep -n "authEnabled: true" _shared/current_user_test.ts` 로 확인).

- [ ] **Step 7: `me/devices.ts` 작성**

```ts
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
```

- [ ] **Step 8: `api-account/index.ts` 에 라우트 추가**

`ME_ROUTES` 를 메서드 배열로 바꾸고 `/me/devices` 를 넣는다. 기존 블록(`} else if (path.startsWith("/me/")) {` 부터 그 else-if 끝까지)을 아래로 교체:

```ts
    } else if (path.startsWith("/me/")) {
      // **순서가 계약이다 — 라우팅 먼저, 인증 나중.** (기존 주석 유지)
      const ME_ROUTES: Record<string, string[]> = {
        "/me/plan": ["GET"],
        "/me/subscription": ["GET"],
        "/me/email-ingest": ["GET"],
        "/me/email-ingest/rotate": ["POST"],
        "/me/devices": ["GET", "POST"],
      };
      const deviceDel = path.match(/^\/me\/devices\/([^/]+)$/);
      const allowed = deviceDel ? ["DELETE"] : ME_ROUTES[path];
      if (allowed === undefined) {
        response = notFound();
      } else if (!allowed.includes(req.method)) {
        response = methodNotAllowed();
      } else {
        const client = createServiceClient(settings);
        // 기기 토큰도 여기까지는 온다 — `/me/*` 는 전부 세션 전용이라 403 으로 막는다.
        const user = requireSessionUser(
          requireAuthenticatedUser(await getCurrentUser(req, settings, { deviceClient: client })),
        );
        const deps = { client, emailIngestDomain: settings.emailIngestDomain };
        try {
          if (path === "/me/plan") {
            response = jsonResponse(await buildPlan(user.userId, deps));
          } else if (path === "/me/subscription") {
            response = jsonResponse(await buildSubscription(user.userId, deps));
          } else if (path === "/me/email-ingest") {
            response = jsonResponse(await buildEmailIngest(user.userId, user.email, deps));
          } else if (path === "/me/email-ingest/rotate") {
            response = jsonResponse(await buildEmailIngestRotate(user.userId, user.email, deps));
          } else if (deviceDel) {
            response = jsonResponse(await revokeDevice(client, user.userId, decodeURIComponent(deviceDel[1])));
          } else if (req.method === "GET") {
            response = jsonResponse({ devices: await listDevices(client, user.userId) });
          } else {
            let body: Record<string, unknown> = {};
            try {
              body = await req.json() as Record<string, unknown>;
            } catch {
              throw new MeHttpError(422, "JSON 본문이 필요합니다.");
            }
            response = jsonResponse(await createDevice(client, user.userId, body["name"]), 201);
          }
        } catch (e) {
          if (e instanceof MeHttpError) {
            response = jsonResponse({ detail: e.detail }, e.status);
          } else {
            throw e;
          }
        }
      }
    }
```

import 추가: `requireSessionUser` (current_user.ts), `createDevice, listDevices, revokeDevice` (`../_shared/me/devices.ts`). `jsonResponse(body, 201)` 은 `errors.ts` 시그니처(`jsonResponse(body, status = 200, headers = {})`)와 맞는다.

- [ ] **Step 9: `api-documents/index.ts` 에 기기 토큰 수용 + 스코프 게이트**

`const caller = await getCurrentUser(req, settings);` 를 아래로 교체:

```ts
    const caller = await getCurrentUser(req, settings, { deviceClient: client });
    // 기기 토큰은 화이트리스트 라우트만. 나머지는 403 — 새어도 읽기·삭제가 안 된다.
    if (caller.authKind === "device" && !deviceScopeAllows(caller.scopes ?? [], req.method, path)) {
      return applyCorsHeaders(
        req,
        jsonResponse({ detail: "기기 토큰의 권한 범위를 벗어난 요청입니다." }, 403),
        settings,
      );
    }
```

import 추가: `import { deviceScopeAllows } from "../_shared/device_token.ts";`

- [ ] **Step 10: fmt·lint·테스트**

Run: `cd supabase/functions && deno fmt --check && deno lint && deno test --allow-env --allow-net --allow-read`
Expected: 전부 PASS.

- [ ] **Step 11: 웹 클라이언트 — `apiPostJson`·`apiDelete`**

`web/src/lib/api/client.ts` 의 `safeReadDetail` 위에 추가:

```ts
/** JSON 본문 POST. `apiPost` 는 본문이 없는 엔드포인트용이라 따로 둔다. */
export async function apiPostJson<T>(path: string, body: unknown): Promise<T> {
  const authInit = await buildAuthInit();
  const res = await fetch(`${BASE_URL}${path}`, {
    ...authInit,
    method: 'POST',
    body: JSON.stringify(body),
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
      ...(authInit.headers ?? {}),
    },
    cache: 'no-store',
  });
  if (!res.ok) {
    throw new ApiError(res.status, await safeReadDetail(res));
  }
  return res.json() as Promise<T>;
}

export async function apiDelete<T>(path: string): Promise<T> {
  const authInit = await buildAuthInit();
  const res = await fetch(`${BASE_URL}${path}`, {
    ...authInit,
    method: 'DELETE',
    headers: { Accept: 'application/json', ...(authInit.headers ?? {}) },
    cache: 'no-store',
  });
  if (!res.ok) {
    throw new ApiError(res.status, await safeReadDetail(res));
  }
  return res.json() as Promise<T>;
}
```

- [ ] **Step 12: `devices-section.tsx` 작성**

```tsx
'use client';

import { useEffect, useState } from 'react';
import { apiDelete, apiGet, apiPostJson } from '@/lib/api/client';

interface Device {
  id: string;
  name: string;
  token_prefix: string;
  scopes: string[];
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

function formatWhen(iso: string | null): string {
  if (!iso) return '아직 사용 안 함';
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? '-'
    : d.toLocaleString('ko-KR', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export function DevicesSection() {
  const [devices, setDevices] = useState<Device[] | null>(null);
  const [name, setName] = useState('');
  const [issued, setIssued] = useState<{ name: string; token: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = () =>
    apiGet<{ devices: Device[] }>('/me/devices')
      .then((r) => {
        setDevices(r.devices);
        setError(null);
      })
      .catch(() => setError('기기 목록을 불러오지 못했습니다.'));

  useEffect(() => {
    void load();
  }, []);

  const create = async () => {
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const d = await apiPostJson<Device & { token: string }>('/me/devices', { name: name.trim() });
      setIssued({ name: d.name, token: d.token });
      setName('');
      await load();
    } catch {
      setError('기기 추가에 실패했습니다. 잠시 후 다시 시도해 주세요.');
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (d: Device) => {
    if (!window.confirm(`"${d.name}" 의 토큰을 폐기하면 그 기기는 즉시 업로드가 막힙니다. 계속할까요?`)) return;
    setBusy(true);
    try {
      await apiDelete(`/me/devices/${d.id}`);
      await load();
    } catch {
      setError('폐기에 실패했습니다.');
    } finally {
      setBusy(false);
    }
  };

  const active = (devices ?? []).filter((d) => !d.revoked_at);

  return (
    <section className="mt-6 rounded-lg border p-4">
      <h2 className="font-semibold">연결된 기기</h2>
      <p className="mt-1 text-sm text-muted-foreground">
        PC 에이전트·단축어·앱이 문서를 자동으로 올릴 때 쓰는 토큰입니다. 기기별로 폐기할 수 있습니다.
      </p>
      {error && <p className="mt-2 text-sm text-red-600">{error}</p>}

      {issued && (
        <div className="mt-3 rounded border border-amber-400 bg-amber-50 p-3 text-sm dark:bg-amber-950">
          <p>
            <strong>{issued.name}</strong> 토큰입니다. <strong>지금 한 번만</strong> 표시됩니다 — 복사해 기기에 넣어 주세요.
          </p>
          <code className="mt-2 block select-all break-all rounded bg-background px-3 py-2">{issued.token}</code>
          <button
            type="button"
            className="mt-2 rounded border px-3 py-1"
            onClick={() => {
              void navigator.clipboard.writeText(issued.token);
            }}
          >
            복사
          </button>
          <button type="button" className="ml-2 mt-2 rounded border px-3 py-1" onClick={() => setIssued(null)}>
            확인했습니다
          </button>
        </div>
      )}

      <div className="mt-3 flex gap-2">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="기기 이름 (예: 회사 노트북)"
          maxLength={60}
          className="flex-1 rounded border px-3 py-1 text-sm"
        />
        <button
          type="button"
          onClick={() => void create()}
          disabled={busy || !name.trim()}
          className="rounded bg-primary px-3 py-1 text-sm text-primary-foreground disabled:opacity-50"
        >
          기기 추가
        </button>
      </div>

      {devices === null ? (
        <p className="mt-2 text-sm text-gray-500">불러오는 중…</p>
      ) : active.length === 0 ? (
        <p className="mt-2 text-sm text-gray-500">연결된 기기가 없습니다.</p>
      ) : (
        <ul className="mt-3 divide-y text-sm">
          {active.map((d) => (
            <li key={d.id} className="flex items-center justify-between py-2">
              <div>
                <div className="font-medium">{d.name}</div>
                <div className="text-xs text-muted-foreground">
                  {d.token_prefix}… · 마지막 사용 {formatWhen(d.last_used_at)}
                </div>
              </div>
              <button
                type="button"
                onClick={() => void revoke(d)}
                disabled={busy}
                className="rounded border px-2 py-1 text-xs disabled:opacity-50"
              >
                폐기
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
```

`web/src/app/settings/page.tsx`: `import { DevicesSection } from '@/components/jet-rag/devices-section';` 추가하고 `<SubscriptionSection />` 아래에 `<DevicesSection />` 삽입.

- [ ] **Step 13: 웹 타입·린트**

Run: `cd web && pnpm exec tsc --noEmit && pnpm lint`
Expected: 오류 0. (`text-muted-foreground`·`bg-background` 는 이 프로젝트 Tailwind 토큰이다 — `grep -rn "text-muted-foreground" web/src | head -1` 로 존재 확인.)

- [ ] **Step 14: 마이그 030 적용 + 라이브 E2E**

적용은 `/deploy-assist` 절차(SQL Editor). 로컬에 DB 접속 수단이 있으면(`.env` 의 `SUPABASE_DB_URL` 또는 Management API PAT) 그걸로 적용한다. 적용 후:

```bash
cd supabase/functions && supabase functions deploy api-account api-documents
# 소유자 세션으로 발급 → 기기 토큰으로 업로드(중복 파일) → 검색은 403 → 폐기 → 401
```

검증 스크립트는 Task 3 Step 6 의 E2E 와 합친다(precheck 까지 한 번에).

- [ ] **Step 15: 커밋**

```bash
git add api/migrations/030_device_tokens.sql supabase/functions/_shared/device_token.ts supabase/functions/_shared/device_token_test.ts supabase/functions/_shared/me/devices.ts supabase/functions/_shared/current_user.ts supabase/functions/_shared/current_user_test.ts supabase/functions/api-account/index.ts supabase/functions/api-documents/index.ts web/src/lib/api/client.ts web/src/components/jet-rag/devices-section.tsx web/src/app/settings/page.tsx
git commit -m "feat(auth): 기기 토큰 — 마이그 030, Bearer jrd_ 분기, /me/devices, 설정 UI

- 무인 클라이언트용 긴 수명 토큰. sha256 해시만 저장, 발급 응답에 1회 표시.
- ingest 스코프 = POST /documents · precheck · status · batch-status 4개. 그 외 403.
- /me/* 는 세션 전용(requireSessionUser). 기기 최대 20개.
- 스펙 2026-09-15 §4 S3."
```

---

## Task 3: S2 — `POST /documents/precheck`

**Files:**
- Create: `supabase/functions/_shared/documents/precheck.ts`, `precheck_test.ts`
- Modify: `supabase/functions/api-documents/index.ts` (POST 분기 앞에 라우트)

- [ ] **Step 1: 테스트 작성**

```ts
import { assertEquals } from "@std/assert";
import { parsePrecheckBody, precheckHashes } from "./precheck.ts";

const H = (c: string) => c.repeat(64);

Deno.test("parsePrecheckBody — 배열·개수·hex64 검증", () => {
  assertEquals(parsePrecheckBody({ hashes: [H("a")] }).ok, true);
  assertEquals(parsePrecheckBody({}).ok, false);
  assertEquals(parsePrecheckBody({ hashes: [] }).ok, false);
  assertEquals(parsePrecheckBody({ hashes: ["ABC"] }).ok, false);
  assertEquals(parsePrecheckBody({ hashes: Array(501).fill(H("b")) }).ok, false);
  // 대문자는 소문자로 정규화한다.
  const r = parsePrecheckBody({ hashes: [H("A")] });
  if (r.ok) assertEquals(r.hashes[0], H("a"));
});

Deno.test("precheckHashes — existing / failed / new 분류", async () => {
  const client = {
    from: () => ({
      select: () => ({
        eq: () => ({
          is: () => ({
            in: () =>
              Promise.resolve({
                data: [
                  { id: "d1", sha256: H("a"), flags: {} },
                  { id: "d2", sha256: H("b"), flags: { failed: true } },
                ],
                error: null,
              }),
          }),
        }),
      }),
    }),
  };
  // deno-lint-ignore no-explicit-any
  const r = await precheckHashes(client as any, "u1", [H("a"), H("b"), H("c")]);
  assertEquals(r[H("a")], { state: "existing", doc_id: "d1" });
  assertEquals(r[H("b")], { state: "failed", doc_id: "d2" });
  assertEquals(r[H("c")], { state: "new" });
});
```

- [ ] **Step 2: 실패 확인** — Run: `deno test --allow-env --allow-net --allow-read _shared/documents/precheck_test.ts` → module not found

- [ ] **Step 3: `precheck.ts` 작성**

```ts
/**
 * `POST /documents/precheck` — sha256 목록을 보내면 이미 있는 것·실패한 것·새 것을 가른다.
 *
 * 에이전트가 파일 전체를 올리지 않고도 중복을 알기 위한 것이다(실측: 중복 3.2MB 판정에
 * 3~4초 전송이 들었다). `failed` 는 `flags.failed` 가 있는 행 — 재업로드 대상이다
 * (persist 의 재시도 분기와 같은 의미).
 */

import type { SupabaseClient } from "@supabase/supabase-js";

export const PRECHECK_MAX = 500;
const HEX64 = /^[0-9a-f]{64}$/;

export type PrecheckState = "existing" | "failed" | "new";
export type PrecheckResults = Record<string, { state: PrecheckState; doc_id?: string }>;

export function parsePrecheckBody(
  body: unknown,
): { ok: true; hashes: string[] } | { ok: false; detail: string } {
  const raw = (body as { hashes?: unknown } | null)?.hashes;
  if (!Array.isArray(raw)) return { ok: false, detail: "`hashes` 배열이 필요합니다." };
  if (raw.length === 0) return { ok: false, detail: "`hashes` 가 비어 있습니다." };
  if (raw.length > PRECHECK_MAX) {
    return { ok: false, detail: `\`hashes\` 는 최대 ${PRECHECK_MAX}개입니다.` };
  }
  const hashes: string[] = [];
  for (const h of raw) {
    if (typeof h !== "string") return { ok: false, detail: "`hashes` 항목은 문자열이어야 합니다." };
    const lower = h.toLowerCase();
    if (!HEX64.test(lower)) return { ok: false, detail: `sha256 hex 형식이 아닙니다: ${h.slice(0, 16)}` };
    hashes.push(lower);
  }
  return { ok: true, hashes: [...new Set(hashes)] };
}

export async function precheckHashes(
  client: SupabaseClient,
  userId: string,
  hashes: string[],
): Promise<PrecheckResults> {
  const { data, error } = await client
    .from("documents")
    .select("id, sha256, flags")
    .eq("user_id", userId)
    .is("deleted_at", null)
    .in("sha256", hashes);
  if (error) throw new Error(`precheck 조회 실패: ${error.message}`);
  const out: PrecheckResults = {};
  for (const h of hashes) out[h] = { state: "new" };
  for (const row of (data ?? []) as { id: string; sha256: string; flags?: Record<string, unknown> | null }[]) {
    out[row.sha256] = row.flags?.["failed"]
      ? { state: "failed", doc_id: row.id }
      : { state: "existing", doc_id: row.id };
  }
  return out;
}
```

- [ ] **Step 4: 통과 확인** — `2 passed`

- [ ] **Step 5: 라우트 결선**

`api-documents/index.ts` 의 `if (path !== "/documents" && path !== "/") return notFound();` **앞**에:

```ts
    // ---- precheck — 업로드와 같은 게이트(로그인 필수), 본문은 JSON ----
    if (path === "/documents/precheck") {
      if (req.method !== "POST") return methodNotAllowed();
      const user = requireAuthenticatedUser(caller);
      let body: unknown = null;
      try {
        body = await req.json();
      } catch {
        return applyCorsHeaders(req, jsonResponse({ detail: "JSON 본문이 필요합니다." }, 422), settings);
      }
      const parsed = parsePrecheckBody(body);
      if (!parsed.ok) return applyCorsHeaders(req, jsonResponse({ detail: parsed.detail }, 422), settings);
      const results = await precheckHashes(client, user.userId, parsed.hashes);
      return applyCorsHeaders(req, jsonResponse({ results }), settings);
    }
```

import: `import { parsePrecheckBody, precheckHashes } from "../_shared/documents/precheck.ts";`

`workers/api-proxy/src/routes.js` 의 경로 표에 `/documents/precheck` 가 `api-documents` 로 가는지 확인한다: `grep -n "documents" workers/api-proxy/src/routes.js`. `/^\/documents/` 류의 접두 규칙이면 추가 불필요. 정확 일치 규칙만 있으면 `[/^\/documents\/precheck$/, "api-documents"]` 를 `/documents` 규칙 **앞**에 추가하고 `workers/api-proxy` 의 테스트(`deno test --allow-net`)를 돌린다.

- [ ] **Step 6: 배포 + 라이브 E2E (Task 2 와 합침)**

`api/scripts/e2e_device_precheck.ts` (Deno, 운영 대상, 부작용 0 — 중복 파일만 씀):

```ts
/**
 * 기기 토큰 + precheck 라이브 E2E.
 *   deno run --allow-net --allow-env --allow-read api/scripts/e2e_device_precheck.ts <session_access_token> <중복파일경로>
 * 1) 세션으로 기기 발급 → 2) 기기 토큰으로 precheck(existing) → 3) 기기 토큰으로 업로드(duplicated)
 * 4) 기기 토큰으로 GET /documents → 403 → 5) 폐기 → 6) 기기 토큰 업로드 → 401
 */
const [session, filePath] = Deno.args;
const API = Deno.env.get("JETRAG_API_BASE") ?? "https://jetrag-api.woong-s.com";
const UA = "JetRag-Agent/0.0-e2e";
const j = (r: Response) => r.json();

const dev = await fetch(`${API}/me/devices`, {
  method: "POST",
  headers: { Authorization: `Bearer ${session}`, "Content-Type": "application/json", "User-Agent": UA },
  body: JSON.stringify({ name: "e2e-test" }),
}).then(j);
console.log("1 발급:", dev.id, dev.token_prefix);
const dh = { Authorization: `Bearer ${dev.token}`, "User-Agent": UA };

const bytes = await Deno.readFile(filePath);
const digest = await crypto.subtle.digest("SHA-256", bytes);
const sha = [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
const pre = await fetch(`${API}/documents/precheck`, {
  method: "POST",
  headers: { ...dh, "Content-Type": "application/json" },
  body: JSON.stringify({ hashes: [sha, "0".repeat(64)] }),
}).then(j);
console.log("2 precheck:", pre.results[sha].state, pre.results["0".repeat(64)].state);

const fd = new FormData();
fd.append("file", new Blob([bytes]), filePath.split("/").pop()!);
fd.append("source_channel", "pc-agent");
const up = await fetch(`${API}/documents`, { method: "POST", headers: dh, body: fd });
console.log("3 upload:", up.status, await up.text());

const list = await fetch(`${API}/documents`, { headers: dh });
console.log("4 GET /documents (403 기대):", list.status);

const rv = await fetch(`${API}/me/devices/${dev.id}`, { method: "DELETE", headers: { Authorization: `Bearer ${session}`, "User-Agent": UA } });
console.log("5 폐기:", rv.status);
const again = await fetch(`${API}/documents/precheck`, {
  method: "POST",
  headers: { ...dh, "Content-Type": "application/json" },
  body: JSON.stringify({ hashes: [sha] }),
});
console.log("6 폐기 후 (401 기대):", again.status);
```

Expected: `2 precheck: existing new`, `3 upload: 202 {"...duplicated":true}`, `4: 403`, `5: 200`, `6: 401`.

- [ ] **Step 7: 커밋**

```bash
git add supabase/functions/_shared/documents/precheck.ts supabase/functions/_shared/documents/precheck_test.ts supabase/functions/api-documents/index.ts workers/api-proxy/src/routes.js api/scripts/e2e_device_precheck.ts
git commit -m "feat(edge): POST /documents/precheck — sha256 목록으로 existing/failed/new 분류 + 기기·precheck 라이브 E2E 스크립트"
```

---

## Task 4: S4 — 계량 교체 (마이그 031 + 용량·월 Vision 페이지 + 게이트 + `/me/plan` + UI)

**Files:**
- Create: `api/migrations/031_metering_v2.sql`
- Modify: `supabase/functions/_shared/me/quota.ts` (PlanLimits 교체, 함수 3개 추가, `countActiveDocuments` 삭제)
- Modify: `supabase/functions/_shared/rate_limit.ts` (`METRIC_DOCS`·`rateLimitDocsPerDay` 제거, `enforceUploadBurst` 추가)
- Modify: `supabase/functions/_shared/config.ts` (`rateLimitDocsPerDay` 제거)
- Create: `supabase/functions/_shared/ingest/quota_gate.ts`, `quota_gate_test.ts`
- Modify: `supabase/functions/_shared/ingest/worker.ts` (gate 주입점)
- Modify: `supabase/functions/api-ingest-worker/index.ts` (gate 결선)
- Modify: `supabase/functions/api-documents/index.ts` (상한 교체, checkStorage 주입)
- Modify: `supabase/functions/_shared/ingest/email_route.ts`, `email_ingest.ts` (docs 카운터 제거, checkStorage)
- Modify: `supabase/functions/_shared/me/pipeline.ts` (`buildPlan`)
- Modify: `web/src/app/settings/page.tsx`, `web/src/components/jet-rag/subscription-section.tsx`

- [ ] **Step 1: 마이그 031 작성**

```sql
-- ============================================================
-- 031_metering_v2.sql — 계량 교체 (자동 수집 ① 스펙 §4 S4)
-- ============================================================
-- 배경
--   문서 수 상한(Free 10·Pro 200)과 일일 30건은 손으로 올리는 전제다. 자동 수집은 첫 동기화에서
--   바로 막힌다. 비용을 만드는 건 문서 수가 아니라 저장 용량과 Vision 페이지(페이지당 $0.005~0.03
--   실측)다. 그 둘만 센다.
--
-- 바뀌는 것
--   1. plans: storage_bytes_limit · vision_pages_per_month 추가, max_documents 제거.
--      값은 **잠정**(스펙 §4 S4). 출시 전 Vision 단가 실측으로 재산정.
--   2. documents.source_channel CHECK 에 pc-agent · ios-shortcut · android-agent.
--   3. ingest_jobs.status 에 'deferred_quota' + deferred_task jsonb (재투입용 페이로드).
--   4. RPC storage_bytes_used(uuid) · vision_pages_used_since(uuid, timestamptz).
--   5. upload_burst 표 + increment_upload_burst — 분당 업로드 남용 방지.
--   6. vision_quota_release() + cron 'vision-quota-release' 매일 00:00 KST(=15:00 UTC):
--      deferred_quota 잡을 큐에 되돌린다. 아직 한도 초과면 워커 게이트가 다시 보류한다(싸다).
--
-- 적용 절차: Supabase Studio → SQL Editor → paste → Run.
-- 검증 SQL:
--   SELECT code, storage_bytes_limit, vision_pages_per_month FROM plans;
--   SELECT public.storage_bytes_used('<owner uuid>');
--   SELECT public.vision_pages_used_since('<owner uuid>', date_trunc('month', now()));
--   SELECT jobname, schedule FROM cron.job WHERE jobname = 'vision-quota-release';
-- ============================================================

-- 1. plans
ALTER TABLE plans
    ADD COLUMN IF NOT EXISTS storage_bytes_limit    BIGINT  NOT NULL DEFAULT 1073741824,  -- 1GB
    ADD COLUMN IF NOT EXISTS vision_pages_per_month INTEGER NOT NULL DEFAULT 100;
UPDATE plans SET storage_bytes_limit = 1073741824,  vision_pages_per_month = 100  WHERE code = 'free';
UPDATE plans SET storage_bytes_limit = 10737418240, vision_pages_per_month = 1000 WHERE code = 'pro';
ALTER TABLE plans DROP COLUMN IF EXISTS max_documents;

-- 2. source_channel
ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_source_channel_check;
ALTER TABLE documents ADD CONSTRAINT documents_source_channel_check CHECK (source_channel IN
    ('drag-drop','os-share','clipboard','url','camera','api','email','pc-agent','ios-shortcut','android-agent'));

-- 3. ingest_jobs
ALTER TABLE ingest_jobs DROP CONSTRAINT IF EXISTS ingest_jobs_status_check;
ALTER TABLE ingest_jobs ADD CONSTRAINT ingest_jobs_status_check CHECK (status IN
    ('queued','running','completed','failed','cancelled','deferred_quota'));
ALTER TABLE ingest_jobs ADD COLUMN IF NOT EXISTS deferred_task JSONB;

-- 4. 사용량 RPC (service_role 전용)
CREATE OR REPLACE FUNCTION public.storage_bytes_used(p_user_id UUID)
RETURNS BIGINT LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT COALESCE(SUM(size_bytes), 0)::BIGINT FROM documents
   WHERE user_id = p_user_id AND deleted_at IS NULL;
$$;

CREATE OR REPLACE FUNCTION public.vision_pages_used_since(p_user_id UUID, p_since TIMESTAMPTZ)
RETURNS INTEGER LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT COUNT(*)::INTEGER FROM vision_usage_log v
    JOIN documents d ON d.id = v.doc_id
   WHERE d.user_id = p_user_id AND v.success = TRUE AND v.called_at >= p_since;
$$;

REVOKE ALL ON FUNCTION public.storage_bytes_used(UUID) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.vision_pages_used_since(UUID, TIMESTAMPTZ) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.storage_bytes_used(UUID) TO service_role;
GRANT EXECUTE ON FUNCTION public.vision_pages_used_since(UUID, TIMESTAMPTZ) TO service_role;

-- 5. 분당 업로드 남용 방지
CREATE TABLE IF NOT EXISTS upload_burst (
    user_key  TEXT NOT NULL,
    minute    TIMESTAMPTZ NOT NULL,
    count     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_key, minute)
);
ALTER TABLE upload_burst ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS upload_burst_service_role_all ON upload_burst;
CREATE POLICY upload_burst_service_role_all ON upload_burst FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);

CREATE OR REPLACE FUNCTION public.increment_upload_burst(p_user_key TEXT, p_minute TIMESTAMPTZ)
RETURNS INTEGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE new_count INTEGER;
BEGIN
  INSERT INTO upload_burst (user_key, minute, count) VALUES (p_user_key, p_minute, 1)
  ON CONFLICT (user_key, minute) DO UPDATE SET count = upload_burst.count + 1
  RETURNING count INTO new_count;
  RETURN new_count;
END; $$;
REVOKE ALL ON FUNCTION public.increment_upload_burst(TEXT, TIMESTAMPTZ) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.increment_upload_burst(TEXT, TIMESTAMPTZ) TO service_role;

-- 6. 보류 잡 재투입 + 하루치 burst 정리
CREATE OR REPLACE FUNCTION public.vision_quota_release()
RETURNS INTEGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE r RECORD; n INTEGER := 0;
BEGIN
  FOR r IN SELECT id, deferred_task FROM ingest_jobs
            WHERE status = 'deferred_quota' AND deferred_task IS NOT NULL LOOP
    PERFORM public.ingest_queue_send(r.deferred_task, 0);
    UPDATE ingest_jobs SET status = 'queued', deferred_task = NULL, error_msg = NULL WHERE id = r.id;
    n := n + 1;
  END LOOP;
  DELETE FROM upload_burst WHERE minute < now() - interval '1 day';
  RETURN n;
END; $$;
REVOKE ALL ON FUNCTION public.vision_quota_release() FROM PUBLIC, anon, authenticated;

SELECT cron.unschedule('vision-quota-release')
 WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'vision-quota-release');
SELECT cron.schedule('vision-quota-release', '0 15 * * *', $cron$
  SELECT public.vision_quota_release();
$cron$);

-- ============================================================
-- 롤백
--   SELECT cron.unschedule('vision-quota-release');
--   DROP FUNCTION IF EXISTS public.vision_quota_release(), public.increment_upload_burst(TEXT, TIMESTAMPTZ),
--     public.vision_pages_used_since(UUID, TIMESTAMPTZ), public.storage_bytes_used(UUID);
--   DROP TABLE IF EXISTS upload_burst;
--   ALTER TABLE ingest_jobs DROP COLUMN IF EXISTS deferred_task;  (status CHECK 는 001 값으로 되돌림)
--   ALTER TABLE plans ADD COLUMN max_documents INTEGER NOT NULL DEFAULT 10; UPDATE plans SET max_documents = 200 WHERE code='pro';
--   ALTER TABLE plans DROP COLUMN storage_bytes_limit, DROP COLUMN vision_pages_per_month;
-- ============================================================
```

- [ ] **Step 2: `quota.ts` — PlanLimits 교체 + 사용량 함수**

`PlanLimits` 와 `getEffectivePlan` 의 select·반환을 바꾼다:

```ts
export interface PlanLimits {
  code: string;
  answers_per_day: number;
  storage_bytes_limit: number;
  vision_pages_per_month: number;
}
// getEffectivePlan 안:
//   .select("code, answers_per_day, storage_bytes_limit, vision_pages_per_month")
//   return { code, answers_per_day: trunc, storage_bytes_limit: Math.trunc(Number(row.storage_bytes_limit)),
//            vision_pages_per_month: Math.trunc(Number(row.vision_pages_per_month)) };
```

`countActiveDocuments` 는 지운다(호출처: `rate_limit.ts`·`pipeline.ts` — 이 Task 에서 함께 제거). 추가:

```ts
/** 보유 용량(바이트). 실패는 `null` — 제한 판정은 fail-open. */
export async function storageUsedBytes(client: SupabaseClient, userId: string): Promise<number | null> {
  try {
    const { data, error } = await client.rpc("storage_bytes_used", { p_user_id: userId });
    if (error) throw new Error(error.message);
    return Math.trunc(Number(data ?? 0));
  } catch (e) {
    console.warn(`storage_bytes_used 실패 — fail-open (user=${userId}):`, e);
    return null;
  }
}

/** 이번 달(KST) 1일 00:00 의 ISO. `/stats` 의 월 집계와 같은 기준이다. */
export function kstMonthStartIso(nowMs: number): string {
  const kst = new Date(nowMs + 9 * 3600_000);
  const start = Date.UTC(kst.getUTCFullYear(), kst.getUTCMonth(), 1) - 9 * 3600_000;
  return new Date(start).toISOString();
}

/** 이번 달 Vision 페이지 수. 실패는 `null`. */
export async function visionPagesUsedMonth(
  client: SupabaseClient,
  userId: string,
  nowMs: number,
): Promise<number | null> {
  try {
    const { data, error } = await client.rpc("vision_pages_used_since", {
      p_user_id: userId,
      p_since: kstMonthStartIso(nowMs),
    });
    if (error) throw new Error(error.message);
    return Math.trunc(Number(data ?? 0));
  } catch (e) {
    console.warn(`vision_pages_used_since 실패 — fail-open (user=${userId}):`, e);
    return null;
  }
}

export interface QuotaSettings {
  authEnabled: boolean;
  quotaEnforcementEnabled: boolean;
  ownerUserId: string | null;
}

/** 플랜 quota 가 이 사용자에게 걸리는가 — `rate_limit.ts` 의 `quotaActive` 와 같은 규칙. */
export function quotaActiveFor(
  user: { userId: string; isAuthenticated: boolean },
  s: QuotaSettings,
): boolean {
  return s.authEnabled && s.quotaEnforcementEnabled && user.isAuthenticated &&
    user.userId !== (s.ownerUserId ?? "");
}

/** persist 에 꽂는 용량 검사. quota 비활성·조회 실패는 `null`(fail-open). */
export function makeStorageCheck(
  client: SupabaseClient,
  user: { userId: string; isAuthenticated: boolean },
  s: QuotaSettings,
): (sizeBytes: number) => Promise<{ allowed: boolean; usedBytes: number; limitBytes: number } | null> {
  return async (sizeBytes) => {
    if (!quotaActiveFor(user, s)) return null;
    const plan = await getEffectivePlan(client, user.userId);
    if (plan === null || plan.storage_bytes_limit <= 0) return null;
    const used = await storageUsedBytes(client, user.userId);
    if (used === null) return null;
    return { allowed: used + sizeBytes <= plan.storage_bytes_limit, usedBytes: used, limitBytes: plan.storage_bytes_limit };
  };
}
```

`quota_test.ts` 가 있으면(`ls _shared/me/*_test.ts`) `max_documents` 를 기대하는 케이스를 새 필드로 바꾼다. `kstMonthStartIso` 테스트를 추가한다:

```ts
Deno.test("kstMonthStartIso — KST 1일 00:00 = 전날 15:00 UTC", () => {
  // 2026-09-15 10:00 KST = 2026-09-15T01:00Z
  assertEquals(kstMonthStartIso(Date.parse("2026-09-15T01:00:00Z")), "2026-08-31T15:00:00.000Z");
  // 2026-09-01 00:30 KST = 2026-08-31T15:30Z 는 9월이다
  assertEquals(kstMonthStartIso(Date.parse("2026-08-31T15:30:00Z")), "2026-08-31T15:00:00.000Z");
  // 2026-08-31 23:30 KST = 2026-08-31T14:30Z 는 8월이다
  assertEquals(kstMonthStartIso(Date.parse("2026-08-31T14:30:00Z")), "2026-07-31T15:00:00.000Z");
});
```

- [ ] **Step 3: `rate_limit.ts` — docs 제거, burst 추가**

- `METRIC_DOCS` export 와 `capForMetric` 의 docs 분기, `RateLimitSettings.rateLimitDocsPerDay`, `enforceRateLimit` 안의 `if (metric === METRIC_DOCS && plan.max_documents > 0) {…}` 블록을 삭제한다.
- 추가:

```ts
export const UPLOAD_BURST_PER_MINUTE = 60;

/** 분 단위 절삭 ISO — `upload_burst.minute`. */
export function minuteFloorIso(nowMs: number): string {
  return new Date(Math.floor(nowMs / 60_000) * 60_000).toISOString();
}

/**
 * 분당 업로드 남용 방지 — 로그인·기기 토큰 공통. 초과면 429. RPC 실패는 fail-open.
 * 플랜 quota 와 무관하게 전원에게 걸린다(owner 포함) — 오작동 에이전트가 무한 루프 돌 때를 위한 것이다.
 */
export async function enforceUploadBurst(
  user: { userId: string },
  deps: EnforceDeps,
): Promise<void> {
  const minute = minuteFloorIso((deps.now ?? Date.now)());
  let n: unknown;
  try {
    const { data, error } = await deps.client.rpc("increment_upload_burst", {
      p_user_key: user.userId,
      p_minute: minute,
    });
    if (error) throw new Error(error.message);
    n = data;
  } catch (e) {
    console.warn("upload_burst RPC 실패 — fail-open:", e);
    return;
  }
  if (typeof n === "number" && n > UPLOAD_BURST_PER_MINUTE) {
    throw new RateLimitError(
      429,
      `분당 업로드 한도(${UPLOAD_BURST_PER_MINUTE}건)를 초과했습니다. 잠시 후 다시 시도해 주세요.`,
    );
  }
}
```

`rate_limit_test.ts` 가 있으면 docs 케이스를 지우고 burst 케이스 2개(60 이하 통과 / 61 → 429)를 추가한다. `config.ts` 에서 `rateLimitDocsPerDay` 필드와 `JETRAG_RATE_LIMIT_DOCS_PER_DAY` 읽기를 지우고 `config_test.ts` 의 해당 기대값도 지운다.

- [ ] **Step 4: `quota_gate.ts` 테스트 → 구현**

테스트 `_shared/ingest/quota_gate_test.ts`:

```ts
import { assertEquals } from "@std/assert";
import { makeQuotaGate, VISION_STAGES } from "./quota_gate.ts";

function fake(opts: { docType: string; used: number; limit: number; plan?: string }) {
  const client = {
    from: () => ({
      select: () => ({
        eq: () => ({
          limit: () =>
            Promise.resolve({
              data: [{ user_id: "u1", doc_type: opts.docType }],
              error: null,
            }),
        }),
      }),
    }),
    rpc: (fn: string) =>
      Promise.resolve({ data: fn === "vision_pages_used_since" ? opts.used : 0, error: null }),
  };
  const plan = () =>
    Promise.resolve({ code: opts.plan ?? "free", answers_per_day: 5, storage_bytes_limit: 1, vision_pages_per_month: opts.limit });
  // deno-lint-ignore no-explicit-any
  return makeQuotaGate({ client: client as any, getPlan: plan, settings: { authEnabled: true, quotaEnforcementEnabled: true, ownerUserId: "owner" }, nowMs: () => 0 });
}

Deno.test("VISION_STAGES 는 scan · vision · vision_missing · extract", () => {
  assertEquals([...VISION_STAGES].sort(), ["extract", "scan", "vision", "vision_missing"]);
});

Deno.test("chunk 같은 비 Vision 단계는 조회 없이 통과", async () => {
  const gate = fake({ docType: "pdf", used: 999, limit: 1 });
  assertEquals(await gate({ job_id: "j", doc_id: "d", stage: "chunk" }), { defer: false });
});

Deno.test("extract 는 image·pptx 일 때만 게이트, 그것도 첫 창(from 0)만", async () => {
  const pdfGate = fake({ docType: "pdf", used: 999, limit: 1 });
  assertEquals(await pdfGate({ job_id: "j", doc_id: "d", stage: "extract" }), { defer: false });
  const imgGate = fake({ docType: "image", used: 999, limit: 1 });
  assertEquals((await imgGate({ job_id: "j", doc_id: "d", stage: "extract" })).defer, true);
  assertEquals(await imgGate({ job_id: "j", doc_id: "d", stage: "extract", from: 4 }), { defer: false });
});

Deno.test("한도 안이면 통과, 초과면 defer + 사유", async () => {
  assertEquals(await fake({ docType: "pdf", used: 99, limit: 100 })({ job_id: "j", doc_id: "d", stage: "vision" }), { defer: false });
  const r = await fake({ docType: "pdf", used: 100, limit: 100 })({ job_id: "j", doc_id: "d", stage: "scan" });
  assertEquals(r.defer, true);
  if (r.defer) assertEquals(r.reason.includes("100/100"), true);
});

Deno.test("owner 는 quota 면제", async () => {
  const client = {
    from: () => ({ select: () => ({ eq: () => ({ limit: () => Promise.resolve({ data: [{ user_id: "owner", doc_type: "pdf" }], error: null }) }) }) }),
    rpc: () => Promise.resolve({ data: 999, error: null }),
  };
  // deno-lint-ignore no-explicit-any
  const gate = makeQuotaGate({ client: client as any, getPlan: () => Promise.resolve({ code: "free", answers_per_day: 5, storage_bytes_limit: 1, vision_pages_per_month: 1 }), settings: { authEnabled: true, quotaEnforcementEnabled: true, ownerUserId: "owner" }, nowMs: () => 0 });
  assertEquals(await gate({ job_id: "j", doc_id: "d", stage: "vision" }), { defer: false });
});
```

구현 `_shared/ingest/quota_gate.ts`:

```ts
/**
 * 월 Vision 페이지 게이트 — Vision 을 부를 수 있는 단계 앞에서 사용자의 이번 달 페이지 수를 본다.
 *
 * 한 곳(워커)에서만 판정한다. 핸들러 넷(scan·vision·vision_missing·extract 의 image/pptx)에
 * 각자 넣으면 네 벌이 된다. 초과면 잡을 `deferred_quota` 로 보류하고 페이로드를 남긴다 —
 * 실패가 아니다. 매일 00:00 KST cron(`vision_quota_release`)이 되돌리고, 여전히 초과면 다시 보류된다.
 *
 * 기존 USD 예산 가드(`budget_guard.ts`)는 **전역** 비용 방어라 그대로 둔다. 이건 **사용자별** 계량이다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";
import { type PlanLimits, type QuotaSettings, quotaActiveFor, visionPagesUsedMonth } from "../me/quota.ts";
import type { TaskPayload } from "./worker.ts";

export const VISION_STAGES = new Set(["scan", "vision", "vision_missing", "extract"]);
const EXTRACT_VISION_DOC_TYPES = new Set(["image", "pptx"]);

export type GateResult = { defer: false } | { defer: true; reason: string };
export type QuotaGate = (task: TaskPayload) => Promise<GateResult>;

export interface QuotaGateDeps {
  client: SupabaseClient;
  settings: QuotaSettings;
  getPlan: (userId: string) => Promise<PlanLimits | null>;
  nowMs?: () => number;
}

export function makeQuotaGate(deps: QuotaGateDeps): QuotaGate {
  const now = deps.nowMs ?? (() => Date.now());
  return async (task) => {
    if (!VISION_STAGES.has(task.stage)) return { defer: false };
    if (task.stage === "extract" && (task.from ?? 0) > 0) return { defer: false };

    const { data, error } = await deps.client
      .from("documents")
      .select("user_id, doc_type")
      .eq("id", task.doc_id)
      .limit(1);
    if (error) {
      console.warn(`quota_gate documents 조회 실패 — 통과 (doc=${task.doc_id}): ${error.message}`);
      return { defer: false };
    }
    const doc = (data ?? [])[0] as { user_id: string; doc_type: string } | undefined;
    if (!doc) return { defer: false };
    if (task.stage === "extract" && !EXTRACT_VISION_DOC_TYPES.has(doc.doc_type)) return { defer: false };

    const user = { userId: doc.user_id, isAuthenticated: true };
    if (!quotaActiveFor(user, deps.settings)) return { defer: false };
    const plan = await deps.getPlan(doc.user_id);
    if (plan === null || plan.vision_pages_per_month <= 0) return { defer: false };
    const used = await visionPagesUsedMonth(deps.client, doc.user_id, now());
    if (used === null) return { defer: false };
    if (used >= plan.vision_pages_per_month) {
      return {
        defer: true,
        reason: `${plan.code} 플랜의 이번 달 Vision 페이지 한도 도달 (${used}/${plan.vision_pages_per_month}) — 다음 달에 이어서 처리`,
      };
    }
    return { defer: false };
  };
}
```

- [ ] **Step 5: `worker.ts` 에 게이트 주입점**

`WorkerDeps` 에 `gate?: (task: TaskPayload) => Promise<{ defer: false } | { defer: true; reason: string }>;` 추가. `DrainResult` 에 `deferred: number` 추가(초기값 0, `drainLoop` 합산에 `total.deferred += r.deferred`). `drainOnce` 의 `const startedMs = Date.now();` **앞**에:

```ts
    // 사용자별 월 Vision 페이지 게이트 — 보류는 실패가 아니다. 메시지는 지우고 페이로드를 잡에 남긴다.
    if (deps.gate) {
      const g = await deps.gate(task);
      if (g.defer) {
        await deps.client.rpc("ingest_queue_delete", { message_id: msg.msg_id });
        await touchJob(deps.client, task.job_id, {
          status: "deferred_quota",
          deferred_task: task,
          error_msg: g.reason,
          last_heartbeat_at: nowIso(),
        });
        out.deferred++;
        continue;
      }
    }
```

`worker_test.ts` 에 케이스 추가:

```ts
Deno.test("게이트가 보류하면 delete + deferred_quota, 핸들러는 안 돈다", async () => {
  const { client, calls, updates } = fakeClient([msg({ message: { job_id: "j1", doc_id: "d1", stage: "vision" } })]);
  let ran = 0;
  const r = await drainOnce({
    // deno-lint-ignore no-explicit-any
    client: client as any,
    handlers: { vision: () => { ran++; return Promise.resolve(); } },
    gate: () => Promise.resolve({ defer: true, reason: "한도" }),
  });
  assertEquals(ran, 0);
  assertEquals(r.deferred, 1);
  assertEquals(calls.some((c) => c.fn === "ingest_queue_delete"), true);
  assertEquals(updates.some((u) => u.status === "deferred_quota" && u.error_msg === "한도"), true);
});
```

`fakeClient` 의 `from()` 이 `update().eq()` 만 지원하므로 그대로 쓸 수 있다.

- [ ] **Step 6: 결선 — 워커·업로드·이메일·`/me/plan`**

`api-ingest-worker/index.ts`: `drainLoop({ client, handlers, … })` 호출에 `gate: makeQuotaGate({ client, settings, getPlan: (uid) => getEffectivePlan(client, uid) })` 추가. import `makeQuotaGate`(`../_shared/ingest/quota_gate.ts`), `getEffectivePlan`(`../_shared/me/quota.ts`). `settings` 는 `loadSettings()` 결과이고 `QuotaSettings` 필드(`authEnabled`·`quotaEnforcementEnabled`·`ownerUserId`)를 이미 가진다.

`api-documents/index.ts` 의 업로드 분기: `enforceRateLimit(METRIC_DOCS, …)` 블록을 아래로 교체:

```ts
    // ② 남용 방지 — 분당 60건. 플랜 quota 는 용량으로 persist 안에서 본다(S4).
    try {
      await enforceUploadBurst(user, { client });
    } catch (e) {
      if (e instanceof RateLimitError) {
        return applyCorsHeaders(req, jsonResponse({ detail: e.detail }, e.status), settings);
      }
      throw e;
    }
```

`handleUpload(form, { client, bucket, userId })` 에 `checkStorage: makeStorageCheck(client, user, settings)` 추가. import 정리(`METRIC_DOCS` 제거, `enforceUploadBurst`·`makeStorageCheck` 추가). precheck 라우트는 burst 를 안 건다(가볍다).

`email_route.ts`: `incrementDocsCounter` 호출과 import 제거. `ingestEmailAttachment(…)` 의 deps 에 `checkStorage: makeStorageCheck(deps.client, { userId, isAuthenticated: true }, deps.settings)` — `EmailRouteDeps.settings` 타입을 `{ emailWebhookSecret: string } & QuotaSettings` 로 넓힌다(호출부는 `settings` 전체를 넘기므로 호환). `email_ingest.ts` 에서 `incrementDocsCounter` 함수를 삭제한다.

`me/pipeline.ts` `buildPlan`:

```ts
export async function buildPlan(userId: string, deps: MeDeps): Promise<Record<string, unknown>> {
  const nowMs = (deps.now ?? Date.now)();
  const plan = await getEffectivePlan(deps.client, userId);
  if (plan === null) {
    throw new MeHttpError(503, "플랜 정보를 불러올 수 없습니다. 잠시 후 다시 시도해 주세요.");
  }
  return {
    plan_code: plan.code,
    storage: {
      used_bytes: (await storageUsedBytes(deps.client, userId)) ?? 0,
      limit_bytes: plan.storage_bytes_limit,
    },
    vision_pages: {
      used: (await visionPagesUsedMonth(deps.client, userId, nowMs)) ?? 0,
      limit: plan.vision_pages_per_month,
      period_start: kstMonthStartIso(nowMs),
    },
    answers: {
      per_day: plan.answers_per_day,
      used_today: await getTodaysCount(deps.client, userId, "answers", nowMs),
    },
  };
}
```

`pipeline_test.ts`(있으면) 의 `/me/plan` 기대 모양을 위와 맞춘다. `verify_current_user_parity.py` 등 Python 대조 하네스 중 `/me/plan` 모양을 비교하는 것이 있으면(`grep -ln "max_documents\|documents_count" api/scripts/verify_*.py`) 그 하네스는 **폐기 대상으로 표시**(파일 상단 docstring 에 "2026-09-15 계량 교체로 원본과 의도적으로 다름 — 폐기" 한 줄)하고 CI 목록(`ci.yml`)에 있으면 뺀다.

- [ ] **Step 7: 웹 — 계량 표시**

`settings/page.tsx` 의 `MePlan` 과 표시를 교체:

```tsx
interface MePlan {
  plan_code: string;
  storage: { used_bytes: number; limit_bytes: number };
  vision_pages: { used: number; limit: number; period_start: string };
  answers: { per_day: number; used_today: number };
}

function gb(n: number): string {
  return `${(n / 1024 ** 3).toFixed(n >= 1024 ** 3 ? 1 : 2)}GB`;
}
// … 목록:
<li>플랜: <strong>{plan.plan_code === 'pro' ? 'Pro' : 'Free'}</strong></li>
<li>저장 용량: {gb(plan.storage.used_bytes)} / {gb(plan.storage.limit_bytes)}</li>
<li>이번 달 Vision 페이지: {plan.vision_pages.used} / {plan.vision_pages.limit}장</li>
<li>오늘 답변: {plan.answers.used_today} / {plan.answers.per_day}회</li>
```

`subscription-section.tsx` 의 문구: `Pro 요금: 월 6,900원 (저장 10GB · Vision 1,000페이지/월 · 답변 일 50회 · 이메일 인제스트)`.

- [ ] **Step 8: 전체 검증**

Run: `cd supabase/functions && deno fmt --check && deno lint && deno test --allow-env --allow-net --allow-read`
Run: `cd web && pnpm exec tsc --noEmit && pnpm lint`
Run: `cd workers/api-proxy && deno test --allow-net`
Expected: 전부 PASS.

`grep -rn "max_documents\|METRIC_DOCS\|rateLimitDocsPerDay\|countActiveDocuments\|incrementDocsCounter\|EMAIL_ALLOWED_EXTENSIONS" supabase/functions web/src` → **0건**.

- [ ] **Step 9: 마이그 031 적용 → 배포 → 라이브 검증**

배포: `supabase functions deploy api-documents api-account api-ingest-worker`.
검증(소유자는 quota 면제라 402 는 못 본다 — RPC 값으로 확인):

```sql
SELECT code, storage_bytes_limit, vision_pages_per_month FROM plans;
SELECT public.storage_bytes_used('<owner uuid>');           -- 25,362,395 근처(2026-09-14 /stats total_size_bytes)
SELECT public.vision_pages_used_since('<owner uuid>', date_trunc('month', now() AT TIME ZONE 'Asia/Seoul') AT TIME ZONE 'Asia/Seoul');
SELECT jobname, schedule, active FROM cron.job WHERE jobname = 'vision-quota-release';
SELECT public.vision_quota_release();                        -- 0 (보류 잡 없음)
```

`curl -s -H "Authorization: Bearer <session>" https://jetrag-api.woong-s.com/me/plan` → 새 모양.
소유자 세션으로 중복 파일 업로드 → 202 duplicated (burst 통과). 같은 파일 61회 루프 → 61번째 429 확인 후 `DELETE FROM upload_burst WHERE user_key = '<owner uuid>'`.

- [ ] **Step 10: 커밋**

```bash
git add api/migrations/031_metering_v2.sql supabase/functions web/src
git commit -m "feat(metering): 문서 수·일일 상한 → 저장 용량 + 월 Vision 페이지 계량 (마이그 031)

- plans: storage_bytes_limit·vision_pages_per_month (잠정 Free 1GB/100p, Pro 10GB/1000p). max_documents 제거.
- persist 에 용량 검사 훅, 초과 402 {reason, used, limit}. 남용 방지는 분당 60건(upload_burst).
- 워커 앞단 quota_gate: scan·vision·vision_missing·extract(image/pptx) 진입 전 월 페이지 검사 → deferred_quota 보류.
- vision_quota_release() 매일 00:00 KST 재투입. source_channel 에 자동 수집 3종.
- /me/plan 새 모양 + 설정 화면. 스펙 2026-09-15 §4 S4."
```

---

## Task 5: 큐 처리량 실측 (스펙 §8 5단계)

**Files:**
- Create: `api/scripts/measure_queue_throughput.ts`

- [ ] **Step 1: 스크립트 작성**

```ts
/**
 * 큐 처리량 실측 — 기존 문서 N 건을 재인제스트해 시간당 완료 건수를 잰다.
 *   deno run --allow-net --allow-env api/scripts/measure_queue_throughput.ts <session_access_token> [N=5]
 * 재인제스트는 vision_page_cache 를 타서 Vision 비용이 거의 0 이다(캐시 미스 페이지만 과금).
 * 결과: 시작·종료 시각, 건당 소요, 시간당 환산. work-log 에 옮겨 적는다.
 */
const [session, nRaw] = Deno.args;
const N = Number(nRaw ?? "5");
const API = Deno.env.get("JETRAG_API_BASE") ?? "https://jetrag-api.woong-s.com";
const H = { Authorization: `Bearer ${session}`, "User-Agent": "JetRag-Measure/0.1" };

const docs = (await fetch(`${API}/documents?limit=${N}`, { headers: H }).then((r) => r.json())).items as { id: string; title: string }[];
const ids = docs.map((d) => d.id);
console.log(`대상 ${ids.length}건:`, docs.map((d) => d.title.slice(0, 20)));

const t0 = Date.now();
for (const id of ids) {
  const r = await fetch(`${API}/documents/${id}/reingest`, { method: "POST", headers: { ...H, "Content-Length": "0" } });
  console.log(`  reingest ${id.slice(0, 8)} → ${r.status}`);
}
const done = new Map<string, number>();
while (done.size < ids.length) {
  await new Promise((r) => setTimeout(r, 10_000));
  const st = await fetch(`${API}/documents/batch-status?ids=${ids.join(",")}`, { headers: H }).then((r) => r.json());
  for (const [id, s] of Object.entries(st.statuses ?? st)) {
    const status = (s as { status?: string }).status;
    if ((status === "completed" || status === "failed") && !done.has(id)) {
      done.set(id, Date.now());
      console.log(`  ${id.slice(0, 8)} ${status} +${Math.round((Date.now() - t0) / 1000)}s`);
    }
  }
}
const totalS = (Math.max(...done.values()) - t0) / 1000;
console.log(`\n${ids.length}건 / ${totalS.toFixed(0)}s → 시간당 ${(ids.length / totalS * 3600).toFixed(0)}건, 건당 ${(totalS / ids.length).toFixed(0)}s`);
```

`GET /documents` 응답의 목록 키(`items` 인지 `documents` 인지)와 `batch-status` 응답 모양은 실행 전에 `curl` 로 한 번 확인해 맞춘다(스펙 규칙: 검출기의 입력을 먼저 본다).

- [ ] **Step 2: 실행 (N=5, 그다음 N=14)**

Expected: 시간당 건수 하나가 나온다. 이 값으로 에이전트 플랜의 백로그 동시성 기본값을 정한다. 결과를 `work-log/2026-09-15 …` 에 표로 남긴다.

- [ ] **Step 3: 커밋**

```bash
git add api/scripts/measure_queue_throughput.ts
git commit -m "measure(scripts): 큐 처리량 실측 스크립트 — 재인제스트 배치로 시간당 완료 건수"
```

---

## 완료 기준 (플랜 전체)

1. `deno test` 전부 PASS (기존 246 + 신규), `deno fmt --check`·`deno lint` 0 오류, 웹 `tsc`·`lint` 0 오류, 프록시 테스트 PASS.
2. 마이그 030·031 운영 적용, 함수 3개 배포, E2E 스크립트(Task 3 Step 6) 기대값 전부 일치.
3. `grep` 잔재 0건 (Task 4 Step 8).
4. 큐 처리량 숫자 1개.
5. 한국어 커밋 5건 + push, work-log `2026-09-15 서버 근본 수정 S1~S4.md` 에 실측치·결정·다음 단계(에이전트 플랜) 기록.
