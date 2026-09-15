/**
 * `current_user.ts` 는 `api/app/auth/dependencies.py` 와 같은 분기·같은 상태코드·같은 detail 을
 * 내야 한다.
 *
 * 특히 지키는 것 두 가지:
 * 1. **무효 토큰은 401** — 익명으로 강등하면 "로그인했는데 남의 데이터가 보이는" 상태가 된다.
 * 2. **익명 fallback 은 owner 컨텍스트** — 쓰기 게이트가 없으면 익명이 owner 데이터에 쓴다.
 */

import { assertEquals, assertRejects, assertThrows } from "@std/assert";
import { SignJWT } from "jose";
import {
  AuthError,
  type AuthSettings,
  cookieParser,
  type CurrentUser,
  type CurrentUserBase,
  extractBearerToken,
  getCurrentUser,
  requireAdmin,
  requireAuthenticatedUser,
  requireSessionUser,
  touchDeviceUser,
} from "./current_user.ts";

const SECRET = "test-secret-at-least-32-bytes-long!!";
const SUPABASE_URL = "https://abcd1234.supabase.co";
const COOKIE_NAME = "sb-abcd1234-auth-token";
const DEFAULT_USER = "00000000-0000-0000-0000-000000000001";
const OWNER = "99999999-9999-9999-9999-999999999999";
const USER = "11111111-1111-1111-1111-111111111111";

function settings(over: Partial<AuthSettings> = {}): AuthSettings {
  return {
    supabaseUrl: SUPABASE_URL,
    authEnabled: true,
    defaultUserId: DEFAULT_USER,
    ownerUserId: OWNER,
    supabaseJwtAlgorithm: "HS256",
    supabaseJwtSecret: SECRET,
    supabaseJwksUrl: null,
    ...over,
  };
}

async function token(over: Record<string, unknown> = {}): Promise<string> {
  const now = Math.floor(Date.now() / 1000);
  return await new SignJWT({ sub: USER, aud: "authenticated", exp: now + 3600, ...over })
    .setProtectedHeader({ alg: "HS256" })
    .sign(new TextEncoder().encode(SECRET));
}

function request(headers: Record<string, string> = {}): Request {
  return new Request("https://api.example.com/auth/me", { headers });
}

/**
 * `@supabase/ssr` 이 큰 세션에 쓰는 `base64-` 형태. 쿠키에 안전한 문자만 들어간다.
 * **percent-encoding 을 하지 않는다** — Starlette 이 디코드하지 않으므로(실측) 원본도
 * 인코딩된 값은 못 읽는다. 같은 제약을 그대로 둔다.
 */
function sessionCookie(jwt: string): string {
  const json = JSON.stringify({ access_token: jwt });
  const bytes = new TextEncoder().encode(json);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  const b64 = btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${COOKIE_NAME}=base64-${b64}`;
}

/* ------------------------------------------------------------------ cookieParser */

Deno.test("cookieParser — 기본 분해와 trim", () => {
  assertEquals(cookieParser("a=1; b=2"), { a: "1", b: "2" });
  assertEquals(cookieParser("  a =  1  ;b=2"), { a: "1", b: "2" });
});

Deno.test("cookieParser — 값에 `=` 가 있어도 첫 `=` 에서만 자른다", () => {
  assertEquals(cookieParser("t=abc=def=ghi"), { t: "abc=def=ghi" });
});

Deno.test("cookieParser — 같은 이름이 여러 번이면 마지막이 이긴다", () => {
  // Starlette cookie_parser 와 같은 규칙. 앞이 이기게 만들면 갱신된 세션을 놓친다.
  assertEquals(cookieParser("a=1; a=2"), { a: "2" });
});

Deno.test("cookieParser — 이름 없는 값은 빈 이름으로 담는다", () => {
  assertEquals(cookieParser("justvalue"), { "": "justvalue" });
});

Deno.test("cookieParser — 빈 헤더·빈 조각", () => {
  assertEquals(cookieParser(null), {});
  assertEquals(cookieParser(""), {});
  assertEquals(cookieParser(";;"), {});
  assertEquals(cookieParser("a=1;;b=2"), { a: "1", b: "2" });
});

Deno.test("cookieParser — 따옴표 값과 이스케이프를 푼다", () => {
  assertEquals(cookieParser('a="plain"'), { a: "plain" });
  assertEquals(cookieParser('a="with\\"quote"'), { a: 'with"quote' });
  // `\\101` = 8진 101 = 'A'
  assertEquals(cookieParser('a="\\101BC"'), { a: "ABC" });
  // 따옴표가 한쪽만 있으면 그대로 둔다.
  assertEquals(cookieParser('a="unbalanced'), { a: '"unbalanced' });
});

/* ------------------------------------------------------------------ Bearer 추출 */

Deno.test("extractBearerToken — 형식과 공백", () => {
  assertEquals(extractBearerToken(request({ Authorization: "Bearer abc" })), "abc");
  assertEquals(extractBearerToken(request({ Authorization: "Bearer   abc  " })), "abc");
  assertEquals(extractBearerToken(request()), null);
  assertEquals(extractBearerToken(request({ Authorization: "Bearer " })), null);
  assertEquals(extractBearerToken(request({ Authorization: "Basic abc" })), null);
  // 원본이 `startswith("Bearer ")` 라 대소문자를 구분한다.
  assertEquals(extractBearerToken(request({ Authorization: "bearer abc" })), null);
});

/* ------------------------------------------------------------------ 3-way 분기 */

Deno.test("authEnabled=false 면 토큰과 무관하게 default 사용자 + 인증됨", async () => {
  const s = settings({ authEnabled: false });
  const u = await getCurrentUser(request(), s);
  assertEquals(u, {
    userId: DEFAULT_USER,
    email: null,
    isAuthenticated: true,
    authKind: "session",
  });

  // 깨진 토큰이 있어도 이 분기에서는 검증 자체를 하지 않는다.
  const u2 = await getCurrentUser(request({ Authorization: "Bearer garbage" }), s);
  assertEquals(u2.userId, DEFAULT_USER);
  assertEquals(u2.isAuthenticated, true);
});

Deno.test("토큰이 없으면 익명 데모 — owner 컨텍스트지만 isAuthenticated=false", async () => {
  const u = await getCurrentUser(request(), settings());
  assertEquals(u, { userId: OWNER, email: null, isAuthenticated: false, authKind: "session" });
});

Deno.test("owner 미설정이면 익명 fallback 이 default 사용자로 간다", async () => {
  const u = await getCurrentUser(request(), settings({ ownerUserId: null }));
  assertEquals(u.userId, DEFAULT_USER);
  assertEquals(u.isAuthenticated, false);
});

Deno.test("유효한 Bearer 토큰이면 본인 컨텍스트", async () => {
  const u = await getCurrentUser(
    request({ Authorization: `Bearer ${await token({ email: "a@b.com" })}` }),
    settings(),
  );
  assertEquals(u, {
    userId: USER,
    email: "a@b.com",
    isAuthenticated: true,
    authKind: "session",
  });
});

Deno.test("쿠키만 있어도 인증된다 (credentials: 'include' 경로)", async () => {
  const u = await getCurrentUser(request({ Cookie: sessionCookie(await token()) }), settings());
  assertEquals(u.userId, USER);
  assertEquals(u.isAuthenticated, true);
});

Deno.test("Bearer 가 쿠키보다 우선한다", async () => {
  const bearer = await token({ sub: "22222222-2222-2222-2222-222222222222" });
  const cookie = sessionCookie(await token({ sub: USER }));
  const u = await getCurrentUser(
    request({ Authorization: `Bearer ${bearer}`, Cookie: cookie }),
    settings(),
  );
  assertEquals(u.userId, "22222222-2222-2222-2222-222222222222");
});

Deno.test("Bearer 가 비면 쿠키로 넘어간다", async () => {
  // "Bearer " 만 오면 추출 결과가 null 이라 쿠키 경로가 살아야 한다.
  const u = await getCurrentUser(
    request({ Authorization: "Bearer ", Cookie: sessionCookie(await token()) }),
    settings(),
  );
  assertEquals(u.userId, USER);
});

Deno.test("무효 토큰은 401 — 익명으로 강등하지 않는다", async () => {
  const now = Math.floor(Date.now() / 1000);
  for (
    const [label, header] of [
      ["형식 오류", "Bearer not-a-jwt"],
      ["만료", `Bearer ${await token({ exp: now - 10 })}`],
      ["aud 불일치", `Bearer ${await token({ aud: "anon" })}`],
    ]
  ) {
    const e = await assertRejects(
      () => getCurrentUser(request({ Authorization: header }), settings()),
      AuthError,
      undefined,
      label,
    );
    assertEquals(e.status, 401);
    // 왜 틀렸는지는 알려주지 않는다 — 원본과 같다.
    assertEquals(e.detail, "인증이 필요합니다.");
    assertEquals(e.headers["WWW-Authenticate"], "Bearer");
  }
});

Deno.test("percent-encoded 쿠키 값은 디코드하지 않는다 (원본과 동일)", async () => {
  // Starlette `cookie_parser` 는 URL 디코드를 하지 않는다(실측). 여기서 디코드를 넣으면
  // Edge 만 세션을 인식해 "Railway 는 익명, Edge 는 로그인" 이라는 갈림이 생긴다.
  // 원본이 못 읽는 것은 여기서도 못 읽어야 한다.
  const json = JSON.stringify({ access_token: await token() });
  const u = await getCurrentUser(
    request({ Cookie: `${COOKIE_NAME}=${encodeURIComponent(json)}` }),
    settings(),
  );
  assertEquals(u.isAuthenticated, false);
});

Deno.test("인코딩되지 않은 raw JSON 쿠키도 읽는다", async () => {
  const json = JSON.stringify({ access_token: await token() });
  const u = await getCurrentUser(request({ Cookie: `${COOKIE_NAME}=${json}` }), settings());
  assertEquals(u.userId, USER);
});

Deno.test("쿠키가 깨져 있으면 토큰 없음으로 보고 익명 fallback", async () => {
  // 쿠키 파싱 실패는 401 이 아니다 — 애초에 토큰을 못 찾은 것과 같다.
  const u = await getCurrentUser(request({ Cookie: `${COOKIE_NAME}=not-json` }), settings());
  assertEquals(u.userId, OWNER);
  assertEquals(u.isAuthenticated, false);
});

Deno.test("SUPABASE_URL 로 ref 를 못 뽑으면 쿠키 경로가 죽고 익명이 된다", async () => {
  const u = await getCurrentUser(
    request({ Cookie: sessionCookie(await token()) }),
    settings({ supabaseUrl: "not a url" }),
  );
  assertEquals(u.isAuthenticated, false);
});

/* ------------------------------------------------------------------ 게이트 */

function user(over: Partial<CurrentUserBase> = {}): CurrentUser {
  return { userId: USER, email: null, isAuthenticated: true, authKind: "session", ...over };
}

function deviceUser(over: Partial<CurrentUserBase> = {}): CurrentUser {
  return {
    userId: USER,
    email: null,
    isAuthenticated: true,
    authKind: "device",
    scopes: ["ingest"],
    deviceId: "d1",
    deviceLastUsedAt: null,
    ...over,
  };
}

Deno.test("requireAuthenticatedUser — 익명은 401", () => {
  assertEquals(requireAuthenticatedUser(user()).userId, USER);
  const e = assertThrows(() => requireAuthenticatedUser(user({ isAuthenticated: false })), AuthError);
  assertEquals(e.status, 401);
  assertEquals(e.detail, "로그인이 필요합니다.");
});

Deno.test("requireAdmin — authEnabled=false 면 무조건 통과", () => {
  const s = settings({ authEnabled: false });
  assertEquals(requireAdmin(user({ isAuthenticated: false }), s).userId, USER);
});

Deno.test("requireAdmin — owner 만 통과", () => {
  const s = settings();
  assertEquals(requireAdmin(user({ userId: OWNER }), s).userId, OWNER);

  const e = assertThrows(() => requireAdmin(user({ userId: USER }), s), AuthError);
  assertEquals(e.status, 403);
  assertEquals(e.detail, "운영자 권한이 필요합니다.");
});

Deno.test("requireAdmin — owner 미설정이면 owner 자신도 못 들어온다 (전면 차단)", () => {
  const s = settings({ ownerUserId: null });
  assertThrows(() => requireAdmin(user({ userId: OWNER }), s), AuthError);
  assertThrows(() => requireAdmin(user({ userId: DEFAULT_USER }), s), AuthError);
});

Deno.test("requireAdmin — 익명은 owner UUID 를 갖고 있어도 403", () => {
  // 익명 fallback 이 owner 컨텍스트라 isAuthenticated 를 안 보면 여기서 뚫린다.
  const e = assertThrows(
    () => requireAdmin(user({ userId: OWNER, isAuthenticated: false }), settings()),
    AuthError,
  );
  assertEquals(e.status, 403);
});

/* ------------------------------------------------------------------ 기기 토큰 */

const DEVICE_TOKEN = `jrd_${"a".repeat(43)}`;

interface FakeRow {
  id: string;
  user_id: string;
  name: string;
  scopes: string[];
  last_used_at: string | null;
  revoked_at: string | null;
}

/** `lookupDeviceToken` 이 쓰는 최소 체인만 흉내낸다. */
function fakeDeviceClient(rows: FakeRow[]) {
  const updates: Record<string, unknown>[] = [];
  const client = {
    from: () => ({
      select: () => ({
        eq: () => ({ limit: () => Promise.resolve({ data: rows, error: null }) }),
      }),
      update: (row: Record<string, unknown>) => {
        updates.push(row);
        return { eq: () => Promise.resolve({ error: null }) };
      },
    }),
  };
  // deno-lint-ignore no-explicit-any
  return { client: client as any, updates };
}

function deviceRow(over: Partial<FakeRow> = {}): FakeRow {
  return {
    id: "d1",
    user_id: USER,
    name: "회사 노트북",
    scopes: ["ingest"],
    last_used_at: null,
    revoked_at: null,
    ...over,
  };
}

Deno.test("기기 토큰 — deviceClient 가 있으면 device 컨텍스트로 푼다", async () => {
  const f = fakeDeviceClient([deviceRow({ last_used_at: "2026-09-15T00:00:00Z" })]);
  const u = await getCurrentUser(
    request({ Authorization: `Bearer ${DEVICE_TOKEN}` }),
    settings(),
    { deviceClient: f.client },
  );
  assertEquals(u, {
    userId: USER,
    email: null,
    isAuthenticated: true,
    authKind: "device",
    scopes: ["ingest"],
    deviceId: "d1",
    deviceLastUsedAt: "2026-09-15T00:00:00Z",
  });
  // **인증 시점에는 last_used_at 을 쓰지 않는다** — 스코프 게이트 통과 후에 호출부가 올린다.
  assertEquals(f.updates.length, 0);
});

Deno.test("기기 토큰 — 폐기된 토큰은 401 + WWW-Authenticate", async () => {
  const f = fakeDeviceClient([deviceRow({ revoked_at: "2026-09-14T00:00:00Z" })]);
  const e = await assertRejects(
    () =>
      getCurrentUser(request({ Authorization: `Bearer ${DEVICE_TOKEN}` }), settings(), {
        deviceClient: f.client,
      }),
    AuthError,
  );
  assertEquals(e.status, 401);
  assertEquals(e.detail, "인증이 필요합니다.");
  assertEquals(e.headers["WWW-Authenticate"], "Bearer");
});

Deno.test("기기 토큰 — 없는 토큰도 같은 401 (폐기와 구분하지 않는다)", async () => {
  const f = fakeDeviceClient([]);
  const e = await assertRejects(
    () =>
      getCurrentUser(request({ Authorization: `Bearer ${DEVICE_TOKEN}` }), settings(), {
        deviceClient: f.client,
      }),
    AuthError,
  );
  assertEquals(e.status, 401);
});

Deno.test("touchDeviceUser — 세션 호출자는 아무것도 쓰지 않는다", async () => {
  const f = fakeDeviceClient([]);
  await touchDeviceUser(f.client, user(), Date.parse("2026-09-15T00:00:00Z"));
  assertEquals(f.updates.length, 0);
});

Deno.test("touchDeviceUser — 1분 안이면 건너뛰고, 넘으면 갱신한다", async () => {
  const base = Date.parse("2026-09-15T00:00:00Z");
  const u = deviceUser() as CurrentUser & { deviceLastUsedAt: string | null };
  u.deviceLastUsedAt = new Date(base).toISOString();

  const skip = fakeDeviceClient([]);
  await touchDeviceUser(skip.client, u, base + 59_000);
  assertEquals(skip.updates.length, 0);

  const hit = fakeDeviceClient([]);
  await touchDeviceUser(hit.client, u, base + 61_000);
  assertEquals(hit.updates.length, 1);
  assertEquals(hit.updates[0]["last_used_at"], new Date(base + 61_000).toISOString());
});

Deno.test("requireSessionUser — 기기 토큰은 403, 세션은 통과", () => {
  assertEquals(requireSessionUser(user()).userId, USER);
  const e = assertThrows(() => requireSessionUser(deviceUser()), AuthError);
  assertEquals(e.status, 403);
  assertEquals(e.detail, "기기 토큰으로는 이 작업을 할 수 없습니다.");
});

Deno.test("기기 토큰 — deviceClient 없이는 401 (JWT 로 취급)", async () => {
  const req = new Request("http://x/documents", {
    headers: { Authorization: `Bearer jrd_${"a".repeat(43)}` },
  });
  let status = 0;
  try {
    await getCurrentUser(req, settings());
  } catch (e) {
    status = (e as { status: number }).status;
  }
  assertEquals(status, 401);
});
