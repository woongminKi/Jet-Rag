/**
 * 미이식 토글 판정 — **값 규칙이 원본마다 다르다**는 게 핵심이다.
 *
 * 대부분은 `lower() == "true"` 라 `"1"` 도 꺼짐인데, decomposition·cross-doc scoped 는
 * `strip().lower() in {true,1,yes,on}` 이라 `"1"` 이 켜짐이다. 한쪽 규칙으로 통일하면
 * 운영자가 `=1` 로 켠 기능을 Edge 가 못 알아채고 **조용히 다른 결과**를 낸다.
 */

import { assertEquals } from "@std/assert";
import { findEnabledUnsupported, UNSUPPORTED_TOGGLES, unsupportedDetail } from "./unsupported.ts";

const env = (o: Record<string, string>) => (k: string) => o[k];

Deno.test("아무 토글도 안 켜져 있으면 통과", () => {
  assertEquals(findEnabledUnsupported(env({})), []);
  assertEquals(findEnabledUnsupported(env({ JETRAG_RERANKER_ENABLED: "false" })), []);
});

Deno.test("`lower()==true` 규칙 — 대소문자는 무시, `1` 은 꺼짐", () => {
  assertEquals(
    findEnabledUnsupported(env({ JETRAG_RERANKER_ENABLED: "TRUE" })),
    ["JETRAG_RERANKER_ENABLED"],
  );
  // 원본이 `== "true"` 라 `"1"` 은 켜짐이 아니다. 여기서 넓게 잡으면 원본과 갈린다.
  assertEquals(findEnabledUnsupported(env({ JETRAG_RERANKER_ENABLED: "1" })), []);
});

Deno.test("느슨한 규칙 — decomposition 은 1/yes/on 도 켜짐", () => {
  for (const v of ["1", "yes", "on", " TRUE "]) {
    assertEquals(
      findEnabledUnsupported(env({ JETRAG_PAID_DECOMPOSITION_ENABLED: v })),
      ["JETRAG_PAID_DECOMPOSITION_ENABLED"],
      `값 ${JSON.stringify(v)} 는 켜짐이어야 한다`,
    );
  }
  assertEquals(findEnabledUnsupported(env({ JETRAG_PAID_DECOMPOSITION_ENABLED: "no" })), []);
});

Deno.test("여러 개가 동시에 켜지면 전부 알려준다", () => {
  const on = findEnabledUnsupported(env({
    JETRAG_HYDE_ENABLED: "true",
    JETRAG_ENTITY_BOOST: "true",
  }));
  assertEquals(on, ["JETRAG_HYDE_ENABLED", "JETRAG_ENTITY_BOOST"]);
  // 메시지에 ENV 이름이 반드시 들어가야 한다 — 이름 없이 "지원 안 함" 만 나오면
  // 운영자가 무엇을 껐다 켜야 하는지 알 수 없다.
  const msg = unsupportedDetail(on);
  for (const e of on) assertEquals(msg.includes(e), true, `${e} 가 메시지에 없다`);
});

Deno.test("목록에 중복 ENV 가 없다", () => {
  const names = UNSUPPORTED_TOGGLES.map((t) => t.env);
  assertEquals(names.length, new Set(names).size);
});
