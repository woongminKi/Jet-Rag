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

// deno-lint-ignore no-explicit-any
function fakeClient(): any {
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
    rpc: () => Promise.resolve({ data: null, error: null }),
    storage: { from: () => ({ upload: () => Promise.resolve({ error: null }) }) },
    from(table: string) {
      if (table === "email_ingest_addresses") {
        return { select: () => q([{ user_id: "u1", token: "abcd1234", owner_email: "a@b.com" }]) };
      }
      if (table === "documents") return { select: () => q([]), insert: () => q({ id: "d1" }) };
      if (table === "ingest_jobs") return { insert: () => q({ id: "j1" }) };
      if (table === "subscriptions") return { select: () => q([{ plan_code: "pro", status: "active" }]) };
      if (table === "plans") {
        return { select: () => q([{ code: "pro", max_documents: 1, answers_per_day: 1 }]) };
      }
      return { select: () => q([]) };
    },
  };
}

function post(body: unknown, secret = SECRET) {
  return handleEmailWebhook(
    {
      client: fakeClient(),
      bucket: "documents",
      settings: { emailWebhookSecret: SECRET },
      nowMs: () => 0,
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
