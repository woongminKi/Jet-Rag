/**
 * 기기 토큰 + precheck 라이브 E2E.
 *
 *   deno run --allow-net --allow-env --allow-read \
 *     api/scripts/e2e_device_precheck.ts <session_access_token> <중복파일경로>
 *
 * 1) 세션으로 기기 발급 → 2) 기기 토큰으로 precheck(existing) → 3) 기기 토큰으로 업로드(duplicated)
 * 4) 기기 토큰으로 GET /documents → 403 → 5) 폐기 → 6) 기기 토큰 업로드 → 401
 *
 * ## 부작용 0
 * **이미 올라가 있는 파일**을 인자로 준다. 업로드는 dedup 에 걸려 `duplicated` 로 끝나므로
 * 새 문서·잡·Storage 객체가 생기지 않는다. 새 파일을 주면 진짜 인제스트가 돈다.
 */
const [session, filePath] = Deno.args;
const API = Deno.env.get("JETRAG_API_BASE") ?? "https://jetrag-api.woong-s.com";
const UA = "JetRag-Agent/0.0-e2e";
const j = (r: Response) => r.json();

const dev = await fetch(`${API}/me/devices`, {
  method: "POST",
  headers: {
    Authorization: `Bearer ${session}`,
    "Content-Type": "application/json",
    "User-Agent": UA,
  },
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

const rv = await fetch(`${API}/me/devices/${dev.id}`, {
  method: "DELETE",
  headers: { Authorization: `Bearer ${session}`, "User-Agent": UA },
});
console.log("5 폐기:", rv.status);
const again = await fetch(`${API}/documents/precheck`, {
  method: "POST",
  headers: { ...dh, "Content-Type": "application/json" },
  body: JSON.stringify({ hashes: [sha] }),
});
console.log("6 폐기 후 (401 기대):", again.status);
