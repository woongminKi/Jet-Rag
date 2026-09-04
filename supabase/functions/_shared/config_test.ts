/**
 * `config.ts` 는 `api/app/config.py` 의 ENV 계약을 그대로 재현해야 한다.
 *
 * 이 테스트는 "동작하는가"가 아니라 **"Python 과 같은가"** 를 고정한다.
 * 이름이나 기본값이 하나만 어긋나도 이관 후 동작이 조용히 달라지기 때문이다
 * (2026-09-04 Step 0 에서 확인 — 코드가 읽는 ENV 45개 중 Railway 설정은 24개뿐이고,
 * 나머지는 **기본값이 곧 현재 운영 동작**이다).
 *
 * 원본 근거: `api/app/config.py` `get_settings()` / `_parse_bool` / `_parse_int`,
 * `api/app/main.py` 의 `_DEFAULT_CORS_ORIGINS`.
 */

import { assertEquals, assertThrows } from "@std/assert";
import { loadSettings } from "./config.ts";

/** 필수값만 채운 최소 ENV. 각 테스트에서 필요한 키를 덧붙인다. */
function baseEnv(extra: Record<string, string> = {}): (k: string) => string | undefined {
  const m = new Map<string, string>([
    ["SUPABASE_URL", "https://abc.supabase.co"],
    ["SUPABASE_SERVICE_ROLE_KEY", "svc-key"],
    ...Object.entries(extra),
  ]);
  return (k) => m.get(k);
}

Deno.test("필수 ENV 미설정 시 어떤 키가 빠졌는지 알려주며 실패한다", () => {
  // Python 은 빈 문자열 fallback 이라 한참 뒤에 엉뚱한 곳에서 터진다.
  // Edge 에서는 **의도적으로** 로드 시점에 끊는다 (config.ts 헤더의 §의도적 차이 참조).
  assertThrows(() => loadSettings(() => undefined), Error, "SUPABASE_URL");
  assertThrows(
    () => loadSettings((k) => (k === "SUPABASE_URL" ? "https://abc.supabase.co" : undefined)),
    Error,
    "SUPABASE_SERVICE_ROLE_KEY",
  );
});

Deno.test("DEFAULT_USER_ID 는 JETRAG_ 접두어가 없다", () => {
  // 플랜 초안이 `JETRAG_DEFAULT_USER_ID` 로 적었는데 원본은 접두어가 없다.
  // Railway 변수 목록에도 `DEFAULT_USER_ID` 로 설정돼 있다.
  const s = loadSettings(baseEnv({ DEFAULT_USER_ID: "11111111-1111-1111-1111-111111111111" }));
  assertEquals(s.defaultUserId, "11111111-1111-1111-1111-111111111111");

  // 접두어 붙은 이름은 무시돼야 한다 — 잡히면 기본값이 그대로 나온다.
  const s2 = loadSettings(baseEnv({ JETRAG_DEFAULT_USER_ID: "22222222-2222-2222-2222-222222222222" }));
  assertEquals(s2.defaultUserId, "00000000-0000-0000-0000-000000000001");
});

Deno.test("불리언 파싱은 Python _parse_bool 과 같다", () => {
  for (const v of ["true", "TRUE", "1", "yes", "on", " On "]) {
    assertEquals(loadSettings(baseEnv({ JETRAG_AUTH_ENABLED: v })).authEnabled, true, `${v} → true`);
  }
  for (const v of ["false", "FALSE", "0", "no", "off"]) {
    assertEquals(loadSettings(baseEnv({ JETRAG_AUTH_ENABLED: v })).authEnabled, false, `${v} → false`);
  }
});

Deno.test("불리언은 인식 불가 값·빈 문자열이면 기본값을 유지한다", () => {
  // 여기가 플랜 초안과 갈린 지점이다. 초안은 "목록에 없으면 false" 라서
  // 기본값이 true 인 항목(quota)에서 동작이 뒤집힌다.
  assertEquals(
    loadSettings(baseEnv({ JETRAG_QUOTA_ENFORCEMENT_ENABLED: "garbage" })).quotaEnforcementEnabled,
    true,
  );
  assertEquals(loadSettings(baseEnv({ JETRAG_QUOTA_ENFORCEMENT_ENABLED: "" })).quotaEnforcementEnabled, true);
  assertEquals(
    loadSettings(baseEnv({ JETRAG_QUOTA_ENFORCEMENT_ENABLED: "off" })).quotaEnforcementEnabled,
    false,
  );
  // 반대로 기본값 false 인 항목도 인식 불가 값이면 그대로 false.
  assertEquals(loadSettings(baseEnv({ JETRAG_AUTH_ENABLED: "garbage" })).authEnabled, false);
});

Deno.test("stale_ingest_job_hours 는 [1,168] clamp, 비숫자는 기본값 24", () => {
  assertEquals(loadSettings(baseEnv()).staleIngestJobHours, 24);
  assertEquals(loadSettings(baseEnv({ JETRAG_STALE_INGEST_JOB_HOURS: "999" })).staleIngestJobHours, 168);
  assertEquals(loadSettings(baseEnv({ JETRAG_STALE_INGEST_JOB_HOURS: "0" })).staleIngestJobHours, 1);
  assertEquals(loadSettings(baseEnv({ JETRAG_STALE_INGEST_JOB_HOURS: "-5" })).staleIngestJobHours, 1);
  assertEquals(loadSettings(baseEnv({ JETRAG_STALE_INGEST_JOB_HOURS: "48" })).staleIngestJobHours, 48);
  // 비숫자는 default 24 로 떨어진 뒤 clamp — 1 이 아니다.
  assertEquals(loadSettings(baseEnv({ JETRAG_STALE_INGEST_JOB_HOURS: "abc" })).staleIngestJobHours, 24);
});

Deno.test("chunk_upsert_batch_size 는 하한 1 만 있고 상한이 없다", () => {
  // 플랜 초안은 상한 10,000 을 뒀지만 원본은 `max(1, parse_int(...))` 로 상한이 없다.
  assertEquals(loadSettings(baseEnv()).chunkUpsertBatchSize, 50);
  assertEquals(loadSettings(baseEnv({ JETRAG_CHUNK_UPSERT_BATCH_SIZE: "0" })).chunkUpsertBatchSize, 1);
  assertEquals(loadSettings(baseEnv({ JETRAG_CHUNK_UPSERT_BATCH_SIZE: "-3" })).chunkUpsertBatchSize, 1);
  assertEquals(
    loadSettings(baseEnv({ JETRAG_CHUNK_UPSERT_BATCH_SIZE: "50000" })).chunkUpsertBatchSize,
    50000,
  );
});

Deno.test("CORS origins 는 콤마 분리 + 공백 제거, 미설정 시 localhost 2개", () => {
  assertEquals(loadSettings(baseEnv()).corsOrigins, [
    "http://localhost:3001",
    "http://localhost:3000",
  ]);
  assertEquals(
    loadSettings(baseEnv({ JETRAG_CORS_ORIGINS: " https://a.com , ,https://b.com " })).corsOrigins,
    ["https://a.com", "https://b.com"],
  );
});

Deno.test("선택 ENV 의 기본값이 Python 과 같다", () => {
  const s = loadSettings(baseEnv());
  assertEquals(s.supabaseStorageBucket, "documents");
  assertEquals(s.supabaseJwtAlgorithm, "HS256");
  assertEquals(s.geminiApiKey, "");
  assertEquals(s.hfApiToken, "");
  assertEquals(s.supabaseAnonKey, "");
  // 미설정은 null — 빈 문자열이 아니다 (Python 의 `or None`).
  assertEquals(s.ownerUserId, null);
  assertEquals(s.supabaseJwtSecret, null);
  assertEquals(s.supabaseJwksUrl, null);
});

Deno.test("빈 문자열로 설정된 선택 ENV 는 null 이다", () => {
  // Python 의 `os.environ.get(...) or None` 과 같은 동작. `""` 를 값으로 취급하면
  // JWT 검증이 빈 시크릿으로 진행돼 조용히 실패한다.
  const s = loadSettings(baseEnv({ SUPABASE_JWT_SECRET: "", OWNER_USER_ID: "" }));
  assertEquals(s.supabaseJwtSecret, null);
  assertEquals(s.ownerUserId, null);
});
