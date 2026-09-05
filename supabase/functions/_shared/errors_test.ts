/**
 * 오류 응답 모양 — **처리되지 않은 예외만 평문**이라는 게 핵심이다.
 *
 * FastAPI 는 `HTTPException` 은 `{"detail": ...}` JSON 으로, 처리되지 않은 예외는
 * `text/plain` 평문으로 낸다. 한 가지로 통일하면 500 응답의 모양이 원본과 갈린다
 * (배포 후 HTTP 대조에서 실제로 발견했다).
 */

import { assertEquals } from "@std/assert";
import { detailResponse, internalServerError, jsonResponse, toResponse } from "./errors.ts";

Deno.test("HTTPException 계열은 JSON `{detail}`", async () => {
  const r = detailResponse(400, "잘못된 요청");
  assertEquals(r.status, 400);
  assertEquals(r.headers.get("content-type"), "application/json");
  assertEquals(await r.json(), { detail: "잘못된 요청" });
});

Deno.test("처리되지 않은 예외는 평문 — 원본 실측과 같은 모양", async () => {
  const r = toResponse(new Error("내부 사정"));
  assertEquals(r.status, 500);
  assertEquals(r.headers.get("content-type"), "text/plain; charset=utf-8");
  assertEquals(await r.text(), "Internal Server Error");
});

Deno.test("원인 문자열을 응답에 흘리지 않는다", async () => {
  const r = toResponse(new Error("SUPABASE_SERVICE_ROLE_KEY=비밀"));
  assertEquals((await r.text()).includes("비밀"), false);
});

Deno.test("internalServerError 는 toResponse 와 같은 응답", async () => {
  const a = internalServerError();
  const b = toResponse(new Error("x"));
  assertEquals(a.status, b.status);
  assertEquals(a.headers.get("content-type"), b.headers.get("content-type"));
  assertEquals(await a.text(), await b.text());
});

Deno.test("jsonResponse 는 헤더를 덧붙일 수 있다", () => {
  const r = jsonResponse({ ok: true }, 200, { "X-Search-Path": "rag" });
  assertEquals(r.headers.get("X-Search-Path"), "rag");
  assertEquals(r.headers.get("content-type"), "application/json");
});
