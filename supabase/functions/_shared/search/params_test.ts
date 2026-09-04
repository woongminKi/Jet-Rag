/**
 * `params.ts` 의 **성공 경로**를 고정한다.
 *
 * 패리티 검사기(`verify_search_params_parity.py`)는 운영 엔드포인트와 **상태코드·detail 만**
 * 대조한다. 통과한 요청이 어떤 값으로 파싱됐는지는 응답 본문에 안 드러나서 볼 수 없다.
 * 그 사각지대 — 기본값·`tags` 다중 값·NFC 정규화 — 를 여기서 덮는다.
 */

import { assertEquals } from "@std/assert";
import { validateSearchParams } from "./params.ts";

function ok(qs: string) {
  const r = validateSearchParams(new URLSearchParams(qs));
  if (!r.ok) throw new Error(`검증 실패를 기대하지 않았다: ${JSON.stringify(r.detail)}`);
  return r.params;
}

Deno.test("기본값 — limit 10 / offset 0 / mode hybrid / 나머지 null", () => {
  const p = ok("q=세무");
  assertEquals(p.limit, 10);
  assertEquals(p.offset, 0);
  assertEquals(p.mode, "hybrid");
  assertEquals(p.tags, null);
  assertEquals(p.docType, null);
  assertEquals(p.fromDate, null);
  assertEquals(p.toDate, null);
  assertEquals(p.docId, null);
});

Deno.test("q 는 원본과 정규화본을 따로 들고 있다", () => {
  // 응답의 `query` 필드에는 원본이, 검색에는 정규화본이 쓰인다.
  const p = ok("q=" + encodeURIComponent("  세무  "));
  assertEquals(p.q, "  세무  ");
  assertEquals(p.cleanQ, "세무");
});

Deno.test("NFD 로 들어와도 NFC 로 정규화한다", () => {
  // DB title 이 NFC 라 질의가 NFD 면 매칭이 통째로 실패한다(원본 W25 D14 주석).
  const nfd = "세무".normalize("NFD");
  const p = ok("q=" + encodeURIComponent(nfd));
  assertEquals(p.cleanQ, "세무".normalize("NFC"));
  assertEquals(p.cleanQ.normalize("NFC"), p.cleanQ);
  // 원본은 손대지 않는다.
  assertEquals(p.q, nfd);
});

Deno.test("tags 는 반복 파라미터를 배열로 모은다", () => {
  // `?tags=A&tags=B` → A AND B 필터. 하나만 읽으면 필터가 조용히 느슨해진다.
  assertEquals(ok("q=a&tags=A&tags=B").tags, ["A", "B"]);
  assertEquals(ok("q=a&tags=A").tags, ["A"]);
  assertEquals(ok("q=a").tags, null);
});

Deno.test("doc_id 는 trim 해서 담는다", () => {
  assertEquals(ok("q=a&doc_id=" + encodeURIComponent("  abc  ")).docId, "abc");
});

Deno.test("날짜는 PostgREST 에 넣을 isoformat 문자열로 담는다", () => {
  // 원본은 `from_dt.isoformat()` 을 그대로 필터에 넣는다(`search.py` 1367).
  // `Date` 로 들고 있으면 오프셋이 UTC 로 정규화되고 마이크로초가 잘려서 요청이 달라진다.
  const from = (qs: string) => ok("q=a&from_date=" + encodeURIComponent(qs)).fromDate;

  // 날짜만 오면 UTC 0시. 로컬 타임존으로 해석하면 경계가 하루 밀린다.
  assertEquals(from("2026-04-01"), "2026-04-01T00:00:00+00:00");
  // 타임존이 없는 datetime 도 UTC 로 본다.
  assertEquals(from("2026-04-01T09:00:00"), "2026-04-01T09:00:00+00:00");
  // 오프셋은 **변환이 아니라 보존**이다 — 원본이 tz-aware datetime 을 그대로 넘긴다.
  assertEquals(from("2026-04-01T09:00:00+09:00"), "2026-04-01T09:00:00+09:00");
  assertEquals(from("2026-04-01T00:00:00Z"), "2026-04-01T00:00:00+00:00");
  // 마이크로초는 6 자리까지 살고 넘치면 절삭한다. `Date` 였으면 밀리초에서 잘렸다.
  assertEquals(from("2026-04-01T09:00:00.123456"), "2026-04-01T09:00:00.123456+00:00");
  assertEquals(from("2026-04-01T09:00:00.1234567"), "2026-04-01T09:00:00.123456+00:00");
  assertEquals(ok("q=a&to_date=2026-04-01").toDate, "2026-04-01T00:00:00+00:00");
});

Deno.test("존재하지 않는 날짜를 롤오버하지 않는다", () => {
  // `new Date("2026-02-30")` 은 3 월 2 일로 넘어간다 — 400 대신 엉뚱한 필터가 된다.
  for (const bad of ["2026-02-30", "2026-04-31", "2026-13-01", "2026-04-01T24:00:00"]) {
    const r = validateSearchParams(new URLSearchParams({ q: "a", from_date: bad }));
    assertEquals(r.ok, false, `${bad} 는 거부해야 한다`);
  }
});

Deno.test("limit·offset 경계값을 그대로 담는다", () => {
  assertEquals(ok("q=a&limit=1").limit, 1);
  assertEquals(ok("q=a&limit=50").limit, 50);
  assertEquals(ok("q=a&offset=0").offset, 0);
  assertEquals(ok("q=a&offset=1000").offset, 1000);
});

Deno.test("여러 파라미터가 동시에 틀리면 선언 순서대로 모은다", () => {
  // pydantic 은 오류를 배열로 모으고 순서는 함수 시그니처 순이다(q → limit → offset).
  const r = validateSearchParams(new URLSearchParams("limit=0&offset=-1"));
  assertEquals(r.ok, false);
  if (r.ok) return;
  assertEquals(r.status, 422);
  const items = r.detail as Array<{ loc: [string, string] }>;
  assertEquals(items.map((i) => i.loc[1]), ["q", "limit", "offset"]);
});
