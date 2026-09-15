/**
 * 계량 v2 의 두 축을 고정한다 — **월 경계**와 **용량 게이트**.
 *
 * 월 경계는 KST 다. UTC 로 재면 매달 1일 00:00~08:59 KST 의 Vision 사용이 전달로 잡혀
 * "이번 달 0장" 인데도 한도 초과가 나거나 그 반대가 된다. 한 줄 틀리면 사용자가 보는
 * 숫자가 바로 틀린다.
 *
 * 용량 게이트는 **fail-open 지점이 세 군데**(quota 비활성 / 플랜 없음·무제한 / 조회 실패)라
 * 하나라도 뒤집히면 장애 때 정상 사용자를 402 로 막는다. 경계(`==` 한도)는 통과다.
 */

import { assertEquals } from "@std/assert";

import { kstMonthStartIso, makeStorageCheck, type QuotaSettings } from "./quota.ts";

const ON: QuotaSettings = {
  authEnabled: true,
  quotaEnforcementEnabled: true,
  ownerUserId: "owner",
};
const USER = { userId: "u1", isAuthenticated: true };

/** `from("subscriptions"|"plans")` 와 `rpc("storage_bytes_used")` 만 흉내낸다. */
// deno-lint-ignore no-explicit-any
function fakeClient(opts: { limit: number | null; used: number | null }): any {
  // deno-lint-ignore no-explicit-any
  const q = (rows: unknown): any => {
    // deno-lint-ignore no-explicit-any
    const o: any = {
      eq: () => o,
      limit: () => o,
      select: () => o,
      then: (res: (v: unknown) => void) => res({ data: rows, error: null }),
    };
    return o;
  };
  return {
    rpc: (fn: string) =>
      fn === "storage_bytes_used" && opts.used === null
        ? Promise.resolve({ data: null, error: { message: "RPC 실패" } })
        : Promise.resolve({ data: opts.used, error: null }),
    from(table: string) {
      if (table === "subscriptions") {
        return { select: () => q([{ plan_code: "free", status: "active" }]) };
      }
      if (table === "plans") {
        return opts.limit === null ? { select: () => q([]) } : {
          select: () =>
            q([{
              code: "free",
              answers_per_day: 5,
              storage_bytes_limit: opts.limit,
              vision_pages_per_month: 100,
            }]),
        };
      }
      return { select: () => q([]) };
    },
  };
}

Deno.test("kstMonthStartIso — KST 1일 00:00 = 전날 15:00 UTC", () => {
  // 2026-09-15 10:00 KST = 2026-09-15T01:00Z
  assertEquals(kstMonthStartIso(Date.parse("2026-09-15T01:00:00Z")), "2026-08-31T15:00:00.000Z");
  // 2026-09-01 00:30 KST = 2026-08-31T15:30Z 는 9월이다
  assertEquals(kstMonthStartIso(Date.parse("2026-08-31T15:30:00Z")), "2026-08-31T15:00:00.000Z");
  // 2026-08-31 23:30 KST = 2026-08-31T14:30Z 는 8월이다
  assertEquals(kstMonthStartIso(Date.parse("2026-08-31T14:30:00Z")), "2026-07-31T15:00:00.000Z");
});

Deno.test("makeStorageCheck — quota 가 안 걸리면 조회 없이 null", async () => {
  const client = fakeClient({ limit: 100, used: 100 });
  // 소유자 면제.
  assertEquals(await makeStorageCheck(client, { userId: "owner", isAuthenticated: true }, ON)(1), null);
  // 익명.
  assertEquals(await makeStorageCheck(client, { userId: "u1", isAuthenticated: false }, ON)(1), null);
  // 스위치 off 둘.
  assertEquals(
    await makeStorageCheck(client, USER, { ...ON, quotaEnforcementEnabled: false })(1),
    null,
  );
  assertEquals(await makeStorageCheck(client, USER, { ...ON, authEnabled: false })(1), null);
});

Deno.test("makeStorageCheck — 플랜 없음·한도 0·조회 실패는 전부 fail-open(null)", async () => {
  // plans 행 없음 → getEffectivePlan 이 null.
  assertEquals(await makeStorageCheck(fakeClient({ limit: null, used: 0 }), USER, ON)(1), null);
  // 한도 0 = 무제한.
  assertEquals(await makeStorageCheck(fakeClient({ limit: 0, used: 0 }), USER, ON)(1), null);
  // RPC 실패.
  assertEquals(await makeStorageCheck(fakeClient({ limit: 100, used: null }), USER, ON)(1), null);
});

Deno.test("makeStorageCheck — used + size 가 한도를 넘으면 allowed false, 같으면 true", async () => {
  const check = makeStorageCheck(fakeClient({ limit: 100, used: 90 }), USER, ON);
  // 경계 — 딱 맞으면 통과다(`<=`).
  assertEquals(await check(10), { allowed: true, usedBytes: 90, limitBytes: 100 });
  assertEquals(await check(11), { allowed: false, usedBytes: 90, limitBytes: 100 });
});
