/**
 * 날짜 상한(9999년) — **사용자가 만들 수 있던 500 을 없앤 자리**를 고정한다.
 *
 * 원본은 Python `datetime` 의 최대 연도가 9999 라 `9999-12-31`(하루 더하기)과
 * `9999년 12월`(다음 달 1일)에서 잡히지 않은 예외를 낸다. 실측(2026-09-05 · 09-07 재확인):
 * `/search?q=9999-12-31 자료` → **500**.
 *
 * 이관 중에는 맞춰서 같이 던졌고, `/search` 가 100% Edge 가 된 뒤 고쳤다 — work-log §51.
 * 대조 스크립트(`verify_meta_fast_path_parity.py`)에도 **의도된 차이**로 적어 뒀다.
 */

import { assertEquals } from "@std/assert";

import { isMetaOnly } from "./meta_fast_path.ts";

const TODAY = (): [number, number, number] => [2026, 9, 5];

Deno.test("9999-12-31 — 던지지 않고 10000-01-01 상한을 만든다", () => {
  const plan = isMetaOnly("9999-12-31 자료", TODAY);
  assertEquals(plan?.dateRange, [
    "9999-12-31T00:00:00+00:00",
    "10000-01-01T00:00:00+00:00",
  ]);
});

Deno.test("9999년 12월 — 다음 달 1일이 10000-01-01 이다", () => {
  const plan = isMetaOnly("9999년 12월 자료", TODAY);
  assertEquals(plan?.dateRange, [
    "9999-12-01T00:00:00+00:00",
    "10000-01-01T00:00:00+00:00",
  ]);
});

Deno.test("경계 바로 아래는 원래대로", () => {
  // 이 케이스는 원본도 200 이었다 — 고치면서 바뀌지 않아야 한다.
  assertEquals(isMetaOnly("9999-12-30 자료", TODAY)?.dateRange, [
    "9999-12-30T00:00:00+00:00",
    "9999-12-31T00:00:00+00:00",
  ]);
  assertEquals(isMetaOnly("9999년 11월 자료", TODAY)?.dateRange, [
    "9999-11-01T00:00:00+00:00",
    "9999-12-01T00:00:00+00:00",
  ]);
});

Deno.test("달력에 없는 날짜는 여전히 날짜로 안 잡힌다", () => {
  // `validYmd` 가 막는 자리 — 상한 수정이 여기를 건드리면 안 된다.
  for (const q of ["9999-13-01 자료", "9999-12-32 자료", "0000-01-01 자료"]) {
    const plan = isMetaOnly(q, TODAY);
    assertEquals(plan?.dateRange ?? null, null, q);
  }
});
