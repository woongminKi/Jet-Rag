/**
 * 결제 — 대조에서 실제로 갈렸던 것과 **돈이 걸린 계약**만 고정한다.
 *
 * 전체 대조는 `api/scripts/verify_billing_parity.py`(69건, DB write 순서까지)가 하지만
 * Python venv 가 있어야 돈다. 여기 있는 건 venv 없이도 도는 회귀 방지선이다.
 */

import { assertEquals, assertRejects } from "@std/assert";

import { fernetDecrypt, fernetEncrypt, FernetError } from "./fernet.ts";
import { addOneMonth, formatIso, parseIso, utcParts } from "./pydate.ts";
import {
  handleBillingRun,
  handleSubscribeApprove,
  type PaymentsRouteDeps,
} from "./routes.ts";

// 이 파일 안에서만 쓰는 테스트용 키. 운영 키가 아니다.
const KEY = "0123456789abcdef0123456789abcdef0123456789ab";

Deno.test("Fernet — 자기 토큰을 되푼다", async () => {
  const key = btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(32))))
    .replace(/\+/g, "-").replace(/\//g, "_");
  for (const s of ["", "a", "SID-1234", "0123456789abcdef", "한글", "x".repeat(500)]) {
    assertEquals(await fernetDecrypt(key, await fernetEncrypt(key, s)), s);
  }
});

Deno.test("Fernet — 위조·잘못된 키를 거절한다", async () => {
  const key = btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(32))))
    .replace(/\+/g, "-").replace(/\//g, "_");
  const token = await fernetEncrypt(key, "SID-1234");

  // MAC 1 비트만 뒤집어도 거절해야 한다.
  const raw = Uint8Array.from(atob(token.replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0));
  raw[raw.length - 1] ^= 0xff;
  const forged = btoa(String.fromCharCode(...raw)).replace(/\+/g, "-").replace(/\//g, "_");
  await assertRejects(() => fernetDecrypt(key, forged), FernetError);

  // 32 바이트가 아닌 키는 애초에 거절한다.
  await assertRejects(() => fernetEncrypt("", "x"), FernetError);
  await assertRejects(() => fernetEncrypt(KEY.slice(0, 10), "x"), FernetError);
});

Deno.test("월 1회 주기 — 말일은 clamp 한다", () => {
  const cases: [string, string][] = [
    ["2026-01-31T12:00:00+00:00", "2026-02-28T12:00:00+00:00"],
    ["2024-01-31T12:00:00+00:00", "2024-02-29T12:00:00+00:00"], // 윤년
    ["2026-03-31T00:00:00+00:00", "2026-04-30T00:00:00+00:00"],
    ["2026-12-15T09:30:00+00:00", "2027-01-15T09:30:00+00:00"], // 연도 넘김
    // 마이크로초 표기 — Python `isoformat()` 은 0 이면 생략, 아니면 **항상 6 자리**다.
    ["2026-05-15T10:20:30.789+00:00", "2026-06-15T10:20:30.789000+00:00"],
  ];
  for (const [input, expected] of cases) {
    assertEquals(formatIso(addOneMonth(parseIso(input)!)), expected, input);
  }
});

Deno.test("불가능한 날짜는 거절한다 — Python `fromisoformat` 과 같다", () => {
  // 정규식만으로 통과시키면 조용히 틀린 값을 계산한다. 대조에서 걸린 자리다.
  for (const bad of ["2026-02-29T00:00:00+00:00", "2026-13-01T00:00:00+00:00", "2026-04-31T00:00:00+00:00"]) {
    let threw = false;
    try {
      parseIso(bad);
    } catch {
      threw = true;
    }
    assertEquals(threw, true, bad);
  }
  assertEquals(parseIso(null), null);
  assertEquals(parseIso(""), null);
});

Deno.test("utcParts — 밀리초는 마이크로초 6자리로 늘어난다", () => {
  assertEquals(formatIso(utcParts(1_772_000_000_000)), "2026-02-25T06:13:20+00:00");
  assertEquals(formatIso(utcParts(1_772_000_000_123)), "2026-02-25T06:13:20.123000+00:00");
});

function deps(over: Partial<PaymentsRouteDeps["settings"]> = {}): PaymentsRouteDeps {
  return {
    // 이 테스트들은 게이트에서 끝나 DB 에 닿지 않는다 — 그게 검증 대상이기도 하다.
    // deno-lint-ignore no-explicit-any
    client: {} as any,
    settings: {
      paymentProvider: "kakaopay",
      kakaopaySecretKey: "sk_test",
      kakaopayCid: "TCSUBSCRIP",
      billingKeyEncryptionKey: KEY,
      billingCronSecret: "cron-secret",
      billingRedirectBase: "https://example.test",
      ...over,
    },
  };
}

Deno.test("결제 기능 게이트 — secret 이 하나만 없어도 503", async () => {
  const off1 = await handleSubscribeApprove(deps({ kakaopaySecretKey: "" }), "u1", "PG");
  assertEquals(off1.status, 503);
  const off2 = await handleSubscribeApprove(deps({ billingKeyEncryptionKey: "" }), "u1", "PG");
  assertEquals(off2.status, 503);
});

Deno.test("pg_token 검증이 게이트보다 먼저다 — 없으면 422", async () => {
  // 원본은 `Query(..., min_length=1)` 이라 FastAPI 가 본문 실행 전에 422 를 낸다.
  assertEquals((await handleSubscribeApprove(deps(), "u1", null)).status, 422);
  assertEquals((await handleSubscribeApprove(deps(), "u1", "")).status, 422);
  // secret 이 없어도 422 가 먼저다(원본의 검증 순서).
  assertEquals(
    (await handleSubscribeApprove(deps({ kakaopaySecretKey: "" }), "u1", null)).status,
    422,
  );
});

Deno.test("`/billing/run` — secret 게이트가 결제보다 먼저다", async () => {
  // secret 미설정 = 기능이 꺼진 상태. 이게 기본값이고 안전한 쪽이다.
  assertEquals((await handleBillingRun(deps({ billingCronSecret: "" }), "x")).status, 503);
  // 불일치는 401. 길이가 달라도 상수 시간 비교가 false 를 낸다.
  assertEquals((await handleBillingRun(deps(), "nope")).status, 401);
  assertEquals((await handleBillingRun(deps(), "")).status, 401);
  assertEquals((await handleBillingRun(deps(), "cron-secret!")).status, 401);
  // secret 은 맞지만 결제 키가 없으면 503 — **DB 에 닿기 전에** 끊긴다.
  assertEquals(
    (await handleBillingRun(deps({ kakaopaySecretKey: "" }), "cron-secret")).status,
    503,
  );
});
