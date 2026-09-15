/**
 * precheck 계약.
 *
 * 여기서 고정하는 것:
 * - 본문 검증 — 배열·개수 상한·hex64·소문자 정규화
 * - 분류 3갈래 (existing / failed / new) — persist 의 dedup 과 같은 의미여야 한다.
 * - **테넌트 격리** — precheck 는 "이 해시가 있느냐"를 답하는 존재 오라클이다.
 *   `user_id`·`deleted_at` 필터가 빠지면 남의 파일 보유 여부가 새어 나간다. 인자까지 잡아 고정한다.
 * - 100 개씩 끊어 조회 — 한 번에 500 을 보내면 게이트웨이 8KB 버퍼에서 414 다.
 */

import { assertEquals } from "@std/assert";
import { parsePrecheckBody, PRECHECK_MAX, precheckHashes } from "./precheck.ts";

const H = (c: string) => c.repeat(64);
/** 서로 다른 해시 n 개. 인덱스를 hex 로 찍어 만든다. */
const hx = (n: number) => n.toString(16).padStart(64, "0");

interface FakeRow {
  id: string;
  flags?: Record<string, unknown>;
}

/** 걸린 필터와 `.in()` 호출을 전부 기록하는 가짜 클라이언트. */
function fakeClient(rows: Record<string, FakeRow>) {
  const cap = {
    tables: [] as string[],
    selects: [] as string[],
    eq: [] as [string, unknown][],
    is: [] as [string, unknown][],
    inCalls: [] as string[][],
    inCols: [] as string[],
  };
  const client = {
    from(table: string) {
      cap.tables.push(table);
      const api = {
        select(cols: string) {
          cap.selects.push(cols);
          return api;
        },
        eq(col: string, val: unknown) {
          cap.eq.push([col, val]);
          return api;
        },
        is(col: string, val: unknown) {
          cap.is.push([col, val]);
          return api;
        },
        in(col: string, vals: string[]) {
          cap.inCols.push(col);
          cap.inCalls.push(vals);
          const data = vals
            .filter((v) => v in rows)
            .map((v) => ({ id: rows[v].id, sha256: v, flags: rows[v].flags ?? {} }));
          return Promise.resolve({ data, error: null });
        },
      };
      return api;
    },
  };
  return { client, cap };
}

Deno.test("parsePrecheckBody — 배열·개수·hex64 검증", () => {
  assertEquals(parsePrecheckBody({ hashes: [H("a")] }).ok, true);
  assertEquals(parsePrecheckBody({}).ok, false);
  assertEquals(parsePrecheckBody({ hashes: [] }).ok, false);
  assertEquals(parsePrecheckBody({ hashes: ["ABC"] }).ok, false);
  assertEquals(parsePrecheckBody({ hashes: Array(PRECHECK_MAX).fill(H("b")) }).ok, true);
  assertEquals(parsePrecheckBody({ hashes: Array(PRECHECK_MAX + 1).fill(H("b")) }).ok, false);
  // 대문자는 소문자로 정규화한다.
  const r = parsePrecheckBody({ hashes: [H("A")] });
  if (r.ok) assertEquals(r.hashes[0], H("a"));
});

Deno.test("precheckHashes — existing / failed / new 분류 + 테넌트 필터", async () => {
  const { client, cap } = fakeClient({
    [H("a")]: { id: "d1" },
    [H("b")]: { id: "d2", flags: { failed: true } },
  });
  // deno-lint-ignore no-explicit-any
  const r = await precheckHashes(client as any, "u1", [H("a"), H("b"), H("c")]);
  assertEquals(r[H("a")], { state: "existing", doc_id: "d1" });
  assertEquals(r[H("b")], { state: "failed", doc_id: "d2" });
  assertEquals(r[H("c")], { state: "new" });

  // 남의 문서를 보면 안 된다 — 필터 인자까지 고정한다.
  assertEquals(cap.tables, ["documents"]);
  assertEquals(cap.eq, [["user_id", "u1"]]);
  assertEquals(cap.is, [["deleted_at", null]]);
  assertEquals(cap.inCols, ["sha256"]);
});

Deno.test("precheckHashes — 100 개씩 끊어 조회하고 결과를 합친다", async () => {
  const hashes = Array.from({ length: 250 }, (_, i) => hx(i));
  // 0 번은 정상, 1 번은 실패 흔적, 249 번(마지막 덩이)도 정상 — 덩이를 넘어 합쳐지는지 본다.
  const { client, cap } = fakeClient({
    [hx(0)]: { id: "d0" },
    [hx(1)]: { id: "d1", flags: { failed: true } },
    [hx(249)]: { id: "d249" },
  });
  // deno-lint-ignore no-explicit-any
  const r = await precheckHashes(client as any, "u1", hashes);

  assertEquals(cap.inCalls.map((c) => c.length), [100, 100, 50]);
  // 끊긴 조각을 합치면 원본 순서 그대로여야 한다 — 빠진 해시가 있으면 조용히 "new" 가 된다.
  assertEquals(cap.inCalls.flat(), hashes);
  // 덩이마다 필터가 새로 붙는다(체인을 매번 다시 만든다).
  assertEquals(cap.eq, [["user_id", "u1"], ["user_id", "u1"], ["user_id", "u1"]]);
  assertEquals(cap.is, [["deleted_at", null], ["deleted_at", null], ["deleted_at", null]]);

  assertEquals(Object.keys(r).length, 250);
  assertEquals(r[hx(0)], { state: "existing", doc_id: "d0" });
  assertEquals(r[hx(1)], { state: "failed", doc_id: "d1" });
  assertEquals(r[hx(249)], { state: "existing", doc_id: "d249" });
  assertEquals(r[hx(150)], { state: "new" });
  assertEquals(Object.values(r).filter((v) => v.state === "new").length, 247);
});
