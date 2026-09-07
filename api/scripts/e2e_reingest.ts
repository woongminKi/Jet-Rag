/**
 * 재인제스트 2 종 E2E — 실제 Supabase + pg_cron 사슬.
 *
 * ## 왜 청크를 일부러 지우는가
 * `reingest-missing` 은 "vision 이 안 닿은 페이지" 를 `chunks` 의 `(vision) p.N`
 * 제목으로 찾는다. 그 상황을 만들려면 실제로 한 페이지의 vision 청크를 없애야 한다.
 * 없애지 않으면 "누락 0" 경로만 타고 정작 보강 경로는 한 번도 안 돈다.
 *
 * ## 비용
 * 같은 파일을 앞서 인제스트해 `vision_page_cache` 가 차 있으면 **Gemini 호출 0** 이다.
 * 캐시가 비어 있으면 페이지당 $0.005~0.03 이 든다(2 페이지 문서).
 *
 * ## 격리
 * 샌드박스 `user_id` 로만 쓰고, 끝나면 documents(→chunks CASCADE)·job·artifact·
 * Storage 객체를 지운다. **`vision_usage_log` 와 `vision_page_cache` 는 남긴다** —
 * 전자는 실제 지출 기록이고 후자는 다음 실행의 비용을 0 으로 만든다.
 *
 * 사용:
 *   deno run --config supabase/functions/deno.json --allow-all \
 *     api/scripts/e2e_reingest.ts [--keep]
 */

import { createClient, type SupabaseClient } from "@supabase/supabase-js";

import { handleUpload } from "../../supabase/functions/_shared/documents/upload.ts";
import {
  reingestDocument,
  reingestMissingVision,
} from "../../supabase/functions/_shared/documents/reingest.ts";

const keep = Deno.args.includes("--keep");

const env: Record<string, string> = {};
try {
  for (const line of (await Deno.readTextFile(".env")).split("\n")) {
    const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)$/);
    if (m) env[m[1]] = m[2].trim().replace(/^["']|["']$/g, "");
  }
} catch { /* 셸 환경변수만 쓴다 */ }
const url = Deno.env.get("SUPABASE_URL") ?? env["SUPABASE_URL"];
const key = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? env["SUPABASE_SERVICE_ROLE_KEY"];
const bucket = Deno.env.get("SUPABASE_STORAGE_BUCKET") ?? env["SUPABASE_STORAGE_BUCKET"] ??
  "documents";
if (!url || !key) {
  console.error("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 가 필요하다");
  Deno.exit(2);
}

const client = createClient(url, key);
/** `ingest_sandbox.py` 와 같은 uuid5 네임스페이스에서 나온 값. */
const SANDBOX_USER = "253e3fa9-d706-5912-8aa2-1c6fd48a9a4d";
const FILE = "assets/public/law_sample2.pdf";

let exitCode = 0;
let docId = "";

interface ChunkRow {
  chunk_idx: number;
  page: number | null;
  section_title: string | null;
  dense_vec: unknown;
}

async function chunksOf(id: string): Promise<ChunkRow[]> {
  const { data, error } = await client
    .from("chunks")
    .select("chunk_idx, page, section_title, dense_vec")
    .eq("doc_id", id)
    .order("chunk_idx", { ascending: true });
  if (error) throw new Error(`chunks 조회 실패: ${error.message}`);
  return (data ?? []) as ChunkRow[];
}

function visionPages(rows: ChunkRow[]): number[] {
  return [
    ...new Set(
      rows.filter((r) => (r.section_title ?? "").startsWith("(vision) p.") && r.page)
        .map((r) => r.page as number),
    ),
  ].sort((a, b) => a - b);
}

/** 큐가 비고 모든 청크에 `dense_vec` 이 찰 때까지 기다린다. */
async function waitChain(jobId: string, label: string): Promise<ChunkRow[]> {
  const deadline = performance.now() + 300_000;
  let rows: ChunkRow[] = [];
  let lastStage = "";
  while (performance.now() < deadline) {
    await new Promise((r) => setTimeout(r, 3000));
    rows = await chunksOf(docId);
    const { data: job } = await client
      .from("ingest_jobs").select("status, current_stage, error_msg")
      .eq("id", jobId).single();
    const j = job as { status?: string; current_stage?: string; error_msg?: string } | null;
    const stage = `${j?.current_stage ?? "-"}/${j?.status ?? "-"}`;
    if (stage !== lastStage) {
      console.log(`    ${label} ${stage}  청크 ${rows.length}`);
      lastStage = stage;
    }
    if (j?.error_msg) {
      console.log(`  **잡 오류**: ${j.error_msg}`);
      exitCode = 1;
      return rows;
    }
    const { data: depth } = await client.rpc("ingest_queue_depth");
    const queued = Number((depth ?? [{}])[0]?.queue_length ?? 0);
    const embedded = rows.filter((r) => r.dense_vec !== null).length;
    if (queued === 0 && rows.length > 0 && embedded === rows.length) break;
  }
  return rows;
}

function check(ok: boolean, msg: string): void {
  console.log(`    ${ok ? "OK  " : "**FAIL**"} ${msg}`);
  if (!ok) exitCode = 1;
}

try {
  // ─────────────────────────── ① 최초 인제스트
  console.log("① 업로드 + 인제스트");
  const bytes = await Deno.readFile(FILE);
  const fd = new FormData();
  fd.append("file", new File([bytes.slice()], "law_sample2.pdf", { type: "application/pdf" }));
  fd.append("mode", "precise"); // flags.ingest_mode 가 실제로 남는지도 본다
  const up = await handleUpload(fd, { client, bucket, userId: SANDBOX_USER });
  if (up.status !== 202) throw new Error(`업로드 실패: ${JSON.stringify(up)}`);
  docId = String((up.body as { doc_id: string }).doc_id);
  const jobId0 = String((up.body as { job_id: string }).job_id);
  console.log(`  doc ${docId}`);

  const { data: d0 } = await client.from("documents").select("flags").eq("id", docId).single();
  check(
    (d0 as { flags?: Record<string, unknown> })?.flags?.ingest_mode === "precise",
    `업로드가 flags.ingest_mode 를 남긴다 (${JSON.stringify((d0 as { flags?: unknown })?.flags)})`,
  );

  const base = await waitChain(jobId0, "①");
  const basePages = visionPages(base);
  console.log(`  청크 ${base.length}  vision 페이지 [${basePages.join(", ")}]`);
  check(base.length > 0, "청크가 생겼다");
  check(basePages.length > 0, "vision 페이지가 있다");

  // ─────────────────────────── ② 한 페이지의 vision 청크를 지운다
  const victim = basePages[basePages.length - 1];
  console.log(`\n② p.${victim} 의 vision 청크 삭제 — 누락 상황을 만든다`);
  const doomed = base.filter(
    (r) => r.page === victim && (r.section_title ?? "").startsWith("(vision) p."),
  );
  for (const r of doomed) {
    const { error } = await client.from("chunks").delete()
      .eq("doc_id", docId).eq("chunk_idx", r.chunk_idx);
    if (error) throw new Error(`chunks 삭제 실패: ${error.message}`);
  }
  const after = await chunksOf(docId);
  console.log(`  ${doomed.length}건 삭제 → 청크 ${after.length}`);
  check(!visionPages(after).includes(victim), `p.${victim} 이 누락 상태다`);

  // ─────────────────────────── ③ reingest-missing
  console.log("\n③ POST /documents/{id}/reingest-missing");
  const rm = await reingestMissingVision(
    { client, bucket }, SANDBOX_USER, docId, new URLSearchParams(),
  );
  console.log(`  ${rm.status} ${JSON.stringify(rm.body)}`);
  check(rm.status === 202, "202 로 접수된다");
  check(
    JSON.stringify((rm.body as { missing_pages_before: number[] }).missing_pages_before) ===
      JSON.stringify([victim]),
    `missing_pages_before 가 [${victim}] 이다`,
  );
  const filled = await waitChain(
    String((rm.body as { job_id: string }).job_id), "③",
  );
  const filledPages = visionPages(filled);
  console.log(`  청크 ${filled.length}  vision 페이지 [${filledPages.join(", ")}]`);
  check(filledPages.includes(victim), `p.${victim} 이 다시 채워졌다`);
  check(
    filled.filter((r) => r.dense_vec === null).length === 0,
    "새 청크까지 dense_vec 이 찼다",
  );
  const idxs = filled.map((r) => r.chunk_idx);
  check(new Set(idxs).size === idxs.length, "chunk_idx 가 겹치지 않는다");
  // 보존이 핵심이다 — 기존 청크가 그대로 남아 있어야 한다.
  const survivors = after.map((r) => r.chunk_idx).filter((i) => idxs.includes(i));
  check(
    survivors.length === after.length,
    `기존 청크 ${after.length}건이 전부 보존됐다 (${survivors.length})`,
  );

  // ─────────────────────────── ④ 전체 reingest
  console.log("\n④ POST /documents/{id}/reingest?mode=fast");
  const rf = await reingestDocument(
    { client, bucket }, SANDBOX_USER, docId, new URLSearchParams({ mode: "fast" }),
  );
  console.log(`  ${rf.status} ${JSON.stringify(rf.body)}`);
  check(rf.status === 202, "202 로 접수된다");
  check(
    (rf.body as { chunks_deleted: number }).chunks_deleted === filled.length,
    `chunks_deleted 가 직전 청크 수와 같다 (${(rf.body as { chunks_deleted: number }).chunks_deleted} vs ${filled.length})`,
  );
  const rebuilt = await waitChain(String((rf.body as { job_id: string }).job_id), "④");
  console.log(`  청크 ${rebuilt.length}  vision 페이지 [${visionPages(rebuilt).join(", ")}]`);
  check(rebuilt.length > 0, "청크가 다시 만들어졌다");
  check(rebuilt[0].chunk_idx === 0, "chunk_idx 가 0 부터 다시 시작한다");
  const { data: d1 } = await client.from("documents").select("flags").eq("id", docId).single();
  check(
    (d1 as { flags?: Record<string, unknown> })?.flags?.ingest_mode === "fast",
    `reingest 가 새 모드를 남긴다 (${JSON.stringify((d1 as { flags?: unknown })?.flags)})`,
  );

  // ─────────────────────────── ⑤ 진행 중 충돌
  console.log("\n⑤ 진행 중 잡이 있으면 409");
  const { data: jrow } = await client.from("ingest_jobs")
    .insert({ doc_id: docId, status: "running" }).select("id").single();
  const conflictId = (jrow as { id: string }).id;
  const c1 = await reingestDocument(
    { client, bucket }, SANDBOX_USER, docId, new URLSearchParams(),
  );
  check(c1.status === 409, `full → 409 (${c1.status})`);
  const c2 = await reingestMissingVision(
    { client, bucket }, SANDBOX_USER, docId, new URLSearchParams(),
  );
  check(c2.status === 409, `missing → 409 (${c2.status})`);
  // 남의 문서는 404
  const c3 = await reingestDocument(
    { client, bucket }, "00000000-0000-5000-8000-000000000000", docId,
    new URLSearchParams(),
  );
  check(c3.status === 404, `남의 문서 → 404 (${c3.status})`);
  await client.from("ingest_jobs").delete().eq("id", conflictId);
} catch (e) {
  console.error("\n**예외**:", e);
  exitCode = 1;
} finally {
  if (keep) {
    console.log(`\n  --keep: doc ${docId} 유지`);
  } else if (docId) {
    await cleanup(client, docId);
  }
}

async function cleanup(c: SupabaseClient, id: string): Promise<void> {
  // 지우기 전에 샌드박스 소유인지 반드시 확인한다.
  const { data: doc } = await c.from("documents")
    .select("id, user_id, storage_path").eq("id", id).single();
  const row = doc as { user_id?: string; storage_path?: string } | null;
  if (!row || row.user_id !== SANDBOX_USER) {
    console.log("\n  **샌드박스 문서가 아니다 — 정리 중단**");
    return;
  }
  // 이 잡들의 큐 메시지를 비운다.
  const { data: jobs } = await c.from("ingest_jobs").select("id").eq("doc_id", id);
  const jobIds = new Set(((jobs ?? []) as { id: string }[]).map((j) => j.id));
  for (let i = 0; i < 80; i++) {
    const { data } = await c.rpc("ingest_queue_read", { vt_seconds: 5, qty: 1 });
    const msgs = (data ?? []) as { msg_id: number; message: { job_id?: string } }[];
    if (msgs.length === 0) break;
    for (const m of msgs) {
      if (m.message?.job_id && jobIds.has(m.message.job_id)) {
        await c.rpc("ingest_queue_delete", { message_id: m.msg_id });
      }
    }
  }
  for (const j of jobIds) {
    await c.from("ingest_artifacts").delete().eq("job_id", j);
    await c.from("ingest_jobs").delete().eq("id", j);
  }
  if (row.storage_path) await c.storage.from(bucket).remove([row.storage_path]);
  await c.from("documents").delete().eq("id", id);
  console.log(`\n  정리 완료 (doc ${id} · job ${jobIds.size}개 · Storage · 산출물)`);
  console.log("  vision_usage_log / vision_page_cache 는 남긴다 (지출 기록·비용 절감)");
}

console.log(exitCode === 0 ? "\nFAIL 0" : "\n**실패 있음**");
Deno.exit(exitCode);
