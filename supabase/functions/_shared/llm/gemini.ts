/**
 * Gemini `generateContent` 호출 — `adapters/impl/gemini_llm.py` + `_gemini_common.py` 포팅.
 *
 * Python 은 `google-genai` SDK 를 쓰고 Edge 에는 그게 없다. 그래서 REST 를 직접 부르는데,
 * **요청 모양을 문서에서 짐작하지 않고 SDK 가 실제로 보내는 것을 캡처해서 맞췄다**
 * (2026-09-06, `BaseApiClient.request` 를 가로채 덤프):
 *
 * ```
 * POST https://generativelanguage.googleapis.com/v1beta/models/<model>:generateContent
 * x-goog-api-key: <키>
 * {
 *   "contents": [{ "parts": [{ "text": "..." }], "role": "user" }],
 *   "systemInstruction": { "parts": [{ "text": "..." }], "role": "user" },
 *   "generationConfig": { "temperature": 0.2 }
 * }
 * ```
 *
 * 짐작했으면 틀렸을 것 2 가지:
 * - `systemInstruction` 의 `role` 이 **`"user"`** 다(`"system"` 이 아니다).
 * - system 은 `contents` 에 들어가지 않고 별도 필드다. 여러 개면 `\n\n` 로 잇는다.
 *
 * ## 응답의 `text` 조립 규칙도 SDK 소스를 보고 옮겼다
 * `GenerateContentResponse._get_text()` — 첫 candidate 만 쓰고, `part.thought === true`
 * 인 파트는 **건너뛴다**. `typeof part.text === "string"` 인 파트만 이어붙이고, 그런 파트가
 * 하나도 없으면 `null` 이다. 빈 문자열 `""` 과 `null` 은 다르다(`""` 는 유효한 결과).
 * 그 뒤 호출부(`gemini_llm.complete`)가 `None` 이거나 공백뿐이면 `RuntimeError` 를 낸다.
 */

export interface ChatMessage {
  role: "system" | "user" | "assistant";
  content: string;
}

/** `_MAX_ATTEMPTS` / `_BASE_BACKOFF_SECONDS` (`_gemini_common.py`). */
export const MAX_ATTEMPTS = 3;
export const BASE_BACKOFF_SECONDS = 1.0;

const BASE_URL = "https://generativelanguage.googleapis.com";
const API_VERSION = "v1beta";

export interface GeminiPart {
  text?: string;
  thought?: boolean;
  [k: string]: unknown;
}

export interface GeminiResponse {
  candidates?: { content?: { parts?: GeminiPart[] } }[];
  [k: string]: unknown;
}

/**
 * SDK `response.text` 와 같은 규칙. 텍스트 파트가 없으면 `null`.
 * **`""` 과 `null` 을 구분한다** — 호출부의 빈 응답 판정이 달라진다.
 */
export function responseText(resp: GeminiResponse): string | null {
  const parts = resp.candidates?.[0]?.content?.parts;
  if (!parts || parts.length === 0) return null;
  let text = "";
  let anyTextPart = false;
  for (const part of parts) {
    if (typeof part.text === "string") {
      if (part.thought === true) continue; // 사고 파트는 본문이 아니다.
      anyTextPart = true;
      text += part.text;
    }
  }
  return anyTextPart ? text : null;
}

/** SDK `_build_contents` — system 은 따로 모으고 나머지는 대화로. */
export function buildContents(
  messages: readonly ChatMessage[],
): { systemInstruction: string | null; contents: unknown[] } {
  const systemParts: string[] = [];
  const contents: unknown[] = [];
  for (const msg of messages) {
    if (msg.role === "system") {
      systemParts.push(msg.content);
    } else if (msg.role === "user") {
      contents.push({ parts: [{ text: msg.content }], role: "user" });
    } else if (msg.role === "assistant") {
      // SDK 는 assistant 를 `model` 로 바꾼다.
      contents.push({ parts: [{ text: msg.content }], role: "model" });
    } else {
      throw new Error(`알 수 없는 role: ${JSON.stringify((msg as ChatMessage).role)}`);
    }
  }
  return {
    systemInstruction: systemParts.length ? systemParts.join("\n\n") : null,
    contents,
  };
}

export function buildRequestBody(
  messages: readonly ChatMessage[],
  opts: { temperature: number; jsonMode?: boolean },
): Record<string, unknown> {
  const { systemInstruction, contents } = buildContents(messages);
  const generationConfig: Record<string, unknown> = { temperature: opts.temperature };
  if (opts.jsonMode) generationConfig.response_mime_type = "application/json";
  const body: Record<string, unknown> = { contents, generationConfig };
  if (systemInstruction !== null) {
    // role 이 "user" 인 것은 SDK 실측이다. 바꾸면 Gemini 가 다르게 받는다.
    body.systemInstruction = { parts: [{ text: systemInstruction }], role: "user" };
  }
  return body;
}

export interface GeminiDeps {
  apiKey: string;
  model: string;
  fetchFn?: typeof fetch;
  /** 백오프 대기. 테스트에서 즉시 반환하도록 주입한다. */
  sleep?: (ms: number) => Promise<void>;
  /** `random.uniform(0, 0.5)` 자리. 대조에서 고정한다. */
  jitter?: () => number;
}

const defaultSleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/**
 * `with_retry` — 3 회 시도, 지수 백오프 `1.0 * 2^(n-1) + uniform(0, 0.5)` 초.
 * 마지막 실패는 그대로 던진다. **마지막 시도 뒤에는 자지 않는다.**
 */
export async function withRetry<T>(
  fn: () => Promise<T>,
  label: string,
  deps: Pick<GeminiDeps, "sleep" | "jitter"> = {},
): Promise<T> {
  const sleep = deps.sleep ?? defaultSleep;
  const jitter = deps.jitter ?? (() => Math.random() * 0.5);
  let lastErr: unknown;
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    try {
      return await fn();
    } catch (e) {
      lastErr = e;
      if (attempt === MAX_ATTEMPTS) break;
      const delay = BASE_BACKOFF_SECONDS * Math.pow(2, attempt - 1) + jitter();
      console.warn(`${label} 실패(attempt=${attempt}/${MAX_ATTEMPTS}, delay=${delay.toFixed(1)}s):`, e);
      await sleep(delay * 1000);
    }
  }
  throw lastErr;
}

/** `GeminiLLMProvider.complete` 와 같은 계약. 빈 응답이면 예외. */
export async function complete(
  messages: readonly ChatMessage[],
  deps: GeminiDeps,
  opts: { temperature?: number; jsonMode?: boolean } = {},
): Promise<string> {
  if (!deps.apiKey) {
    throw new Error("GEMINI_API_KEY 가 설정되지 않았습니다. .env 를 확인하세요.");
  }
  const body = buildRequestBody(messages, {
    temperature: opts.temperature ?? 0.2,
    jsonMode: opts.jsonMode,
  });
  const url = `${BASE_URL}/${API_VERSION}/models/${deps.model}:generateContent`;
  const doFetch = deps.fetchFn ?? fetch;

  return await withRetry(
    async () => {
      const res = await doFetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json", "x-goog-api-key": deps.apiKey },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const detail = await res.text();
        // 상태 코드를 메시지에 남긴다 — 호출부의 quota 판정(`isQuotaExhausted`)이 이걸 본다.
        throw new Error(`Gemini ${res.status}: ${detail.slice(0, 500)}`);
      }
      const json = (await res.json()) as GeminiResponse;
      const text = responseText(json);
      if (text === null || text.trim() === "") {
        throw new Error(`Gemini 응답이 비어있습니다: ${JSON.stringify(json).slice(0, 500)}`);
      }
      return text;
    },
    "gemini.generate_content",
    deps,
  );
}
