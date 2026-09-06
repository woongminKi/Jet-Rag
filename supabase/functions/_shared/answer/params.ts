/**
 * `/answer` 쿼리 파라미터 검증 — pydantic `Query(...)` 재현.
 *
 * 아래는 운영 Railway 실측(2026-09-06)이다. 짐작으로 쓰면 틀린다.
 *
 * | 입력 | 결과 |
 * |---|---|
 * | `q` 없음 | 422 `missing` |
 * | `q=` | 422 `string_too_short` (`ctx.min_length=1`) |
 * | `q` 201자 | 422 `string_too_long` (`ctx.max_length=200`) |
 * | `q` 200자 | 통과 |
 * | `q=   `(공백만) | **422 가 아니라 400** — 핸들러 안에서 `질문이 비어있습니다.` |
 * | `top_k=0` / `11` | 422 `greater_than_equal` / `less_than_equal` |
 * | `top_k=abc` / `` | 422 `int_parsing` |
 * | `top_k=" 5 "` · `"5.0"` · `"+5"` | **전부 5 로 통과** |
 * | `top_k="5.5"` · `"1e1"` | 422 `int_parsing` (지수 표기도 거부) |
 * | `doc_id` | 검증 없음 — 없는 id 면 결과 0 건으로 200 |
 *
 * ## 길이는 코드포인트 단위다
 * pydantic 은 Python `len()` 을 보므로 **코드포인트**다. JS `.length` 는 UTF-16 이라
 * 이모지·한자 확장에서 갈린다. `[...s].length` 로 센다.
 */

/** `_MAX_QUERY_LEN` · `_DEFAULT_TOP_K` · `_MAX_TOP_K`. */
export const MAX_QUERY_LEN = 200;
export const DEFAULT_TOP_K = 5;
export const MAX_TOP_K = 10;
export const MIN_TOP_K = 1;

export interface AnswerParams {
  q: string;
  topK: number;
  docId: string | null;
}

export type ParamResult =
  | { ok: true; params: AnswerParams }
  | { ok: false; detail: unknown[] };

/** 코드포인트 길이 — Python `len()` 과 같다. */
function cpLength(s: string): number {
  let n = 0;
  for (const _ of s) n++;
  return n;
}

/**
 * pydantic v2 의 문자열 → int 강제 변환.
 *
 * 실측: `" 5 "`·`"+5"`·`"5.0"` 은 5 로 통과하고 `""`·`"abc"` 는 `int_parsing` 이다.
 * 즉 **정수 리터럴을 먼저 보고, 아니면 실수로 읽어 소수부가 0 이면 받아들인다.**
 * 소수부가 있으면(`"5.5"`) 다른 오류 타입이라 여기서는 `fractional` 로 구분해 돌려준다.
 */
export function pydanticInt(
  raw: string,
): { ok: true; value: number } | { ok: false; kind: "parsing" } {
  const s = raw.trim();
  if (s === "") return { ok: false, kind: "parsing" };
  if (/^[+-]?\d+$/.test(s)) {
    const n = Number(s);
    if (!Number.isSafeInteger(n)) return { ok: false, kind: "parsing" };
    return { ok: true, value: n };
  }
  // 소수점 표기는 **값이 정수일 때만** 받는다. `"5.0"` → 5, `"5.5"` → 오류.
  // 지수 표기(`"1e1"`)는 거부한다 — 대조에서 잡혔다(pydantic 은 안 받는데 내 첫 구현은 10 을 냈다).
  if (/^[+-]?(\d+\.\d*|\.\d+)$/.test(s)) {
    const f = Number(s);
    if (Number.isFinite(f) && Number.isInteger(f)) return { ok: true, value: f };
  }
  return { ok: false, kind: "parsing" };
}

/**
 * 쿼리 검증. **선언 순서가 곧 오류 배열 순서다** — q → top_k.
 * `doc_id` 는 제약이 없어 오류를 만들지 않는다.
 */
export function validateAnswerParams(sp: URLSearchParams): ParamResult {
  const errors: unknown[] = [];

  const rawQ = sp.get("q");
  let q = "";
  if (rawQ === null) {
    errors.push({ type: "missing", loc: ["query", "q"], msg: "Field required", input: null });
  } else {
    const len = cpLength(rawQ);
    if (len < 1) {
      errors.push({
        type: "string_too_short",
        loc: ["query", "q"],
        msg: "String should have at least 1 character",
        input: rawQ,
        ctx: { min_length: 1 },
      });
    } else if (len > MAX_QUERY_LEN) {
      errors.push({
        type: "string_too_long",
        loc: ["query", "q"],
        msg: `String should have at most ${MAX_QUERY_LEN} characters`,
        input: rawQ,
        ctx: { max_length: MAX_QUERY_LEN },
      });
    } else {
      q = rawQ;
    }
  }

  const rawTopK = sp.get("top_k");
  let topK = DEFAULT_TOP_K;
  if (rawTopK !== null) {
    const parsed = pydanticInt(rawTopK);
    if (!parsed.ok) {
      // 소수부가 있어도 `int_parsing` 이다 — `int_from_float` 은 **문자열이 아니라
      // 실수 타입**이 들어올 때 나는 오류라, 쿼리 파라미터 경로에는 나타나지 않는다.
      errors.push({
        type: "int_parsing",
        loc: ["query", "top_k"],
        msg: "Input should be a valid integer, unable to parse string as an integer",
        input: rawTopK,
      });
    } else if (parsed.value < MIN_TOP_K) {
      errors.push({
        type: "greater_than_equal",
        loc: ["query", "top_k"],
        msg: `Input should be greater than or equal to ${MIN_TOP_K}`,
        input: rawTopK,
        ctx: { ge: MIN_TOP_K },
      });
    } else if (parsed.value > MAX_TOP_K) {
      errors.push({
        type: "less_than_equal",
        loc: ["query", "top_k"],
        msg: `Input should be less than or equal to ${MAX_TOP_K}`,
        input: rawTopK,
        ctx: { le: MAX_TOP_K },
      });
    } else {
      topK = parsed.value;
    }
  }

  if (errors.length) return { ok: false, detail: errors };
  return { ok: true, params: { q, topK, docId: sp.get("doc_id") } };
}
