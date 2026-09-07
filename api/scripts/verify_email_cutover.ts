/**
 * `/ingest/email` 컷오버 전후 점검 — **순서를 틀리면 라이브 이메일 채널이 죽는다.**
 *
 * Cloudflare Email Worker 는 `https://jetrag-api.woong-s.com/ingest/email` 로 쏘고,
 * 그 호스트가 프록시다. 프록시 규칙을 먼저 배포했는데 Edge 에 secret 이 없으면
 * **모든 수신 메일이 503 으로 버려진다.** 그래서 순서가 이렇다:
 *
 *   1. Supabase Edge 에 `JETRAG_EMAIL_WEBHOOK_SECRET` 설정 (Worker 와 같은 값)
 *   2. 이 스크립트로 Edge 가 401 을 내는지 확인 (503 이면 아직 1번이 안 된 것)
 *   3. 프록시 배포
 *   4. 이 스크립트로 프록시가 Edge 로 가는지 확인
 *   5. 실제 메일 1 통
 *
 * ## secret 값을 몰라도 판정할 수 있다
 * **틀린** secret 을 보낸다. 응답이
 * - `503` → Edge 에 secret 이 **없다**
 * - `401` → 있다
 * 둘을 가르는 데 올바른 값이 필요 없다 — 값을 이 스크립트도, 로그도 보지 않는다.
 *
 * ## 어느 백엔드가 받았는지는 헤더로 가른다
 * 본문이 똑같아서(`{"detail":"webhook secret 불일치"}`) 본문으로는 못 가른다.
 * | 헤더 | 백엔드 |
 * |---|---|
 * | `x-railway-request-id` | Railway (아직 컷오버 전) |
 * | `x-served-by: supabase-edge-runtime` | Edge (컷오버 완료) |
 *
 * 사용:
 *   deno run --allow-net api/scripts/verify_email_cutover.ts
 */

const PROXY = "https://jetrag-api.woong-s.com";
const EDGE = "https://mpmtydudhojpukuuadrd.supabase.co/functions/v1/api-documents";

/**
 * 일부러 틀린 값. 올바른 secret 은 이 스크립트가 알 필요가 없다.
 * **ASCII 여야 한다** — HTTP 헤더 값은 ByteString 이라 한글을 넣으면 `fetch` 가
 * 요청을 만들기도 전에 TypeError 를 낸다(실측으로 걸렸다).
 */
const WRONG_SECRET = "deliberately-wrong-value";

type Backend = "railway" | "edge" | "unknown";

function backendOf(h: Headers): Backend {
  if (h.get("x-served-by") === "supabase-edge-runtime") return "edge";
  if (h.has("x-railway-request-id") || h.has("x-railway-edge")) return "railway";
  return "unknown";
}

async function probe(base: string, method: string, path: string, secret?: string) {
  const res = await fetch(`${base}${path}`, {
    method,
    headers: {
      "content-type": "application/json",
      ...(secret === undefined ? {} : { "x-jetrag-webhook-secret": secret }),
    },
    body: method === "POST"
      ? JSON.stringify({ to: "u-abcd1234@in.x", from: "a@b.com" })
      : undefined,
  });
  const text = await res.text();
  return { status: res.status, backend: backendOf(res.headers), text: text.slice(0, 200) };
}

let exitCode = 0;
const say = (ok: boolean, msg: string) => {
  if (!ok) exitCode = 1;
  console.log(`  ${ok ? "OK  " : "**X**"} ${msg}`);
};

console.log("\n── 1. Edge 함수 직접 ──");
const e = await probe(EDGE, "POST", "/ingest/email", WRONG_SECRET);
console.log(`  POST(틀린 secret) → ${e.status} [${e.backend}] ${e.text}`);
const secretSet = e.status === 401;
if (e.status === 503) {
  console.log("  **Edge 에 JETRAG_EMAIL_WEBHOOK_SECRET 이 없다.**");
  console.log("  → supabase secrets set JETRAG_EMAIL_WEBHOOK_SECRET=<Worker 와 같은 값>");
  console.log("  → 이걸 하기 전에 프록시를 배포하면 수신 메일이 전부 버려진다.");
  exitCode = 1;
} else {
  say(e.status === 401, `secret 설정됨 (401 기대, 받은 값 ${e.status})`);
}
say(e.backend === "edge", `Edge 가 받았다 (backend=${e.backend})`);

const eGet = await probe(EDGE, "GET", "/ingest/email");
say(eGet.status === 405, `GET → 405 (받은 값 ${eGet.status})`);

console.log("\n── 2. 프록시 ──");
const p = await probe(PROXY, "POST", "/ingest/email", WRONG_SECRET);
console.log(`  POST(틀린 secret) → ${p.status} [${p.backend}] ${p.text}`);
say(p.status === 401, `틀린 secret 은 401 (받은 값 ${p.status})`);

const pGet = await probe(PROXY, "GET", "/ingest/email");
say(pGet.status === 405, `GET → 405 (받은 값 ${pGet.status})`);
const pDeep = await probe(PROXY, "POST", "/ingest/email/extra", WRONG_SECRET);
say(pDeep.status === 404, `/ingest/email/extra → 404 (받은 값 ${pDeep.status})`);

console.log("\n── 판정 ──");
if (p.backend === "railway") {
  console.log("  컷오버 **전** — 프록시가 아직 Railway 로 보낸다.");
  console.log(
    secretSet
      ? "  Edge 준비 완료. 이제 프록시를 배포해도 된다:\n" +
        "    cd workers/api-proxy && npx wrangler deploy"
      : "  **아직 배포하면 안 된다** — 위의 secret 설정이 먼저다.",
  );
} else if (p.backend === "edge") {
  console.log("  컷오버 **완료** — 프록시가 Edge 로 보낸다.");
  console.log("  남은 확인: 실제 메일 1 통을 u-{token}@in.woong-s.com 으로 보내고");
  console.log("  documents 에 source_channel='email' 행이 생기는지 본다.");
} else {
  console.log(`  **어느 백엔드인지 못 가렸다** (backend=${p.backend}). 헤더를 다시 본다.`);
  exitCode = 1;
}

Deno.exit(exitCode);
