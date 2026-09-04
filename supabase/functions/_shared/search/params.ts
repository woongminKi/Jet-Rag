/**
 * `/search` 쿼리 파라미터 검증 — `api/app/routers/search.py` 의 진입부 포팅.
 *
 * ## 검증이 두 층으로 나뉜다
 * 원본은 FastAPI 라 **프레임워크가 먼저** 걸러내고(422), 통과한 값만 핸들러가 다시 본다(400).
 * 두 층의 응답 모양이 아예 다르다:
 *
 * | 층 | 상태 | `detail` |
 * |---|---|---|
 * | pydantic Query 검증 | **422** | 구조화된 **배열** — `{type, loc, msg, input, ctx?}` |
 * | 핸들러 내부 검사 | **400** | 사람이 읽는 **문자열** |
 *
 * 프론트가 이 모양에 의존하므로 층을 합치면 안 된다. 아래 값들은 운영 엔드포인트를
 * 직접 때려 받아 적은 것이다(2026-09-04).
 *
 * ## 순서도 계약이다
 * pydantic 은 **함수 시그니처 선언 순서**로 검증한다: `q → limit → offset → … → doc_id → mode`.
 * 핸들러는 그 다음에 **빈 질의 → doc_type → doc_id → mode → from_date → to_date** 순으로 본다.
 * 여러 개가 동시에 틀리면 어느 것이 먼저 나오는지가 달라지므로 순서를 맞춘다.
 *
 * ## `doc_type` 은 pydantic 이 아니라 핸들러가 본다
 * 시그니처의 `Query(default=None)` 에는 제약이 없어서 422 가 안 난다. 60 줄 뒤 핸들러가
 * `_DOC_TYPES` 화이트리스트로 검사해 **400** 을 낸다. 시그니처만 보고 포팅하면 통째로
 * 빠지는 자리다(실제로 처음에 빠뜨렸다). 대소문자 구분하며 빈 문자열도 거부한다(실측).
 *
 * ## 미검증 — meta_fast_path 와의 순서
 * 원본은 빈 질의 검사 **직후** meta fast path 를 돌리고, 거기서 결과가 나오면 아래 검사를
 * 하나도 거치지 않고 반환한다(`search.py` 702 → 709). 즉 fast path 가 뜨는 질의에서는
 * `doc_type=bogus` 가 400 이 아니라 200 이 될 수 있고, **메타 필터 4종이 통째로 무시된다**
 * (`_run_meta_fast_path` 는 tags·doc_type·from_date·to_date 를 인자로 받지 않는다).
 * 비인증 프로브는 문서 0 건이라 fast path 가 항상 fallback 해서 이걸 재현하지 못했다 —
 * 소스 기준 판정이다. 호출 순서는 Task 2.6/2.8 에서 결정한다.
 *
 * ## 경계 하나 — 공백만 있는 질의
 * `q=" "` 는 pydantic 의 `min_length=1` 을 **통과한다**(길이 1). 그 뒤 핸들러가 `strip()` 해서
 * 비면 400 `검색어가 비어있습니다.` 를 낸다. 전각 공백(U+3000)도 마찬가지다(실측).
 * 즉 "빈 질의" 판정은 422 가 아니라 400 이다.
 */

import { parseSearchDate } from "./iso_datetime.ts";

/** pydantic 이 만드는 422 항목. */
export interface ValidationErrorItem {
  type: string;
  loc: [string, string];
  msg: string;
  input: string | null;
  ctx?: Record<string, number>;
}

export type SearchMode = "hybrid" | "dense" | "sparse";

export interface SearchParams {
  /** 원본 그대로 — 로그·응답의 `query` 필드에 쓴다. */
  q: string;
  /** NFC 정규화 + trim. 실제 검색에 쓰는 값. */
  cleanQ: string;
  limit: number;
  offset: number;
  tags: string[] | null;
  docType: string | null;
  /** 정규화된 isoformat 문자열. PostgREST `created_at` 필터에 그대로 넣는다. */
  fromDate: string | null;
  toDate: string | null;
  docId: string | null;
  mode: SearchMode;
}

export type ValidationResult =
  | { ok: true; status: 200; detail: null; params: SearchParams }
  | { ok: false; status: 400 | 422; detail: string | ValidationErrorItem[] };

const MAX_QUERY_LEN = 200;
const MIN_LIMIT = 1;
const MAX_LIMIT = 50;
const DEFAULT_LIMIT = 10;
const MAX_DOC_ID_LEN = 64;
const MODES: readonly string[] = ["hybrid", "dense", "sparse"];
/** `001_init.sql` 의 doc_type CHECK 제약과 동일. 원본 `_DOC_TYPES`. */
const DOC_TYPES: ReadonlySet<string> = new Set([
  "pdf",
  "hwp",
  "hwpx",
  "docx",
  "pptx",
  "image",
  "url",
  "txt",
  "md",
]);

/** 코드포인트 길이. pydantic 의 `len()` 은 코드포인트 기준이라 `.length` 와 다를 수 있다. */
function charLen(s: string): number {
  return [...s].length;
}

/** pydantic 의 정수 파싱 — 공백은 허용, 소수점·문자는 거부. */
function parseIntStrict(raw: string): number | null {
  const t = raw.trim();
  if (!/^[+-]?\d+$/.test(t)) return null;
  return Number.parseInt(t, 10);
}

function intField(
  name: string,
  raw: string | null,
  fallback: number,
  ge: number,
  le: number | null,
  errors: ValidationErrorItem[],
): number {
  if (raw === null) return fallback;
  const n = parseIntStrict(raw);
  if (n === null) {
    errors.push({
      type: "int_parsing",
      loc: ["query", name],
      msg: "Input should be a valid integer, unable to parse string as an integer",
      input: raw,
    });
    return fallback;
  }
  if (n < ge) {
    errors.push({
      type: "greater_than_equal",
      loc: ["query", name],
      msg: `Input should be greater than or equal to ${ge}`,
      input: raw,
      ctx: { ge },
    });
    return fallback;
  }
  if (le !== null && n > le) {
    errors.push({
      type: "less_than_equal",
      loc: ["query", name],
      msg: `Input should be less than or equal to ${le}`,
      input: raw,
      ctx: { le },
    });
    return fallback;
  }
  return n;
}

export function validateSearchParams(sp: URLSearchParams): ValidationResult {
  const errors: ValidationErrorItem[] = [];

  // --- 1층: pydantic Query 검증 (선언 순서 q → limit → offset) ---
  const rawQ = sp.get("q");
  if (rawQ === null) {
    errors.push({ type: "missing", loc: ["query", "q"], msg: "Field required", input: null });
  } else if (charLen(rawQ) < 1) {
    errors.push({
      type: "string_too_short",
      loc: ["query", "q"],
      msg: "String should have at least 1 character",
      input: rawQ,
      ctx: { min_length: 1 },
    });
  } else if (charLen(rawQ) > MAX_QUERY_LEN) {
    errors.push({
      type: "string_too_long",
      loc: ["query", "q"],
      msg: `String should have at most ${MAX_QUERY_LEN} characters`,
      input: rawQ,
      ctx: { max_length: MAX_QUERY_LEN },
    });
  }

  const limit = intField("limit", sp.get("limit"), DEFAULT_LIMIT, MIN_LIMIT, MAX_LIMIT, errors);
  const offset = intField("offset", sp.get("offset"), 0, 0, null, errors);

  if (errors.length) return { ok: false, status: 422, detail: errors };

  // --- 2층: 핸들러 내부 검사 (빈 질의 → doc_type → doc_id → mode → from_date → to_date) ---
  const q = rawQ as string;
  // DB title 이 NFC 라 질의도 NFC 로 맞춘다. NFD 로 오면 매칭이 통째로 실패한다.
  const cleanQ = q.trim().normalize("NFC");
  if (!cleanQ) return { ok: false, status: 400, detail: "검색어가 비어있습니다." };

  const docType = sp.get("doc_type");
  if (docType !== null && !DOC_TYPES.has(docType)) {
    return { ok: false, status: 400, detail: `doc_type='${docType}' 가 유효하지 않습니다.` };
  }

  let docId = sp.get("doc_id");
  if (docId !== null) {
    docId = docId.trim();
    if (!docId || charLen(docId) > MAX_DOC_ID_LEN) {
      return { ok: false, status: 400, detail: "doc_id 형식이 유효하지 않습니다." };
    }
  }

  const mode = sp.get("mode") ?? "hybrid";
  if (!MODES.includes(mode)) {
    return {
      ok: false,
      status: 400,
      detail: `mode='${mode}' 가 유효하지 않습니다 (hybrid/dense/sparse).`,
    };
  }

  const rawFrom = sp.get("from_date");
  const fromDate = parseSearchDate(rawFrom);
  if (fromDate === undefined) {
    return { ok: false, status: 400, detail: `from_date='${rawFrom}' 가 ISO 8601 형식이 아닙니다.` };
  }
  const rawTo = sp.get("to_date");
  const toDate = parseSearchDate(rawTo);
  if (toDate === undefined) {
    return { ok: false, status: 400, detail: `to_date='${rawTo}' 가 ISO 8601 형식이 아닙니다.` };
  }

  const tags = sp.getAll("tags");

  return {
    ok: true,
    status: 200,
    detail: null,
    params: {
      q,
      cleanQ,
      limit,
      offset,
      tags: tags.length ? tags : null,
      docType,
      fromDate,
      toDate,
      docId,
      mode: mode as SearchMode,
    },
  };
}
