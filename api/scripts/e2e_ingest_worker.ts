/**
 * `extract` → `chunk` 워커를 **실제 Supabase 에 붙여** 끝까지 돌린다.
 *
 * ## 가짜 클라이언트로는 증명 못 하는 것
 * 단위 테스트는 계약(호출 순서·멱등 옵션·거절 조건)만 고정한다. **PostgREST 쿼리가
 * 실제로 도는지**(`.lt()` · `.order()` · rpc 인자 이름), 큐 왕복이 맞물리는지는
 * 붙여 봐야 안다.
 *
 * ## 운영 DB 를 건드린다 — 격리 규칙
 * - `documents` 는 **읽기만** 한다.
 * - 쓰는 것은 이 스크립트가 만든 `ingest_jobs` 1 행과 그 job_id 의 `ingest_artifacts`뿐.
 * - 끝나면 전부 지운다(`--keep` 으로 남길 수 있다).
 * - 큐에 남은 이 잡의 메시지도 드레인해서 비운다.
 *
 * 사용:
 *   deno run --config supabase/functions/deno.json --allow-all \
 *     api/scripts/e2e_ingest_worker.ts <doc_id> [--pages 10] [--keep]
 */

import { createClient } from "@supabase/supabase-js";

import { drainOnce } from "../../supabase/functions/_shared/ingest/worker.ts";
import { makeExtractHandler } from "../../supabase/functions/_shared/ingest/handlers/extract.ts";
import { makeChunkHandler } from "../../supabase/functions/_shared/ingest/handlers/chunk.ts";

function arg(name: string, dflt?: string): string | undefined {
  const i = Deno.args.indexOf(`--${name}`);
  return i >= 0 ? Deno.args[i + 1] : dflt;
}

const docId = Deno.args.find((a) => !a.startsWith("--") && a.length > 20);
if (!docId) {
  console.error("사용: e2e_ingest_worker.ts <doc_id> [--pages N] [--keep]");
  Deno.exit(2);
}
const pagesPerTask = Number(arg("pages", "10"));
const keep = Deno.args.includes("--keep");

// --- .env 에서 자격증명 (셸 환경변수가 있으면 그쪽 우선) ---
const env: Record<string, string> = {};
try {
  for (const line of (await Deno.readTextFile(".env")).split("\n")) {
    const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)$/);
    if (m) env[m[1]] = m[2].trim().replace(/^["']|["']$/g, "");
  }
} catch { /* .env 없으면 셸 환경변수만 쓴다 */ }
const url = Deno.env.get("SUPABASE_URL") ?? env["SUPABASE_URL"];
const key = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? env["SUPABASE_SERVICE_ROLE_KEY"];
const bucket = Deno.env.get("SUPABASE_STORAGE_BUCKET") ?? env["SUPABASE_STORAGE_BUCKET"] ??
  "documents";
if (!url || !key) throw new Error("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 를 못 찾았다");

const client = createClient(url, key, { auth: { persistSession: false } });

// --- 대상 문서 확인 (읽기만) ---
const { data: docs, error: dErr } = await client
  .from("documents").select("id, doc_type, storage_path, title").eq("id", docId).limit(1);
if (dErr) throw new Error(`documents 조회 실패: ${dErr.message}`);
const doc = (docs ?? [])[0];
if (!doc) throw new Error(`문서를 못 찾았다: ${docId}`);
console.log(`  대상  ${doc.doc_type}  ${String(doc.title ?? "").slice(0, 40)}`);

// --- 잡 생성 ---
const { data: jobRow, error: jErr } = await client
  .from("ingest_jobs").insert({ doc_id: docId, status: "queued" }).select("id").single();
if (jErr) throw new Error(`ingest_jobs 생성 실패: ${jErr.message}`);
const jobId = jobRow.id as string;
console.log(`  job   ${jobId}`);

let exitCode = 0;
try {
  const { error: sErr } = await client.rpc("ingest_queue_send", {
    payload: { job_id: jobId, doc_id: docId, stage: "extract" },
  });
  if (sErr) throw new Error(`enqueue 실패: ${sErr.message}`);

  const handlers = {
    extract: makeExtractHandler({ client, bucket, pagesPerTask }),
    chunk: makeChunkHandler({ client }),
  };

  const t0 = performance.now();
  let rounds = 0;
  let lastStage = "";
  // 넉넉한 상한 — 무한 루프 방지용이지 예상 횟수가 아니다.
  for (; rounds < 400; rounds++) {
    const r = await drainOnce({ client, handlers, batch: 1, vtSeconds: 120 });
    if (r.read === 0) break;
    if (r.errors.length) {
      console.log(`  [${rounds}] **오류** ${JSON.stringify(r.errors)}`);
      exitCode = 1;
      // 실패한 메시지는 `vt` 동안 안 보인다. 아래 정리 루프가 그걸 못 지워서 큐에
      // 남고, job 은 지워지므로 **다음 실행이 FK 위반으로 죽는다**(실제로 겪었다).
      // msg_id 로 바로 보관해 끊는다.
      for (const e of r.errors) {
        await client.rpc("ingest_queue_archive", { message_id: e.msg_id });
      }
      break;
    }
    // 진행 상황 — 어디까지 갔는지 보이게.
    const { data: arts } = await client
      .from("ingest_artifacts").select("stage, seq")
      .eq("job_id", jobId).order("seq", { ascending: true });
    const stages = (arts ?? []).map((a) => a.stage);
    const cur = stages[stages.length - 1] ?? "";
    if (cur !== lastStage) {
      console.log(`  [${rounds}] ${cur} …`);
      lastStage = cur;
    }
    if (stages.includes("chunk")) break;
  }
  const ms = performance.now() - t0;

  // --- 결과 ---
  const { data: arts } = await client
    .from("ingest_artifacts").select("stage, seq, payload")
    .eq("job_id", jobId).order("seq", { ascending: true });
  const rows = arts ?? [];
  const ex = rows.filter((r) => r.stage === "extract");
  const ch = rows.filter((r) => r.stage === "chunk");

  console.log();
  console.log(`  드레인 ${rounds}회, ${(ms / 1000).toFixed(1)}s`);
  console.log(`  extract 산출물 ${ex.length}건  seq ${ex.map((r) => r.seq).join(",")}`);
  const totalSections = ex.reduce(
    (a, r) => a + ((r.payload as { sections?: unknown[] })?.sections?.length ?? 0),
    0,
  );
  const totalPages = (ex[0]?.payload as { total_pages?: number })?.total_pages ?? 0;
  console.log(`  페이지 ${totalPages}  섹션 ${totalSections}`);
  if (ch.length === 0) {
    console.log("  **chunk 산출물이 없다**");
    exitCode = 1;
  } else {
    const p = ch[0].payload as {
      chunk_count: number;
      section_count: number;
      extract_parts: number;
      records: { text: string; page: number | null; section_title: string | null }[];
    };
    console.log(`  chunk 산출물  청크 ${p.chunk_count}  섹션 ${p.section_count}  ` +
      `extract 조각 ${p.extract_parts}`);
    // 이 payload 를 다음 단계(load)가 통째로 읽는다. Edge 메모리 상한은 240MB
    // (Phase 0 실측)이라 크기를 눈에 보이게 남긴다.
    const bytes = new TextEncoder().encode(JSON.stringify(ch[0].payload)).length;
    console.log(`  chunk payload 크기 ${(bytes / 1e6).toFixed(1)}MB`);
    const titled = p.records.filter((r) => r.section_title).length;
    console.log(`  section_title 있는 청크 ${titled}/${p.records.length}`);
    console.log(`  첫 청크: ${JSON.stringify(p.records[0]?.text?.slice(0, 60))}`);
    if (p.section_count !== totalSections) {
      console.log(`  **섹션 수 불일치** extract 합계 ${totalSections} vs chunk ${p.section_count}`);
      exitCode = 1;
    }
  }
} finally {
  if (keep) {
    console.log(`\n  --keep: job ${jobId} 와 산출물을 남긴다`);
  } else {
    // 큐에 남은 이 잡의 메시지를 비운다 — 안 그러면 pg_cron 이 나중에 집어간다.
    for (let i = 0; i < 50; i++) {
      const { data } = await client.rpc("ingest_queue_read", { vt_seconds: 5, qty: 1 });
      const msgs = (data ?? []) as { msg_id: number; message: { job_id?: string } }[];
      if (msgs.length === 0) break;
      for (const m of msgs) {
        if (m.message?.job_id === jobId) {
          await client.rpc("ingest_queue_delete", { message_id: m.msg_id });
        }
      }
    }
    await client.from("ingest_artifacts").delete().eq("job_id", jobId);
    await client.from("ingest_jobs").delete().eq("id", jobId);
    console.log(`\n  정리 완료 (job ${jobId} · 산출물 삭제)`);
  }
}
Deno.exit(exitCode);
