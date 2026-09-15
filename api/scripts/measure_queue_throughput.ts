/**
 * 큐 처리량 실측 — 기존 문서 N 건을 재인제스트해 시간당 완료 건수를 잰다.
 *
 *   deno run --allow-net --allow-env api/scripts/measure_queue_throughput.ts <session_access_token> [N=5]
 *
 * 왜 재인제스트인가: 새 파일을 올리면 Vision 비용이 실제로 나간다. 재인제스트는
 * `vision_page_cache` 를 타서 캐시 미스 페이지에만 과금된다(2026-09-15 기준 캐시 206행).
 * 임베딩(DeepInfra)은 다시 나가지만 문서당 1원 미만이다.
 *
 * 결과(시작·종료·건당 소요·시간당 환산)를 work-log 에 옮겨 적는다. 이 숫자로 PC 에이전트의
 * 백로그 동시성 기본값을 정한다(스펙 §8 5단계).
 *
 * 응답 모양(2026-09-15 실측): `GET /documents` → `{items:[…]}`,
 * `GET /documents/batch-status` → `{items:[{doc_id, job:{status,…}}]}`.
 */
const [session, nRaw] = Deno.args;
if (!session) {
  console.error("usage: measure_queue_throughput.ts <session_access_token> [N]");
  Deno.exit(2);
}
const N = Number(nRaw ?? "5");
const API = Deno.env.get("JETRAG_API_BASE") ?? "https://jetrag-api.woong-s.com";
const H = { Authorization: `Bearer ${session}`, "User-Agent": "JetRag-Measure/0.1" };

const listRes = await fetch(`${API}/documents?limit=${N}`, { headers: H });
if (!listRes.ok) {
  console.error("문서 목록 실패:", listRes.status, await listRes.text());
  Deno.exit(1);
}
const docs = (await listRes.json()).items as { id: string; title: string; doc_type: string; size_bytes: number }[];
const ids = docs.map((d) => d.id);
console.log(`대상 ${ids.length}건:`);
for (const d of docs) console.log(`  ${d.id.slice(0, 8)} ${d.doc_type.padEnd(5)} ${(d.size_bytes / 1e6).toFixed(1).padStart(5)}MB ${d.title.slice(0, 30)}`);

const t0 = Date.now();
for (const id of ids) {
  const r = await fetch(`${API}/documents/${id}/reingest`, {
    method: "POST",
    headers: { ...H, "Content-Length": "0" },
  });
  console.log(`  reingest ${id.slice(0, 8)} → ${r.status}${r.ok ? "" : " " + (await r.text()).slice(0, 120)}`);
}

const done = new Map<string, number>();
const stageSeen = new Map<string, string>();
while (done.size < ids.length) {
  await new Promise((r) => setTimeout(r, 10_000));
  const st = await fetch(`${API}/documents/batch-status?ids=${ids.join(",")}`, { headers: H });
  if (!st.ok) {
    console.warn("batch-status 실패:", st.status);
    continue;
  }
  const body = await st.json() as { items: { doc_id: string; job: { status: string; current_stage: string | null } | null }[] };
  for (const it of body.items) {
    const status = it.job?.status;
    const stage = it.job?.current_stage ?? "-";
    if (stageSeen.get(it.doc_id) !== stage) {
      stageSeen.set(it.doc_id, stage);
      console.log(`  ${it.doc_id.slice(0, 8)} ${status} ${stage} +${Math.round((Date.now() - t0) / 1000)}s`);
    }
    if ((status === "completed" || status === "failed" || status === "deferred_quota") && !done.has(it.doc_id)) {
      done.set(it.doc_id, Date.now());
    }
  }
  if (Date.now() - t0 > 60 * 60_000) {
    console.error("60분 초과 — 중단");
    break;
  }
}
const totalS = (Math.max(...done.values()) - t0) / 1000;
const totalMB = docs.reduce((a, d) => a + d.size_bytes, 0) / 1e6;
console.log(
  `\n${done.size}/${ids.length}건 · ${totalMB.toFixed(1)}MB / ${totalS.toFixed(0)}s → 시간당 ${
    (done.size / totalS * 3600).toFixed(0)
  }건, 건당 ${(totalS / done.size).toFixed(0)}s`,
);
