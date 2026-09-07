/**
 * BGE-M3 임베딩 — `adapters/impl/bgem3_deepinfra_embedding.py` 포팅.
 *
 * ## 왜 DeepInfra 인가
 * 운영 ENV 가 `JETRAG_EMBED_PROVIDER=deepinfra` 다. HF 경로(`bgem3_hf_embedding`)는
 * cold-start 가 있어 v1.5 W-1 에 always-warm 인 이쪽으로 갈아탔다. 이식 대상은
 * **실제로 도는 쪽 하나**다.
 *
 * ## 배치 응답은 `index` 로 정렬한다
 * OpenAI 규격상 입력 순서를 따르지만 원본이 방어적으로 정렬한다. 여기서도 그대로 둔다 —
 * 순서가 틀리면 **엉뚱한 청크에 벡터가 박히고** 검색 결과가 조용히 망가진다.
 *
 * ## 재시도 정책도 그대로다
 * 3 회, transient(네트워크·429·5xx)만, backoff `5s × 2^(n-1) + jitter`,
 * `Retry-After` 헤더가 있으면 그걸 쓰되 60s 로 자른다(악의적 헤더 방어).
 * 4xx 인증 오류나 응답 파싱 실패는 **즉시** 실패한다 — 재시도해도 같다.
 *
 * ## Deno 에는 httpx 가 없다
 * 원본의 예외 분류(`httpx.ConnectError` 등)를 `fetch` 세계로 옮겼다. `fetch` 는
 * 네트워크 실패를 `TypeError` 로 뭉뚱그리고 타임아웃은 `AbortError` 로 준다. 그래서
 * **상태 코드가 있으면 코드로, 없으면 네트워크 오류로** 본다 — 원본의 분류 의도와 같다.
 */

/** 원본 상수. 바꾸면 기존 벡터와 차원·모델이 어긋난다. */
export const MODEL_SLUG = "BAAI/bge-m3";
export const EMBED_URL = "https://api.deepinfra.com/v1/openai/embeddings";
export const DENSE_DIM = 1024;

const MAX_ATTEMPTS = 3;
const BASE_BACKOFF_SECONDS = 5.0;
const REQUEST_TIMEOUT_MS = 60_000;
const MAX_RETRY_AFTER_SECONDS = 60.0;

/** 원본 `_RETRYABLE_STATUS_CODES`. */
const RETRYABLE_STATUS = new Set([429, 500, 502, 503, 504]);

/** 상태 코드를 들고 다니는 오류 — 재시도 판정에 쓴다. */
export class EmbedHttpError extends Error {
  constructor(
    readonly status: number,
    readonly retryAfter: string | null,
    body: string,
  ) {
    super(`DeepInfra ${status}: ${body.slice(0, 200)}`);
    this.name = "EmbedHttpError";
  }
}

function isRetryable(e: unknown): boolean {
  if (e instanceof EmbedHttpError) return RETRYABLE_STATUS.has(e.status);
  // 파싱 실패는 재시도해도 같다 — 원본도 RuntimeError 를 비-transient 로 본다.
  if (e instanceof SyntaxError) return false;
  if (e instanceof Error && e.name === "EmbedParseError") return false;
  // 나머지(TypeError=네트워크, AbortError=타임아웃)는 transient 로 본다.
  return true;
}

class EmbedParseError extends Error {
  constructor(msg: string) {
    super(msg);
    this.name = "EmbedParseError";
  }
}

/** 원본 `_parse_retry_after` — 초 또는 HTTP-date. 음수/무효는 무시, 60s 로 자른다. */
export function parseRetryAfter(raw: string | null, nowMs: number): number | null {
  if (!raw) return null;
  const s = raw.trim();
  let seconds: number;
  // 원본은 `float(int(raw))` 라 **부호 붙은 정수**만 받는다. "1.5" 는 `int()` 가
  // 던져서 날짜 경로로 간다. 음수를 여기서 배제하면 `Date.parse("-5")` 가 엉뚱한
  // 값을 내놓을 수 있어 원본과 갈린다(테스트가 잡았다).
  if (/^[+-]?\d+$/.test(s)) {
    seconds = Number(s);
  } else {
    const t = Date.parse(s);
    if (Number.isNaN(t)) return null;
    seconds = (t - nowMs) / 1000;
  }
  if (seconds <= 0) return null;
  return Math.min(seconds, MAX_RETRY_AFTER_SECONDS);
}

/** 원본 `_parse_batch_response`. 길이·차원·순서를 전부 확인한다. */
export function parseBatchResponse(data: unknown, expected: number): number[][] {
  if (data === null || typeof data !== "object" || !("data" in data)) {
    const keys = data && typeof data === "object" ? Object.keys(data) : [];
    throw new EmbedParseError(`예상치 못한 DeepInfra batch 응답 스키마: keys=${JSON.stringify(keys)}`);
  }
  const items = (data as { data: unknown }).data;
  if (!Array.isArray(items) || items.length !== expected) {
    throw new EmbedParseError(
      `배치 응답 길이 불일치: got=${Array.isArray(items) ? items.length : typeof items}, expect=${expected}`,
    );
  }
  // index 기준 정렬 — spec 상 보장되지만 방어. 순서가 틀리면 엉뚱한 청크에 박힌다.
  const sorted = [...items];
  let sortable = true;
  for (const it of sorted) {
    const idx = (it as { index?: unknown })?.index;
    if (idx !== undefined && !Number.isFinite(Number(idx))) sortable = false;
  }
  if (sortable) {
    sorted.sort((a, b) =>
      Number((a as { index?: number })?.index ?? 0) - Number((b as { index?: number })?.index ?? 0)
    );
  }

  const out: number[][] = [];
  for (let i = 0; i < sorted.length; i++) {
    const emb = (sorted[i] as { embedding?: unknown })?.embedding;
    if (!Array.isArray(emb) || emb.length !== DENSE_DIM) {
      throw new EmbedParseError(
        `item[${i}] 차원 불일치: len=${Array.isArray(emb) ? emb.length : typeof emb}`,
      );
    }
    out.push(emb.map(Number));
  }
  return out;
}

export interface EmbedDeps {
  token: string;
  /** 테스트 주입 — 실제 API 를 때리지 않는다. */
  fetchImpl?: typeof fetch;
  /** 테스트 주입 — 대기를 건너뛴다. */
  sleep?: (ms: number) => Promise<void>;
  now?: () => number;
  /** 테스트 주입 — jitter 를 고정한다. */
  jitter?: () => number;
}

const defaultSleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** 텍스트 배열을 한 번에 임베딩한다. 반환 순서는 입력 순서와 같다. */
export async function embedBatch(texts: string[], deps: EmbedDeps): Promise<number[][]> {
  if (texts.length === 0) return [];
  const doFetch = deps.fetchImpl ?? fetch;
  const sleep = deps.sleep ?? defaultSleep;
  const now = deps.now ?? Date.now;
  const jitter = deps.jitter ?? Math.random;

  let lastErr: unknown = null;
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    try {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS);
      let res: Response;
      try {
        res = await doFetch(EMBED_URL, {
          method: "POST",
          headers: {
            Authorization: `Bearer ${deps.token}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ model: MODEL_SLUG, input: texts }),
          signal: ctrl.signal,
        });
      } finally {
        clearTimeout(timer);
      }
      if (!res.ok) {
        throw new EmbedHttpError(res.status, res.headers.get("Retry-After"), await res.text());
      }
      return parseBatchResponse(await res.json(), texts.length);
    } catch (e) {
      lastErr = e;
      if (!isRetryable(e) || attempt === MAX_ATTEMPTS) break;
      const ra = e instanceof EmbedHttpError ? parseRetryAfter(e.retryAfter, now()) : null;
      const delay = ra !== null ? ra + jitter() : BASE_BACKOFF_SECONDS * Math.pow(2, attempt - 1) + jitter();
      await sleep(Math.round(delay * 1000));
    }
  }
  throw lastErr instanceof Error ? lastErr : new Error(String(lastErr));
}
