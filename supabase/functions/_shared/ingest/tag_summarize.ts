/**
 * `ingest/stages/tag_summarize.py` 포팅 — 태그·요약을 LLM 으로 만들어 문서에 저장.
 *
 * 호출 두 번이다. 태그(앞 3,000자)와 요약(앞 12,000자)이 각각 별도 모델·온도를 쓴다.
 *
 * ## 실패해도 파이프라인을 세우지 않는다
 * LLM 이 최종 실패하면 해당 필드를 비운 채로 넘어간다. **부분 성공도 허용**한다 —
 * 태그만 되고 요약이 실패하면 태그만 저장한다.
 *
 * ## quota 를 만나면 두 번째 호출을 건너뛴다
 * 첫 호출이 quota 로 죽었으면 두 번째도 죽는다. 비용만 쓰고 실패할 호출을 아낀다.
 *
 * ## DB 쓰기 실패는 다르다
 * LLM 실패는 삼키지만 `documents` 갱신 실패는 **던진다**. 그건 인제스트가 실패한 것이다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

import { complete, type ChatMessage } from "../llm/gemini.ts";
import { isQuotaExhausted } from "../llm/quota.ts";
import { pyStrip } from "../search/pystr.ts";

const TAG_INPUT_CHARS = 3000;
const SUMMARY_INPUT_CHARS = 12000;

/** `factory._GEMINI_DEFAULT_MODELS` 의 `tag` · `summary`. 둘 다 lite 다. */
const DEFAULT_TAG_MODEL = "gemini-2.5-flash-lite";
const DEFAULT_SUMMARY_MODEL = "gemini-2.5-flash-lite";

/** Python 슬라이스는 **코드포인트** 단위다. `.slice()` 는 UTF-16 이라 이모지에서 갈린다. */
function cpSlice(s: string, end: number): string {
  let out = "";
  let n = 0;
  for (const ch of s) {
    if (n >= end) break;
    out += ch;
    n++;
  }
  return out;
}

const TAG_SYSTEM =
  "당신은 한국어 문서의 태그 추출 도우미입니다. 주어진 텍스트에서 다음 JSON 을 생성하세요.\n" +
  "- topic_tags: 주제 키워드 3~7 개 (한국어, 명사형)\n" +
  "- entity_tags: 인명·조직·제품·지명 등 고유명사 0~10 개\n" +
  "- document_type: 보고서|논문|기사|블로그|메모|회의록|이메일|메신저|공지|기타 중 하나\n" +
  "- time_reference: 문서가 다루는 시점 (YYYY 또는 YYYY-MM, 없으면 null)\n" +
  "응답은 반드시 위 4개 키를 가진 단일 JSON 객체만 포함. 설명·Markdown·코드블록 금지.";

const SUMMARY_SYSTEM =
  "당신은 한국어 문서 요약 도우미입니다. 주어진 텍스트에서 다음 JSON 을 생성하세요.\n" +
  "- summary_3line: 3줄 요약 (각 줄 60자 이내, '\\n' 구분)\n" +
  "- implications: 이 문서가 개인 지식 관점에서 의미하는 바 (1~2문장)\n" +
  "응답은 반드시 위 2개 키를 가진 단일 JSON 객체만 포함. 설명·Markdown·코드블록 금지.";

/**
 * 원본 `_parse_json` — `response_mime_type` 을 줘도 코드블록이 올 때가 있어 벗겨 낸다.
 *
 * `split("```", 2)[1]` 은 **가운데 조각**이다. 앞에 ``` 로 시작하는 게 확인된 뒤라
 * 조각이 최소 둘은 나온다.
 */
export function parseJson(text: string): unknown {
  let cleaned = pyStrip(text);
  if (cleaned.startsWith("```")) {
    // Python `str.split(sep, maxsplit=2)` — 최대 3 조각.
    const parts: string[] = [];
    let rest = cleaned;
    for (let i = 0; i < 2; i++) {
      const at = rest.indexOf("```");
      if (at < 0) break;
      parts.push(rest.slice(0, at));
      rest = rest.slice(at + 3);
    }
    parts.push(rest);
    cleaned = parts[1] ?? "";
    if (cleaned.startsWith("json")) cleaned = cleaned.slice(4);
    // `strip("`\n ")` — 그 세 글자를 양끝에서 걷어낸다.
    cleaned = stripChars(cleaned, "`\n ");
  }
  // Python `json.loads` 는 `NaN`/`Infinity` 를 받지만 `JSON.parse` 는 거부한다.
  // LLM 이 JSON 모드로 그런 값을 낼 일은 없어 그대로 둔다.
  return JSON.parse(cleaned);
}

function stripChars(s: string, chars: string): string {
  const set = new Set([...chars]);
  const arr = [...s];
  let a = 0;
  let b = arr.length;
  while (a < b && set.has(arr[a])) a++;
  while (b > a && set.has(arr[b - 1])) b--;
  return arr.slice(a, b).join("");
}

/** Python 진리값 — 빈 배열·빈 객체가 falsy 다. */
function pyTruthy(v: unknown): boolean {
  if (v === null || v === undefined) return false;
  if (typeof v === "boolean") return v;
  if (typeof v === "number") return v !== 0;
  if (typeof v === "string") return v !== "";
  if (Array.isArray(v)) return v.length > 0;
  if (typeof v === "object") return Object.keys(v as object).length > 0;
  return true;
}

/**
 * Python `list(x)` — 문자열이면 **글자 단위**로 쪼개지고 dict 면 키 목록이 된다.
 *
 * LLM 이 `topic_tags: "보고서"` 처럼 문자열을 주면 원본은 `['보','고','서']` 를 저장한다.
 * 이상해 보여도 그게 현재 동작이라 그대로 옮긴다.
 */
function pyList(v: unknown): unknown[] {
  if (typeof v === "string") return [...v];
  if (Array.isArray(v)) return [...v];
  if (v !== null && typeof v === "object") return Object.keys(v as object);
  // Python 은 int 등 비-iterable 에 `TypeError` 를 낸다.
  throw new TypeError(`'${typeof v}' object is not iterable`);
}

/**
 * Python `list(dict.fromkeys(items))` — **순서를 지키면서** 중복을 없앤다.
 *
 * dict 키는 hashable 이어야 한다 — 리스트·딕트가 섞여 있으면 원본은 `TypeError` 를 낸다.
 * JS `Set` 은 객체도 받아 조용히 통과하므로 여기서 막는다.
 */
function dedupeOrdered(items: unknown[]): unknown[] {
  const seen = new Set<unknown>();
  const out: unknown[] = [];
  for (const it of items) {
    if (it !== null && typeof it === "object") {
      throw new TypeError(`unhashable type: '${Array.isArray(it) ? "list" : "dict"}'`);
    }
    if (seen.has(it)) continue;
    seen.add(it);
    out.push(it);
  }
  return out;
}

export interface TagSummarizeDeps {
  client: SupabaseClient;
  env: Record<string, string | undefined>;
  /** 테스트 주입 — 실제 LLM 대신. `purpose` 로 태그·요약을 구분한다. */
  complete?: (
    purpose: "tag" | "summary",
    messages: readonly ChatMessage[],
    opts: { temperature: number; jsonMode: boolean },
  ) => Promise<string>;
}

function modelFor(env: Record<string, string | undefined>, purpose: "tag" | "summary"): string {
  // `factory._resolve_llm_model` — ENV override → provider default.
  const override = env[`JETRAG_LLM_MODEL_${purpose.toUpperCase()}`];
  if (override) return override;
  return purpose === "tag" ? DEFAULT_TAG_MODEL : DEFAULT_SUMMARY_MODEL;
}

async function callLlm(
  deps: TagSummarizeDeps,
  purpose: "tag" | "summary",
  system: string,
  user: string,
  temperature: number,
): Promise<string> {
  const messages: ChatMessage[] = [
    { role: "system", content: system },
    { role: "user", content: user },
  ];
  if (deps.complete) {
    return await deps.complete(purpose, messages, { temperature, jsonMode: true });
  }
  return await complete(
    messages,
    { apiKey: deps.env["GEMINI_API_KEY"] ?? "", model: modelFor(deps.env, purpose) },
    { temperature, jsonMode: true },
  );
}

/** 원본 `_call_tags`. 본문이 비면 **호출하지 않고** 빈 결과를 만든다. */
export async function callTags(
  deps: TagSummarizeDeps,
  rawText: string,
): Promise<unknown> {
  const head = cpSlice(rawText ?? "", TAG_INPUT_CHARS);
  if (pyStrip(head) === "") {
    return {
      topic_tags: [],
      entity_tags: [],
      document_type: null,
      time_reference: null,
    };
  }
  const text = await callLlm(
    deps, "tag", TAG_SYSTEM, `다음 텍스트에서 태그를 추출하세요:\n\n${head}`, 0.1,
  );
  return parseJson(text);
}

/** 원본 `_call_summary`. */
export async function callSummary(
  deps: TagSummarizeDeps,
  rawText: string,
): Promise<unknown> {
  const body = cpSlice(rawText ?? "", SUMMARY_INPUT_CHARS);
  if (pyStrip(body) === "") {
    return { summary_3line: "", implications: "" };
  }
  const text = await callLlm(
    deps, "summary", SUMMARY_SYSTEM, `다음 텍스트를 요약하세요:\n\n${body}`, 0.2,
  );
  return parseJson(text);
}

/** 원본 `_persist` 가 만드는 patch. flags 머지는 호출자가 이미 읽어 둔 값으로 한다. */
export function buildPatch(
  tags: unknown,
  summary: unknown,
  existingFlags: Record<string, unknown>,
): Record<string, unknown> {
  const patch: Record<string, unknown> = {};
  if (tags !== null && tags !== undefined) {
    // 원본은 `tags.get(...)` 이다 — dict 가 아니면 AttributeError 로 스테이지가 실패한다.
    if (typeof tags !== "object" || Array.isArray(tags)) {
      throw new TypeError("tags 응답이 객체가 아니다 (원본은 AttributeError)");
    }
    const t = tags as Record<string, unknown>;
    const topic = pyList(pyTruthy(t["topic_tags"]) ? t["topic_tags"] : []);
    const entity = pyList(pyTruthy(t["entity_tags"]) ? t["entity_tags"] : []);
    patch["tags"] = dedupeOrdered([...topic, ...entity]);
    const flagsPatch: Record<string, unknown> = {};
    if (pyTruthy(t["document_type"])) flagsPatch["document_type"] = t["document_type"];
    if (pyTruthy(t["time_reference"])) flagsPatch["time_reference"] = t["time_reference"];
    if (Object.keys(flagsPatch).length > 0) {
      patch["flags"] = { ...existingFlags, ...flagsPatch };
    }
  }
  if (summary !== null && summary !== undefined) {
    if (typeof summary !== "object" || Array.isArray(summary)) {
      throw new TypeError("summary 응답이 객체가 아니다 (원본은 AttributeError)");
    }
    const s = summary as Record<string, unknown>;
    // 여기는 `or` 가 아니라 `is not None` 이다 — **빈 문자열도 저장된다.**
    if (s["summary_3line"] !== null && s["summary_3line"] !== undefined) {
      patch["summary"] = s["summary_3line"];
    }
    if (s["implications"] !== null && s["implications"] !== undefined) {
      patch["implications"] = s["implications"];
    }
  }
  return patch;
}

export interface TagSummarizeResult {
  tags: unknown;
  summary: unknown;
  errors: string[];
  quotaExhausted: boolean;
  patch: Record<string, unknown>;
}

/**
 * 원본 `run_tag_summarize_stage` — 두 번 부르고 결과를 문서에 쓴다.
 *
 * 반환값의 `errors` 가 비어 있지 않으면 호출자가 스테이지 로그를 그에 맞게 마감한다
 * (`tags` 와 `summary` 가 **둘 다** 없을 때만 `failed`).
 */
export async function runTagSummarizeStage(
  deps: TagSummarizeDeps,
  docId: string,
  rawText: string,
): Promise<TagSummarizeResult> {
  let tags: unknown = null;
  let summary: unknown = null;
  const errors: string[] = [];
  let quotaExhausted = false;

  try {
    tags = await callTags(deps, rawText);
  } catch (e) {
    console.warn(`태그 호출 실패 (doc=${docId}): ${e}`);
    errors.push(`tags: ${e}`);
    if (isQuotaExhausted(e)) quotaExhausted = true;
  }

  if (quotaExhausted) {
    console.info(`tag_summarize: doc=${docId} quota 감지 → summary 호출 skip (LLM 비용 절약)`);
    errors.push("summary: skipped due to quota");
  } else {
    try {
      summary = await callSummary(deps, rawText);
    } catch (e) {
      console.warn(`요약 호출 실패 (doc=${docId}): ${e}`);
      errors.push(`summary: ${e}`);
    }
  }

  // flags 머지를 위해 현재 값을 읽는다. 원본은 `_merge_flags` 가 필요할 때만 읽는다.
  let existingFlags: Record<string, unknown> = {};
  const needsFlags = tags !== null && typeof tags === "object" && !Array.isArray(tags) &&
    (pyTruthy((tags as Record<string, unknown>)["document_type"]) ||
      pyTruthy((tags as Record<string, unknown>)["time_reference"]));
  if (needsFlags) {
    const { data, error } = await deps.client
      .from("documents").select("flags").eq("id", docId).limit(1);
    if (error) throw new Error(`flags 조회 실패: ${error.message}`);
    existingFlags =
      ((data ?? [])[0] as { flags?: Record<string, unknown> } | undefined)?.flags ?? {};
  }

  const patch = buildPatch(tags, summary, existingFlags);
  if (Object.keys(patch).length > 0) {
    // **DB 쓰기 실패는 던진다.** LLM 실패와 달리 이건 인제스트가 실패한 것이다.
    const { error } = await deps.client.from("documents").update(patch).eq("id", docId);
    if (error) throw new Error(`documents 갱신 실패: ${error.message}`);
  }

  return { tags, summary, errors, quotaExhausted, patch };
}
