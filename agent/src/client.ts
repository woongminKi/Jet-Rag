/**
 * 서버 접점 — precheck · upload · batch-status.
 *
 * ## HTTP 를 아는 유일한 모듈이다
 * 바깥(scheduler·run)은 `UploadOutcome` 6갈래와 `ApiError` 4갈래만 본다. 상태코드 분기가
 * 여러 곳에 흩어지면 서버 계약이 바뀔 때 한 곳만 고치고 넘어가게 된다.
 *
 * ## 한국어 문구를 매칭하지 않는다
 * 서버는 모든 업로드 4xx 에 `code` 를 싣는다(스펙 §10). 분기는 `code` 로만 한다.
 *
 * ## 5xx 는 JSON 이 아니다
 * FastAPI 동등성 때문에 `text/plain "Internal Server Error"` 다. `res.json()` 을 먼저
 * 부르면 파싱 예외가 나서 "네트워크 오류"로 오분류된다 — content-type 을 먼저 본다.
 *
 * ## 429 는 사용자 단위다
 * 분당 60건 상한의 키가 `user_id` 라 웹·다른 기기와 나눠 쓴다(스펙 §10). 그래서 429 는
 * 이 파일 하나의 문제가 아니라 **전역 일시정지** 신호다(`run.ts` 가 처리).
 */

import { userAgent, VERSION } from "./version.ts";

export const RETRY = {
  baseMs: 5_000,
  maxMs: 300_000,
  factor: 2,
  jitterRatio: 0.2,
  maxAttempts: 8,
} as const;

/** 402 뒤 재시도 간격 (스펙 §5.2). */
export const QUOTA_WAIT_MS = 6 * 60 * 60 * 1000;
/** 서버 큐가 시간당 ~5건이라 잦은 폴링은 의미가 없다. */
export const POLL_INTERVAL_MS = 30_000;

export const PRECHECK_CHUNK = 200;
export const BATCH_STATUS_CHUNK = 50;

/** 5s → 10 → 20 … 300s 상한. jitter ±20% — 여러 기기가 동시에 재시도해 429 를 만들지 않게. */
export function backoffMs(attempts: number, rand: () => number = Math.random): number {
  const raw = Math.min(RETRY.maxMs, RETRY.baseMs * Math.pow(RETRY.factor, Math.max(0, attempts - 1)));
  const jitter = raw * RETRY.jitterRatio * (rand() * 2 - 1);
  return Math.max(1000, Math.round(raw + jitter));
}

export function parseRetryAfterMs(header: string | null, now: number = Date.now()): number | null {
  if (header === null) return null;
  const secs = Number(header.trim());
  if (Number.isFinite(secs) && secs >= 0) return Math.round(secs * 1000);
  const at = Date.parse(header);
  if (Number.isFinite(at)) return Math.max(0, at - now);
  return null;
}

export type UploadOutcome =
  | { kind: "accepted"; docId: string; jobId: string | null; duplicated: boolean }
  | { kind: "excluded"; code: string; detail: string }
  | { kind: "quota"; used: number; limit: number; detail: string }
  | { kind: "retry"; detail: string; retryAfterMs: number | null }
  | { kind: "auth"; detail: string }
  | { kind: "forbidden"; detail: string };

export type ApiErrorKind = "auth" | "forbidden" | "retry" | "client";

export class ApiError extends Error {
  constructor(
    readonly kind: ApiErrorKind,
    readonly status: number,
    message: string,
    readonly retryAfterMs: number | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export type PrecheckState = "existing" | "failed" | "new";
export interface PrecheckEntry {
  state: PrecheckState;
  doc_id?: string;
}

export interface JobInfo {
  job_id?: string | null;
  status: string;
  current_stage: string | null;
  error_msg: string | null;
}

export interface ClientOptions {
  apiBase: string;
  token: string;
  os?: string;
  fetchImpl?: typeof fetch;
  requestTimeoutMs?: number;
  uploadTimeoutMs?: number;
}

interface Parsed {
  status: number;
  json: Record<string, unknown> | null;
  text: string;
  retryAfterMs: number | null;
}

async function parse(res: Response): Promise<Parsed> {
  const ct = (res.headers.get("content-type") ?? "").toLowerCase();
  const text = await res.text();
  let json: Record<string, unknown> | null = null;
  if (ct.includes("application/json")) {
    try {
      json = JSON.parse(text) as Record<string, unknown>;
    } catch {
      json = null;
    }
  }
  return {
    status: res.status,
    json,
    text,
    retryAfterMs: parseRetryAfterMs(res.headers.get("retry-after")),
  };
}

function detailOf(p: Parsed): string {
  const d = p.json?.["detail"];
  if (typeof d === "string" && d !== "") return d;
  if (Array.isArray(d)) return JSON.stringify(d);
  const t = p.text.trim();
  return t === "" ? `HTTP ${p.status}` : t.slice(0, 300);
}

/** 업로드 응답 → 상태 기계가 쓰는 6갈래. */
export function classifyUpload(p: Parsed): UploadOutcome {
  const detail = detailOf(p);
  if (p.status === 202) {
    const j = p.json ?? {};
    return {
      kind: "accepted",
      docId: String(j["doc_id"] ?? ""),
      jobId: typeof j["job_id"] === "string" ? j["job_id"] : null,
      duplicated: j["duplicated"] === true,
    };
  }
  if (p.status === 401) return { kind: "auth", detail };
  if (p.status === 403) return { kind: "forbidden", detail };
  if (p.status === 402) {
    const j = p.json ?? {};
    return { kind: "quota", used: Number(j["used"] ?? 0), limit: Number(j["limit"] ?? 0), detail };
  }
  if (p.status === 429 || p.status === 408 || p.status >= 500) {
    return { kind: "retry", detail, retryAfterMs: p.retryAfterMs };
  }
  if (p.status === 400 || p.status === 413 || p.status === 422) {
    const code = typeof p.json?.["code"] === "string"
      ? (p.json["code"] as string)
      : (p.status === 422 ? "form" : "unknown");
    return { kind: "excluded", code, detail };
  }
  // 계약에 없는 4xx — 재시도해도 안 바뀔 가능성이 크다. 영구 제외로 남기고 사유를 보존한다.
  return { kind: "excluded", code: `http_${p.status}`, detail };
}

function toApiError(p: Parsed): ApiError {
  const detail = detailOf(p);
  if (p.status === 401) return new ApiError("auth", 401, detail);
  if (p.status === 403) return new ApiError("forbidden", 403, detail);
  if (p.status === 429 || p.status === 408 || p.status >= 500) {
    return new ApiError("retry", p.status, detail, p.retryAfterMs);
  }
  return new ApiError("client", p.status, detail);
}

export class ApiClient {
  readonly #base: string;
  readonly #token: string;
  readonly #ua: string;
  readonly #fetch: typeof fetch;
  readonly #requestTimeoutMs: number;
  readonly #uploadTimeoutMs: number;

  constructor(opts: ClientOptions) {
    this.#base = opts.apiBase.replace(/\/+$/, "");
    this.#token = opts.token;
    this.#ua = userAgent(opts.os ?? Deno.build.os);
    this.#fetch = opts.fetchImpl ?? fetch;
    this.#requestTimeoutMs = opts.requestTimeoutMs ?? 15_000;
    this.#uploadTimeoutMs = opts.uploadTimeoutMs ?? 120_000;
  }

  get version(): string {
    return VERSION;
  }

  #headers(extra: Record<string, string> = {}): Headers {
    return new Headers({
      "Authorization": `Bearer ${this.#token}`,
      "User-Agent": this.#ua,
      ...extra,
    });
  }

  async #send(path: string, init: RequestInit, timeoutMs: number): Promise<Parsed> {
    const res = await this.#fetch(`${this.#base}${path}`, {
      ...init,
      signal: AbortSignal.timeout(timeoutMs),
    });
    return await parse(res);
  }

  /** 업로드는 **던지지 않는다** — 네트워크 실패도 `retry` 로 돌려준다. */
  async upload(file: { name: string; bytes: Uint8Array; title?: string }): Promise<UploadOutcome> {
    const form = new FormData();
    form.append("file", new Blob([file.bytes as unknown as BlobPart]), file.name);
    form.append("source_channel", "pc-agent");
    if (file.title) form.append("title", file.title);
    try {
      const p = await this.#send(
        "/documents",
        { method: "POST", headers: this.#headers(), body: form },
        this.#uploadTimeoutMs,
      );
      return classifyUpload(p);
    } catch (e) {
      // AbortError(타임아웃) 포함. 서버가 받았을 수도 있지만 UNIQUE 충돌은 duplicated 로 닫힌다.
      return { kind: "retry", detail: `요청 실패: ${(e as Error).message}`, retryAfterMs: null };
    }
  }

  /** 해시 목록을 200개씩 끊어 보낸다(서버 상한 500, 내부 `.in()` 은 100). */
  async precheck(hashes: string[]): Promise<Record<string, PrecheckEntry>> {
    const out: Record<string, PrecheckEntry> = {};
    for (let i = 0; i < hashes.length; i += PRECHECK_CHUNK) {
      const chunk = hashes.slice(i, i + PRECHECK_CHUNK);
      const p = await this.#send("/documents/precheck", {
        method: "POST",
        headers: this.#headers({ "content-type": "application/json" }),
        body: JSON.stringify({ hashes: chunk }),
      }, this.#requestTimeoutMs);
      if (p.status !== 200) throw toApiError(p);
      const results = (p.json?.["results"] ?? {}) as Record<string, PrecheckEntry>;
      for (const [k, v] of Object.entries(results)) out[k] = v;
    }
    return out;
  }

  /** `BATCH_STATUS_MAX_IDS = 50`. 소유하지 않은 id 는 응답에서 빠진다(404 아님). */
  async batchStatus(docIds: string[]): Promise<Map<string, JobInfo | null>> {
    const out = new Map<string, JobInfo | null>();
    for (let i = 0; i < docIds.length; i += BATCH_STATUS_CHUNK) {
      const chunk = docIds.slice(i, i + BATCH_STATUS_CHUNK);
      const p = await this.#send(
        `/documents/batch-status?ids=${chunk.map(encodeURIComponent).join(",")}`,
        { method: "GET", headers: this.#headers() },
        this.#requestTimeoutMs,
      );
      if (p.status !== 200) throw toApiError(p);
      const items = (p.json?.["items"] ?? []) as { doc_id: string; job: JobInfo | null }[];
      for (const it of items) out.set(it.doc_id, it.job ?? null);
    }
    return out;
  }
}
