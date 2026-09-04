/**
 * `api/app/config.py` 의 ENV 계약 포팅.
 *
 * ## 상태를 두지 않는다
 * Python 은 `@lru_cache` 로 `get_settings()` 를 싱글톤으로 잡는다. 그건 상주 프로세스 전제다.
 * Edge Functions 는 인스턴스 재사용이 보장되지 않으므로 캐시를 두면 디버깅 불가능한 불일치가
 * 생긴다. 요청마다 `loadSettings()` 를 부른다 — ENV 읽기 비용은 무시할 수준이다.
 *
 * ## 테스트 주입
 * `Deno.env` 를 직접 부르지 않고 `EnvReader` 를 인자로 받는다. 그래야 ENV 계약을
 * 순수 함수로 고정할 수 있다(`config_test.ts`).
 *
 * ## 원본과의 의도적 차이 1건
 * Python 은 `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` 가 없으면 **빈 문자열**로 두고
 * 한참 뒤 엉뚱한 지점에서 터진다. 여기서는 **로드 시점에 끊는다.** Edge 는 secrets 누락이
 * 흔한 실패 모드고, 빈 문자열로 진행하면 "인증은 통과했는데 데이터가 안 보인다" 같은
 * 진단하기 어려운 상태가 된다. 나머지 항목은 전부 Python 동작을 그대로 따른다.
 *
 * ## 플랜 초안에서 고친 것 3건 (2026-09-04, 원본 대조)
 * 1. `DEFAULT_USER_ID` — 초안은 `JETRAG_DEFAULT_USER_ID` 라고 썼지만 원본에 접두어가 없다.
 *    Railway 변수 목록도 `DEFAULT_USER_ID` 다. 이름이 틀리면 익명 사용자 UUID 가 통째로 바뀐다.
 * 2. 불리언 파싱 — 초안은 "허용 목록에 없으면 false" 였다. 원본은 **인식 불가 값이면 기본값 유지**다.
 *    기본값이 true 인 항목(`JETRAG_QUOTA_ENFORCEMENT_ENABLED`)에서 동작이 뒤집힌다.
 * 3. `JETRAG_CHUNK_UPSERT_BATCH_SIZE` — 초안은 상한 10,000 을 뒀지만 원본은 하한 1 만 있다.
 */

export type EnvReader = (key: string) => string | undefined;

export interface Settings {
  supabaseUrl: string;
  /** anon key (`SUPABASE_KEY`). 사용자 컨텍스트 호출에 쓴다. */
  supabaseAnonKey: string;
  supabaseServiceRoleKey: string;
  supabaseStorageBucket: string;
  geminiApiKey: string;
  hfApiToken: string;
  defaultUserId: string;
  ownerUserId: string | null;
  authEnabled: boolean;
  supabaseJwtSecret: string | null;
  supabaseJwtAlgorithm: string;
  supabaseJwksUrl: string | null;
  staleIngestJobHours: number;
  chunkUpsertBatchSize: number;
  quotaEnforcementEnabled: boolean;
  corsOrigins: string[];
}

/** `api/app/config.py` 의 `_STALE_INGEST_JOB_HOURS_*` 와 같은 값. */
const STALE_HOURS_DEFAULT = 24;
const STALE_HOURS_MIN = 1;
const STALE_HOURS_MAX = 168;

/** `api/app/main.py` 의 `_DEFAULT_CORS_ORIGINS`. */
const DEFAULT_CORS = "http://localhost:3001,http://localhost:3000";

const DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001";

const BOOL_TRUE = new Set(["true", "1", "yes", "on"]);
const BOOL_FALSE = new Set(["false", "0", "no", "off"]);

function required(read: EnvReader, key: string): string {
  const v = read(key);
  if (!v) throw new Error(`${key} 가 설정되지 않았습니다 (Edge Function secrets 를 확인하세요).`);
  return v;
}

/** Python `os.environ.get(k) or None` — 빈 문자열도 null 로 떨어뜨린다. */
function optional(read: EnvReader, key: string): string | null {
  const v = read(key);
  return v ? v : null;
}

/**
 * Python `_parse_bool` 과 동일.
 * 미설정·빈 문자열·인식 불가 값은 **기본값을 유지한다** (false 로 떨어뜨리지 않는다).
 */
function bool(read: EnvReader, key: string, fallback: boolean): boolean {
  const raw = read(key);
  if (raw === undefined || raw === "") return fallback;
  const v = raw.trim().toLowerCase();
  if (BOOL_TRUE.has(v)) return true;
  if (BOOL_FALSE.has(v)) return false;
  return fallback;
}

/** Python `_parse_int` 과 동일. 미설정·빈 문자열·비숫자는 기본값. 음수는 허용한다(clamp 는 호출부). */
function int(read: EnvReader, key: string, fallback: number): number {
  const raw = read(key);
  if (raw === undefined || raw === "") return fallback;
  // Python `int()` 는 "12abc" 를 거부한다. parseInt 는 12 로 읽으므로 전체 일치를 요구한다.
  if (!/^[+-]?\d+$/.test(raw.trim())) return fallback;
  const n = Number.parseInt(raw, 10);
  return Number.isNaN(n) ? fallback : n;
}

function clamp(n: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, n));
}

export function loadSettings(read: EnvReader = (k) => Deno.env.get(k)): Settings {
  return {
    supabaseUrl: required(read, "SUPABASE_URL"),
    supabaseAnonKey: read("SUPABASE_KEY") ?? "",
    supabaseServiceRoleKey: required(read, "SUPABASE_SERVICE_ROLE_KEY"),
    supabaseStorageBucket: read("SUPABASE_STORAGE_BUCKET") ?? "documents",
    geminiApiKey: read("GEMINI_API_KEY") ?? "",
    hfApiToken: read("HF_API_TOKEN") ?? "",
    // 접두어 없음 — §플랜 초안에서 고친 것 1 참조.
    defaultUserId: read("DEFAULT_USER_ID") ?? DEFAULT_USER_ID,
    ownerUserId: optional(read, "OWNER_USER_ID"),
    authEnabled: bool(read, "JETRAG_AUTH_ENABLED", false),
    supabaseJwtSecret: optional(read, "SUPABASE_JWT_SECRET"),
    supabaseJwtAlgorithm: read("SUPABASE_JWT_ALGORITHM") ?? "HS256",
    supabaseJwksUrl: optional(read, "SUPABASE_JWKS_URL"),
    // 비숫자면 default 24 로 떨어진 **뒤에** clamp — 1 이 아니다.
    staleIngestJobHours: clamp(
      int(read, "JETRAG_STALE_INGEST_JOB_HOURS", STALE_HOURS_DEFAULT),
      STALE_HOURS_MIN,
      STALE_HOURS_MAX,
    ),
    // 하한만 있고 상한은 없다 — §플랜 초안에서 고친 것 3 참조.
    chunkUpsertBatchSize: Math.max(1, int(read, "JETRAG_CHUNK_UPSERT_BATCH_SIZE", 50)),
    quotaEnforcementEnabled: bool(read, "JETRAG_QUOTA_ENFORCEMENT_ENABLED", true),
    corsOrigins: (read("JETRAG_CORS_ORIGINS") ?? DEFAULT_CORS)
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean),
  };
}
