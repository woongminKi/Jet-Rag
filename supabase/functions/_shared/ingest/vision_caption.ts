/**
 * Gemini Vision 캡셔너 — `adapters/impl/gemini_vision.py` 포팅.
 *
 * 이미지 1 장 → `VisionCaption`(type / ocr_text / caption / table_caption /
 * figure_caption / structured). 프롬프트 하나로 JSON 을 받는 단일 호출 구조다.
 *
 * ## SDK 가 아니라 REST 다
 * 원본은 `google-genai` SDK 의 `types.Part.from_bytes` 를 쓴다. Deno 에는 그 SDK 가
 * 없어 REST 로 직접 부른다 — `inline_data.data` 에 **base64** 를 넣는 게 SDK 가 하던
 * 일이다. `_shared/llm/gemini.ts` 는 텍스트 전용이라 여기서 이미지 파트를 만든다.
 *
 * ## 파트 순서가 계약이다
 * 원본은 **이미지 → 프롬프트** 순이다. 뒤집으면 모델 응답이 달라질 수 있어 그대로 둔다.
 *
 * ## 관대한 파싱이 계약이다
 * `type` 이 화이트리스트 밖이면 **"기타"로 강제**하고, `table_caption`/`figure_caption`
 * 은 빈 문자열·비문자열·부재를 전부 `null` 로 만든다. 검색 보조 인덱스가 의미 없는
 * 행을 만들지 않게 하려는 것이다(원본 주석). 반대로 JSON 자체가 깨지면 **던진다**.
 *
 * ## HEIC 는 그대로 넘긴다
 * Gemini 가 직접 지원한다(DE-17). mime_type 만 정확하면 된다.
 */

/** 원본 `VisionCategory` 화이트리스트. */
export const VALID_TYPES = [
  "문서",
  "스크린샷",
  "메신저대화",
  "화이트보드",
  "명함",
  "차트",
  "표",
  "기타",
] as const;
export type VisionCategory = typeof VALID_TYPES[number];

export const DEFAULT_VISION_MODEL = "gemini-2.5-flash";

/** 기획서 §10.4 의 단일 호출 JSON 프롬프트. **문구를 바꾸면 응답이 바뀐다.** */
export const VISION_PROMPT = `당신은 이미지에서 정보를 정확하게 추출하는 분석가입니다.
다음 JSON 스키마에 정확히 맞춰 한국어로 응답하세요. 다른 텍스트 없이 JSON 만 출력합니다.

{
  "type": "문서|스크린샷|메신저대화|화이트보드|명함|차트|표|기타 중 하나 (애매하면 기타)",
  "ocr_text": "이미지의 모든 텍스트를 위→아래·좌→우 순서로. 텍스트가 없으면 빈 문자열",
  "caption": "이미지의 한국어 한 문장 요약 (≤ 80자, 끝에 마침표 없이)",
  "table_caption": "이미지에 표가 있을 때 표의 한국어 제목/주제 한 문장 (≤ 60자, 없으면 null). 표가 여러 개면 첫 표 기준",
  "figure_caption": "이미지에 그림/도식/차트가 있을 때 그림의 한국어 제목/주제 한 문장 (≤ 60자, 없으면 null)",
  "structured": "type 별 구조화 객체 — 명함: {name, title, contact}, 차트: {axis, series, values}, 표: {headers, rows}, 화이트보드: {action_items: [\\"항목1\\", \\"항목2\\", ...]} (담당자·기한 명시 시 그대로 보존). 구조화 불가 시 null"
}
`;

export interface VisionUsage {
  prompt_tokens: number;
  image_tokens: number | null;
  output_tokens: number;
  thinking_tokens: number;
  estimated_cost: number;
  model_used: string;
  retry_attempt?: number;
}

export interface VisionCaption {
  type: VisionCategory;
  ocr_text: string;
  caption: string;
  structured: Record<string, unknown> | null;
  usage: VisionUsage | null;
  table_caption: string | null;
  figure_caption: string | null;
}

/**
 * `factory._GEMINI_PRICING` 을 **그대로** 옮겼다 — 100 만 토큰당 USD.
 *
 * 처음엔 기억으로 적었다가 `gemini-2.5-pro`(표에 없는 모델)를 지어내고 2.0 계열 3 종을
 * 빠뜨렸다. 그대로 뒀으면 2.0 모델에서 fallback 단가(0.30/2.50)가 적용돼
 * `estimated_cost` 가 3~6 배 부풀려진다. 단가는 반드시 원본을 보고 옮긴다.
 */
const GEMINI_PRICING: Record<string, { input: number; output: number; thinking: number }> = {
  "gemini-2.5-flash": { input: 0.30, output: 2.50, thinking: 2.50 },
  "gemini-2.5-flash-lite": { input: 0.10, output: 0.40, thinking: 0.40 },
  // 2.0 계열 — 신규 사용자 deprecated, ENV override 시만 쓰인다.
  "gemini-2.0-flash": { input: 0.10, output: 0.40, thinking: 0.40 },
  "gemini-2.0-flash-lite": { input: 0.075, output: 0.30, thinking: 0.30 },
  "gemini-2.0-flash-thinking-exp": { input: 0.10, output: 0.40, thinking: 0.40 },
};
/** 모르는 모델은 보수적으로 flash 단가(원본 `_PRICING_FALLBACK_MODEL`). */
const PRICING_FALLBACK = GEMINI_PRICING["gemini-2.5-flash"];

export function estimateCost(opts: {
  model: string;
  promptTokens: number;
  outputTokens: number;
  thinkingTokens: number;
}): number {
  const p = GEMINI_PRICING[opts.model] ?? PRICING_FALLBACK;
  return (
    opts.promptTokens * p.input +
    opts.outputTokens * p.output +
    opts.thinkingTokens * p.thinking
  ) / 1_000_000;
}

/** REST 응답의 `usageMetadata` → 기록용 usage. 없으면 `null`. */
export function parseUsageMetadata(
  resp: Record<string, unknown>,
  model: string,
): VisionUsage | null {
  const m = resp["usageMetadata"] as Record<string, unknown> | undefined;
  if (!m) return null;
  const promptTokens = Number(m["promptTokenCount"] ?? 0) || 0;
  const outputTokens = Number(m["candidatesTokenCount"] ?? 0) || 0;
  const thinkingTokens = Number(m["thoughtsTokenCount"] ?? 0) || 0;

  // IMAGE modality 토큰만 합산. 0 이거나 details 가 없으면 `null`(원본과 같다).
  let imageTokens = 0;
  const details = m["promptTokensDetails"];
  if (Array.isArray(details)) {
    for (const d of details) {
      const modality = String((d as { modality?: unknown })?.modality ?? "").toUpperCase();
      // SDK 는 `MediaModality.IMAGE` 로도 오므로 접미사로 본다(원본과 같은 판정).
      if (modality.endsWith("IMAGE")) {
        imageTokens += Number((d as { tokenCount?: unknown })?.tokenCount ?? 0) || 0;
      }
    }
  }

  return {
    prompt_tokens: promptTokens,
    image_tokens: imageTokens || null,
    output_tokens: outputTokens,
    thinking_tokens: thinkingTokens,
    estimated_cost: estimateCost({ model, promptTokens, outputTokens, thinkingTokens }),
    model_used: model,
  };
}

/**
 * Python 의 진리값 — **빈 배열·빈 객체가 falsy** 다.
 *
 * JS 는 `[]` 와 `{}` 가 truthy 라 `data.get(k) or ""` 를 그대로 옮기면 갈린다.
 * 대조가 `ocr_text: []` 에서 잡았다(py `""` / ts `"[]"`).
 */
export function pyTruthy(v: unknown): boolean {
  if (v === null || v === undefined || v === false || v === "" || v === 0) return false;
  if (typeof v === "number") return !Number.isNaN(v) ? v !== 0 : true; // NaN 은 truthy
  if (Array.isArray(v)) return v.length > 0;
  if (typeof v === "object") return Object.keys(v as object).length > 0;
  return Boolean(v);
}

/**
 * Python `str()` — **JSON 에 나올 수 있는 타입만** 다룬다.
 *
 * 모델이 `ocr_text` 에 배열이나 객체를 넣어 보내면 원본은 `str(...)` 로 문자열화하는데,
 * 그 결과가 JS `String()` 과 다르다:
 *
 * | 값 | Python `str()` | JS `String()` |
 * |---|---|---|
 * | `["a","b"]` | `['a', 'b']` | `a,b` |
 * | `{"k":"v"}` | `{'k': 'v'}` | `[object Object]` |
 * | `true` | `True` | `true` |
 * | `null` | `None` | `null` |
 *
 * 컨테이너 안에서는 `repr` 규칙이라 문자열에 따옴표가 붙는다. `'` 가 들어 있고 `"` 는
 * 없으면 바깥 따옴표가 `"` 로 바뀐다(실측으로 확인).
 */
export function pyStr(v: unknown): string {
  if (typeof v === "string") return v; // 최상위 문자열은 따옴표 없이 그대로
  return pyRepr(v);
}

function pyRepr(v: unknown): string {
  if (v === null) return "None";
  if (v === true) return "True";
  if (v === false) return "False";
  if (typeof v === "number") {
    // Python 은 `1.0` 을 float 으로 보고 `"1.0"` 을 낸다. JSON 정수는 int 라 `"1"`.
    return Number.isInteger(v) ? String(v) : String(v);
  }
  if (typeof v === "string") return pyStrRepr(v);
  if (Array.isArray(v)) return `[${v.map(pyRepr).join(", ")}]`;
  if (typeof v === "object") {
    const entries = Object.entries(v as Record<string, unknown>)
      .map(([k, val]) => `${pyStrRepr(k)}: ${pyRepr(val)}`);
    return `{${entries.join(", ")}}`;
  }
  return String(v);
}

/** Python 문자열 `repr` — 기본은 `'`, `'` 가 있고 `"` 가 없으면 `"` 로 감싼다. */
function pyStrRepr(s: string): string {
  const useDouble = s.includes("'") && !s.includes('"');
  const quote = useDouble ? '"' : "'";
  let out = "";
  for (const ch of s) {
    if (ch === "\\") out += "\\\\";
    else if (ch === quote) out += "\\" + ch;
    else if (ch === "\n") out += "\\n";
    else if (ch === "\r") out += "\\r";
    else if (ch === "\t") out += "\\t";
    else out += ch;
  }
  return quote + out + quote;
}

/** 응답 JSON 문자열 → `VisionCaption`. 관대하게 읽되 JSON 이 깨지면 던진다. */
export function parseVisionResponse(
  text: string,
  opts: { resp?: Record<string, unknown>; model?: string; retryAttempt?: number } = {},
): VisionCaption {
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch (e) {
    throw new Error(
      `Gemini Vision JSON 파싱 실패: ${e}; 응답 앞 200자: ${JSON.stringify(text.slice(0, 200))}`,
    );
  }
  if (data === null || typeof data !== "object" || Array.isArray(data)) {
    throw new Error(
      `Gemini Vision 응답이 dict 가 아닙니다: ${Array.isArray(data) ? "list" : typeof data}; ` +
        `앞 200자: ${JSON.stringify(text.slice(0, 200))}`,
    );
  }
  const d = data as Record<string, unknown>;

  const rawType = d["type"];
  const type: VisionCategory = VALID_TYPES.includes(rawType as VisionCategory)
    ? rawType as VisionCategory
    : "기타"; // 화이트리스트 밖은 조용히 "기타" — 원본과 같다.

  // 원본은 `data.get(k) or ""` 라 null·0·false 가 전부 빈 문자열이 된다.
  // 비문자열은 `str(...)` 로 바꾸는데 **JS `String()` 과 다르다** — 배열이면
  // Python 은 `"['a', 'b']"`, JS 는 `"a,b"` 다. 대조가 이걸 잡았다.
  const ocrRaw = d["ocr_text"];
  const ocrText = !pyTruthy(ocrRaw) ? "" : (typeof ocrRaw === "string" ? ocrRaw : pyStr(ocrRaw));
  const capRaw = d["caption"];
  const caption = !pyTruthy(capRaw) ? "" : (typeof capRaw === "string" ? capRaw : pyStr(capRaw));

  // 빈 문자열·공백·비문자열은 전부 null — 의미 없는 인덱스 행을 막는다.
  const norm = (v: unknown): string | null => typeof v === "string" && v.trim() ? v : null;

  const rawStructured = d["structured"];
  const structured = rawStructured !== null && typeof rawStructured === "object" &&
      !Array.isArray(rawStructured) && Object.keys(rawStructured).length > 0
    ? rawStructured as Record<string, unknown>
    : null;

  let usage: VisionUsage | null = null;
  if (opts.resp) {
    usage = parseUsageMetadata(opts.resp, opts.model ?? DEFAULT_VISION_MODEL);
    if (usage && opts.retryAttempt !== undefined) usage.retry_attempt = opts.retryAttempt;
  }

  return {
    type,
    ocr_text: ocrText,
    caption,
    structured,
    usage,
    table_caption: norm(d["table_caption"]),
    figure_caption: norm(d["figure_caption"]),
  };
}

/** 이미지 파트를 포함한 요청 본문. **이미지가 먼저**, 프롬프트가 뒤다. */
export function buildVisionRequestBody(
  imageBase64: string,
  mimeType: string,
): Record<string, unknown> {
  return {
    contents: [{
      role: "user",
      parts: [
        { inline_data: { mime_type: mimeType, data: imageBase64 } },
        { text: VISION_PROMPT },
      ],
    }],
    generationConfig: {
      temperature: 0.2,
      response_mime_type: "application/json",
    },
  };
}

export function toBase64(bytes: Uint8Array): string {
  // `String.fromCharCode(...bytes)` 는 큰 이미지에서 스택을 넘긴다 — 청크로 자른다.
  let bin = "";
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    bin += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
  }
  return btoa(bin);
}
