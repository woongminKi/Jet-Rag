import { assertEquals } from "@std/assert";
import { QUOTA_WAIT_MS, RETRY } from "./client.ts";
import { CONCURRENCY, pickBatch, planJob, planPrecheck, planUpload } from "./scheduler.ts";

const NOW = 1_700_000_000_000;
const R = () => 0.5; // jitter 0

Deno.test("scheduler — 202 신규는 registered + 첫 폴링 예약", () => {
  const t = planUpload({ kind: "accepted", docId: "d1", jobId: "j1", duplicated: false }, 0, NOW, R);
  assertEquals(t.state, "registered");
  assertEquals(t.docId, "d1");
  assertEquals(t.jobId, "j1");
  assertEquals(t.nextAttemptAt, NOW + 30_000);
});

Deno.test("scheduler — 202 중복은 바로 done (파일 전체를 보냈지만 끝이다)", () => {
  const t = planUpload({ kind: "accepted", docId: "d2", jobId: null, duplicated: true }, 0, NOW, R);
  assertEquals(t.state, "done");
  assertEquals(t.docId, "d2");
});

Deno.test("scheduler — 4xx 제외는 재시도하지 않는다", () => {
  const t = planUpload({ kind: "excluded", code: "too_large", detail: "50MB 초과" }, 3, NOW, R);
  assertEquals(t.state, "excluded");
  assertEquals(t.nextAttemptAt, 0);
  assertEquals(t.lastError, "[too_large] 50MB 초과");
});

Deno.test("scheduler — 402 는 6시간 뒤 한도대기", () => {
  const t = planUpload({ kind: "quota", used: 900, limit: 1000, detail: "용량 초과" }, 0, NOW, R);
  assertEquals(t.state, "quota_wait");
  assertEquals(t.nextAttemptAt, NOW + QUOTA_WAIT_MS);
  assertEquals(t.lastError?.includes("900"), true);
});

Deno.test("scheduler — 재시도는 지수 백오프, Retry-After 가 있으면 그쪽을 쓴다", () => {
  const a = planUpload({ kind: "retry", detail: "500", retryAfterMs: null }, 0, NOW, R);
  assertEquals(a.state, "pending");
  assertEquals(a.nextAttemptAt, NOW + 5_000);
  const b = planUpload({ kind: "retry", detail: "500", retryAfterMs: null }, 2, NOW, R);
  assertEquals(b.nextAttemptAt, NOW + 20_000);
  const c = planUpload({ kind: "retry", detail: "429", retryAfterMs: 45_000 }, 0, NOW, R);
  assertEquals(c.nextAttemptAt, NOW + 45_000);
});

Deno.test("scheduler — 재시도 상한을 넘으면 failed 로 닫는다 (무한 재시도 금지)", () => {
  const t = planUpload({ kind: "retry", detail: "500", retryAfterMs: null }, RETRY.maxAttempts, NOW, R);
  assertEquals(t.state, "failed");
  assertEquals(t.lastError?.includes(`${RETRY.maxAttempts}`), true);
});

Deno.test("scheduler — 401/403 은 attempts 를 태우지 않고 루프를 멈춘다", () => {
  const a = planUpload({ kind: "auth", detail: "토큰 폐기" }, 1, NOW, R);
  assertEquals(a.state, "pending");
  assertEquals(a.nextAttemptAt, NOW);
  assertEquals(a.fatal, "auth");
  const b = planUpload({ kind: "forbidden", detail: "스코프 밖" }, 1, NOW, R);
  assertEquals(b.fatal, "forbidden");
});

Deno.test("scheduler — precheck 결과: existing 은 전송 없이 완료", () => {
  assertEquals(planPrecheck("existing", "d9", NOW).state, "done");
  assertEquals(planPrecheck("existing", "d9", NOW).docId, "d9");
  // failed 흔적·신규는 업로드 대상 (서버가 같은 행에 재시도를 건다)
  assertEquals(planPrecheck("failed", "d8", NOW).state, "pending");
  assertEquals(planPrecheck("new", undefined, NOW).state, "pending");
});

Deno.test("scheduler — 잡 상태 매핑", () => {
  assertEquals(planJob({ status: "queued", current_stage: null, error_msg: null }, NOW).state, "registered");
  assertEquals(
    planJob({ status: "running", current_stage: "extract", error_msg: null }, NOW).nextAttemptAt,
    NOW + 30_000,
  );
  assertEquals(planJob({ status: "completed", current_stage: null, error_msg: null }, NOW).state, "done");
  const f = planJob({ status: "failed", current_stage: "vision", error_msg: "파서 오류" }, NOW);
  assertEquals(f.state, "failed");
  assertEquals(f.lastError?.includes("파서 오류"), true);
  assertEquals(planJob({ status: "cancelled", current_stage: null, error_msg: null }, NOW).state, "failed");
});

Deno.test("scheduler — deferred_quota 는 실패가 아니다 (월초에 서버가 재투입)", () => {
  const t = planJob({ status: "deferred_quota", current_stage: "vision", error_msg: null }, NOW);
  assertEquals(t.state, "registered");
  assertEquals(t.nextAttemptAt, NOW + QUOTA_WAIT_MS);
  assertEquals(t.lastError?.includes("한도"), true);
});

Deno.test("scheduler — 잡이 아직 없으면 계속 폴링", () => {
  assertEquals(planJob(null, NOW).state, "registered");
  assertEquals(planJob(null, NOW).nextAttemptAt, NOW + 30_000);
});

Deno.test("scheduler — 동시성: 실시간 2, 백로그는 실시간이 완전히 빌 때만 1", () => {
  assertEquals(CONCURRENCY, { live: 2, backlog: 1 });
  // 실시간 대기 3, 진행 0 → 2건
  assertEquals(
    pickBatch({ livePending: 3, liveInFlight: 0, backlogPending: 10, backlogInFlight: 0 }),
    { live: 2, backlog: 0 },
  );
  // 실시간 진행 중이면 백로그는 손대지 않는다 — 서버 큐가 시간당 5건이라 방금 넣은 파일이 뒤로 밀린다.
  assertEquals(
    pickBatch({ livePending: 0, liveInFlight: 1, backlogPending: 10, backlogInFlight: 0 }),
    { live: 0, backlog: 0 },
  );
  // 실시간이 완전히 비면 백로그 1건
  assertEquals(
    pickBatch({ livePending: 0, liveInFlight: 0, backlogPending: 10, backlogInFlight: 0 }),
    { live: 0, backlog: 1 },
  );
  // 이미 백로그 1건이 돌고 있으면 추가 없음
  assertEquals(
    pickBatch({ livePending: 0, liveInFlight: 0, backlogPending: 10, backlogInFlight: 1 }),
    { live: 0, backlog: 0 },
  );
  // 대기보다 많이 잡지 않는다
  assertEquals(
    pickBatch({ livePending: 1, liveInFlight: 0, backlogPending: 0, backlogInFlight: 0 }),
    { live: 1, backlog: 0 },
  );
});
