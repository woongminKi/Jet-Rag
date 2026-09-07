/**
 * 프록시 컷오버 전후 점검 — **순서를 틀리면 라이브 기능이 죽는다.**
 *
 * 지금 저장소에는 **코드는 이식됐지만 프록시를 아직 안 돌린** 경로가 둘 있다.
 * 워커를 그냥 배포하면 둘 다 503 이 된다 — 이 스크립트가 그걸 먼저 막는다.
 *
 * | 경로 | 필요한 Edge secret |
 * |---|---|
 * | `POST /ingest/email` | `JETRAG_EMAIL_WEBHOOK_SECRET` |
 * | `POST /billing/run` | `JETRAG_BILLING_CRON_SECRET` |
 *
 * `JETRAG_KAKAOPAY_SECRET_KEY` 는 **컷오버 조건이 아니다** — 심사 대기라 Railway 에도
 * 없고, 없으면 양쪽 다 503 이라 동작이 같다. 발급되면 그때 넣으면 결제가 켜진다.
 *
 * ── 아래는 이메일 경로 설명 ──
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

// ─────────────────────────────────────────────────────────────
// 결제 — 같은 방식. **틀린 secret 을 보내** 503(미설정)과 401(설정됨)을 가른다.
// ─────────────────────────────────────────────────────────────
const EDGE_PAY = "https://mpmtydudhojpukuuadrd.supabase.co/functions/v1/api-payments";

console.log("\n── 3. 결제 Edge 함수 ──");
const payReady = await fetch(`${EDGE_PAY}/payments/subscribe/ready`, { method: "POST" });
const payReadyText = (await payReady.text()).slice(0, 160);
console.log(`  POST /payments/subscribe/ready (비인증) → ${payReady.status} ${payReadyText}`);
// 비인증이면 결제 키 유무와 무관하게 401 이어야 한다(라우팅 → 인증 → 게이트 순서).
say(payReady.status === 401, `비인증은 401 (받은 값 ${payReady.status})`);

const runRes = await fetch(`${EDGE_PAY}/billing/run`, {
  method: "POST",
  headers: { "x-billing-cron-secret": WRONG_SECRET },
});
const runText = (await runRes.text()).slice(0, 200);
console.log(`  POST /billing/run (틀린 secret) → ${runRes.status} ${runText}`);
const cronSecretSet = runRes.status === 401;
// **카카오페이 키는 컷오버 조건이 아니다.**
// `JETRAG_KAKAOPAY_SECRET_KEY` 는 심사 중이라 아직 발급된 적이 없다 — Railway 에도 없다
// (memory `jetrag_w5_6_kakaopay_ship`). 없으면 양쪽 다 503 이므로 **동작이 같다.**
// 컷오버가 요구하는 건 "결제가 켜져 있을 것" 이 아니라 "Edge 가 Railway 와 같을 것" 이다.
// 처음엔 이걸 차단 요인으로 넣어 뒀는데, 그러면 심사가 끝날 때까지 이메일까지 못 넘긴다.
const payKeysPending = runRes.status === 503 && !runText.includes("billing cron");
if (runRes.status === 503 && runText.includes("billing cron")) {
  console.log("  **Edge 에 JETRAG_BILLING_CRON_SECRET 이 없다.**");
} else if (payKeysPending) {
  console.log("  결제 키 미설정 → 503. **Railway 도 같은 상태라 컷오버를 막지 않는다**");
  console.log("  (JETRAG_KAKAOPAY_SECRET_KEY 는 카카오페이 심사 대기 — 발급 후 넣으면 활성)");
}

const payGetRes = await fetch(`${EDGE_PAY}/billing/run`, { method: "GET" });
await payGetRes.body?.cancel();
say(payGetRes.status === 405, `GET /billing/run → 405 (받은 값 ${payGetRes.status})`);

console.log("\n── 4. 프록시가 결제를 어디로 보내나 ──");
const pProxy = await fetch(`${PROXY}/billing/run`, {
  method: "POST",
  headers: { "x-billing-cron-secret": WRONG_SECRET },
});
const pProxyText = (await pProxy.text()).slice(0, 120);
const payBackend = backendOf(pProxy.headers);
console.log(`  POST /billing/run → ${pProxy.status} [${payBackend}] ${pProxyText}`);

console.log("\n── 배포해도 되는가 ──");
const emailReady = secretSet;
// 결제 준비 = **cron secret 만**. 카카오페이 키는 위 이유로 조건이 아니다.
const payReadyOk = cronSecretSet;
console.log(`  이메일  Edge 준비 ${emailReady ? "완료" : "**미완료**"} · 프록시 ${p.backend}`);
console.log(`  결제    Edge 준비 ${payReadyOk ? "완료" : "**미완료**"} · 프록시 ${payBackend}`);
if (emailReady && payReadyOk) {
  console.log("\n  두 경로 다 준비됐다. 워커를 배포해도 된다:");
  console.log("    cd workers/api-proxy && npx wrangler deploy");
} else {
  console.log("\n  **워커를 배포하면 준비 안 된 쪽이 죽는다.** 위의 secret 부터 넣어라.");
  exitCode = 1;
}

Deno.exit(exitCode);
