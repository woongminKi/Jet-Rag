/**
 * 분당 업로드 남용 방지의 경계를 고정한다.
 *
 * 이 게이트만은 **owner 포함 전원**에게 걸린다 — 오작동하는 자동 수집 에이전트가 같은
 * 파일을 무한 루프로 올리는 상황을 막는 장치이고, 그런 에이전트는 대개 소유자 토큰으로
 * 돈다. 반대로 RPC 가 흔들릴 때 통과시키지 못하면 정상 업로드가 전부 429 가 된다.
 * 경계(60 통과 / 61 차단)와 fail-open 둘 다 여기서 고정한다.
 */

import { assertEquals } from "@std/assert";

import { enforceUploadBurst, minuteFloorIso, RateLimitError, UPLOAD_BURST_PER_MINUTE } from "./rate_limit.ts";

interface Call {
  fn: string;
  args: Record<string, unknown>;
}

/** `rpc` 만 흉내낸다. `count` 가 `null` 이면 RPC 실패로 만든다. */
function fakeClient(count: number | null) {
  const calls: Call[] = [];
  const client = {
    rpc(fn: string, args: Record<string, unknown>) {
      calls.push({ fn, args });
      return count === null
        ? Promise.resolve({ data: null, error: { message: "RPC 실패" } })
        : Promise.resolve({ data: count, error: null });
    },
  };
  return { client, calls };
}

async function run(count: number | null, nowMs = 0): Promise<number | null> {
  const { client } = fakeClient(count);
  try {
    // deno-lint-ignore no-explicit-any
    await enforceUploadBurst({ userId: "u1" }, { client: client as any, now: () => nowMs });
    return null;
  } catch (e) {
    return e instanceof RateLimitError ? e.status : -1;
  }
}

Deno.test("minuteFloorIso — 초·밀리초를 버린다", () => {
  assertEquals(minuteFloorIso(Date.parse("2026-09-15T01:23:45.678Z")), "2026-09-15T01:23:00.000Z");
  assertEquals(minuteFloorIso(Date.parse("2026-09-15T01:24:00.000Z")), "2026-09-15T01:24:00.000Z");
  // 음수 쪽(1970 이전)으로 새지 않는다 — floor 라 과거로 내려간다.
  assertEquals(minuteFloorIso(0), "1970-01-01T00:00:00.000Z");
});

Deno.test("분당 60건까지는 통과한다", async () => {
  assertEquals(await run(1), null);
  assertEquals(await run(UPLOAD_BURST_PER_MINUTE), null);
});

Deno.test("61번째는 429 — 402 가 아니다(플랜이 아니라 남용 방지다)", async () => {
  assertEquals(await run(UPLOAD_BURST_PER_MINUTE + 1), 429);
  assertEquals(await run(1000), 429);
});

Deno.test("RPC 실패·비숫자 응답은 fail-open", async () => {
  assertEquals(await run(null), null);
  const { client } = fakeClient(0);
  // 숫자가 아닌 응답도 막지 않는다.
  const weird = {
    rpc: () => Promise.resolve({ data: "많음", error: null }),
  };
  // deno-lint-ignore no-explicit-any
  await enforceUploadBurst({ userId: "u1" }, { client: weird as any });
  // deno-lint-ignore no-explicit-any
  await enforceUploadBurst({ userId: "u1" }, { client: client as any });
});

Deno.test("RPC 인자는 user_id 와 분 절삭 시각이다", async () => {
  const { client, calls } = fakeClient(1);
  await enforceUploadBurst(
    { userId: "u1" },
    // deno-lint-ignore no-explicit-any
    { client: client as any, now: () => Date.parse("2026-09-15T01:23:45Z") },
  );
  assertEquals(calls, [{
    fn: "increment_upload_burst",
    args: { p_user_key: "u1", p_minute: "2026-09-15T01:23:00.000Z" },
  }]);
});
