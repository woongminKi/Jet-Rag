/**
 * `api/app/services/query_classifier.py` 포팅 — 질의를 9 라벨로 나누는 룰 분류기.
 *
 * `/admin/queries/stats` 의 `query_type_distribution` 이 이 함수를 질의마다 부른다.
 * DB·외부 호출 0, 순수 함수다.
 *
 * ## Python 정규식을 그대로 옮기면 틀린다
 * | 원본 | Python | JS 기본 |
 * |---|---|---|
 * | `\d` | 유니코드 Nd 전부(680 자) — `"３"`, `"٣"` 도 숫자 | ASCII `0-9` 만 |
 * | `\s` | `\x1c-\x1f`, `\x85` 포함 / `U+FEFF` 제외 | 그 반대 |
 * | `strip()` | 위 공백 집합 | `trim()` 은 `U+FEFF` 를 뗀다 |
 *
 * 그래서 `\d` → `\p{Nd}`, `\s` → `[PY_SP]`, `strip()` → `pyStrip()` 으로 옮기고
 * `u` 플래그를 붙였다. 셋 다 대조에서 실제로 갈린 적이 있는 항목이다.
 *
 * ## `/admin` 에서는 인자 3개가 안 쓰인다
 * 호출부가 `classify_query_type(query)` 뿐이라 `sourceChunkText` ·
 * `expectedDocTitles` · `isNegative` 는 늘 기본값이다 → `synonym_mismatch` 와
 * "제목 2개 이상 → cross_doc" 분기는 **admin 경로에서 도달 불가능**하다.
 * 그래도 원본 그대로 옮겼다. 지금 빼면 나중에 evals 를 옮길 때 조용히 갈린다.
 */

import { PY_SP, pyStrip } from "./search/pystr.ts";

export type QueryType =
  | "exact_fact"
  | "fuzzy_memory"
  | "synonym_mismatch"
  | "numeric_lookup"
  | "table_lookup"
  | "vision_diagram"
  | "summary"
  | "cross_doc"
  | "out_of_scope";

/** 응답 스키마가 **항상 9 키**를 노출한다 — 0 건 라벨도 프론트가 행으로 그린다. 순서 유지. */
export const QUERY_TYPE_LABELS: readonly QueryType[] = [
  "exact_fact",
  "fuzzy_memory",
  "synonym_mismatch",
  "numeric_lookup",
  "table_lookup",
  "vision_diagram",
  "summary",
  "cross_doc",
  "out_of_scope",
];

const VISION_KEYWORDS = ["다이어그램", "그림", "도식", "구조도", "이미지", "사진", "도표"];
const TABLE_KEYWORDS = ["표", "리스트", "목록", "별표", "카테고리", "항목 목록"];
const SUMMARY_KEYWORDS = ["요약", "핵심", "정리", "개요", "짧게", "한줄", "한 줄"];
const CROSS_DOC_KEYWORDS = ["비교", "차이", "대비", "달라", "차이점"];
const FUZZY_KEYWORDS = [
  "그때",
  "어디 있더라",
  "어디 있었",
  "뭐였지",
  "있었나",
  "있었지",
  "었더라",
  "았더라",
  "기억나",
];

// 단위 alternation 은 **긴 것 먼저** — "개월" 이 "개" 보다 먼저 매칭돼야 한다.
// 순서를 바꾸면 "3개월" 이 "3개" + "월" 로 쪼개져도 결과는 같지만, 원본 정규식과
// 다른 물건이 되므로 그대로 둔다.
const NUMERIC_PATTERNS: RegExp[] = [
  new RegExp(
    `\\p{Nd}+(?:\\.\\p{Nd}+)?[${PY_SP}]*(?:개월|시간|kg|km|cm|%|원|년|월|일|회|건|개|점|명|분|초|m)`,
    "u",
  ),
  new RegExp(`몇[${PY_SP}]*[가-힣]`, "u"),
  /얼마/u,
];
const NUMERIC_KEYWORDS = ["얼마", "금액", "가격", "비용", "수치", "수량", "개수", "지원금"];

/** query 가 한쪽 표현, source 가 반대편 표현이면 `synonym_mismatch`. */
const SYNONYM_PAIRS: [string, string][] = [
  ["개인정보", "비식별화"],
  ["환자 정보", "비식별화"],
  ["색상", "컬러"],
  ["시트", "가죽"],
  ["규정", "내규"],
  ["직원", "임직원"],
  ["회의", "협의"],
];

export interface ClassifyOptions {
  sourceChunkText?: string;
  expectedDocTitles?: string[] | null;
  isNegative?: boolean;
}

/**
 * 질의 → 9 라벨 하나. 우선순위는 원본 순서 그대로다.
 *
 * 1 `isNegative` → out_of_scope · 2 vision · 3 table · 4 cross_doc ·
 * 5 numeric · 6 summary · 7 synonym · 8 fuzzy · 9 exact_fact(기본)
 */
export function classifyQueryType(
  query: string,
  opts: ClassifyOptions = {},
): QueryType {
  if (opts.isNegative) return "out_of_scope";

  const q = pyStrip(query);

  if (VISION_KEYWORDS.some((kw) => q.includes(kw))) return "vision_diagram";
  if (TABLE_KEYWORDS.some((kw) => q.includes(kw))) return "table_lookup";

  const titles = opts.expectedDocTitles;
  if (titles && titles.length >= 2) return "cross_doc";
  if (CROSS_DOC_KEYWORDS.some((kw) => q.includes(kw))) return "cross_doc";

  if (NUMERIC_PATTERNS.some((p) => p.test(q))) return "numeric_lookup";
  if (NUMERIC_KEYWORDS.some((kw) => q.includes(kw))) return "numeric_lookup";

  if (SUMMARY_KEYWORDS.some((kw) => q.includes(kw))) return "summary";

  const src = opts.sourceChunkText ?? "";
  if (src) {
    for (const [a, b] of SYNONYM_PAIRS) {
      const inQueryA = q.includes(a);
      const inQueryB = q.includes(b);
      const inSourceA = src.includes(a);
      const inSourceB = src.includes(b);
      if (
        (inQueryA && inSourceB && !inSourceA) ||
        (inQueryB && inSourceA && !inSourceB)
      ) {
        return "synonym_mismatch";
      }
    }
  }

  if (FUZZY_KEYWORDS.some((kw) => q.includes(kw))) return "fuzzy_memory";

  return "exact_fact";
}
