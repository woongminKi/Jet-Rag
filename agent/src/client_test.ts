import { assertEquals, assertRejects } from "@std/assert";
import { ApiClient, ApiError, backoffMs, parseRetryAfterMs, RETRY } from "./client.ts";

interface Recorded {
  method: string;
  path: string;
  auth: string | null;
  ua: string | null;
  body: string;
}

function mockServer(handler: (req: Request, rec: Recorded) => Response | Promise<Response>) {
  const calls: Recorded[] = [];
  const server = Deno.serve({ port: 0, onListen: () => {} }, async (req) => {
    const url = new URL(req.url);
    const rec: Recorded = {
      method: req.method,
      path: url.pathname + url.search,
      auth: req.headers.get("authorization"),
      ua: req.headers.get("user-agent"),
      body: req.method === "POST" && !url.pathname.endsWith("/documents") ? await req.text() : "",
    };
    calls.push(rec);
    return await handler(req, rec);
  });
  const port = (server.addr as Deno.NetAddr).port;
  return { base: `http://127.0.0.1:${port}`, calls, close: () => server.shutdown() };
}

const TOKEN = "jrd_" + "a".repeat(43);
const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

function client(base: string) {
  return new ApiClient({
    apiBase: base,
    token: TOKEN,
    os: "darwin",
    requestTimeoutMs: 3000,
    uploadTimeoutMs: 3000,
  });
}

async function uploadOnce(res: (req: Request) => Response | Promise<Response>) {
  const s = mockServer((req) => res(req));
  try {
    const c = client(s.base);
    const out = await c.upload({ name: "a.pdf", bytes: new Uint8Array([1, 2, 3]) });
    return { out, calls: s.calls };
  } finally {
    await s.close();
  }
}

Deno.test("client — UA 와 Bearer 를 싣는다 (Cloudflare 가 기본 UA 를 막는다)", async () => {
  const { calls } = await uploadOnce(() => json(202, { doc_id: "d1", job_id: "j1", duplicated: false }));
  assertEquals(calls[0].ua, "JetRag-Agent/0.1.0 (darwin)");
  assertEquals(calls[0].auth, `Bearer ${TOKEN}`);
  assertEquals(calls[0].path, "/documents");
});

Deno.test("client — 202: 신규와 중복", async () => {
  const a = await uploadOnce(() => json(202, { doc_id: "d1", job_id: "j1", duplicated: false }));
  assertEquals(a.out, { kind: "accepted", docId: "d1", jobId: "j1", duplicated: false });
  const b = await uploadOnce(() => json(202, { doc_id: "d2", job_id: null, duplicated: true }));
  assertEquals(b.out, { kind: "accepted", docId: "d2", jobId: null, duplicated: true });
});

Deno.test("client — 400/413/422 는 영구 제외, code 를 그대로 싣는다", async () => {
  const a = await uploadOnce(() =>
    json(400, { detail: "허용되지 않는 확장자입니다.", code: "ext", ext: ".exe" })
  );
  assertEquals(a.out.kind, "excluded");
  assertEquals((a.out as { code: string }).code, "ext");
  const b = await uploadOnce(() => json(413, { detail: "파일 크기 상한(50MB) 초과", code: "too_large" }));
  assertEquals((b.out as { code: string }).code, "too_large");
  const c = await uploadOnce(() => json(422, { detail: "source_channel 이 올바르지 않습니다" }));
  assertEquals(c.out.kind, "excluded");
  // 422 는 에이전트 버그 신호다. code 가 없어도 사유는 남아야 한다.
  assertEquals((c.out as { code: string }).code, "form");
});

Deno.test("client — 402 는 한도대기, used/limit 을 보존", async () => {
  const { out } = await uploadOnce(() =>
    json(402, { detail: "저장 용량 한도 초과", code: "storage_limit", used: 900, limit: 1000 })
  );
  assertEquals(out, {
    kind: "quota",
    used: 900,
    limit: 1000,
    detail: "저장 용량 한도 초과",
  });
});

Deno.test("client — 503 code channel 은 재시도 (마이그가 밀린 서버)", async () => {
  const { out } = await uploadOnce(() => json(503, { detail: "채널 값을 DB 가 거절", code: "channel" }));
  assertEquals(out.kind, "retry");
});

Deno.test("client — 5xx 는 text/plain 이다. JSON 파싱으로 죽지 않는다", async () => {
  const { out } = await uploadOnce(() =>
    new Response("Internal Server Error", { status: 500, headers: { "content-type": "text/plain" } })
  );
  assertEquals(out.kind, "retry");
  assertEquals((out as { detail: string }).detail.includes("Internal Server Error"), true);
});

Deno.test("client — 429 는 Retry-After 를 존중", async () => {
  const { out } = await uploadOnce(() =>
    new Response("", { status: 429, headers: { "retry-after": "30", "content-type": "text/plain" } })
  );
  assertEquals(out.kind, "retry");
  assertEquals((out as { retryAfterMs: number | null }).retryAfterMs, 30_000);
});

Deno.test("client — 401 은 중단 신호, 403 은 버그 신호", async () => {
  const a = await uploadOnce(() => json(401, { detail: "기기 토큰이 폐기되었습니다." }));
  assertEquals(a.out.kind, "auth");
  const b = await uploadOnce(() => json(403, { detail: "기기 토큰의 권한 범위를 벗어난 요청입니다." }));
  assertEquals(b.out.kind, "forbidden");
});

Deno.test("client — 네트워크 실패는 retry", async () => {
  // 아무도 듣고 있지 않은 포트.
  const c = new ApiClient({
    apiBase: "http://127.0.0.1:1",
    token: TOKEN,
    requestTimeoutMs: 500,
    uploadTimeoutMs: 500,
  });
  const out = await c.upload({ name: "a.pdf", bytes: new Uint8Array([1]) });
  assertEquals(out.kind, "retry");
});

Deno.test("client — precheck 는 200개씩 끊어 보내고 합친다", async () => {
  const s = mockServer((_req, rec) => {
    const hashes = (JSON.parse(rec.body) as { hashes: string[] }).hashes;
    const results: Record<string, unknown> = {};
    for (const h of hashes) results[h] = { state: "new" };
    return json(200, { results });
  });
  try {
    const hashes = Array.from({ length: 250 }, (_, i) => i.toString(16).padStart(64, "0"));
    const out = await client(s.base).precheck(hashes);
    assertEquals(Object.keys(out).length, 250);
    assertEquals(s.calls.length, 2);
    assertEquals((JSON.parse(s.calls[0].body) as { hashes: string[] }).hashes.length, 200);
    assertEquals((JSON.parse(s.calls[1].body) as { hashes: string[] }).hashes.length, 50);
  } finally {
    await s.close();
  }
});

Deno.test("client — batch-status 는 50개씩 (서버 BATCH_STATUS_MAX_IDS)", async () => {
  const s = mockServer((req) => {
    const ids = new URL(req.url).searchParams.get("ids")!.split(",");
    return json(200, {
      items: ids.map((id) => ({ doc_id: id, job: { status: "running", current_stage: "extract" } })),
    });
  });
  try {
    const ids = Array.from({ length: 120 }, (_, i) => `doc-${i}`);
    const out = await client(s.base).batchStatus(ids);
    assertEquals(out.size, 120);
    assertEquals(s.calls.length, 3);
    assertEquals(out.get("doc-0")?.status, "running");
  } finally {
    await s.close();
  }
});

Deno.test("client — 조회 계열의 401/429 는 ApiError 로 던진다", async () => {
  const s = mockServer(() => json(401, { detail: "무효" }));
  try {
    const c = client(s.base);
    const e = await assertRejects(() => c.precheck(["0".repeat(64)]), ApiError);
    assertEquals((e as ApiError).kind, "auth");
  } finally {
    await s.close();
  }
});

Deno.test("client — 백오프 표 (5s→5m, jitter 0)", () => {
  const r = () => 0.5; // jitter 0
  assertEquals(backoffMs(1, r), 5_000);
  assertEquals(backoffMs(2, r), 10_000);
  assertEquals(backoffMs(3, r), 20_000);
  assertEquals(backoffMs(6, r), 160_000);
  assertEquals(backoffMs(7, r), RETRY.maxMs);
  assertEquals(backoffMs(99, r), RETRY.maxMs);
  // jitter 는 ±20% 안에 있다.
  const lo = backoffMs(2, () => 0), hi = backoffMs(2, () => 1);
  assertEquals(lo, 8_000);
  assertEquals(hi, 12_000);
});

Deno.test("client — Retry-After 는 초 또는 HTTP 날짜", () => {
  const now = Date.UTC(2026, 8, 15, 0, 0, 0);
  assertEquals(parseRetryAfterMs("30", now), 30_000);
  assertEquals(parseRetryAfterMs("0", now), 0);
  assertEquals(parseRetryAfterMs(new Date(now + 60_000).toUTCString(), now), 60_000);
  assertEquals(parseRetryAfterMs(null, now), null);
  assertEquals(parseRetryAfterMs("나중에", now), null);
});
