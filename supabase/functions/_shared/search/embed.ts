/**
 * 질의 dense 임베딩 — `adapters/impl/bgem3_*_embedding.py` + `services/embed_query_cache.py` 포팅.
 *
 * ## 제공자가 둘이고 결과가 미세하게 다르다
 * `JETRAG_EMBED_PROVIDER` 로 HF ↔ DeepInfra 를 바꾼다(기본 `hf`). 같은 BGE-M3 모델이지만
 * 두 제공자의 출력은 **완전히 같지 않다** — 프로젝트 기록의 결정성 시험에서 최소 코사인
 * 0.999984 였다. 즉 제공자가 다르면 dense 순위가 미세하게 달라질 수 있다.
 *
 * 그래서 **둘 다 옮겼다.** 한쪽만 옮기면 운영자가 ENV 를 뒤집었을 때 Edge 에서만
 * 조용히 다른 제공자를 계속 쓰게 된다 — 플랜 §4 가 막으려는 바로 그 함정이다.
 *
 * | 제공자 | 엔드포인트 | 요청 | 응답 |
 * |---|---|---|---|
 * | `hf` | `router.huggingface.co/.../feature-extraction` | `{"inputs": text}` | 1 차원 배열 |
 * | `deepinfra` | `api.deepinfra.com/v1/openai/embeddings` | `{"model", "input"}` | `{"data":[{"embedding"}]}` |
 *
 * ## 캐시가 2 단이고, Edge 에서 1 단의 성격이 바뀐다
 * 원본은 프로세스 내 LRU(512) → DB `embed_query_cache` 순으로 본다. Edge 는 isolate 가
 * 휘발성이라 **모듈 스코프 LRU 가 요청 사이에 남는다는 보장이 없다.** 캐시 키·DB 계층은
 * 그대로라 **결과값은 같고**, 달라지는 건 `embed_cache_hit` 지표의 비율뿐이다.
 * (지표가 달라지는 건 조용한 차이가 아니므로 여기 적어 둔다.)
 *
 * 영구 캐시 키는 `sha256(NFC(strip(text)))` + `model_id="BAAI/bge-m3"` 로 제공자와
 * 무관하다 — 그래서 HF 로 만든 항목을 DeepInfra 가 그대로 쓴다(원본 의도).
 *
 * ## 실패 분류가 검색 동작을 가른다
 * transient(5xx·429·네트워크)면 sparse-only 로 낮춰서라도 응답하고, 4xx 면 **503 을 던진다.**
 * 토큰이 만료됐는데 한 달 동안 sparse-only 로 조용히 도는 걸 막으려는 설계라, 이 분류를
 * 잘못 옮기면 장애가 안 보이게 된다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

export const MODEL_SLUG = "BAAI/bge-m3";
export const DENSE_DIM = 1024;
const MAX_ATTEMPTS = 3;
const BASE_BACKOFF_SECONDS = 5.0;
const REQUEST_TIMEOUT_MS = 60_000;
/** 서버가 준 Retry-After 의 상한 — 이상한 값에 무한정 붙잡히지 않으려는 방어. */
const MAX_RETRY_AFTER_SECONDS = 60.0;
const LRU_MAXSIZE = 512;
const RETRYABLE_STATUS: ReadonlySet<number> = new Set([429, 500, 502, 503, 504]);

const HF_URL = `https://router.huggingface.co/hf-inference/models/${MODEL_SLUG}/pipeline/feature-extraction`;
const DEEPINFRA_URL = "https://api.deepinfra.com/v1/openai/embeddings";

/** Python `re` 의 `\s` 집합 — `strip()` 이 버리는 문자와 같다(전수 확인). */
const PY_SP = " \\t\\n\\r\\f\\v\\u001c-\\u001f\\u0085\\u00a0\\u1680\\u2000-\\u200a" +
  "\\u2028\\u2029\\u202f\\u205f\\u3000";
const PY_STRIP_RE = new RegExp(`^[${PY_SP}]+|[${PY_SP}]+$`, "gu");

export type EmbedProvider = "hf" | "deepinfra";

/** 알 수 없는 값은 기본 `hf` 로 떨어뜨린다 — ENV 오타로 기동이 깨지지 않게(원본 동작). */
export function resolveEmbedProvider(read: (k: string) => string | undefined): EmbedProvider {
  const choice = (read("JETRAG_EMBED_PROVIDER") ?? "hf").trim().toLowerCase();
  return choice === "deepinfra" ? "deepinfra" : "hf";
}

/** `JETRAG_EMBED_QUERY_CACHE` — `"0"` 일 때만 끈다. */
export function embedCacheEnabled(read: (k: string) => string | undefined): boolean {
  return (read("JETRAG_EMBED_QUERY_CACHE") ?? "1") !== "0";
}

/** 임베딩 호출 실패. `status` 가 있으면 HTTP 응답을 받은 것이다. */
export class EmbedError extends Error {
  constructor(message: string, readonly status?: number, readonly retryAfter?: number) {
    super(message);
    this.name = "EmbedError";
  }
}

/**
 * transient 인지 — 5xx·429·네트워크 오류만 true.
 * 4xx 는 false 라 호출부가 503 을 던진다(토큰 만료를 조용히 넘기지 않으려는 설계).
 */
export function isTransientEmbedError(e: unknown): boolean {
  if (e instanceof EmbedError) {
    // status 가 없으면 네트워크·타임아웃 — 재시도 가치가 있다.
    return e.status === undefined || RETRYABLE_STATUS.has(e.status);
  }
  // 응답 스키마 오류 등은 재시도해도 같으므로 transient 가 아니다(원본 동작).
  return false;
}

/** 영구 캐시 키 — 제공자와 무관하게 같은 값이 나와야 항목이 공유된다. */
export async function cacheKey(text: string): Promise<string> {
  const normalized = text.replace(PY_STRIP_RE, "").normalize("NFC");
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(normalized));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// --- 프로세스 내 LRU (Edge 에서는 isolate 수명만큼만 산다) --------------------
const lru = new Map<string, number[]>();

function lruGet(text: string): number[] | undefined {
  const v = lru.get(text);
  if (v === undefined) return undefined;
  // 최근 사용으로 올린다 — Map 은 삽입 순서를 지키므로 지웠다 다시 넣는다.
  lru.delete(text);
  lru.set(text, v);
  return v;
}

function lruSet(text: string, vec: number[]): void {
  lru.set(text, vec);
  while (lru.size > LRU_MAXSIZE) {
    const oldest = lru.keys().next().value as string;
    lru.delete(oldest);
  }
}

/** 테스트용 — LRU 를 비운다. 영구 캐시(DB)는 건드리지 않는다. */
export function clearEmbedLru(): void {
  lru.clear();
}

// --- 응답 파싱 ---------------------------------------------------------------

function parseHfResponse(data: unknown): number[] {
  if (!Array.isArray(data) || data.length === 0) {
    throw new EmbedError(`예상치 못한 BGE-M3 응답: ${typeof data}`);
  }
  if (typeof data[0] === "number") {
    if (data.length !== DENSE_DIM) {
      throw new EmbedError(`차원 불일치: 받은=${data.length}, 기대=${DENSE_DIM}`);
    }
    return data.map(Number);
  }
  // 드물게 배치 모양으로 1 건이 온다 — 이때는 차원을 검사하지 않는다(원본 그대로).
  if (Array.isArray(data[0])) return (data[0] as unknown[]).map(Number);
  throw new EmbedError(`예상치 못한 내부 타입: ${typeof data[0]}`);
}

function parseDeepInfraResponse(data: unknown): number[] {
  if (typeof data !== "object" || data === null || !("data" in data)) {
    throw new EmbedError("예상치 못한 DeepInfra 응답 스키마");
  }
  const items = (data as { data: unknown }).data;
  if (!Array.isArray(items) || items.length === 0) {
    throw new EmbedError("DeepInfra data 배열 비어있음");
  }
  const first = items[0];
  const emb = typeof first === "object" && first !== null
    ? (first as { embedding?: unknown }).embedding
    : null;
  if (!Array.isArray(emb)) throw new EmbedError("DeepInfra embedding 타입 비정상");
  if (emb.length !== DENSE_DIM) {
    throw new EmbedError(`차원 불일치: 받은=${emb.length}, 기대=${DENSE_DIM}`);
  }
  return emb.map(Number);
}

// --- HTTP + 재시도 -----------------------------------------------------------

/**
 * `Retry-After` → 대기 초. 없거나 파싱 실패면 null(호출부가 지수 백오프로 간다).
 *
 * 원본 규약을 그대로 따른다 — **정수만** 받고(`float(int(raw))` 이라 `"5.5"` 는 탈락),
 * 실패하면 HTTP-date 로 다시 시도하고, **0 이하는 null**, 마지막에 60 초로 클램프한다.
 * 클램프는 서버가 이상한 값을 줬을 때 무한정 기다리지 않으려는 방어다.
 */
export function parseRetryAfter(raw: string | null, now: number = Date.now()): number | null {
  if (!raw) return null;
  const t = raw.trim();
  let seconds: number;
  if (/^[+-]?\d+$/.test(t)) {
    seconds = Number.parseInt(t, 10);
  } else {
    const at = Date.parse(t);
    if (Number.isNaN(at)) return null;
    seconds = (at - now) / 1000;
  }
  if (seconds <= 0) return null;
  return Math.min(seconds, MAX_RETRY_AFTER_SECONDS);
}

async function postJson(url: string, headers: HeadersInit, body: unknown): Promise<unknown> {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), REQUEST_TIMEOUT_MS);
  let resp: Response;
  try {
    resp = await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      signal: ctl.signal,
    });
  } catch (e) {
    // 네트워크·타임아웃 — status 없이 던져서 transient 로 분류되게 한다.
    throw new EmbedError(`임베딩 요청 실패: ${e instanceof Error ? e.message : String(e)}`);
  } finally {
    clearTimeout(timer);
  }
  if (!resp.ok) {
    const retryAfter = parseRetryAfter(resp.headers.get("Retry-After"));
    // 본문을 비워두면 연결이 남으므로 읽어서 버린다.
    await resp.text().catch(() => "");
    throw new EmbedError(`임베딩 HTTP ${resp.status}`, resp.status, retryAfter ?? undefined);
  }
  return await resp.json();
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function withRetry<T>(fn: () => Promise<T>): Promise<T> {
  let last: unknown;
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    try {
      return await fn();
    } catch (e) {
      last = e;
      if (!isTransientEmbedError(e)) break;
      if (attempt === MAX_ATTEMPTS) break;
      const ra = e instanceof EmbedError ? e.retryAfter : undefined;
      const delay = ra !== undefined
        ? ra + Math.random()
        : BASE_BACKOFF_SECONDS * 2 ** (attempt - 1) + Math.random();
      await sleep(delay * 1000);
    }
  }
  throw last;
}

// --- 본체 -------------------------------------------------------------------

export interface EmbedDeps {
  read: (k: string) => string | undefined;
  /** 영구 캐시용. 없으면 DB 계층을 건너뛴다. */
  client?: SupabaseClient;
  /** 백그라운드 쓰기 등록 — Edge 에서는 `EdgeRuntime.waitUntil`. */
  waitUntil?: (p: Promise<unknown>) => void;
}

export interface EmbedQueryResult {
  vector: number[];
  /** LRU·영구 캐시 어느 쪽이든 외부 호출을 안 했으면 true. */
  cacheHit: boolean;
  cacheSource: "lru" | "persistent" | "miss";
  provider: EmbedProvider;
}

async function callProvider(
  text: string,
  provider: EmbedProvider,
  read: (k: string) => string | undefined,
): Promise<number[]> {
  if (provider === "deepinfra") {
    const token = read("DEEPINFRA_API_TOKEN") ?? "";
    if (!token) throw new EmbedError("DEEPINFRA_API_TOKEN 이 없습니다");
    const data = await postJson(DEEPINFRA_URL, {
      "Authorization": `Bearer ${token}`,
      "Content-Type": "application/json",
    }, { model: MODEL_SLUG, input: text });
    return parseDeepInfraResponse(data);
  }
  const token = read("HF_API_TOKEN") ?? "";
  if (!token) throw new EmbedError("HF_API_TOKEN 이 없습니다");
  const data = await postJson(HF_URL, {
    "Authorization": `Bearer ${token}`,
    "Content-Type": "application/json",
  }, { inputs: text });
  return parseHfResponse(data);
}

/** 영구 캐시 조회. 실패·차원 불일치는 조용히 miss 로 본다(검색을 막지 않는다). */
async function lookupPersistent(
  client: SupabaseClient,
  sha: string,
): Promise<number[] | null> {
  try {
    const { data } = await client
      .from("embed_query_cache")
      .select("vector, dim")
      .eq("text_sha256", sha)
      .eq("model_id", MODEL_SLUG)
      .limit(1);
    const row = data?.[0] as { vector?: unknown } | undefined;
    const raw = row?.vector;
    if (!Array.isArray(raw) || raw.length !== DENSE_DIM) return null;
    return raw.map(Number);
  } catch {
    return null;
  }
}

async function upsertPersistent(
  client: SupabaseClient,
  sha: string,
  vector: number[],
): Promise<void> {
  try {
    await client.from("embed_query_cache").upsert(
      { text_sha256: sha, model_id: MODEL_SLUG, dim: DENSE_DIM, vector },
      { onConflict: "text_sha256,model_id", ignoreDuplicates: true },
    );
  } catch {
    // best-effort — 다음 호출에서 다시 시도한다.
  }
}

/**
 * 질의 임베딩. LRU → 영구 캐시 → 제공자 호출 순으로 본다.
 * 실패는 `EmbedError` 로 던지고, 호출부가 `isTransientEmbedError` 로 갈라 처리한다.
 */
export async function embedQuery(text: string, deps: EmbedDeps): Promise<EmbedQueryResult> {
  const provider = resolveEmbedProvider(deps.read);

  const hit = lruGet(text);
  if (hit !== undefined) {
    return { vector: [...hit], cacheHit: true, cacheSource: "lru", provider };
  }

  const useCache = embedCacheEnabled(deps.read);
  let sha: string | null = null;
  if (useCache && deps.client) {
    sha = await cacheKey(text);
    const persisted = await lookupPersistent(deps.client, sha);
    if (persisted !== null) {
      lruSet(text, [...persisted]);
      return { vector: [...persisted], cacheHit: true, cacheSource: "persistent", provider };
    }
  }

  const vector = await withRetry(() => callProvider(text, provider, deps.read));

  if (useCache && deps.client && sha !== null) {
    const p = upsertPersistent(deps.client, sha, vector);
    // 응답을 늦추지 않는다 — Edge 는 `waitUntil` 이 스레드풀 fire-and-forget 자리다.
    if (deps.waitUntil) deps.waitUntil(p);
    else await p;
  }
  lruSet(text, [...vector]);
  return { vector, cacheHit: false, cacheSource: "miss", provider };
}
