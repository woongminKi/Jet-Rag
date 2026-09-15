import { assertEquals, assertMatch } from "@std/assert";
import {
  DEVICE_TOKEN_PREFIX,
  deviceScopeAllows,
  generateDeviceToken,
  hashDeviceToken,
  isDeviceTokenFormat,
} from "./device_token.ts";

Deno.test("generateDeviceToken — jrd_ + 43자 base64url, prefix 8자, hash 는 sha256 hex", async () => {
  const t = await generateDeviceToken();
  assertMatch(t.token, /^jrd_[A-Za-z0-9_-]{43}$/);
  assertEquals(t.prefix, t.token.slice(0, 8));
  assertEquals(t.hash, await hashDeviceToken(t.token));
  assertMatch(t.hash, /^[0-9a-f]{64}$/);
});

Deno.test("두 번 생성하면 다르다", async () => {
  const a = await generateDeviceToken();
  const b = await generateDeviceToken();
  assertEquals(a.token === b.token, false);
});

Deno.test("isDeviceTokenFormat — 접두어와 길이", () => {
  assertEquals(isDeviceTokenFormat(`${DEVICE_TOKEN_PREFIX}${"a".repeat(43)}`), true);
  assertEquals(isDeviceTokenFormat("eyJhbGciOi..."), false);
  assertEquals(isDeviceTokenFormat("jrd_short"), false);
});

Deno.test("deviceScopeAllows — ingest 는 4개 라우트만", () => {
  const s = ["ingest"];
  assertEquals(deviceScopeAllows(s, "POST", "/documents"), true);
  assertEquals(deviceScopeAllows(s, "POST", "/documents/precheck"), true);
  assertEquals(deviceScopeAllows(s, "GET", "/documents/abc/status"), true);
  assertEquals(deviceScopeAllows(s, "GET", "/documents/batch-status"), true);
  assertEquals(deviceScopeAllows(s, "GET", "/documents"), false);
  assertEquals(deviceScopeAllows(s, "GET", "/documents/abc"), false);
  assertEquals(deviceScopeAllows(s, "POST", "/documents/abc/reingest"), false);
  assertEquals(deviceScopeAllows(s, "GET", "/search"), false);
  assertEquals(deviceScopeAllows([], "POST", "/documents"), false);
});
