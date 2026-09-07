/**
 * `services/synonym_inject.py` 포팅 — chunk 끝에 `[검색어: ...]` 동의어 마커를 붙인다.
 *
 * ## 이미 데이터에 있다
 * 실측(2026-09-07): 전체 청크 **37,080 개 중 199 개**에 이 마커가 들어 있고
 * `metadata.synonym_source` 가 `"dict"` 다. 즉 이 기능은 켜진 적이 있고 실제 결과가 남아
 * 있다 — 안 옮긴 채 재인제스트하면 그 199 개의 sparse 매칭이 사라진다.
 *
 * 읽기 쪽(`stripSynonymMarker`)은 이미 옮겨져 있어 마커가 사용자에게 보이진 않는다.
 * 여기서 채우는 건 **쓰기 쪽**이다.
 *
 * ## 순서가 결과를 바꾼다
 * 후보는 `iterDictKeys()` 순서로 쌓이다가 **5 개(`CAP_PER_CHUNK`)에서 잘린다.**
 * 사전 순서가 곧 "어떤 후보가 살아남는가" 다 — `synonym_dict.ts` 가 `Map` 을 쓰는 이유다.
 *
 * ## 이미 본문에 있는 후보는 넣지 않는다
 * `c in text` 로 거른다. 같은 문자열을 두 번 인덱싱하지 않으려는 것이고, 이 검사는
 * **부분 문자열** 기준이다(형태소 아님).
 *
 * ## 대괄호를 지운다
 * 후보에 `[` 나 `]` 가 섞이면 `[검색어: ...]` 구조가 깨져 `stripSynonymMarker` 가 마커를
 * 반만 떼고 나머지를 사용자에게 노출한다. 정적 사전 36 entry 는 해당 없고 LLM 후보만
 * 위험한데, 원본이 **한 곳에서** 막아 뒀다(senior-qa P2).
 */

import { pyStrip } from "../search/pystr.ts";
import { iterDictKeys, lookupSynonyms } from "./synonym_dict.ts";

/** 원본 `_CAP_PER_DOC_LLM_PAIRS`. */
const CAP_PER_DOC_LLM_PAIRS = 8;
/** 원본 `_LLM_INPUT_CHARS` — `tag_summarize` 의 `_TAG_INPUT_CHARS` 와 같은 값이다. */
const LLM_INPUT_CHARS = 3000;

const MARKER_PREFIX = "[검색어: ";
const MARKER_SUFFIX = "]";

/** `(term, synonyms)` 쌍. */
export type DocLlmPair = [string, string[]];

/**
 * 원본 `collect_synonym_candidates`.
 *
 * ENV 검사는 여기 없다 — Edge 는 `chunk_records.toChunkRecords` 가
 * `env.synonymInjectionEnabled` 로 이미 막고 들어온다. 원본은 함수 안에서 보지만
 * **호출 시점 평가**라는 성질은 같다(모듈 로드 시점이 아니다).
 */
export function collectSynonymCandidates(
  text: string,
  docLlmPairs: DocLlmPair[] | null = null,
  capPerChunk = 5,
): string[] {
  if (!text) return [];

  const out: string[] = [];
  for (const key of iterDictKeys()) {
    if (text.includes(key)) out.push(...lookupSynonyms(key));
  }
  if (docLlmPairs && docLlmPairs.length > 0) {
    for (const [term, synonyms] of docLlmPairs) {
      if (term && text.includes(term)) out.push(...synonyms);
    }
  }

  const seen = new Set<string>();
  const result: string[] = [];
  for (const cand of out) {
    // `.strip()` → `[`·`]` 제거 → 다시 `.strip()`. Python 공백 집합이라 `trim()` 이 아니다.
    const c = pyStrip(pyStrip(cand ?? "").replaceAll("[", "").replaceAll("]", ""));
    if (!c || seen.has(c) || text.includes(c)) continue;
    seen.add(c);
    result.push(c);
    if (result.length >= capPerChunk) break;
  }
  return result;
}

/** 원본 `inject_marker` — 후보가 없으면 원문 그대로. 결과는 NFC 다. */
export function injectMarker(text: string, candidates: string[]): string {
  if (candidates.length === 0) return text;
  const marker = MARKER_PREFIX + candidates.join(" ") + MARKER_SUFFIX;
  return (text + "\n\n" + marker).normalize("NFC");
}

/**
 * `chunk_records` 의 `SynonymInjector` 계약 — 후보를 모아 마커까지 붙인다.
 * 후보가 없으면 `null`(호출부가 metadata 도 안 건드린다).
 */
export function injectSynonyms(
  text: string,
  docLlmPairs: DocLlmPair[] | null,
): { text: string; candidates: string[] } | null {
  const candidates = collectSynonymCandidates(text, docLlmPairs);
  if (candidates.length === 0) return null;
  return { text: injectMarker(text, candidates), candidates };
}

// ---------------------------------------------------------------------------
// (b) doc 단위 LLM 후보 — 원본도 "최소 구현" 이라고 적어 뒀다.
// ---------------------------------------------------------------------------

/**
 * 원본 `_parse_llm_pairs` — 형식이 안 맞으면 **빈 배열**이다(예외 없음).
 *
 * ```` 코드펜스 벗기기: `split("```", 2)` 는 Python 에서 **최대 2 회 분할**(조각 3 개)이라
 * `parts[1]` 이 펜스 안쪽이다. JS `split` 의 limit 은 의미가 달라 그대로 쓰면 틀린다.
 */
export function parseLlmPairs(text: string): DocLlmPair[] {
  let cleaned = pyStrip(text ?? "");
  if (cleaned.startsWith("```")) {
    // Python `"a```b```c".split("```", 2)` → ["a", "b", "c```..."] 와 같은 동작.
    const first = cleaned.indexOf("```");
    const second = cleaned.indexOf("```", first + 3);
    cleaned = second === -1 ? "" : cleaned.slice(first + 3, second);
    if (cleaned.startsWith("json")) cleaned = cleaned.slice(4);
    // Python `.strip("`\n ")` — 양끝에서 백틱·개행·공백을 깎는다.
    cleaned = cleaned.replace(/^[`\n ]+|[`\n ]+$/g, "");
  }
  let data: unknown;
  try {
    data = JSON.parse(cleaned);
  } catch {
    return [];
  }
  if (data === null || typeof data !== "object" || Array.isArray(data)) return [];
  const pairsRaw = (data as Record<string, unknown>)["pairs"];
  if (!Array.isArray(pairsRaw)) return [];

  const out: DocLlmPair[] = [];
  for (const item of pairsRaw) {
    if (item === null || typeof item !== "object" || Array.isArray(item)) continue;
    const rec = item as Record<string, unknown>;
    const term = rec["term"];
    const synonyms = rec["synonyms"];
    if (typeof term !== "string" || pyStrip(term) === "") continue;
    if (!Array.isArray(synonyms)) continue;
    const clean = synonyms
      .filter((s): s is string => typeof s === "string" && pyStrip(s) !== "")
      .map((s) => pyStrip(s));
    if (clean.length === 0) continue;
    out.push([pyStrip(term), clean]);
    if (out.length >= CAP_PER_DOC_LLM_PAIRS) break;
  }
  return out;
}

/** 원본 프롬프트 그대로. 한 글자만 달라도 다른 후보가 나온다. */
export const SYNONYM_SYSTEM_PROMPT =
  "당신은 한국어 문서 검색 보조 사전 생성기입니다. 주어진 텍스트의 핵심 용어와 " +
  "그 동의어·약어·일상어 후보를 보수적으로 3~5쌍 추출하세요.\n" +
  "- 너무 일반적인 명사(정보·관리·규정 등 단독) 는 제외\n" +
  "- 각 쌍의 term 은 텍스트에 실제 등장하는 표현\n" +
  "응답은 반드시 다음 형식의 단일 JSON 객체만 포함 (설명·Markdown·코드블록 금지):\n" +
  '{"pairs":[{"term":"...","synonyms":["...","..."]}]}';

export function synonymUserPrompt(head: string): string {
  return `다음 텍스트에서 검색 보조 사전 쌍을 추출하세요:\n\n${head}`;
}

/**
 * 원본 `generate_doc_llm_pairs` — doc 당 1 회. **실패는 전부 빈 배열**이다.
 *
 * 여기서 던지면 chunk 저장이 통째로 막힌다. 원본이 `except Exception` 으로 삼키는
 * 이유이고, 그 계약을 그대로 옮긴다.
 */
export async function generateDocLlmPairs(
  rawText: string,
  complete: (system: string, user: string) => Promise<string>,
): Promise<DocLlmPair[]> {
  // Python 슬라이싱은 **코드포인트** 기준이다.
  const head = [...(rawText ?? "")].slice(0, LLM_INPUT_CHARS).join("");
  if (pyStrip(head) === "") return [];
  try {
    return parseLlmPairs(await complete(SYNONYM_SYSTEM_PROMPT, synonymUserPrompt(head)));
  } catch (e) {
    console.warn(`synonym LLM 후보 생성 실패 (graceful, 빈 배열 반환): ${e}`);
    return [];
  }
}
