/**
 * Python 문자 판정 3종을 **전 코드포인트**에서 대조한다.
 *
 * 왜 전수인가: 이 셋의 차이는 유니코드 버전에서 온다. 런타임이 올라가면 조용히 갈릴 수
 * 있고, 그러면 `_looks_like_table_cell` → 섹션 병합 → **청크 경계**가 통째로 바뀐다.
 * 샘플 몇 개로는 못 잡는다.
 *
 * fixture(`api/scripts/fixtures/pychar_ranges.json`)는 Python 3.12 에서 뜬 범위다.
 */

import { assertEquals } from "@std/assert";
import { pyIsAlnum, pyIsDigit, pyIsSpace } from "./pychar.ts";

const fixture = JSON.parse(
  await Deno.readTextFile(
    new URL("../../../api/scripts/fixtures/pychar_ranges.json", import.meta.url),
  ),
) as { isspace: [number, number][]; isdigit: [number, number][]; isalnum: [number, number][] };

function toSet(ranges: [number, number][]): Set<number> {
  const s = new Set<number>();
  for (const [a, b] of ranges) for (let c = a; c <= b; c++) s.add(c);
  return s;
}

Deno.test("isspace — 전 코드포인트가 Python 과 같다", () => {
  const want = toSet(fixture.isspace);
  const diff: number[] = [];
  for (let cp = 0; cp <= 0x10FFFF; cp++) {
    if (cp >= 0xD800 && cp <= 0xDFFF) continue;
    if (pyIsSpace(String.fromCodePoint(cp)) !== want.has(cp)) diff.push(cp);
  }
  assertEquals(diff.slice(0, 10), [], `다른 코드포인트 ${diff.length}개`);
});

Deno.test("isdigit — 전 코드포인트가 Python 과 같다", () => {
  const want = toSet(fixture.isdigit);
  const diff: number[] = [];
  for (let cp = 0; cp <= 0x10FFFF; cp++) {
    if (cp >= 0xD800 && cp <= 0xDFFF) continue;
    if (pyIsDigit(String.fromCodePoint(cp)) !== want.has(cp)) diff.push(cp);
  }
  assertEquals(diff.slice(0, 10), [], `다른 코드포인트 ${diff.length}개`);
});

Deno.test("isalnum — 전 코드포인트가 Python 과 같다", () => {
  // **구현은 `[\p{L}\p{N}]` - 예외** 방식이다. 예외 목록으로 검증하면 같은 경로를 두 번
  // 도는 자기 대조가 되므로, fixture 에는 Python 집합을 통째로 두고 그걸로 판정한다.
  const want = toSet(fixture.isalnum);
  const diff: number[] = [];
  for (let cp = 0; cp <= 0x10FFFF; cp++) {
    if (cp >= 0xD800 && cp <= 0xDFFF) continue;
    if (pyIsAlnum(String.fromCodePoint(cp)) !== want.has(cp)) diff.push(cp);
  }
  assertEquals(diff.slice(0, 10), [], `다른 코드포인트 ${diff.length}개`);
});

Deno.test("알려진 함정 값들", () => {
  // Python isspace 만 참 — JS `\s` 에는 없다
  for (const ch of ["\x1c", "\x1d", "\x1e", "\x1f", "\x85"]) {
    assertEquals(pyIsSpace(ch), true, JSON.stringify(ch));
  }
  // JS `\s` 만 참 — Python isspace 는 거짓
  assertEquals(pyIsSpace("﻿"), false);
  // Python isdigit 만 참 — `\p{Nd}` 에는 없다
  for (const ch of ["²", "³", "¹"]) assertEquals(pyIsDigit(ch), true, ch);
  // isalnum 은 Python 이 부분집합 — 새 유니코드 문자는 거짓이어야 한다
  assertEquals(pyIsAlnum("Ᲊ"), false);
  assertEquals(pyIsAlnum("가"), true);
  assertEquals(pyIsAlnum("A"), true);
  assertEquals(pyIsAlnum("7"), true);
  assertEquals(pyIsAlnum("²"), true); // No 도 isalnum 참
  assertEquals(pyIsAlnum(" "), false);
  assertEquals(pyIsAlnum("."), false);
});

Deno.test("빈 문자열은 전부 거짓", () => {
  assertEquals([pyIsSpace(""), pyIsDigit(""), pyIsAlnum("")], [false, false, false]);
});
