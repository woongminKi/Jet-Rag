/**
 * `/me/devices` 핸들러 계약.
 *
 * 여기서 고정하는 것:
 * - 이름 검증(빈 값·61자)과 상한(20개)이 **DB 를 건드리기 전에** 끊는지
 * - 발급 시 DB 에 들어가는 건 해시·접두어뿐 — **원문 토큰은 저장되지 않는다**
 * - 폐기 3 갈래 (갱신됨 / 이미 폐기됨 / 없음·남의 것)
 */

import { assertEquals, assertRejects } from "@std/assert";
import { createDevice, type DeviceView, listDevices, revokeDevice } from "./devices.ts";
import { MeHttpError } from "./pipeline.ts";

const USER = "11111111-1111-1111-1111-111111111111";

function device(over: Partial<DeviceView> = {}): DeviceView {
  return {
    id: "d1",
    name: "회사 노트북",
    token_prefix: "jrd_abcd",
    scopes: ["ingest"],
    created_at: "2026-09-15T00:00:00Z",
    last_used_at: null,
    revoked_at: null,
    ...over,
  };
}

interface FakeOpts {
  /** `listDevices` 의 종결자(`order`)가 돌려줄 행. */
  list?: DeviceView[];
  /** `update(...).select()` 가 돌려줄 행 — 폐기가 실제로 갱신한 행. */
  updated?: DeviceView[];
  /** 폐기 실패 후 2차 조회(`limit`)가 돌려줄 행. */
  existing?: DeviceView[];
  /** `insert(...).select().single()` 이 돌려줄 행. */
  inserted?: DeviceView;
}

function fakeClient(opts: FakeOpts = {}) {
  const inserts: Record<string, unknown>[] = [];
  const updates: Record<string, unknown>[] = [];
  const filters: [string, unknown][] = [];

  const client = {
    from(_table: string) {
      const api = {
        select: () => api,
        eq: (col: string, val: unknown) => {
          filters.push([col, val]);
          return api;
        },
        is: () => api,
        // 읽기 두 경로는 종결자가 다르다 — 목록은 order, 폐기 2차 조회는 limit.
        order: () => Promise.resolve({ data: opts.list ?? [], error: null }),
        limit: () => Promise.resolve({ data: opts.existing ?? [], error: null }),
        insert(row: Record<string, unknown>) {
          inserts.push(row);
          return {
            select: () => ({
              single: () => Promise.resolve({ data: opts.inserted ?? device(), error: null }),
            }),
          };
        },
        update(row: Record<string, unknown>) {
          updates.push(row);
          const chain = {
            eq: (col: string, val: unknown) => {
              filters.push([col, val]);
              return chain;
            },
            is: () => chain,
            select: () => Promise.resolve({ data: opts.updated ?? [], error: null }),
          };
          return chain;
        },
      };
      return api;
    },
  };
  // deno-lint-ignore no-explicit-any
  return { client: client as any, inserts, updates, filters };
}

/* ------------------------------------------------------------------ 목록 */

Deno.test("listDevices — 본인 행만 최신순", async () => {
  const f = fakeClient({ list: [device({ id: "d2" }), device()] });
  const rows = await listDevices(f.client, USER);
  assertEquals(rows.map((r) => r.id), ["d2", "d1"]);
  assertEquals(f.filters, [["user_id", USER]]);
});

/* ------------------------------------------------------------------ 발급 */

Deno.test("createDevice — 빈 이름은 422, DB 를 건드리지 않는다", async () => {
  const f = fakeClient();
  const e = await assertRejects(() => createDevice(f.client, USER, "   "), MeHttpError);
  assertEquals(e.status, 422);
  assertEquals(f.inserts.length, 0);
});

Deno.test("createDevice — 61자 이름은 422", async () => {
  const f = fakeClient();
  const e = await assertRejects(() => createDevice(f.client, USER, "가".repeat(61)), MeHttpError);
  assertEquals(e.status, 422);
  assertEquals(f.inserts.length, 0);
});

Deno.test("createDevice — 이름이 문자열이 아니어도 422", async () => {
  const f = fakeClient();
  const e = await assertRejects(() => createDevice(f.client, USER, undefined), MeHttpError);
  assertEquals(e.status, 422);
});

Deno.test("createDevice — 활성 20개면 409", async () => {
  const list = Array.from({ length: 20 }, (_, i) => device({ id: `d${i}` }));
  const f = fakeClient({ list });
  const e = await assertRejects(() => createDevice(f.client, USER, "21번째"), MeHttpError);
  assertEquals(e.status, 409);
  assertEquals(f.inserts.length, 0);
});

Deno.test("createDevice — 폐기된 기기는 상한에 안 센다", async () => {
  // 20개가 있어도 전부 폐기됐으면 통과해야 한다.
  const list = Array.from(
    { length: 20 },
    (_, i) => device({ id: `d${i}`, revoked_at: "2026-09-01T00:00:00Z" }),
  );
  const f = fakeClient({ list });
  const r = await createDevice(f.client, USER, "새 기기");
  assertEquals(r.token.startsWith("jrd_"), true);
});

Deno.test("createDevice — 원문 토큰은 저장하지 않는다 (해시·접두어만)", async () => {
  const f = fakeClient({ list: [] });
  const r = await createDevice(f.client, USER, "  회사 노트북  ");
  assertEquals(r.token.startsWith("jrd_"), true);

  assertEquals(f.inserts.length, 1);
  const row = f.inserts[0];
  assertEquals(row["user_id"], USER);
  // 앞뒤 공백은 잘라서 넣는다.
  assertEquals(row["name"], "회사 노트북");
  assertEquals(typeof row["token_hash"], "string");
  assertEquals((row["token_hash"] as string).length, 64);
  assertEquals(row["token_prefix"], r.token.slice(0, 8));
  // **원문이 어떤 컬럼에도 없어야 한다.** 여기가 뚫리면 DB 유출 = 토큰 유출이다.
  assertEquals(Object.values(row).includes(r.token), false);
  assertEquals(JSON.stringify(row).includes(r.token.slice(8)), false);
});

/* ------------------------------------------------------------------ 폐기 */

Deno.test("revokeDevice — 갱신된 행을 돌려준다", async () => {
  const revoked = device({ revoked_at: "2026-09-15T01:00:00Z" });
  const f = fakeClient({ updated: [revoked] });
  const r = await revokeDevice(f.client, USER, "d1");
  assertEquals(r.revoked_at, "2026-09-15T01:00:00Z");
  assertEquals(f.updates.length, 1);
  // 소유자 한정 — id 만으로 지우면 남의 기기를 폐기할 수 있다.
  assertEquals(f.filters, [["id", "d1"], ["user_id", USER]]);
});

Deno.test("revokeDevice — 이미 폐기된 본인 기기는 그 행을 돌려준다 (멱등)", async () => {
  const already = device({ revoked_at: "2026-09-10T00:00:00Z" });
  const f = fakeClient({ updated: [], existing: [already] });
  const r = await revokeDevice(f.client, USER, "d1");
  assertEquals(r.revoked_at, "2026-09-10T00:00:00Z");
});

Deno.test("revokeDevice — 없는 id·남의 기기는 404 (존재 위장)", async () => {
  const f = fakeClient({ updated: [], existing: [] });
  const e = await assertRejects(() => revokeDevice(f.client, USER, "d9"), MeHttpError);
  assertEquals(e.status, 404);
  assertEquals(e.detail, "Not Found");
});
