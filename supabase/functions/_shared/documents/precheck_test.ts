/**
 * precheck 계약.
 *
 * 여기서 고정하는 것:
 * - 본문 검증 — 배열·개수 상한·hex64·소문자 정규화
 * - 분류 3갈래 (existing / failed / new) — persist 의 dedup 과 같은 의미여야 한다.
 */

import { assertEquals } from "@std/assert";
import { parsePrecheckBody, precheckHashes } from "./precheck.ts";

const H = (c: string) => c.repeat(64);

Deno.test("parsePrecheckBody — 배열·개수·hex64 검증", () => {
  assertEquals(parsePrecheckBody({ hashes: [H("a")] }).ok, true);
  assertEquals(parsePrecheckBody({}).ok, false);
  assertEquals(parsePrecheckBody({ hashes: [] }).ok, false);
  assertEquals(parsePrecheckBody({ hashes: ["ABC"] }).ok, false);
  assertEquals(parsePrecheckBody({ hashes: Array(501).fill(H("b")) }).ok, false);
  // 대문자는 소문자로 정규화한다.
  const r = parsePrecheckBody({ hashes: [H("A")] });
  if (r.ok) assertEquals(r.hashes[0], H("a"));
});

Deno.test("precheckHashes — existing / failed / new 분류", async () => {
  const client = {
    from: () => ({
      select: () => ({
        eq: () => ({
          is: () => ({
            in: () =>
              Promise.resolve({
                data: [
                  { id: "d1", sha256: H("a"), flags: {} },
                  { id: "d2", sha256: H("b"), flags: { failed: true } },
                ],
                error: null,
              }),
          }),
        }),
      }),
    }),
  };
  // deno-lint-ignore no-explicit-any
  const r = await precheckHashes(client as any, "u1", [H("a"), H("b"), H("c")]);
  assertEquals(r[H("a")], { state: "existing", doc_id: "d1" });
  assertEquals(r[H("b")], { state: "failed", doc_id: "d2" });
  assertEquals(r[H("c")], { state: "new" });
});
