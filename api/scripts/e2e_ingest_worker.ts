/**
 * `extract` → `chunk` 워커를 **실제 Supabase 에 붙여** 끝까지 돌린다.
 *
 * ## 가짜 클라이언트로는 증명 못 하는 것
 * 단위 테스트는 계약(호출 순서·멱등 옵션·거절 조건)만 고정한다. **PostgREST 쿼리가
 * 실제로 도는지**(`.lt()` · `.order()` · rpc 인자 이름), 큐 왕복이 맞물리는지는
 * 붙여 봐야 안다.
 *
 * ## 운영 DB 를 건드린다 — 격리 규칙
 * `load` 가 붙으면서 **`chunks` 에 쓰기가 생겼다.** upsert 키가 `doc_id,chunk_idx` 라
 * 원본 문서로 돌리면 운영 청크를 덮어쓴다. 그래서 대상 문서를 그대로 쓰지 않고,
 * **샌드박스 user_id 로 복제 행을 하나 만들어** 거기에 넣는다:
 *
 * - 원본 `documents` 행은 **읽기만** 한다. Storage 파일도 읽기만(경로를 그대로 재사용).
 * - 쓰는 것은 이 스크립트가 만든 복제 `documents` 1 행 + 그 `chunks` + `ingest_jobs`
 *   1 행 + 그 job_id 의 `ingest_artifacts` 뿐.
 * - 끝나면 복제 문서를 지운다 — `chunks` 는 `ON DELETE CASCADE` 로 함께 사라진다.
 * - 큐에 남은 이 잡의 메시지도 비운다.
 *
 * 샌드박스 user_id 는 `ingest_sandbox.py` 와 같은 네임스페이스에서 uuid5 로 만든다 —
 * 매번 같고 운영 UUID 와 겹칠 수 없다.
 *
 * 사용:
 *   deno run --config supabase/functions/deno.json --allow-all \
 *     api/scripts/e2e_ingest_worker.ts <doc_id> [--pages 10] [--keep]
 */

import { createClient } from "@supabase/supabase-js";

import { drainOnce } from "../../supabase/functions/_shared/ingest/worker.ts";
import { makeExtractHandler } from "../../supabase/functions/_shared/ingest/handlers/extract.ts";
import { makeChunkHandler } from "../../supabase/functions/_shared/ingest/handlers/chunk.ts";
import { makeLoadHandler } from "../../supabase/functions/_shared/ingest/handlers/load.ts";

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
/**
 * `--edge`: 드레인을 **배포된 Edge 함수**에 HTTP 로 시킨다.
 *
 * 로컬 Deno 통과는 Edge 통과의 근거가 아니다(Phase 0 교훈 — `@ohah/hwpjs` 가 로컬은
 * 되고 Edge 에서 죽었다). mupdf WASM 24MB 가 Edge 런타임에서 뜨는지는 여기서만 안다.
 * 준비·검증·정리는 그대로 로컬에서 하고 **드레인만** 바꾼다.
 */
const useEdge = Deno.args.includes("--edge");

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

// --- 원본 문서 확인 (읽기만) ---
const { data: docs, error: dErr } = await client
  .from("documents")
  .select("id, doc_type, storage_path, title, sha256, size_bytes, content_type, source_channel")
  .eq("id", docId).limit(1);
if (dErr) throw new Error(`documents 조회 실패: ${dErr.message}`);
const src = (docs ?? [])[0];
if (!src) throw new Error(`문서를 못 찾았다: ${docId}`);
console.log(`  원본  ${src.doc_type}  ${String(src.title ?? "").slice(0, 40)}`);

/**
 * 샌드박스 user_id — `ingest_sandbox.py` 와 같은 네임스페이스·이름 규칙.
 * (uuid5(5a4d0000-0000-4000-8000-000000000000, "ingest-sandbox/e2e"))
 */
async function uuid5(ns: string, name: string): Promise<string> {
  const hex = ns.replace(/-/g, "");
  const nsBytes = new Uint8Array(16);
  for (let i = 0; i < 16; i++) nsBytes[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  const nameBytes = new TextEncoder().encode(name);
  const buf = new Uint8Array(nsBytes.length + nameBytes.length);
  buf.set(nsBytes);
  buf.set(nameBytes, nsBytes.length);
  const h = new Uint8Array(await crypto.subtle.digest("SHA-1", buf));
  h[6] = (h[6] & 0x0f) | 0x50; // version 5
  h[8] = (h[8] & 0x3f) | 0x80; // variant
  const s = [...h.slice(0, 16)].map((b) => b.toString(16).padStart(2, "0")).join("");
  return `${s.slice(0, 8)}-${s.slice(8, 12)}-${s.slice(12, 16)}-${s.slice(16, 20)}-${s.slice(20, 32)}`;
}
const sandboxUser = await uuid5("5a4d0000-0000-4000-8000-000000000000", "ingest-sandbox/e2e");

// --- 샌드박스 복제 문서 (chunks 를 여기에 넣는다 — 운영 청크 무손상) ---
// `UNIQUE(user_id, sha256)` 이 있으므로 sha256 을 살짝 바꿔 매번 새 행이 되게 한다.
const { data: cloneRow, error: cErr } = await client
  .from("documents").insert({
    user_id: sandboxUser,
    title: `[e2e] ${String(src.title ?? "").slice(0, 60)}`,
    doc_type: src.doc_type,
    source_channel: "api",
    storage_path: src.storage_path, // 원본 파일을 읽기만 한다
    sha256: `e2e-${crypto.randomUUID()}`,
    size_bytes: src.size_bytes ?? 0,
    content_type: src.content_type ?? "application/octet-stream",
  }).select("id").single();
if (cErr) throw new Error(`샌드박스 문서 생성 실패: ${cErr.message}`);
const cloneId = cloneRow.id as string;
console.log(`  복제  ${cloneId}  (user ${sandboxUser.slice(0, 8)}…)`);

// --- 잡 생성 ---
const { data: jobRow, error: jErr } = await client
  .from("ingest_jobs").insert({ doc_id: cloneId, status: "queued" }).select("id").single();
if (jErr) throw new Error(`ingest_jobs 생성 실패: ${jErr.message}`);
const jobId = jobRow.id as string;
console.log(`  job   ${jobId}`);

let exitCode = 0;
try {
  const { error: sErr } = await client.rpc("ingest_queue_send", {
    payload: { job_id: jobId, doc_id: cloneId, stage: "extract" },
  });
  if (sErr) throw new Error(`enqueue 실패: ${sErr.message}`);

  const handlers = {
    extract: makeExtractHandler({ client, bucket, pagesPerTask }),
    chunk: makeChunkHandler({ client }),
    load: makeLoadHandler({ client }),
  };

  /** Edge `/drain` 1 회. 반환 모양은 `drainOnce` 와 같다. */
  async function drainViaEdge() {
    const res = await fetch(`${url}/functions/v1/api-ingest-worker/drain`, {
      method: "POST",
      headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
    });
    const body = await res.text();
    if (!res.ok) throw new Error(`Edge drain ${res.status}: ${body.slice(0, 400)}`);
    return JSON.parse(body) as Awaited<ReturnType<typeof drainOnce>>;
  }
  const drain = useEdge
    ? drainViaEdge
    : () => drainOnce({ client, handlers, batch: 1, vtSeconds: 120 });
  console.log(`  드레인 경로: ${useEdge ? "**배포된 Edge 함수**" : "로컬 Deno"}`);

  const t0 = performance.now();
  let rounds = 0;
  let lastStage = "";
  // 넉넉한 상한 — 무한 루프 방지용이지 예상 횟수가 아니다.
  for (; rounds < 400; rounds++) {
    const r = await drain();
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
    const cur = stages.includes("chunk") ? "chunk" : (stages[stages.length - 1] ?? "");
    if (cur !== lastStage) {
      console.log(`  [${rounds}] ${cur} …`);
      lastStage = cur;
    }
    // **큐가 빌 때까지 돈다.** chunk 도달에서 멈추면 load 가 한 번도 안 돈다.
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

    // --- load 결과: chunks 테이블에 실제로 들어갔는가 ---
    const { count: loaded } = await client
      .from("chunks").select("id", { count: "exact", head: true }).eq("doc_id", cloneId);
    console.log(`  chunks 적재 ${loaded}/${p.chunk_count}  (chunk part ${ch.length}개)`);
    if (loaded !== p.chunk_count) {
      console.log("  **적재 수가 청크 수와 다르다**");
      exitCode = 1;
    }
    // 표본 한 행을 실제로 열어 본다 — 개수만 맞고 내용이 비면 소용없다.
    const { data: sample } = await client
      .from("chunks").select("chunk_idx, text, page, section_title, char_range, metadata")
      .eq("doc_id", cloneId).order("chunk_idx", { ascending: true }).limit(1);
    const row0 = (sample ?? [])[0];
    if (row0) {
      console.log(`  첫 행: idx=${row0.chunk_idx} page=${row0.page} ` +
        `char_range=${JSON.stringify(row0.char_range)} ` +
        `title=${JSON.stringify(String(row0.section_title ?? "").slice(0, 24))}`);
      if (row0.text !== p.records[0]?.text) {
        console.log("  **첫 행 텍스트가 산출물과 다르다**");
        exitCode = 1;
      }
    } else {
      console.log("  **chunks 에 행이 없다**");
      exitCode = 1;
    }
  }
} finally {
  if (keep) {
    console.log(`\n  --keep: 복제 문서 ${cloneId} · job ${jobId} · 산출물을 남긴다`);
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
    // 복제 문서를 지우면 `chunks` 는 ON DELETE CASCADE 로 함께 사라진다.
    // **샌드박스 user_id 인지 확인하고 지운다** — 원본을 지우는 사고를 막는 마지막 관문.
    const { data: chk } = await client
      .from("documents").select("id, user_id").eq("id", cloneId).limit(1);
    const owner = (chk ?? [])[0]?.user_id;
    if (owner && owner !== sandboxUser) {
      console.log(`\n  **삭제 중단** — ${cloneId} 의 user_id 가 샌드박스가 아니다`);
    } else {
      await client.from("documents").delete().eq("id", cloneId).eq("user_id", sandboxUser);
      console.log(`\n  정리 완료 (복제 문서 ${cloneId} · job · 산출물 삭제)`);
    }
  }
}
Deno.exit(exitCode);
