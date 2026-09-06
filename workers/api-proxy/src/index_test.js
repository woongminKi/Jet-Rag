/**
 * 프록시는 배포해 봐야 아는 부분이 많지만, **경로 매핑과 요청 변환**은 배포 없이 고정할 수 있다.
 * 여기가 틀리면 증상이 "전 API 가 404" 또는 "쿠키가 안 붙어 전원 로그아웃" 이라 크게 터진다.
 *
 * `fetch` 를 가로채 나가는 요청을 그대로 들여다본다.
 */

import { assertEquals, assertStringIncludes } from "@std/assert";
import handler from "./index.js";
import { resolveTarget } from "./routes.js";

const ENV = {
  SUPABASE_FUNCTIONS_BASE: "https://ref.supabase.co/functions/v1",
  LEGACY_ORIGIN: "https://jet-rag-production.up.railway.app",
};

/** 나가는 요청을 붙잡아 돌려준다. 실제 네트워크는 타지 않는다. */
async function capture(request, env = ENV) {
  const original = globalThis.fetch;
  let sent = null;
  globalThis.fetch = (input, init) => {
    sent = input instanceof Request ? input : new Request(input, init);
    return Promise.resolve(new Response("upstream", { status: 200 }));
  };
  try {
    const response = await handler.fetch(request, env);
    return { sent, response };
  } finally {
    globalThis.fetch = original;
  }
}

function req(path, init = {}) {
  return new Request(`https://jetrag-api.woong-s.com${path}`, init);
}

/* --------------------------------------------------- 이관 선언 vs 원본 라우트 */

/**
 * Edge 로 실제 옮긴 원본 경로. 프록시 규칙이 여기 없는 경로를 Edge 로 보내면 그 경로는
 * **운영에서 404 가 된다** — 실제로 `/search/eval-precision` 이 그렇게 죽었다.
 *
 * 목록을 손으로 적는 게 핵심이다. 규칙에서 자동으로 뽑으면 규칙이 틀렸을 때 같이 틀린다.
 */
const MIGRATED_PATHS = new Set([
  "/auth/me",
  "/health",
  "/search",
  "/stats",
  "/stats/trend",
  "/me/plan",
  "/me/subscription",
  "/me/email-ingest",
  "/me/email-ingest/rotate",
  "/admin/queries/stats",
  "/admin/feedback/stats",
  "/answer",
]);

Deno.test("프록시가 Edge 로 보내는 원본 라우트는 전부 이관돼 있어야 한다", async () => {
  // `api/scripts/fixtures/fastapi_routes.json` 은 FastAPI 앱에서 뽑은 실제 라우트 전수다.
  // 원본에 라우트가 추가됐는데 프록시 규칙이 그걸 삼키면 여기서 터진다.
  const url = new URL("../../../api/scripts/fixtures/fastapi_routes.json", import.meta.url);
  const routes = JSON.parse(await Deno.readTextFile(url));
  const swallowed = [];
  for (const { path } of routes) {
    const target = resolveTarget(path);
    if (target !== null && !MIGRATED_PATHS.has(path)) swallowed.push(`${path} → ${target}`);
  }
  assertEquals(swallowed, [], `Edge 로 가는데 이관되지 않은 경로: ${swallowed.join(", ")}`);
});

Deno.test("이관 선언 목록은 원본에 실재하는 경로여야 한다", async () => {
  const url = new URL("../../../api/scripts/fixtures/fastapi_routes.json", import.meta.url);
  const routes = JSON.parse(await Deno.readTextFile(url));
  const known = new Set(routes.map((r) => r.path));
  // 오타나 이미 사라진 경로를 이관했다고 적어 두면 위 테스트가 헐거워진다.
  const ghosts = [...MIGRATED_PATHS].filter((p) => !known.has(p));
  assertEquals(ghosts, [], `원본에 없는 경로를 이관 목록에 적었다: ${ghosts.join(", ")}`);
});

Deno.test("이관 선언 경로는 프록시가 실제로 Edge 로 보내야 한다", () => {
  const orphans = [...MIGRATED_PATHS].filter((p) => resolveTarget(p) === null);
  assertEquals(orphans, [], `이관했다고 적었는데 프록시가 안 보내는 경로: ${orphans.join(", ")}`);
});

/* ------------------------------------------------------------------ 경로 매핑 */

Deno.test("이관된 경로만 Edge 로 간다", () => {
  assertEquals(resolveTarget("/auth/me"), "api-account");
  assertEquals(resolveTarget("/health"), "api-account");
  // 2026-09-05 전환 — Phase 2 의 `/search`.
  assertEquals(resolveTarget("/search"), "api-search");
  // 2026-09-06 전환 — `/stats` 와 `/stats/trend`.
  assertEquals(resolveTarget("/stats"), "api-account");
  assertEquals(resolveTarget("/stats/trend"), "api-account");
  // 2026-09-06 전환 — `/me/*`.
  assertEquals(resolveTarget("/me/plan"), "api-account");
  assertEquals(resolveTarget("/me/subscription"), "api-account");
  assertEquals(resolveTarget("/me/email-ingest"), "api-account");
  assertEquals(resolveTarget("/me/email-ingest/rotate"), "api-account");
  // 2026-09-06 전환 — `/admin` 읽기 2개.
  assertEquals(resolveTarget("/admin/queries/stats"), "api-account");
  assertEquals(resolveTarget("/admin/feedback/stats"), "api-account");
  // 2026-09-06 전환 — `/answer` 본체.
  assertEquals(resolveTarget("/answer"), "api-answer");
  // 아직 안 열린 경로들 — 기존 백엔드로 가야 한다. 여기가 통째로 초록이 되면
  // "다 옮겼다" 로 착각하게 되므로 한 줄씩 지우면서 연다.
  for (const p of ["/documents", "/payments/ready", "/email/inbound"]) {
    assertEquals(resolveTarget(p), null, p);
  }
});

Deno.test("`/search` 는 자기 자신과 후행 슬래시만", () => {
  // 원본이 `/search/` 에 307 을 내주므로 그 경로도 받는다. 함수가 슬래시를 뗀다.
  assertEquals(resolveTarget("/search"), "api-search");
  assertEquals(resolveTarget("/search/"), "api-search");
  // **하위 경로는 넘기지 않는다.** `/search/eval-precision` 은 원본에 실재하는 라우트인데
  // 예전 접두어 규칙이 이걸 삼켜 운영에서 404 를 냈다. 회귀 고정.
  assertEquals(resolveTarget("/search/eval-precision"), null);
  assertEquals(resolveTarget("/searchfoo"), null);
});

Deno.test("`/health` 는 정확히 일치할 때만 (접두어 오매칭 방지)", () => {
  assertEquals(resolveTarget("/health"), "api-account");
  assertEquals(resolveTarget("/healthz"), null);
  assertEquals(resolveTarget("/health/deep"), null);
});

Deno.test("`/me/` 는 슬래시가 있을 때만 (`/me`·`/mefoo` 는 넘기지 않는다)", () => {
  assertEquals(resolveTarget("/me/plan"), "api-account");
  // 후행 슬래시도 넘어간다 — 원본이 `/me/plan/` 에 401 을 내주고(실측) 함수도 슬래시를 뗀다.
  assertEquals(resolveTarget("/me/plan/"), "api-account");
  // 라우트가 없는 것들은 Railway 가 이미 404 를 내주므로 그대로 둔다.
  assertEquals(resolveTarget("/me"), null);
  assertEquals(resolveTarget("/mefoo"), null);
});

Deno.test("`/admin` 은 읽기 2개만 — subscriptions 는 아직 Railway", () => {
  assertEquals(resolveTarget("/admin/queries/stats"), "api-account");
  assertEquals(resolveTarget("/admin/feedback/stats"), "api-account");
  // 2026-09-06 전환 — `/answer` 본체.
  assertEquals(resolveTarget("/answer"), "api-answer");
  // **POST 가 구독을 바꾸는 쓰기라 안 연다.** 경로로는 메서드를 못 가르므로 GET 도 같이
  // Railway 에 남는다. 이 줄이 초록으로 바뀌면 Phase 3 에서 의도적으로 연 것이어야 한다.
  assertEquals(resolveTarget("/admin/subscriptions"), null);
  assertEquals(resolveTarget("/admin/bogus"), null);
  assertEquals(resolveTarget("/admin"), null);
  // 접두어 오매칭 방지 — 슬래시를 요구한다.
  assertEquals(resolveTarget("/admin/queriesX"), null);
});

Deno.test("`/answer` 는 본체만 — feedback·eval-ragas 는 아직 Railway", () => {
  assertEquals(resolveTarget("/answer"), "api-answer");
  assertEquals(resolveTarget("/answer/"), "api-answer");
  // **하위 경로를 삼키지 않는다.** `/search/eval-precision` 사고의 재발 방지.
  assertEquals(resolveTarget("/answer/feedback"), null);
  assertEquals(resolveTarget("/answer/eval-ragas"), null);
  assertEquals(resolveTarget("/answerx"), null);
});

Deno.test("`/auth/` 는 하위 경로 전체", () => {
  assertEquals(resolveTarget("/auth/me"), "api-account");
  assertEquals(resolveTarget("/auth/callback"), "api-account");
  // `/auth` 만 오면 슬래시가 없어 매칭되지 않는다 — 원본에도 그런 라우트가 없다.
  assertEquals(resolveTarget("/auth"), null);
});

/* ------------------------------------------------------------------ Edge 로 전달 */

Deno.test("Edge 대상 URL 을 함수명 + 원본 경로로 만든다", async () => {
  const { sent } = await capture(req("/auth/me?x=1"));
  assertEquals(sent.url, "https://ref.supabase.co/functions/v1/api-account/auth/me?x=1");
});

Deno.test("원본 경로를 X-Forwarded-Path 로 넘긴다", async () => {
  const { sent } = await capture(req("/auth/me?x=1"));
  // 쿼리는 빼고 경로만 — 함수는 경로로 라우팅한다.
  assertEquals(sent.headers.get("X-Forwarded-Path"), "/auth/me");
});

Deno.test("함수를 DB 와 같은 지역에서 돌리도록 x-region 을 붙인다", async () => {
  // Supabase 기본 라우팅은 *호출자*(= 이 Worker) 근처를 고른다. Worker 는 사용자 근처
  // PoP 에서 돌므로 DB 와 멀어질 수 있고, 검색은 DB 왕복이 여러 번이라 그대로 지연이 된다.
  // 실측(2026-09-05): 같은 함수가 서울 132ms vs 미국서부 828ms.
  const { sent } = await capture(req("/search?q=a"), {
    ...ENV,
    SUPABASE_FUNCTION_REGION: "ap-northeast-2",
  });
  assertEquals(sent.headers.get("x-region"), "ap-northeast-2");
});

Deno.test("지역이 미설정이면 헤더를 안 붙인다 (Supabase 기본 라우팅)", async () => {
  const { sent } = await capture(req("/search?q=a"), ENV);
  assertEquals(sent.headers.get("x-region"), null);
});

Deno.test("쿠키와 Authorization 을 그대로 넘긴다", async () => {
  // 이게 안 넘어가면 세션이 통째로 끊긴다. 프록시의 존재 이유다.
  const { sent } = await capture(
    req("/auth/me", {
      headers: { Cookie: "sb-ref-auth-token=abc", Authorization: "Bearer token-123" },
    }),
  );
  assertEquals(sent.headers.get("Cookie"), "sb-ref-auth-token=abc");
  assertEquals(sent.headers.get("Authorization"), "Bearer token-123");
});

Deno.test("POST 의 본문과 메서드를 보존한다", async () => {
  const { sent } = await capture(
    req("/auth/me", { method: "POST", body: '{"a":1}', headers: { "content-type": "application/json" } }),
  );
  assertEquals(sent.method, "POST");
  assertEquals(await sent.text(), '{"a":1}');
});

/* ------------------------------------------------------------------ 기존 백엔드로 전달 */

// 여기서 쓰는 예시 경로는 **아직 안 옮긴 것**이어야 한다. 옮기고 나면 이 테스트가
// 깨지므로, 깨지면 예시를 바꾸면 된다 — 실제로 `/stats` 전환 때 `/stats/overview` 를
// 쓰고 있어서 세 건이 한꺼번에 깨졌다(그 경로는 원본에도 없어서 회귀는 아니었다).
Deno.test("미이관 경로는 기존 백엔드로, 경로·쿼리를 유지한다", async () => {
  const { sent } = await capture(req("/documents?limit=7"));
  assertEquals(sent.url, "https://jet-rag-production.up.railway.app/documents?limit=7");
  // 기존 백엔드로 갈 때는 이 헤더를 붙이지 않는다 — 원본이 모르는 헤더다.
  assertEquals(sent.headers.get("X-Forwarded-Path"), null);
});

Deno.test("LEGACY_ORIGIN 이 비면 404 (Phase 6 의 종료 상태)", async () => {
  const { sent, response } = await capture(req("/documents"), { ...ENV, LEGACY_ORIGIN: "" });
  assertEquals(sent, null, "네트워크 호출이 없어야 한다");
  assertEquals(response.status, 404);
  assertEquals(await response.json(), { detail: "Not Found" });
});

Deno.test("LEGACY_ORIGIN 이 자기 자신이면 루프 대신 500", async () => {
  // 설정 실수로 jetrag-api.woong-s.com 을 넣으면 Worker 가 자기를 부른다.
  const { sent, response } = await capture(req("/documents"), {
    ...ENV,
    LEGACY_ORIGIN: "https://jetrag-api.woong-s.com",
  });
  assertEquals(sent, null, "루프를 만들지 않아야 한다");
  assertEquals(response.status, 500);
  assertStringIncludes((await response.json()).detail, "설정 오류");
});
