/**
 * `/ingest/email` — 대조에서 실제로 갈렸던 두 규칙을 고정한다.
 *
 * 전체 대조는 `api/scripts/verify_email_ingest_parity.py`(102건) 가 하지만 그건 Python
 * venv 가 있어야 돈다. 여기 있는 건 **틀렸던 것만** 남긴 회귀 방지선이다.
 */

import { assertEquals } from "@std/assert";

import { decodeBase64Strict, handleEmailWebhook } from "./email_route.ts";
import { parseToken, senderAllowed } from "./email_ingest.ts";

const SECRET = "s3cr3t";

interface FakeOpts {
  /** `storage_bytes_used` 가 돌려줄 값. 미지정이면 0(=여유 있음). */
  storageUsed?: number;
  /** 실제로 파일이 만들어졌는지 — 용량 초과면 둘 다 0 이어야 한다. */
  trace?: { uploads: number; inserts: number };
}

// deno-lint-ignore no-explicit-any
function fakeClient(opts: FakeOpts = {}): any {
  // deno-lint-ignore no-explicit-any
  const q = (rows: unknown): any => {
    // deno-lint-ignore no-explicit-any
    const o: any = {
      eq: () => o,
      is: () => o,
      limit: () => o,
      single: () => o,
      select: () => o,
      then: (res: (v: unknown) => void) => res({ data: rows, error: null }),
    };
    return o;
  };
  return {
    rpc: (fn: string) =>
      Promise.resolve({
        data: fn === "storage_bytes_used" ? (opts.storageUsed ?? 0) : null,
        error: null,
      }),
    storage: {
      from: () => ({
        upload: () => {
          if (opts.trace) opts.trace.uploads++;
          return Promise.resolve({ error: null });
        },
      }),
    },
    from(table: string) {
      if (table === "email_ingest_addresses") {
        return { select: () => q([{ user_id: "u1", token: "abcd1234", owner_email: "a@b.com" }]) };
      }
      if (table === "documents") {
        return {
          select: () => q([]),
          insert: () => {
            if (opts.trace) opts.trace.inserts++;
            return q({ id: "d1" });
          },
        };
      }
      if (table === "ingest_jobs") return { insert: () => q({ id: "j1" }) };
      if (table === "subscriptions") return { select: () => q([{ plan_code: "pro", status: "active" }]) };
      if (table === "plans") {
        return {
          select: () =>
            q([{
              code: "pro",
              answers_per_day: 1,
              storage_bytes_limit: 10737418240,
              vision_pages_per_month: 1000,
            }]),
        };
      }
      return { select: () => q([]) };
    },
  };
}

function post(body: unknown, secret = SECRET, opts: FakeOpts = {}) {
  return handleEmailWebhook(
    {
      client: fakeClient(opts),
      bucket: "documents",
      // quota 를 켠 채로 돈다 — 용량 검사(`makeStorageCheck`)까지 실제로 태운다.
      settings: {
        emailWebhookSecret: SECRET,
        authEnabled: true,
        quotaEnforcementEnabled: true,
        ownerUserId: null,
      },
    },
    new Request("https://x/ingest/email", {
      method: "POST",
      headers: { "x-jetrag-webhook-secret": secret },
      body: typeof body === "string" ? body : JSON.stringify(body),
    }),
  );
}

Deno.test("base64 는 패딩까지 엄격하다 — `atob` 이 받아주는 것을 Python 은 거부한다", () => {
  // 이게 통과해 버리면 거절돼야 할 첨부가 실제로 인제스트된다.
  assertEquals(decodeBase64Strict("aGVsbG8"), null); // 길이 7
  assertEquals(decodeBase64Strict("aGVsbG8=="), null); // 길이 9
  assertEquals(decodeBase64Strict("="), null);
  assertEquals(decodeBase64Strict("aGVsbG8!"), null); // 알파벳 밖
  assertEquals(decodeBase64Strict("aGVs bG8="), null); // 공백
  assertEquals(decodeBase64Strict("-_-_"), null); // base64url 은 표준이 아니다
  assertEquals([...decodeBase64Strict("aGVsbG8=")!], [104, 101, 108, 108, 111]);
  assertEquals([...decodeBase64Strict("")!], []);
});

Deno.test("첨부 검증은 처리보다 먼저다 — 하나만 어긋나도 요청 전체가 422", async () => {
  const to = "u-abcd1234@in.x";
  const ok = {
    filename: "a.pdf",
    content_type: "application/pdf",
    content_base64: btoa("%PDF-1.4\n" + "x".repeat(40)),
  };
  // `content_base64` 는 pydantic 기본값이 없다 — 없으면 처리에 들어가지 않는다.
  assertEquals((await post({ to, from: "a@b.com", attachments: [{ filename: "a.pdf" }] })).status, 422);
  assertEquals((await post({ to, from: "a@b.com", attachments: [ok, { filename: "b.pdf" }] })).status, 422);
  assertEquals(
    (await post({ to, from: "a@b.com", attachments: [{ ...ok, content_base64: 1 }] })).status,
    422,
  );
  assertEquals((await post({ to, from: "a@b.com", attachments: "nope" })).status, 422);
  // 키가 아예 없는 건 기본값 `[]` 라 422 가 아니라 `ignored` 다.
  assertEquals(await post({ to, from: "a@b.com" }).then((r) => r.body.status), "ignored");
});

Deno.test("거절은 조용히, 설정 오류는 시끄럽게", async () => {
  const to = "u-abcd1234@in.x";
  const att = {
    filename: "a.pdf",
    content_type: "application/pdf",
    content_base64: btoa("%PDF-1.4\n" + "x".repeat(40)),
  };
  // secret 불일치만 401 — Worker 재시도·반송 메일을 피하려고 나머지는 200 이다.
  assertEquals((await post({ to, from: "a@b.com" }, "nope")).status, 401);
  const bad = await post({ to: "someone@in.x", from: "a@b.com", attachments: [att] });
  assertEquals([bad.status, bad.body.status], [200, "ignored"]);
  const wrongSender = await post({ to, from: "x@y.com", attachments: [att] });
  assertEquals([wrongSender.status, wrongSender.body.status], [200, "ignored"]);
  const good = await post({ to, from: "Name <A@B.com>", attachments: [att] });
  assertEquals([good.status, good.body.status], [200, "processed"]);
  assertEquals((good.body.results as Record<string, unknown>[])[0].status, "accepted");
});

Deno.test("토큰·발신자 규칙 — Python `isalnum()`/`strip()` 과 같다", () => {
  assertEquals(parseToken("Jet <u-abcd1234@in.x>"), "abcd1234");
  assertEquals(parseToken("  U-ABCD1234@IN.X  "), "abcd1234");
  assertEquals(parseToken("u-abcd123@in.x"), null); // 7 자
  assertEquals(parseToken("u-abcd_123@in.x"), null); // `_` 는 isalnum 아님
  assertEquals(parseToken("u-@in.x"), null); // 빈 토큰
  assertEquals(parseToken("u-abcd1234@in.x, other@x.com"), null); // 앵커가 막는다

  assertEquals(senderAllowed("Name <A@B.com>", "  a@b.com  "), true);
  assertEquals(senderAllowed("a@b.com", null), false); // owner_email 없으면 거절
  assertEquals(senderAllowed("a@b.com", ""), false);
  assertEquals(senderAllowed("x@y.com", "a@b.com"), false);
});

Deno.test("용량 한도를 넘으면 첨부는 skipped — 파일도 행도 만들지 않는다", async () => {
  const to = "u-abcd1234@in.x";
  const ok = {
    filename: "a.pdf",
    content_type: "application/pdf",
    content_base64: btoa("%PDF-1.4\n" + "x".repeat(40)),
  };

  // 여유가 있으면 평소대로 받는다 — 대조군이 없으면 "원래 안 되는 것" 과 구분이 안 된다.
  const under = { uploads: 0, inserts: 0 };
  const okRes = await post({ to, from: "a@b.com", attachments: [ok] }, SECRET, {
    storageUsed: 0,
    trace: under,
  });
  assertEquals(okRes.status, 200);
  assertEquals((okRes.body.results as { status: string }[])[0].status, "accepted");
  assertEquals([under.uploads, under.inserts], [1, 1]);

  // 플랜 한도(10GB)를 이미 다 쓴 상태.
  const over = { uploads: 0, inserts: 0 };
  const res = await post({ to, from: "a@b.com", attachments: [ok] }, SECRET, {
    storageUsed: 10737418240,
    trace: over,
  });
  // 거절은 200 + skipped 다 — 4xx 를 내면 Worker 가 재시도하거나 반송 메일이 간다.
  assertEquals(res.status, 200);
  assertEquals(res.body.results, [{
    status: "skipped",
    filename: "a.pdf",
    reason: "저장 용량 한도 초과",
  }]);
  // 한도를 넘겼는데 파일이 올라가면 한도가 한도가 아니다.
  assertEquals([over.uploads, over.inserts], [0, 0]);
});
