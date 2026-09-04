/**
 * `cookie_token.ts` 는 `api/app/auth/cookie_token.py` 와 **완전히 같은 판정**을 해야 한다.
 *
 * 여기가 어긋나면 증상이 "로그인이 안 된다"가 아니라 **"어떤 사용자만 조용히 익명으로 떨어진다"**
 * 이다. 쿠키가 3,180자를 넘어 청크로 쪼개진 세션, base64 prefix 가 붙은 세션처럼 일부 경로만
 * 깨지기 때문이다. 그래서 형식별 경계를 전부 고정한다.
 *
 * 원본 근거: `derive_project_ref` / `_join_chunked_cookie` / `_decode_cookie_value` /
 * `extract_access_token`.
 */

import { assertEquals } from "@std/assert";
import { deriveProjectRef, extractAccessToken } from "./cookie_token.ts";

const REF = "abcd1234";
const NAME = `sb-${REF}-auth-token`;

/** `@supabase/ssr` 이 저장하는 세션 JSON 의 최소 형태. */
function sessionJson(token: string): string {
  return JSON.stringify({ access_token: token, refresh_token: "r", expires_in: 3600 });
}

/** Python `base64.urlsafe_b64encode` 결과에서 padding 을 뗀 형태 — ssr 이 이렇게 저장할 수 있다. */
function base64UrlNoPad(s: string): string {
  const bytes = new TextEncoder().encode(s);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/* ------------------------------------------------------------------ deriveProjectRef */

Deno.test("deriveProjectRef — hostname 첫 segment", () => {
  assertEquals(deriveProjectRef("https://abcd1234.supabase.co"), "abcd1234");
  assertEquals(deriveProjectRef("https://abcd1234.supabase.co/rest/v1"), "abcd1234");
  // Python urlsplit().hostname 은 소문자로 정규화한다.
  assertEquals(deriveProjectRef("https://ABCD1234.supabase.co"), "abcd1234");
  // 포트가 붙어도 hostname 만 본다.
  assertEquals(deriveProjectRef("http://localhost:54321"), "localhost");
});

Deno.test("deriveProjectRef — `//host` 는 Python urlsplit 처럼 netloc 으로 본다", () => {
  // URL 생성자는 base 없이 던지므로 이 형태만 더미 스킴을 붙인다.
  assertEquals(deriveProjectRef("//abcd1234.supabase.co"), "abcd1234");
});

Deno.test("deriveProjectRef — 유도 불가면 null (graceful)", () => {
  assertEquals(deriveProjectRef(""), null);
  // 스킴이 없으면 Python 은 netloc 을 못 잡아 hostname 이 None 이다.
  assertEquals(deriveProjectRef("abcd1234.supabase.co"), null);
  assertEquals(deriveProjectRef("not a url"), null);
});

/* ------------------------------------------------------------------ 단일 쿠키 */

Deno.test("단일 쿠키에서 access_token 추출", () => {
  assertEquals(extractAccessToken({ [NAME]: sessionJson("jwt-1") }, REF), "jwt-1");
});

Deno.test("project_ref 가 비면 추출하지 않는다", () => {
  assertEquals(extractAccessToken({ [NAME]: sessionJson("jwt-1") }, ""), null);
  assertEquals(extractAccessToken({ [NAME]: sessionJson("jwt-1") }, null), null);
});

Deno.test("쿠키가 없으면 null", () => {
  assertEquals(extractAccessToken({}, REF), null);
  assertEquals(extractAccessToken({ "other-cookie": "x" }, REF), null);
});

/* ------------------------------------------------------------------ 청크 분할 */

Deno.test("청크 쿠키를 인덱스 순서대로 이어 붙인다", () => {
  const json = sessionJson("jwt-chunked");
  const mid = Math.floor(json.length / 2);
  assertEquals(
    extractAccessToken({ [`${NAME}.0`]: json.slice(0, mid), [`${NAME}.1`]: json.slice(mid) }, REF),
    "jwt-chunked",
  );
});

Deno.test("단일 쿠키가 있으면 청크보다 우선한다", () => {
  assertEquals(
    extractAccessToken(
      { [NAME]: sessionJson("single-wins"), [`${NAME}.0`]: sessionJson("chunk-loses") },
      REF,
    ),
    "single-wins",
  );
});

Deno.test("청크에 구멍이 있으면 그 앞까지만 이어 붙인다", () => {
  const json = sessionJson("jwt-gap");
  const a = json.slice(0, 10);
  // .0 만 있고 .1 이 없으므로 결합 결과는 잘린 JSON → 파싱 실패 → null.
  assertEquals(extractAccessToken({ [`${NAME}.0`]: a, [`${NAME}.2`]: json.slice(10) }, REF), null);
});

Deno.test("`.0` 없이 `.1` 만 있으면 null", () => {
  assertEquals(extractAccessToken({ [`${NAME}.1`]: sessionJson("x") }, REF), null);
});

/* ------------------------------------------------------------------ base64- prefix */

Deno.test("base64- prefix 는 base64url 로 선해독한다", () => {
  const raw = `base64-${base64UrlNoPad(sessionJson("jwt-b64"))}`;
  assertEquals(extractAccessToken({ [NAME]: raw }, REF), "jwt-b64");
});

Deno.test("base64- prefix + 청크 분할 조합", () => {
  const raw = `base64-${base64UrlNoPad(sessionJson("jwt-b64-chunked"))}`;
  const mid = Math.floor(raw.length / 2);
  assertEquals(
    extractAccessToken({ [`${NAME}.0`]: raw.slice(0, mid), [`${NAME}.1`]: raw.slice(mid) }, REF),
    "jwt-b64-chunked",
  );
});

Deno.test("한글이 든 세션도 UTF-8 로 복원된다", () => {
  const raw = `base64-${base64UrlNoPad(JSON.stringify({ access_token: "토큰-한글", name: "김우민" }))}`;
  assertEquals(extractAccessToken({ [NAME]: raw }, REF), "토큰-한글");
});

Deno.test("base64 안 공백·개행은 제거하고 padding 을 계산한다", () => {
  // Python 은 공백을 포함한 길이로 padding 을 계산해 성공 여부가 우연히 갈린다.
  // 여기서는 공백을 먼저 지워 **항상** 복원한다 — cookie_token.ts 의 §의도적 차이 참조.
  const enc = base64UrlNoPad(sessionJson("jwt-ws"));
  for (const ws of ["  ", "\n", "\t", " \n "]) {
    const raw = `base64-${enc.slice(0, 8)}${ws}${enc.slice(8)}`;
    assertEquals(extractAccessToken({ [NAME]: raw }, REF), "jwt-ws", `공백 ${JSON.stringify(ws)}`);
  }
});

Deno.test("깨진 base64 는 null (graceful)", () => {
  assertEquals(extractAccessToken({ [NAME]: "base64-!!!not-base64!!!" }, REF), null);
  assertEquals(extractAccessToken({ [NAME]: "base64-" }, REF), null);
});

/* ------------------------------------------------------------------ 값 형태 분기 */

Deno.test("배열 형식이면 첫 원소를 토큰으로 본다", () => {
  // ssr 일부 버전이 [access_token, refresh_token] 배열을 저장한다.
  assertEquals(extractAccessToken({ [NAME]: JSON.stringify(["jwt-arr", "refresh"]) }, REF), "jwt-arr");
});

Deno.test("토큰이 빈 문자열이거나 문자열이 아니면 null", () => {
  assertEquals(extractAccessToken({ [NAME]: JSON.stringify({ access_token: "" }) }, REF), null);
  assertEquals(extractAccessToken({ [NAME]: JSON.stringify({ access_token: 123 }) }, REF), null);
  assertEquals(extractAccessToken({ [NAME]: JSON.stringify({ access_token: null }) }, REF), null);
  assertEquals(extractAccessToken({ [NAME]: JSON.stringify({}) }, REF), null);
  assertEquals(extractAccessToken({ [NAME]: JSON.stringify([]) }, REF), null);
  assertEquals(extractAccessToken({ [NAME]: JSON.stringify([""]) }, REF), null);
  assertEquals(extractAccessToken({ [NAME]: JSON.stringify([123]) }, REF), null);
});

Deno.test("JSON 이 객체·배열이 아니면 null", () => {
  // 배열을 먼저 걸러야 한다 — JS 에서 배열도 typeof 는 "object" 다.
  assertEquals(extractAccessToken({ [NAME]: JSON.stringify("bare-string") }, REF), null);
  assertEquals(extractAccessToken({ [NAME]: JSON.stringify(42) }, REF), null);
  assertEquals(extractAccessToken({ [NAME]: JSON.stringify(null) }, REF), null);
});

Deno.test("JSON 이 아니면 null", () => {
  assertEquals(extractAccessToken({ [NAME]: "" }, REF), null);
  assertEquals(extractAccessToken({ [NAME]: "not json at all" }, REF), null);
  assertEquals(extractAccessToken({ [NAME]: "{broken" }, REF), null);
});

/* ------------------------------------------------------------------ 방어 상한 */

Deno.test("청크는 16개까지만 읽는다", () => {
  // 17개로 쪼갠 값은 16개까지만 이어 붙여 잘린 JSON 이 되므로 null 이어야 한다.
  const json = sessionJson("jwt-too-many-chunks".padEnd(200, "x"));
  const size = Math.ceil(json.length / 17);
  const cookies: Record<string, string> = {};
  for (let i = 0; i < 17; i++) cookies[`${NAME}.${i}`] = json.slice(i * size, (i + 1) * size);
  assertEquals(extractAccessToken(cookies, REF), null);
});
