/**
 * `stripNulls` — Postgres jsonb 가 U+0000 을 거부해서 넣은 방어막.
 *
 * 실제 arXiv PDF 로 저장이 죽고 나서 만든 것이라, **원본
 * `SupabasePgVectorStore._strip_null_bytes` 와 같은 계약**인지 고정한다.
 */

import { assertEquals } from "@std/assert";
import { stripNulls } from "./strip_nul.ts";

const NUL = "\u0000";

Deno.test("문자열에서 NUL 을 지우고 개수를 센다", () => {
  const r = stripNulls(`a${NUL}b${NUL}${NUL}c`);
  assertEquals(r.value, "abc");
  assertEquals(r.removed, 3);
});

Deno.test("NUL 이 없으면 그대로 (같은 참조여도 무방)", () => {
  const r = stripNulls("깨끗한 문자열");
  assertEquals(r.value, "깨끗한 문자열");
  assertEquals(r.removed, 0);
});

Deno.test("배열·객체를 재귀로 훑는다", () => {
  const r = stripNulls({
    text: `본문${NUL}`,
    nested: { deep: [`a${NUL}`, { x: `b${NUL}` }] },
  });
  assertEquals(r.value, { text: "본문", nested: { deep: ["a", { x: "b" }] } });
  assertEquals(r.removed, 3);
});

Deno.test("문자열이 아닌 값은 그대로 둔다", () => {
  // 원본 계약: None/int/float/bool 은 손대지 않는다. null 을 object 로 오인하면 터진다.
  const r = stripNulls({ n: 1, f: 1.5, b: true, z: null, arr: [1, null, false] });
  assertEquals(r.value, { n: 1, f: 1.5, b: true, z: null, arr: [1, null, false] });
  assertEquals(r.removed, 0);
});

Deno.test("객체 **키**의 NUL 도 지운다", () => {
  // 원본은 값만 훑지만, 키가 NUL 을 담으면 jsonb 에서 똑같이 터진다.
  // 리터럴로 쓰면 TS 가 키 이름을 `"k\0"` 로 좁혀 비교가 막힌다 — 런타임 값으로 만든다.
  const input: Record<string, unknown> = {};
  input[`k${NUL}`] = "v";
  const r = stripNulls(input);
  assertEquals(r.value, { k: "v" });
  assertEquals(r.removed, 1);
});

Deno.test("빈 문자열·빈 객체·빈 배열", () => {
  assertEquals(stripNulls({ s: "", o: {}, a: [] }).value, { s: "", o: {}, a: [] });
});

Deno.test("NUL 만으로 된 문자열은 빈 문자열이 된다", () => {
  const r = stripNulls(NUL + NUL);
  assertEquals(r.value, "");
  assertEquals(r.removed, 2);
});

Deno.test("실제 청크 레코드 모양 — records 배열 깊숙이 든 NUL 을 잡는다", () => {
  const payload = {
    chunk_count: 2,
    records: [
      { chunk_idx: 0, text: `수식${NUL}조각`, section_title: null, metadata: {} },
      { chunk_idx: 1, text: "정상", section_title: `제목${NUL}`, metadata: { entities: {} } },
    ],
  };
  const r = stripNulls(payload);
  assertEquals(r.removed, 2);
  const recs = (r.value as typeof payload).records;
  assertEquals(recs[0].text, "수식조각");
  assertEquals(recs[1].section_title, "제목");
  assertEquals(recs[0].chunk_idx, 0);
});
