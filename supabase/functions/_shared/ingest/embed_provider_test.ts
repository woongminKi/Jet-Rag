/**
 * BGE-M3 DeepInfra 어댑터 계약.
 *
 * 여기서 틀리면 **엉뚱한 청크에 벡터가 박히거나**(순서), 쿼터를 태우거나(재시도 정책),
 * 차원이 안 맞아 pgvector 가 거부한다. 전부 조용히 나빠지는 종류라 계약으로 고정한다.
 */

import { assertEquals, assertRejects } from "@std/assert";
import {
  DENSE_DIM,
  EMBED_URL,
  embedBatch,
  MODEL_SLUG,
  parseBatchResponse,
  parseRetryAfter,
} from "./embed_provider.ts";

const vec = (seed: number) => Array.from({ length: DENSE_DIM }, (_, i) => (seed + i) % 7);

function res(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), { status, headers });
}

// ---------------------------------------------------------------------------
// parseBatchResponse
// ---------------------------------------------------------------------------

Deno.test("배치 응답을 index 로 정렬한다 — 순서가 틀리면 엉뚱한 청크에 박힌다", () => {
  const out = parseBatchResponse({
    data: [
      { embedding: vec(2), index: 2 },
      { embedding: vec(0), index: 0 },
      { embedding: vec(1), index: 1 },
    ],
  }, 3);
  assertEquals(out[0][0], 0);
  assertEquals(out[1][0], 1);
  assertEquals(out[2][0], 2);
});

Deno.test("index 가 없으면 받은 순서를 유지한다", () => {
  const out = parseBatchResponse({ data: [{ embedding: vec(5) }, { embedding: vec(9) }] }, 2);
  assertEquals(out[0][0], 5);
  assertEquals(out[1][0], 9 % 7);
});

Deno.test("길이가 다르면 던진다", () => {
  let threw = false;
  try {
    parseBatchResponse({ data: [{ embedding: vec(0) }] }, 2);
  } catch (e) {
    threw = true;
    assertEquals((e as Error).message.includes("길이 불일치"), true);
  }
  assertEquals(threw, true);
});

Deno.test("차원이 다르면 던진다 — pgvector 가 거부하기 전에 잡는다", () => {
  let threw = false;
  try {
    parseBatchResponse({ data: [{ embedding: [1, 2, 3], index: 0 }] }, 1);
  } catch (e) {
    threw = true;
    assertEquals((e as Error).message.includes("차원 불일치"), true);
  }
  assertEquals(threw, true);
});

Deno.test("data 키가 없으면 던진다", () => {
  let threw = false;
  try {
    parseBatchResponse({ oops: 1 }, 1);
  } catch (e) {
    threw = true;
    assertEquals((e as Error).message.includes("스키마"), true);
  }
  assertEquals(threw, true);
});

// ---------------------------------------------------------------------------
// parseRetryAfter — 원본은 `float(int(raw))` 라 정수만 받는다
// ---------------------------------------------------------------------------

Deno.test("Retry-After: 정수 초", () => {
  assertEquals(parseRetryAfter("7", 0), 7);
});

Deno.test("Retry-After: 60초로 자른다 (악의적 헤더 방어)", () => {
  assertEquals(parseRetryAfter("99999", 0), 60);
});

Deno.test("Retry-After: 0·음수는 무시", () => {
  assertEquals(parseRetryAfter("0", 0), null);
  assertEquals(parseRetryAfter("-5", 0), null);
});

Deno.test("Retry-After: HTTP-date", () => {
  const now = Date.parse("Tue, 01 Jan 2030 00:00:00 GMT");
  assertEquals(parseRetryAfter("Tue, 01 Jan 2030 00:00:30 GMT", now), 30);
});

Deno.test("Retry-After: 과거 날짜는 무시", () => {
  const now = Date.parse("Tue, 01 Jan 2030 00:01:00 GMT");
  assertEquals(parseRetryAfter("Tue, 01 Jan 2030 00:00:00 GMT", now), null);
});

Deno.test("Retry-After: 없거나 파싱 불가면 null", () => {
  assertEquals(parseRetryAfter(null, 0), null);
  assertEquals(parseRetryAfter("나중에", 0), null);
});

// ---------------------------------------------------------------------------
// embedBatch — 요청 모양과 재시도
// ---------------------------------------------------------------------------

Deno.test("요청 모양 — URL·모델·헤더", async () => {
  let seen: { url: string; init: RequestInit } | null = null;
  await embedBatch(["가", "나"], {
    token: "T",
    fetchImpl: ((url: string, init: RequestInit) => {
      seen = { url, init };
      return Promise.resolve(
        res({ data: [{ embedding: vec(0), index: 0 }, { embedding: vec(1), index: 1 }] }),
      );
    }) as unknown as typeof fetch,
  });
  assertEquals(seen!.url, EMBED_URL);
  assertEquals(seen!.init.method, "POST");
  assertEquals((seen!.init.headers as Record<string, string>)["Authorization"], "Bearer T");
  assertEquals(JSON.parse(seen!.init.body as string), { model: MODEL_SLUG, input: ["가", "나"] });
});

Deno.test("빈 입력은 호출하지 않는다", async () => {
  let called = 0;
  const out = await embedBatch([], {
    token: "T",
    fetchImpl: (() => {
      called++;
      return Promise.resolve(res({}));
    }) as unknown as typeof fetch,
  });
  assertEquals(out, []);
  assertEquals(called, 0);
});

Deno.test("5xx 는 재시도하고 3회에서 포기한다", async () => {
  let n = 0;
  const delays: number[] = [];
  await assertRejects(
    () =>
      embedBatch(["가"], {
        token: "T",
        fetchImpl: (() => {
          n++;
          return Promise.resolve(res({ e: 1 }, 503));
        }) as unknown as typeof fetch,
        sleep: (ms) => (delays.push(ms), Promise.resolve()),
        jitter: () => 0,
      }),
    Error,
    "503",
  );
  assertEquals(n, 3);
  // backoff 5s × 2^(n-1) — 마지막 시도 뒤에는 안 잔다.
  assertEquals(delays, [5000, 10000]);
});

Deno.test("429 는 Retry-After 를 따른다", async () => {
  let n = 0;
  const delays: number[] = [];
  await assertRejects(() =>
    embedBatch(["가"], {
      token: "T",
      fetchImpl: (() => {
        n++;
        return Promise.resolve(res({ e: 1 }, 429, { "Retry-After": "3" }));
      }) as unknown as typeof fetch,
      sleep: (ms) => (delays.push(ms), Promise.resolve()),
      jitter: () => 0,
      now: () => 0,
    })
  );
  assertEquals(delays, [3000, 3000]);
});

Deno.test("**401 은 재시도하지 않는다** — 쿼터를 태우지 않는다", async () => {
  let n = 0;
  await assertRejects(
    () =>
      embedBatch(["가"], {
        token: "bad",
        fetchImpl: (() => {
          n++;
          return Promise.resolve(res({ e: "unauthorized" }, 401));
        }) as unknown as typeof fetch,
        sleep: () => Promise.resolve(),
      }),
    Error,
    "401",
  );
  assertEquals(n, 1);
});

Deno.test("파싱 실패는 재시도하지 않는다 — 다시 불러도 같다", async () => {
  let n = 0;
  await assertRejects(() =>
    embedBatch(["가"], {
      token: "T",
      fetchImpl: (() => {
        n++;
        return Promise.resolve(res({ data: [{ embedding: [1, 2], index: 0 }] }));
      }) as unknown as typeof fetch,
      sleep: () => Promise.resolve(),
    })
  );
  assertEquals(n, 1);
});

Deno.test("네트워크 오류는 재시도한다", async () => {
  let n = 0;
  await assertRejects(() =>
    embedBatch(["가"], {
      token: "T",
      fetchImpl: (() => {
        n++;
        return Promise.reject(new TypeError("network"));
      }) as unknown as typeof fetch,
      sleep: () => Promise.resolve(),
      jitter: () => 0,
    })
  );
  assertEquals(n, 3);
});

Deno.test("재시도 후 성공하면 결과를 돌려준다", async () => {
  let n = 0;
  const out = await embedBatch(["가"], {
    token: "T",
    fetchImpl: (() => {
      n++;
      return Promise.resolve(
        n === 1 ? res({ e: 1 }, 500) : res({ data: [{ embedding: vec(3), index: 0 }] }),
      );
    }) as unknown as typeof fetch,
    sleep: () => Promise.resolve(),
    jitter: () => 0,
  });
  assertEquals(n, 2);
  assertEquals(out.length, 1);
  assertEquals(out[0].length, DENSE_DIM);
});
