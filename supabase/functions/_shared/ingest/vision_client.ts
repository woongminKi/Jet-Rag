/**
 * Gemini Vision 호출 — `gemini_vision.GeminiVisionCaptioner.caption` + `_gemini_common.with_retry`.
 *
 * ## SDK 대신 REST
 * `google-genai` 파이썬 SDK 가 Deno 에 없다. `v1beta/models/{model}:generateContent`
 * 를 직접 부른다. 요청 본문·프롬프트·파트 순서는 `vision_caption.ts` 가 이미 원본과
 * 맞춰 뒀다(**이미지가 먼저, 프롬프트가 뒤**).
 *
 * ## `response.text` 는 파트 이어붙이기다
 * SDK 의 `.text` 는 첫 후보의 파트 중 `text` 를 가진 것들을 이어 붙인다. REST 응답에서
 * 같은 방식으로 만든다 — 파트가 하나뿐이라 대개 차이가 없지만, 규칙은 규칙이다.
 *
 * ## 재시도는 기본값이 1 이다 = 재시도 안 한다
 * `JETRAG_GEMINI_RETRY` 기본 1. 2026-05-06 에 3 → 1 로 내렸다 — sweep 이 페이지 단위
 * 재시도를 이미 보장하는데 retry 까지 곱하면 50p PDF 에서 호출이 450 회가 된다.
 * 백오프의 `random.uniform(0, 0.5)` 는 재시도가 켜졌을 때만 쓰이고, 그때는 어차피
 * 양쪽이 다른 난수를 뽑는다.
 */

import {
  buildVisionRequestBody,
  DEFAULT_VISION_MODEL,
  parseVisionResponse,
  toBase64,
  type VisionCaption,
} from "./vision_caption.ts";

const GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models";
const BASE_BACKOFF_MS = 1000;

/** 원본 `_MAX_ATTEMPTS` — ENV 없으면 1(= 재시도 없음). */
export function maxAttempts(env: Record<string, string | undefined>): number {
  const raw = env["JETRAG_GEMINI_RETRY"];
  if (raw === undefined) return 1;
  const n = Number.parseInt(raw, 10);
  return Number.isFinite(n) ? n : 1;
}

/** 원본 `response.text` — 첫 후보의 텍스트 파트를 이어 붙인다. 없으면 null. */
export function responseText(resp: Record<string, unknown>): string | null {
  const candidates = resp["candidates"];
  if (!Array.isArray(candidates) || candidates.length === 0) return null;
  const content = (candidates[0] as Record<string, unknown>)?.["content"];
  const parts = (content as Record<string, unknown>)?.["parts"];
  if (!Array.isArray(parts)) return null;
  const texts: string[] = [];
  for (const p of parts) {
    const t = (p as Record<string, unknown>)?.["text"];
    if (typeof t === "string") texts.push(t);
  }
  return texts.length === 0 ? null : texts.join("");
}

export class VisionCallError extends Error {
  /** `image_parser` 실패 경로가 `record_call(retry_attempt=...)` 로 흘려보내는 값. */
  readonly retryAttempt: number;
  constructor(message: string, retryAttempt: number) {
    super(message);
    this.name = "VisionCallError";
    this.retryAttempt = retryAttempt;
  }
}

export interface VisionClientDeps {
  apiKey: string;
  env: Record<string, string | undefined>;
  model?: string;
  /** 테스트 주입 — 실제 호출 없이 응답을 넣는다. */
  fetchImpl?: typeof fetch;
  /** 테스트 주입 — 백오프 대기. */
  sleep?: (ms: number) => Promise<void>;
}

/**
 * 이미지 1장 → `VisionCaption`.
 *
 * 실패는 `VisionCallError` 로 던진다. 호출자(`vision_enrich`)가 페이지 단위로 잡아
 * sweep 재시도 대상에 넣는다 — 문서 전체를 죽이지 않는다.
 */
export async function captionImage(
  deps: VisionClientDeps,
  imageBytes: Uint8Array,
  mimeType: string,
): Promise<VisionCaption> {
  if (!deps.apiKey) {
    throw new VisionCallError("GEMINI_API_KEY 가 설정되지 않았습니다.", 0);
  }
  const model = deps.model ?? DEFAULT_VISION_MODEL;
  const body = buildVisionRequestBody(toBase64(imageBytes), mimeType);
  const doFetch = deps.fetchImpl ?? fetch;
  const sleep = deps.sleep ?? ((ms: number) => new Promise((r) => setTimeout(r, ms)));
  const attempts = maxAttempts(deps.env);

  let used = 0;
  let lastError = "";
  for (let attempt = 1; attempt <= attempts; attempt++) {
    used = attempt;
    try {
      const res = await doFetch(
        `${GEMINI_ENDPOINT}/${model}:generateContent`,
        {
          method: "POST",
          headers: {
            "content-type": "application/json",
            // 키를 쿼리스트링에 실으면 로그·프록시에 남는다. 헤더로 보낸다.
            "x-goog-api-key": deps.apiKey,
          },
          body: JSON.stringify(body),
        },
      );
      if (!res.ok) {
        const detail = (await res.text()).slice(0, 500);
        throw new Error(`Gemini Vision HTTP ${res.status}: ${detail}`);
      }
      const resp = await res.json() as Record<string, unknown>;
      const text = responseText(resp);
      if (text === null || text.trim() === "") {
        // 원본은 응답 객체 repr 을 붙인다 — SDK 객체라 재현이 안 되므로 JSON 을 넣는다.
        throw new Error(
          `Gemini Vision 응답이 비어있습니다: ${JSON.stringify(resp).slice(0, 500)}`,
        );
      }
      return parseVisionResponse(text, { resp, model, retryAttempt: attempt });
    } catch (e) {
      lastError = String(e);
      if (attempt === attempts) break;
      const delay = BASE_BACKOFF_MS * 2 ** (attempt - 1) + Math.random() * 500;
      console.warn(
        `gemini.vision.caption 실패(attempt=${attempt}/${attempts}, ` +
          `delay=${(delay / 1000).toFixed(1)}s): ${lastError}`,
      );
      await sleep(delay);
    }
  }
  throw new VisionCallError(lastError, used);
}
