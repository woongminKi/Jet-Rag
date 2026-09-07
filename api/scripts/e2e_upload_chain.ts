/**
 * 업로드 → 인제스트 사슬 **전체** E2E.
 *
 * `handleUpload` 을 실제 Supabase 에 붙여 부르고, 그 뒤는 **pg_cron 이 알아서** 돌게
 * 둔다. 즉 사용자가 파일을 올렸을 때 실제로 일어나는 일을 그대로 재현한다.
 *
 * ## HTTP 층은 여기서 안 본다
 * 완전한 HTTP E2E 는 로그인 토큰이 필요한데 사용자 자격증명을 쓸 수 없다. HTTP 층
 * (인증 401 · 메서드 405 · 경로 404)은 curl 로 따로 확인한다. 여기서는 그 아래
 * **업로드 본체부터 dense_vec 까지**를 본다.
 *
 * ## 격리
 * 샌드박스 user_id(`ingest_sandbox.py` 와 같은 uuid5 네임스페이스)로만 쓴다.
 * 끝나면 documents(→ chunks CASCADE) · job · artifact · **Storage 객체**까지 지운다.
 *
 * 사용:
 *   deno run --config supabase/functions/deno.json --allow-all \
 *     api/scripts/e2e_upload_chain.ts <파일경로> [--keep]
 */

import { createClient } from "@supabase/supabase-js";

import { handleUpload } from "../../supabase/functions/_shared/documents/upload.ts";

const filePath = Deno.args.find((a) => !a.startsWith("--"));
if (!filePath) {
  console.error("사용: e2e_upload_chain.ts <파일경로> [--keep]");
  Deno.exit(2);
}
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
if (!url || !key) throw new Error("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 를 못 찾았다");

const client = createClient(url, key, { auth: { persistSession: false } });

/** `ingest_sandbox.py` 와 같은 네임스페이스·이름 규칙. */
async function uuid5(ns: string, name: string): Promise<string> {
  const hex = ns.replace(/-/g, "");
  const nsBytes = new Uint8Array(16);
  for (let i = 0; i < 16; i++) nsBytes[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  const nameBytes = new TextEncoder().encode(name);
  const buf = new Uint8Array(nsBytes.length + nameBytes.length);
  buf.set(nsBytes);
  buf.set(nameBytes, nsBytes.length);
  const h = new Uint8Array(await crypto.subtle.digest("SHA-1", buf));
  h[6] = (h[6] & 0x0f) | 0x50;
  h[8] = (h[8] & 0x3f) | 0x80;
  const s = [...h].map((b) => b.toString(16).padStart(2, "0")).join("");
  return `${s.slice(0, 8)}-${s.slice(8, 12)}-${s.slice(12, 16)}-${s.slice(16, 20)}-${
    s.slice(20, 32)
  }`;
}
const sandboxUser = await uuid5("5a4d0000-0000-4000-8000-000000000000", "ingest-sandbox/e2e");

const bytes = await Deno.readFile(filePath);
const name = filePath.split("/").pop()!;
console.log(`  파일  ${name}  ${(bytes.length / 1e6).toFixed(2)}MB`);
console.log(`  user  ${sandboxUser.slice(0, 8)}… (샌드박스)`);

const form = new FormData();
form.append("file", new File([bytes.slice()], name));
form.append("source_channel", "api");

const t0 = performance.now();
const res = await handleUpload(form, { client, bucket, userId: sandboxUser });
const uploadMs = performance.now() - t0;
console.log(`  업로드 응답  ${res.status}  ${JSON.stringify(res.body)}  ${uploadMs.toFixed(0)}ms`);

const docId = res.body.doc_id as string;
const jobId = res.body.job_id as string | null;
let exitCode = res.status === 202 && jobId ? 0 : 1;
if (exitCode) console.log("  **업로드가 202/job 을 못 냈다**");

let storagePath: string | null = null;
try {
  const { data: doc } = await client
    .from("documents").select("storage_path, doc_type, title, size_bytes")
    .eq("id", docId).single();
  storagePath = doc?.storage_path ?? null;
  console.log(`  documents   ${doc?.doc_type}  ${storagePath}`);
  if (storagePath?.includes("pending")) {
    console.log("  **storage_path 가 pending 이다 — Edge 는 final path 를 써야 한다**");
    exitCode = 1;
  }

  // Storage 에 실제로 올라갔는지 — 경로만 맞고 파일이 없으면 소용없다.
  if (storagePath) {
    const { data: blob, error } = await client.storage.from(bucket).download(storagePath);
    if (error || !blob) {
      console.log(`  **Storage 에 파일이 없다: ${error?.message}**`);
      exitCode = 1;
    } else {
      const got = new Uint8Array(await blob.arrayBuffer());
      const same = got.length === bytes.length;
      console.log(`  Storage     ${got.length}B  ${same ? "크기 일치" : "**크기 불일치**"}`);
      if (!same) exitCode = 1;
    }
  }

  // ---- 여기서부터는 pg_cron 이 한다. 기다리기만 한다 ----
  console.log("  pg_cron 대기 …");
  let lastStage = "";
  let gracefulSkip = false;
  let embedded = 0;
  let chunkCount = 0;
  const deadline = performance.now() + 300_000;
  while (performance.now() < deadline) {
    await new Promise((r) => setTimeout(r, 3000));
    const { count: n } = await client
      .from("chunks").select("id", { count: "exact", head: true }).eq("doc_id", docId);
    const { count: e } = await client
      .from("chunks").select("id", { count: "exact", head: true })
      .eq("doc_id", docId).not("dense_vec", "is", null);
    const { data: job } = await client
      .from("ingest_jobs").select("status, current_stage, error_msg").eq("id", jobId).single();
    chunkCount = n ?? 0;
    embedded = e ?? 0;
    const stage = `${job?.current_stage ?? "-"}/${job?.status ?? "-"}`;
    if (stage !== lastStage) {
      console.log(`    ${stage}  청크 ${chunkCount}  임베딩 ${embedded}`);
      lastStage = stage;
    }
    if (job?.error_msg) {
      console.log(`  **잡 오류**: ${job.error_msg}`);
      exitCode = 1;
      break;
    }
    // 원본에도 파서가 없는 포맷(`txt`/`md`)은 여기서 **정상 종료**한다 — 청크가 0 인
    // 것이 맞다. 이 분기가 없으면 하네스가 성공을 실패로 읽는다(실제로 그랬다).
    if (job?.status === "completed" && (job?.current_stage ?? "") === "done") {
      const { data: fr } = await client
        .from("documents").select("flags").eq("id", docId).single();
      gracefulSkip = Boolean((fr?.flags as Record<string, unknown> | null)?.["extract_skipped"]);
      if (gracefulSkip) break;
    }
    const { data: depth } = await client.rpc("ingest_queue_depth");
    const queued = Number((depth ?? [{}])[0]?.queue_length ?? 0);
    if (queued === 0 && chunkCount > 0 && embedded === chunkCount) break;
  }

  if (gracefulSkip) {
    const { data: fr } = await client
      .from("documents").select("flags").eq("id", docId).single();
    const reason = (fr?.flags as Record<string, unknown>)?.["extract_skipped_reason"];
    console.log(`  결과  graceful skip — 원본에도 파서가 없는 포맷. ${reason}`);
    console.log("  (청크 0 · 후속 스테이지 없음이 정상이다. 잡은 completed.)");
  } else {
    console.log(`  결과  청크 ${chunkCount}  dense_vec ${embedded}/${chunkCount}`);
    if (chunkCount === 0 || embedded !== chunkCount) {
      console.log("  **사슬이 끝까지 안 갔다**");
      exitCode = 1;
    }
  }

  // ---- tag_summarize · doc_embed · dedup ----
  const { data: tailRow } = await client
    .from("documents")
    .select("tags, summary, implications, doc_embedding, flags")
    .eq("id", docId).single();
  const tr = tailRow as {
    tags?: unknown[]; summary?: string | null; implications?: string | null;
    doc_embedding?: unknown; flags?: Record<string, unknown>;
  } | null;
  const vecLen = Array.isArray(tr?.doc_embedding)
    ? tr!.doc_embedding.length
    : (typeof tr?.doc_embedding === "string" ? "문자열" : 0);
  console.log(
    `  tail    태그 ${(tr?.tags ?? []).length}개  요약 ${tr?.summary ? "있음" : "없음"}  ` +
      `함의 ${tr?.implications ? "있음" : "없음"}  doc_embedding ${vecLen}차원  ` +
      `dup_tier=${tr?.flags?.["duplicate_tier"] ?? "-"}`,
  );
  if (tr?.tags?.length) console.log(`    태그: ${(tr.tags as string[]).slice(0, 8).join(", ")}`);
  if (tr?.summary) console.log(`    요약: ${String(tr.summary).replace(/\n/g, " / ").slice(0, 90)}`);
  // 스테이지 로그가 남는지 — 이게 비면 `/status?include_logs` 와 ETA 가 죽는다.
  const { data: logRows } = await client
    .from("ingest_logs").select("stage, status, duration_ms")
    .eq("job_id", jobId).order("id", { ascending: true });
  const lg = (logRows ?? []) as { stage: string; status: string; duration_ms: number }[];
  console.log(`  logs    ${lg.length}행  ${lg.map((l) => `${l.stage}:${l.status}`).join(" ")}`);
  if (lg.length === 0) {
    console.log("  **ingest_logs 가 비었다 — /status?include_logs 와 ETA 가 죽는다**");
    exitCode = 1;
  }

  // ---- chunk_filter · content_gate ----
  const { data: allChunks } = await client
    .from("chunks").select("flags, metadata").eq("doc_id", docId);
  const cr = (allChunks ?? []) as {
    flags?: Record<string, unknown>;
    metadata?: Record<string, unknown>;
  }[];
  const reasons: Record<string, number> = {};
  let withPii = 0;
  let withWm = 0;
  for (const c of cr) {
    const r = c.flags?.["filtered_reason"];
    if (typeof r === "string") reasons[r] = (reasons[r] ?? 0) + 1;
    if (c.metadata?.["pii_ranges"]) withPii++;
    if (c.metadata?.["watermark_hits"]) withWm++;
  }
  const { data: docFlags } = await client
    .from("documents").select("flags").eq("id", docId).single();
  const df = (docFlags as { flags?: Record<string, unknown> })?.flags ?? {};
  console.log(
    `  gate    filtered=${JSON.stringify(reasons)}  pii청크 ${withPii}  ` +
      `워터마크청크 ${withWm}  has_pii=${df["has_pii"]} has_watermark=${df["has_watermark"]} ` +
      `third_party=${df["third_party"]}`,
  );
  // content_gate 는 **항상** 이 세 키를 남긴다 — 없으면 단계가 안 돈 것이다.
  // (graceful skip 은 chunk 자체를 안 만드므로 이 단계가 안 도는 게 맞다.)
  for (const k of gracefulSkip ? [] : ["has_pii", "has_watermark", "third_party"]) {
    if (!(k in df)) {
      console.log(`  **documents.flags 에 ${k} 가 없다 — content_gate 가 안 돌았다**`);
      exitCode = 1;
    }
  }

  // ---- 스캔 PDF 경로 ----
  const { data: sArt } = await client
    .from("ingest_artifacts").select("seq, payload")
    .eq("job_id", jobId).eq("stage", "scan").order("seq", { ascending: true });
  const sRows = (sArt ?? []) as { seq: number; payload: Record<string, unknown> }[];
  if (sRows.length > 0) {
    const { data: docRow } = await client
      .from("documents").select("flags").eq("id", docId).single();
    const flags = (docRow as { flags?: Record<string, unknown> })?.flags ?? {};
    const called = sRows.reduce((n, r) => n + Number(r.payload?.["called"] ?? 0), 0);
    const warn = sRows.flatMap((r) => (r.payload?.["warnings"] as string[]) ?? []);
    console.log(
      `  scan    창 ${sRows.length}개  호출 ${called}  ` +
        `flags.scan=${flags["scan"]}  total=${sRows[0].payload?.["total_pages"]}`,
    );
    for (const w of warn) console.log(`    경고: ${w}`);
    const { data: sc } = await client
      .from("chunks").select("section_title, text")
      .eq("doc_id", docId).like("section_title", "p.%").limit(3);
    for (const c of (sc ?? []) as { section_title: string; text: string }[]) {
      console.log(`    ${c.section_title} → ${c.text.slice(0, 60).replace(/\n/g, " ")}`);
    }
    if (flags["scan"] !== true) {
      console.log("  **flags.scan 이 안 붙었다**");
      exitCode = 1;
    }
    if ((sc ?? []).length === 0) {
      console.log("  **scan 산출물은 있는데 청크가 없다**");
      exitCode = 1;
    }
  }

  // ---- vision 이 실제로 돌았는지 ----
  // 청크 수만 보면 vision 이 통째로 빠져도 통과한다. `(vision) p.N` 제목이 붙은
  // 청크가 곧 Gemini 를 다녀온 증거다.
  const { data: vArt } = await client
    .from("ingest_artifacts").select("seq, payload")
    .eq("job_id", jobId).eq("stage", "vision").order("seq", { ascending: true });
  const vRows = (vArt ?? []) as { seq: number; payload: Record<string, unknown> }[];
  const { data: vChunks } = await client
    .from("chunks").select("section_title, text")
    .eq("doc_id", docId).like("section_title", "(vision) p.%").limit(5);
  const vc = (vChunks ?? []) as { section_title: string; text: string }[];

  if (vRows.length === 0) {
    console.log("  vision  아티팩트 없음 — 비활성이거나 대상 아님");
  } else {
    const carry = vRows[vRows.length - 1].payload?.["carry"] as
      | { calledCount?: number; completed?: number; skippedByNeedScore?: number[] }
      | undefined;
    const warn = vRows.flatMap((r) => (r.payload?.["warnings"] as string[]) ?? []);
    console.log(
      `  vision  창 ${vRows.length}개  호출 ${carry?.calledCount ?? "?"}  ` +
        `처리 ${carry?.completed ?? "?"}  ` +
        `need_score skip ${carry?.skippedByNeedScore?.length ?? "?"}  ` +
        `청크 ${vc.length}건`,
    );
    for (const w of warn) console.log(`    경고: ${w}`);
    for (const c of vc.slice(0, 3)) {
      console.log(`    ${c.section_title} → ${c.text.slice(0, 60).replace(/\n/g, " ")}`);
    }
    if (vc.length === 0) {
      console.log("  **vision 아티팩트는 있는데 청크가 없다**");
      exitCode = 1;
    }
  }
} finally {
  if (keep) {
    console.log(`\n  --keep: doc ${docId} 유지`);
  } else {
    // 큐에 남은 이 잡의 메시지를 비운다.
    for (let i = 0; i < 60; i++) {
      const { data } = await client.rpc("ingest_queue_read", { vt_seconds: 5, qty: 1 });
      const msgs = (data ?? []) as { msg_id: number; message: { job_id?: string } }[];
      if (msgs.length === 0) break;
      for (const m of msgs) {
        if (m.message?.job_id === jobId) {
          await client.rpc("ingest_queue_delete", { message_id: m.msg_id });
        }
      }
    }
    if (jobId) await client.from("ingest_artifacts").delete().eq("job_id", jobId);
    // **샌드박스 소유인지 확인하고** 지운다 — 마지막 관문.
    const { data: chk } = await client
      .from("documents").select("user_id").eq("id", docId).single();
    if (chk && chk.user_id !== sandboxUser) {
      console.log(`\n  **삭제 중단** — ${docId} 가 샌드박스 소유가 아니다`);
    } else {
      if (storagePath) await client.storage.from(bucket).remove([storagePath]);
      await client.from("documents").delete().eq("id", docId).eq("user_id", sandboxUser);
      console.log(`\n  정리 완료 (doc ${docId} · Storage · job · 산출물)`);
    }
  }
}
Deno.exit(exitCode);
