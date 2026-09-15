/**
 * 게이트가 **어디에 걸리고 어디에 안 걸리는지**를 고정한다.
 *
 * 범위를 넓히면 텍스트 PDF 인제스트가 Vision 한도 때문에 멈추고, 좁히면 image·pptx 가
 * 한도 밖에서 돈다. `extract` 는 첫 창만 보는 것도 계약이다 — 중간 창을 보류하면
 * 산출물이 반만 남은 채 다음 달까지 멈춘다.
 */

import { assertEquals } from "@std/assert";
import { makeQuotaGate, VISION_STAGES } from "./quota_gate.ts";

function fake(
  opts: { docType: string; used: number; limit: number; plan?: string },
  counts?: { selects: number },
) {
  const client = {
    from: () => ({
      select: () => ({
        eq: () => ({
          limit: () => {
            if (counts) counts.selects++;
            return Promise.resolve({
              data: [{ user_id: "u1", doc_type: opts.docType }],
              error: null,
            });
          },
        }),
      }),
    }),
    rpc: (fn: string) =>
      Promise.resolve({ data: fn === "vision_pages_used_since" ? opts.used : 0, error: null }),
  };
  const plan = () =>
    Promise.resolve({
      code: opts.plan ?? "free",
      answers_per_day: 5,
      storage_bytes_limit: 1,
      vision_pages_per_month: opts.limit,
    });
  return makeQuotaGate({
    // deno-lint-ignore no-explicit-any
    client: client as any,
    getPlan: plan,
    settings: { authEnabled: true, quotaEnforcementEnabled: true, ownerUserId: "owner" },
    nowMs: () => 0,
  });
}

Deno.test("VISION_STAGES 는 scan · vision · vision_missing · extract", () => {
  assertEquals([...VISION_STAGES].sort(), ["extract", "scan", "vision", "vision_missing"]);
});

Deno.test("chunk 같은 비 Vision 단계는 조회 없이 통과", async () => {
  const gate = fake({ docType: "pdf", used: 999, limit: 1 });
  assertEquals(await gate({ job_id: "j", doc_id: "d", stage: "chunk" }), { defer: false });
});

Deno.test("extract 는 image·pptx 일 때만 게이트, 그것도 첫 창(from 0)만", async () => {
  const pdfGate = fake({ docType: "pdf", used: 999, limit: 1 });
  assertEquals(await pdfGate({ job_id: "j", doc_id: "d", stage: "extract" }), { defer: false });
  const imgGate = fake({ docType: "image", used: 999, limit: 1 });
  assertEquals((await imgGate({ job_id: "j", doc_id: "d", stage: "extract" })).defer, true);
  assertEquals(
    await imgGate({ job_id: "j", doc_id: "d", stage: "extract", from: 4 }),
    { defer: false },
  );
});

Deno.test("한도 안이면 통과, 초과면 defer + 사유", async () => {
  assertEquals(
    await fake({ docType: "pdf", used: 99, limit: 100 })({
      job_id: "j",
      doc_id: "d",
      stage: "vision",
    }),
    { defer: false },
  );
  const r = await fake({ docType: "pdf", used: 100, limit: 100 })({
    job_id: "j",
    doc_id: "d",
    stage: "scan",
  });
  assertEquals(r.defer, true);
  if (r.defer) assertEquals(r.reason.includes("100/100"), true);
});

Deno.test("한도 0(무제한)은 통과", async () => {
  const gate = fake({ docType: "pdf", used: 999, limit: 0 });
  assertEquals(await gate({ job_id: "j", doc_id: "d", stage: "vision" }), { defer: false });
});

Deno.test("owner 는 quota 면제", async () => {
  const client = {
    from: () => ({
      select: () => ({
        eq: () => ({
          limit: () => Promise.resolve({ data: [{ user_id: "owner", doc_type: "pdf" }], error: null }),
        }),
      }),
    }),
    rpc: () => Promise.resolve({ data: 999, error: null }),
  };
  const gate = makeQuotaGate({
    // deno-lint-ignore no-explicit-any
    client: client as any,
    getPlan: () =>
      Promise.resolve({
        code: "free",
        answers_per_day: 5,
        storage_bytes_limit: 1,
        vision_pages_per_month: 1,
      }),
    settings: { authEnabled: true, quotaEnforcementEnabled: true, ownerUserId: "owner" },
    nowMs: () => 0,
  });
  assertEquals(await gate({ job_id: "j", doc_id: "d", stage: "vision" }), { defer: false });
});

Deno.test("같은 문서의 태스크가 연달아 와도 documents 조회는 1회다", async () => {
  // PDF 한 장씩 도는 게 정상 패턴이라, 캐시가 없으면 페이지 수만큼 select 가 붙는다.
  const counts = { selects: 0 };
  const gate = fake({ docType: "pdf", used: 0, limit: 100 }, counts);
  await gate({ job_id: "j", doc_id: "d1", stage: "vision", from: 0 });
  await gate({ job_id: "j", doc_id: "d1", stage: "vision", from: 4 });
  assertEquals(counts.selects, 1);
  // 다른 문서는 따로 센다.
  await gate({ job_id: "j", doc_id: "d2", stage: "vision" });
  assertEquals(counts.selects, 2);
});
