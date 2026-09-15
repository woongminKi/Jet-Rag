/**
 * 창 분할 **등가성** 계약 — 이 파일이 이 기능의 존재 이유를 지킨다.
 *
 * 창으로 쪼갠 결과가 전체를 한 번에 처리한 것과 달라지면, 그 차이는 검색 품질 저하로만
 * 보이고 원인을 못 찾는다. 그래서 세 가지를 **실제 SK 사업보고서 섹션**(픽스처
 * `fixtures/chunk_sections_sample.json`, 1,903 섹션 · 18 페이지 · null page 60개 ·
 * 꼬리에 vision seam)으로 고정한다:
 *
 * - (A) `page` 가 바뀌는 지점에서만 컷 → 전체와 **identical**
 * - (B) `page` 를 무시하고 고정 개수로 컷 → **다르다**(반례가 살아 있어야 한다)
 * - (D) extract 꼬리와 vision 첫 섹션이 같은 `page` 인데 그 사이를 컷 → **다르다**
 *
 * (B)·(D) 가 "같다" 로 바뀌면 컷 규칙을 느슨하게 해도 된다는 뜻이 아니라, 병합 로직이
 * 바뀌어 이 테스트가 더 이상 아무것도 안 지킨다는 뜻이다 — 그래서 같이 고정한다.
 */

import { assertEquals, assertNotEquals } from "@std/assert";
import { mergeShortSections } from "./chunk_merge.ts";
import { runChunkStage, splitLongSections } from "./chunk_records.ts";
import type { ExtractedSection } from "./hwp_extract.ts";
import {
  accumulateHfCounts,
  emptyDocFlags,
  mergeFlagsOr,
  readArtifactsPerTask,
  splitTailByPage,
  windowPlan,
} from "./chunk_window.ts";
import { collectShortCounts, headerFooterTexts } from "./chunk_filter.ts";

const ENV = {
  captionPrefixEnabled: false,
  synonymInjectionEnabled: false,
  synonymLlmEnabled: false,
};

const FIXTURE: ExtractedSection[] = JSON.parse(
  await Deno.readTextFile(new URL("./fixtures/chunk_sections_sample.json", import.meta.url)),
);

/** 픽스처가 이 테스트가 요구하는 성질을 실제로 갖고 있는지 먼저 본다. */
Deno.test("픽스처 — 병합이 실제로 일어나고 page 경계·null page·vision seam 이 있다", () => {
  const merged = mergeShortSections(splitLongSections(FIXTURE));
  assertEquals(FIXTURE.length > merged.length, true, "병합이 안 일어나면 등가성 테스트가 공회전한다");
  assertEquals(FIXTURE.length, 1903);
  assertEquals(merged.length, 170);
  assertEquals(FIXTURE.filter((s) => s.page === null).length, 60);
  assertEquals(new Set(FIXTURE.map((s) => s.page)).size, 18);
  // 꼬리 3개가 vision 유래(seam)이고, 바로 앞 extract 섹션과 **같은 page** 다.
  const tail = FIXTURE.slice(-3);
  assertEquals(tail.every((s) => (s.section_title ?? "").startsWith("(vision)")), true);
  assertEquals(tail.every((s) => s.page === FIXTURE[FIXTURE.length - 4].page), true);
});

/** `page` 가 바뀌는 지점에서만 자른다 — 핸들러의 `splitTailByPage` 루프와 같은 규칙. */
function cutByWindows(sections: ExtractedSection[], perWindow: number): ExtractedSection[][] {
  const wins: ExtractedSection[][] = [];
  let carry: ExtractedSection[] = [];
  for (let i = 0; i < sections.length; i += perWindow) {
    const isLast = i + perWindow >= sections.length;
    const combined = [...carry, ...sections.slice(i, i + perWindow)];
    if (isLast) {
      wins.push(combined);
      carry = [];
    } else {
      const { head, tail } = splitTailByPage(combined);
      wins.push(head);
      carry = tail;
    }
  }
  if (carry.length > 0) wins.push(carry);
  return wins;
}

Deno.test("(A) page 가 바뀌는 지점에서만 컷 — 병합 결과가 전체와 identical", () => {
  const whole = mergeShortSections(splitLongSections(FIXTURE));
  for (const per of [37, 200, 500, 1000]) {
    const out: ExtractedSection[] = [];
    for (const w of cutByWindows(FIXTURE, per)) {
      out.push(...mergeShortSections(splitLongSections(w)));
    }
    assertEquals(JSON.stringify(out), JSON.stringify(whole), `창 크기 ${per}`);
  }
});

Deno.test("(A) chunk_idx 까지 이어 붙이면 전체 처리와 identical", () => {
  const whole = runChunkStage({ docId: "d1", sections: FIXTURE, env: ENV });
  const out: ReturnType<typeof runChunkStage> = [];
  for (const w of cutByWindows(FIXTURE, 200)) {
    out.push(...runChunkStage({ docId: "d1", sections: w, env: ENV, idxOffset: out.length }));
  }
  assertEquals(JSON.stringify(out), JSON.stringify(whole));
  assertEquals(out.map((r) => r.chunk_idx), whole.map((r) => r.chunk_idx));
});

Deno.test("(B) page 를 무시한 고정 개수 컷은 **다르다** — 반례가 살아 있어야 한다", () => {
  const whole = mergeShortSections(splitLongSections(FIXTURE));
  for (const stride of [97, 251, 499]) {
    const out: ExtractedSection[] = [];
    for (let i = 0; i < FIXTURE.length; i += stride) {
      out.push(...mergeShortSections(splitLongSections(FIXTURE.slice(i, i + stride))));
    }
    assertNotEquals(JSON.stringify(out), JSON.stringify(whole), `stride ${stride}`);
  }
});

Deno.test("(D) extract 꼬리와 vision 첫 섹션 사이를 컷하면 **다르다**", () => {
  const whole = mergeShortSections(splitLongSections(FIXTURE));
  const ext = FIXTURE.slice(0, FIXTURE.length - 3);
  const vis = FIXTURE.slice(-3);
  const out = [
    ...mergeShortSections(splitLongSections(ext)),
    ...mergeShortSections(splitLongSections(vis)),
  ];
  assertNotEquals(JSON.stringify(out), JSON.stringify(whole));
  // 그래서 창 경계는 stage 경계가 아니라 **page 경계**여야 한다.
  assertEquals(out.length, 171);
  assertEquals(whole.length, 170);
});

// ---------------------------------------------------------------------------
// splitTailByPage
// ---------------------------------------------------------------------------

function s(page: number | null, text = "x"): ExtractedSection {
  return { text, page, section_title: null, bbox: null, metadata: {} };
}

Deno.test("splitTailByPage — 마지막 page 와 같은 연속 묶음만 떼어낸다", () => {
  const r = splitTailByPage([s(1), s(1), s(2), s(3), s(3), s(3)]);
  assertEquals(r.head.map((x) => x.page), [1, 1, 2]);
  assertEquals(r.tail.map((x) => x.page), [3, 3, 3]);
});

Deno.test("splitTailByPage — page 가 전부 같으면 head 가 빈다(전부 다음 창으로)", () => {
  const r = splitTailByPage([s(5), s(5), s(5)]);
  assertEquals(r.head, []);
  assertEquals(r.tail.length, 3);
});

Deno.test("splitTailByPage — null page 끼리도 한 묶음이다 (null === null)", () => {
  const r = splitTailByPage([s(1), s(null), s(null)]);
  assertEquals(r.head.map((x) => x.page), [1]);
  assertEquals(r.tail.map((x) => x.page), [null, null]);
});

Deno.test("splitTailByPage — null 앞에 다른 page 가 다시 나오면 거기서 끊긴다", () => {
  const r = splitTailByPage([s(null), s(null), s(7)]);
  assertEquals(r.head.map((x) => x.page), [null, null]);
  assertEquals(r.tail.map((x) => x.page), [7]);
});

Deno.test("splitTailByPage — 빈 배열", () => {
  assertEquals(splitTailByPage([]), { head: [], tail: [] });
});

// ---------------------------------------------------------------------------
// windowPlan — 현행 chunk.ts 의 순서 규칙을 그대로 옮겼는지
// ---------------------------------------------------------------------------

Deno.test("windowPlan — vision 은 extract **전부 뒤에**, 각 stage 안은 seq 순", () => {
  const p = windowPlan([
    { stage: "vision", seq: 4 },
    { stage: "extract", seq: 10 },
    { stage: "extract", seq: 0 },
    { stage: "vision", seq: 0 },
  ], 4);
  assertEquals(p.plan, [
    { stage: "extract", seq: 0 },
    { stage: "extract", seq: 10 },
    { stage: "vision", seq: 0 },
    { stage: "vision", seq: 4 },
  ]);
  assertEquals(p.totalWindows, 1);
  assertEquals([p.extractCount, p.visionCount, p.scanCount], [2, 2, 0]);
});

Deno.test("windowPlan — scan 이 있으면 extract 를 **대체**하고 vision 은 안 섞인다", () => {
  const p = windowPlan([
    { stage: "extract", seq: 0 },
    { stage: "scan", seq: 0 },
    { stage: "vision", seq: 0 },
  ], 4);
  assertEquals(p.plan, [{ stage: "scan", seq: 0 }]);
  assertEquals([p.extractCount, p.scanCount, p.visionCount], [1, 1, 1]);
});

Deno.test("windowPlan — totalWindows 는 올림이고, 빈 플랜도 1 이다", () => {
  const rows = Array.from({ length: 9 }, (_, i) => ({ stage: "extract", seq: i }));
  assertEquals(windowPlan(rows, 4).totalWindows, 3);
  assertEquals(windowPlan(rows, 9).totalWindows, 1);
  assertEquals(windowPlan(rows, 1).totalWindows, 9);
  // 빈 문서라도 part 를 하나는 남겨야 `load` 가 "순서가 깨졌다" 로 오해하지 않는다.
  assertEquals(windowPlan([], 4).totalWindows, 1);
});

Deno.test("readArtifactsPerTask — 정수 ≥1 만 받는다", () => {
  assertEquals(readArtifactsPerTask(() => undefined), 4);
  assertEquals(readArtifactsPerTask(() => "7"), 7);
  assertEquals(readArtifactsPerTask(() => "0"), 4);
  assertEquals(readArtifactsPerTask(() => "-3"), 4);
  assertEquals(readArtifactsPerTask(() => "abc"), 4);
  assertEquals(readArtifactsPerTask(() => "2.9"), 2);
});

// ---------------------------------------------------------------------------
// hfCounts 누적 — 창으로 나눠 세도 전체와 같은 판정이 나와야 한다
// ---------------------------------------------------------------------------

Deno.test("accumulateHfCounts — 창으로 나눠 세도 전체 카운트와 같다", () => {
  const whole = runChunkStage({ docId: "d1", sections: FIXTURE, env: ENV });
  const expected = collectShortCounts(whole);

  let counts: Record<string, number> = {};
  let idx = 0;
  for (const w of cutByWindows(FIXTURE, 200)) {
    const recs = runChunkStage({ docId: "d1", sections: w, env: ENV, idxOffset: idx });
    idx += recs.length;
    counts = accumulateHfCounts(counts, recs);
  }
  assertEquals(Object.keys(counts).length, expected.size);
  for (const [t, n] of expected) assertEquals(counts[t], n, t);
  // 판정(≥3회)도 같아야 한다 — 이게 `load` 가 실제로 쓰는 값이다.
  assertEquals(
    [...headerFooterTexts(Object.entries(counts))].sort(),
    [...headerFooterTexts(expected)].sort(),
  );
});

Deno.test("accumulateHfCounts — `__proto__` 가 텍스트여도 own property 로 센다", () => {
  const rec = (text: string, i: number) => ({
    doc_id: "d1",
    chunk_idx: i,
    text,
    page: 1,
    section_title: null,
    bbox: null,
    char_range: [0, text.length] as [number, number],
    metadata: {},
  });
  const counts = accumulateHfCounts({}, [rec("__proto__", 0), rec("__proto__", 1)]);
  assertEquals(counts["__proto__"], 2);
  assertEquals(Object.keys(counts), ["__proto__"]);
  // JSON 왕복 후에도 살아남아야 캐리로 나를 수 있다.
  const round = JSON.parse(JSON.stringify(counts));
  assertEquals(accumulateHfCounts(round, [rec("__proto__", 2)])["__proto__"], 3);
});

// ---------------------------------------------------------------------------
// mergeFlagsOr — 문서 flags 는 창을 넘어 OR 누적
// ---------------------------------------------------------------------------

Deno.test("mergeFlagsOr — 한 창에서만 true 여도 문서는 true 다", () => {
  let f = emptyDocFlags();
  f = mergeFlagsOr(f, { has_pii: false, has_watermark: false, third_party: false });
  f = mergeFlagsOr(f, { has_pii: true, has_watermark: false, third_party: false });
  f = mergeFlagsOr(f, { has_pii: false, has_watermark: false, third_party: false });
  assertEquals(f, { has_pii: true, has_watermark: false, third_party: false });
});

Deno.test("mergeFlagsOr — watermark_hits 는 합집합 + 코드포인트 정렬", () => {
  let f = emptyDocFlags();
  f = mergeFlagsOr(f, { has_watermark: true, watermark_hits: ["대외비"] });
  f = mergeFlagsOr(f, { has_watermark: true, watermark_hits: ["CONFIDENTIAL", "대외비"] });
  assertEquals(f.watermark_hits, ["CONFIDENTIAL", "대외비"]);
  assertEquals(f.has_watermark, true);
  // 키 순서도 단일 창(content_gate)과 같아야 한다.
  assertEquals(Object.keys(f), ["has_pii", "has_watermark", "third_party", "watermark_hits"]);
});

Deno.test("mergeFlagsOr — 히트가 없으면 watermark_hits 키 자체가 없다", () => {
  const f = mergeFlagsOr(emptyDocFlags(), { has_pii: false, has_watermark: false, third_party: false });
  assertEquals("watermark_hits" in f, false);
});
