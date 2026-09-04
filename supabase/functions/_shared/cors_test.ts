/**
 * `cors.ts` 는 `app/main.py` 의 `CORSMiddleware` 설정과 **같은 헤더**를 내야 한다.
 *
 * CORS 는 틀려도 서버 로그에 안 남는다. 브라우저가 응답을 버리고 콘솔에만 찍히므로
 * "배포했는데 프론트에서만 안 되는" 형태로 나타난다. 그래서 헤더 단위로 고정한다.
 */

import { assertEquals } from "@std/assert";
import {
  applyCorsHeaders,
  type CorsSettings,
  isAllowedOrigin,
  isPreflight,
  preflightResponse,
} from "./cors.ts";

const ORIGINS = ["http://localhost:3001", "http://localhost:3000", "https://jetrag.woong-s.com"];
const settings: CorsSettings = { corsOrigins: ORIGINS };

function req(method: string, headers: Record<string, string> = {}): Request {
  return new Request("https://api.example.com/auth/me", { method, headers });
}

/* ------------------------------------------------------------------ origin 매칭 */

Deno.test("허용 목록에 있는 origin", () => {
  for (const o of ORIGINS) assertEquals(isAllowedOrigin(o, settings), true, o);
});

Deno.test("Vercel preview 는 정규식으로 허용", () => {
  assertEquals(isAllowedOrigin("https://jetrag-abc123.vercel.app", settings), true);
  assertEquals(isAllowedOrigin("https://a.b.c.vercel.app", settings), true);
});

Deno.test("정규식은 fullmatch — 뒤에 다른 도메인을 붙여 뚫을 수 없다", () => {
  // `re.search` 였다면 아래 첫 줄이 통과한다. 원본은 fullmatch 라 막힌다.
  assertEquals(isAllowedOrigin("https://evil.vercel.app.attacker.com", settings), false);
  assertEquals(isAllowedOrigin("http://insecure.vercel.app", settings), false); // https 만
  assertEquals(isAllowedOrigin("https://vercel.app", settings), false); // 서브도메인 필요
  assertEquals(isAllowedOrigin("https://evil.com", settings), false);
});

Deno.test("경로가 낀 값은 원본과 똑같이 통과한다 (실제 위험 없음)", () => {
  // `.*` 가 `/` 도 먹어서 Python 쪽 fullmatch 도 True 다(실측). 여기서 임의로 조이면
  // Railway 는 허용하는데 Edge 만 막는 갈림이 생긴다 — 포팅에서는 원본을 따른다.
  //
  // 실제로 뚫리지 않는 이유: `Origin` 헤더는 RFC 6454 상 scheme://host[:port] 뿐이라
  // 브라우저가 경로가 든 값을 보내지 않는다. 즉 이 입력은 실제 요청에 나타나지 않는다.
  assertEquals(isAllowedOrigin("https://evil.com/x.vercel.app", settings), true);
});

Deno.test("목록 밖 origin 은 거부", () => {
  assertEquals(isAllowedOrigin("https://evil.com", settings), false);
  // 포트가 다르면 다른 origin 이다.
  assertEquals(isAllowedOrigin("http://localhost:9999", settings), false);
});

/* ------------------------------------------------------------------ preflight 판정 */

Deno.test("preflight 은 OPTIONS + Access-Control-Request-Method 둘 다 있어야 한다", () => {
  assertEquals(isPreflight(req("OPTIONS", { "Access-Control-Request-Method": "GET" })), true);
  // OPTIONS 만 오면 일반 응답 취급이다.
  assertEquals(isPreflight(req("OPTIONS")), false);
  assertEquals(isPreflight(req("GET", { "Access-Control-Request-Method": "GET" })), false);
});

Deno.test("Origin 이 없으면 preflight 응답을 만들지 않는다", () => {
  assertEquals(preflightResponse(req("OPTIONS", { "Access-Control-Request-Method": "GET" }), settings), null);
});

/* ------------------------------------------------------------------ preflight 응답 */

Deno.test("정상 preflight — 200 + 고정 헤더", async () => {
  const res = preflightResponse(
    req("OPTIONS", {
      "Origin": ORIGINS[0],
      "Access-Control-Request-Method": "POST",
      "Access-Control-Request-Headers": "authorization, content-type",
    }),
    settings,
  )!;
  assertEquals(res.status, 200);
  assertEquals(await res.text(), "OK");
  assertEquals(res.headers.get("Access-Control-Allow-Origin"), ORIGINS[0]);
  assertEquals(res.headers.get("Access-Control-Allow-Credentials"), "true");
  assertEquals(res.headers.get("Access-Control-Allow-Methods"), "GET, POST");
  assertEquals(res.headers.get("Access-Control-Max-Age"), "600");
  assertEquals(res.headers.get("Vary"), "Origin");
  // allow_headers=["*"] → 요청받은 목록을 그대로 되비춘다.
  assertEquals(res.headers.get("Access-Control-Allow-Headers"), "authorization, content-type");
});

Deno.test("요청 헤더가 없으면 Access-Control-Allow-Headers 도 없다", () => {
  const res = preflightResponse(
    req("OPTIONS", { "Origin": ORIGINS[0], "Access-Control-Request-Method": "GET" }),
    settings,
  )!;
  assertEquals(res.headers.get("Access-Control-Allow-Headers"), null);
});

Deno.test("허용 밖 origin 은 400 이지만 CORS 헤더는 붙는다", async () => {
  const res = preflightResponse(
    req("OPTIONS", { "Origin": "https://evil.com", "Access-Control-Request-Method": "GET" }),
    settings,
  )!;
  assertEquals(res.status, 400);
  assertEquals(await res.text(), "Disallowed CORS origin");
  assertEquals(res.headers.get("Access-Control-Allow-Origin"), null);
  // 실패해도 나머지 헤더는 온다 — 개발자 도구에 이유가 보이게.
  assertEquals(res.headers.get("Access-Control-Allow-Methods"), "GET, POST");
});

Deno.test("허용 밖 메서드는 400 — OPTIONS 는 목록에 없다", async () => {
  for (
    const [m, body] of [
      ["DELETE", "Disallowed CORS method"],
      ["PUT", "Disallowed CORS method"],
      ["OPTIONS", "Disallowed CORS method"],
    ]
  ) {
    const res = preflightResponse(
      req("OPTIONS", { "Origin": ORIGINS[0], "Access-Control-Request-Method": m }),
      settings,
    )!;
    assertEquals(res.status, 400, m);
    assertEquals(await res.text(), body);
  }
});

Deno.test("origin·method 둘 다 틀리면 실패 사유가 순서대로 나온다", async () => {
  const res = preflightResponse(
    req("OPTIONS", { "Origin": "https://evil.com", "Access-Control-Request-Method": "DELETE" }),
    settings,
  )!;
  assertEquals(res.status, 400);
  assertEquals(await res.text(), "Disallowed CORS origin, method");
});

/* ------------------------------------------------------------------ 일반 응답 */

Deno.test("Origin 이 없으면 응답을 그대로 돌려준다", () => {
  const original = new Response("body", { status: 200 });
  const out = applyCorsHeaders(req("GET"), original, settings);
  assertEquals(out, original);
  assertEquals(out.headers.get("Access-Control-Allow-Credentials"), null);
});

Deno.test("허용 origin 이면 ACAO + credentials + Vary", async () => {
  const out = applyCorsHeaders(
    req("GET", { Origin: ORIGINS[2] }),
    new Response("payload", { status: 201, headers: { "content-type": "application/json" } }),
    settings,
  );
  assertEquals(out.status, 201);
  assertEquals(await out.text(), "payload");
  assertEquals(out.headers.get("Access-Control-Allow-Origin"), ORIGINS[2]);
  assertEquals(out.headers.get("Access-Control-Allow-Credentials"), "true");
  assertEquals(out.headers.get("Vary"), "Origin");
  // 원래 헤더는 보존된다.
  assertEquals(out.headers.get("content-type"), "application/json");
});

Deno.test("기존 Vary 는 덮어쓰지 않고 덧붙인다", () => {
  const out = applyCorsHeaders(
    req("GET", { Origin: ORIGINS[0] }),
    new Response("x", { headers: { Vary: "Accept-Encoding" } }),
    settings,
  );
  assertEquals(out.headers.get("Vary"), "Accept-Encoding, Origin");
});

Deno.test("허용 밖 origin 이면 credentials 만 붙고 ACAO 는 없다", () => {
  const out = applyCorsHeaders(req("GET", { Origin: "https://evil.com" }), new Response("x"), settings);
  assertEquals(out.headers.get("Access-Control-Allow-Origin"), null);
  // 원본도 이렇게 동작한다. ACAO 가 없으므로 브라우저가 막는다.
  assertEquals(out.headers.get("Access-Control-Allow-Credentials"), "true");
  assertEquals(out.headers.get("Vary"), null);
});

Deno.test("에러 응답에도 CORS 헤더가 붙는다", () => {
  // 401 에 CORS 헤더가 없으면 프론트가 상태코드조차 못 읽는다.
  const out = applyCorsHeaders(
    req("GET", { Origin: ORIGINS[0] }),
    new Response(JSON.stringify({ detail: "인증이 필요합니다." }), { status: 401 }),
    settings,
  );
  assertEquals(out.status, 401);
  assertEquals(out.headers.get("Access-Control-Allow-Origin"), ORIGINS[0]);
});
