/**
 * `jwt.ts` 는 `api/app/auth/jwt_verify.py` 와 **같은 판정과 같은 실패 메시지**를 내야 한다.
 *
 * 메시지까지 고정하는 이유: 이 문자열이 그대로 401 detail 로 사용자에게 나간다.
 * 바뀌면 프론트의 오류 분기나 사용자 안내가 조용히 달라진다.
 *
 * **운영 경로는 ES256/JWKS 다.** 프로젝트 JWKS 실측(2026-09-04):
 * `{kty: EC, alg: ES256, crv: P-256, use: sig}` — 신규 Supabase 프로젝트의 기본이다.
 * HS256 은 레거시 호환 경로이므로 둘 다 덮는다.
 */

import { assertEquals, assertRejects } from "@std/assert";
import { exportJWK, generateKeyPair, SignJWT } from "jose";
import { JWTValidationError, verifyJwt } from "./jwt.ts";
import type { JwtSettings } from "./jwt.ts";

const AUD = "authenticated";
const SUB = "11111111-1111-1111-1111-111111111111";
const SECRET = "test-secret-at-least-32-bytes-long!!";

function hsSettings(over: Partial<JwtSettings> = {}): JwtSettings {
  return {
    supabaseJwtAlgorithm: "HS256",
    supabaseJwtSecret: SECRET,
    supabaseJwksUrl: null,
    ...over,
  };
}

/** 기본 claim 을 채운 HS256 토큰. `over` 로 개별 claim 을 지우거나 바꾼다. */
async function hsToken(over: Record<string, unknown> = {}, opts: { alg?: string } = {}): Promise<string> {
  const now = Math.floor(Date.now() / 1000);
  const claims: Record<string, unknown> = { sub: SUB, aud: AUD, exp: now + 3600, ...over };
  for (const k of Object.keys(claims)) if (claims[k] === undefined) delete claims[k];
  // SignJWT 는 setExpirationTime 등을 쓰지 않으면 payload 를 그대로 서명한다.
  return await new SignJWT(claims)
    .setProtectedHeader({ alg: opts.alg ?? "HS256" })
    .sign(new TextEncoder().encode(SECRET));
}

/* ------------------------------------------------------------------ 빈 토큰 · 알고리즘 */

Deno.test("빈 토큰은 즉시 거부", async () => {
  const e = await assertRejects(() => verifyJwt("", hsSettings()), JWTValidationError);
  assertEquals(e.message, "토큰이 비어 있습니다.");
});

Deno.test("화이트리스트 밖 알고리즘은 거부하며 알고리즘명을 알려준다", async () => {
  const e = await assertRejects(
    () => verifyJwt("x.y.z", hsSettings({ supabaseJwtAlgorithm: "none" })),
    JWTValidationError,
  );
  assertEquals(e.message, "JWT 알고리즘 'none' 은 지원되지 않습니다.");

  // PS256 은 원본 화이트리스트에 없다.
  const e2 = await assertRejects(
    () => verifyJwt("x.y.z", hsSettings({ supabaseJwtAlgorithm: "PS256" })),
    JWTValidationError,
  );
  assertEquals(e2.message, "JWT 알고리즘 'PS256' 은 지원되지 않습니다.");
});

Deno.test("대칭 알고리즘인데 secret 이 없으면 설정 오류", async () => {
  const e = await assertRejects(
    () => verifyJwt("x.y.z", hsSettings({ supabaseJwtSecret: null })),
    JWTValidationError,
  );
  assertEquals(e.message, "JWT secret 이 설정되지 않았습니다 (서버 설정 오류).");
});

Deno.test("비대칭 알고리즘인데 JWKS URL 이 없으면 설정 오류", async () => {
  const e = await assertRejects(
    () =>
      verifyJwt("x.y.z", {
        supabaseJwtAlgorithm: "ES256",
        supabaseJwtSecret: null,
        supabaseJwksUrl: null,
      }),
    JWTValidationError,
  );
  assertEquals(e.message, "JWKS URL 이 설정되지 않았습니다 (비대칭 JWT 검증 불가).");
});

/* ------------------------------------------------------------------ HS256 경로 */

Deno.test("HS256 — 정상 토큰에서 sub/email 추출", async () => {
  const token = await hsToken({ email: "a@b.com" });
  const v = await verifyJwt(token, hsSettings());
  assertEquals(v.userId, SUB);
  assertEquals(v.email, "a@b.com");
});

Deno.test("HS256 — email 이 없거나 문자열이 아니면 null", async () => {
  assertEquals((await verifyJwt(await hsToken(), hsSettings())).email, null);
  assertEquals((await verifyJwt(await hsToken({ email: 123 }), hsSettings())).email, null);
  assertEquals((await verifyJwt(await hsToken({ email: null }), hsSettings())).email, null);
});

Deno.test("HS256 — 만료 토큰", async () => {
  const now = Math.floor(Date.now() / 1000);
  const expired = await hsToken({ exp: now - 10 });
  const e = await assertRejects(() => verifyJwt(expired, hsSettings()), JWTValidationError);
  assertEquals(e.message, "토큰이 만료되었습니다.");
});

Deno.test("HS256 — audience 불일치", async () => {
  const wrongAud = await hsToken({ aud: "anon" });
  const e = await assertRejects(() => verifyJwt(wrongAud, hsSettings()), JWTValidationError);
  assertEquals(e.message, "토큰 audience 가 유효하지 않습니다.");
});

Deno.test("HS256 — aud 가 배열이어도 authenticated 가 들어 있으면 통과", async () => {
  const v = await verifyJwt(await hsToken({ aud: ["authenticated", "other"] }), hsSettings());
  assertEquals(v.userId, SUB);
});

Deno.test("HS256 — 서명이 다르면 거부", async () => {
  const token = await hsToken();
  const e = await assertRejects(
    () => verifyJwt(token, hsSettings({ supabaseJwtSecret: "another-secret-at-least-32-bytes!!" })),
    JWTValidationError,
  );
  assertEquals(e.message, "토큰 검증에 실패했습니다.");
});

Deno.test("HS256 — 필수 claim(exp/sub) 누락은 거부", async () => {
  for (const missing of ["exp", "sub"]) {
    const token = await hsToken({ [missing]: undefined });
    const e = await assertRejects(
      () => verifyJwt(token, hsSettings()),
      JWTValidationError,
      undefined,
      `${missing} 누락`,
    );
    assertEquals(e.message, "토큰 검증에 실패했습니다.");
  }
});

Deno.test("HS256 — aud claim 자체가 없으면 거부", async () => {
  const noAud = await hsToken({ aud: undefined });
  const e = await assertRejects(() => verifyJwt(noAud, hsSettings()), JWTValidationError);
  assertEquals(e.message, "토큰 검증에 실패했습니다.");
});

Deno.test("HS256 — 형식이 JWT 가 아니면 거부", async () => {
  for (const bad of ["not-a-jwt", "a.b", "a.b.c.d", "...."]) {
    const e = await assertRejects(() => verifyJwt(bad, hsSettings()), JWTValidationError, undefined, bad);
    assertEquals(e.message, "토큰 검증에 실패했습니다.");
  }
});

Deno.test("HS256 — 설정과 다른 알고리즘으로 서명된 토큰은 거부", async () => {
  // 설정은 HS256 인데 토큰 헤더가 HS512 인 경우. algorithms 를 설정값 하나로 고정해야 막힌다.
  const token = await hsToken({}, { alg: "HS512" });
  const e = await assertRejects(() => verifyJwt(token, hsSettings()), JWTValidationError);
  assertEquals(e.message, "토큰 검증에 실패했습니다.");
});

Deno.test("HS256 — sub 가 빈 문자열이면 사용자 식별 불가", async () => {
  // 서명·만료·audience 는 통과하지만 sub 가 쓸 수 없는 값인 경우.
  const emptySub = await hsToken({ sub: "" });
  const e = await assertRejects(() => verifyJwt(emptySub, hsSettings()), JWTValidationError);
  // jose 의 requiredClaims 는 빈 문자열을 "있음" 으로 보므로 우리 쪽 검사가 잡아야 한다.
  assertEquals(e.message, "토큰에 사용자 식별자(sub)가 없습니다.");
});

/* ------------------------------------------------------------------ ES256 / JWKS 경로 (운영) */

Deno.test("ES256 — JWKS 로 검증하고 sub 를 뽑는다", async () => {
  const { publicKey, privateKey } = await generateKeyPair("ES256", { extractable: true });
  const jwk = await exportJWK(publicKey);
  const jwks = { keys: [{ ...jwk, alg: "ES256", use: "sig", kid: "test-kid" }] };

  const server = Deno.serve({ port: 0, onListen: () => {} }, () => Response.json(jwks));
  try {
    const url = `http://localhost:${(server.addr as Deno.NetAddr).port}/jwks.json`;
    const token = await new SignJWT({ sub: SUB, aud: AUD, email: "e@f.com" })
      .setProtectedHeader({ alg: "ES256", kid: "test-kid" })
      .setExpirationTime("1h")
      .sign(privateKey);

    const v = await verifyJwt(token, {
      supabaseJwtAlgorithm: "ES256",
      supabaseJwtSecret: null,
      supabaseJwksUrl: url,
    });
    assertEquals(v.userId, SUB);
    assertEquals(v.email, "e@f.com");
  } finally {
    await server.shutdown();
  }
});

Deno.test("ES256 — JWKS 를 못 받으면 조회 실패 메시지", async () => {
  const e = await assertRejects(
    () =>
      verifyJwt("x.y.z", {
        supabaseJwtAlgorithm: "ES256",
        supabaseJwtSecret: null,
        // 사용하지 않는 포트 — 연결 자체가 실패한다.
        supabaseJwksUrl: "http://localhost:1/jwks.json",
      }),
    JWTValidationError,
  );
  assertEquals(e.message, "JWKS 공개키 조회에 실패했습니다.");
});

Deno.test("ES256 — kid 가 JWKS 에 없으면 조회 실패", async () => {
  const { publicKey, privateKey } = await generateKeyPair("ES256", { extractable: true });
  const jwk = await exportJWK(publicKey);
  const jwks = { keys: [{ ...jwk, alg: "ES256", use: "sig", kid: "known-kid" }] };

  const server = Deno.serve({ port: 0, onListen: () => {} }, () => Response.json(jwks));
  try {
    const url = `http://localhost:${(server.addr as Deno.NetAddr).port}/jwks.json`;
    const token = await new SignJWT({ sub: SUB, aud: AUD })
      .setProtectedHeader({ alg: "ES256", kid: "unknown-kid" })
      .setExpirationTime("1h")
      .sign(privateKey);

    const e = await assertRejects(
      () =>
        verifyJwt(token, {
          supabaseJwtAlgorithm: "ES256",
          supabaseJwtSecret: null,
          supabaseJwksUrl: url,
        }),
      JWTValidationError,
    );
    assertEquals(e.message, "JWKS 공개키 조회에 실패했습니다.");
  } finally {
    await server.shutdown();
  }
});
