/**
 * 룰 기반 엔티티 추출 — `services/entity_extract.py` 의 정형 패턴 4종 포팅.
 *
 * `chunk` 가 청크마다 부른다(`chunks.metadata.entities`). LLM 보강 부분
 * (`persons`/`orgs`/`products`)은 chunk 경로에서 안 쓰므로 옮기지 않았다.
 *
 * ## `\b` 를 그대로 옮기면 한국어 금액이 통째로 안 잡힌다
 * Python `\b` 는 **유니코드 `\w`** 경계, JS `\b` 는 **ASCII `\w`** 경계다. 실측(2026-09-07):
 *
 * | 입력 | Python | JS `\b` |
 * |---|---|---|
 * | `50,000원` | 매칭 | **안 함** (`원` 이 JS 기준 non-word) |
 * | `100만원` | 매칭 | **안 함** |
 * | `$100달러` | 안 함 | **매칭** (`달` 이 JS 기준 non-word) |
 * | `약25%` | 안 함 | **매칭** |
 *
 * 그래서 `\b` 를 `PY_WORD_CLASS` lookaround 로 풀어 쓴다. **`v` 플래그가 필요하다**
 * (집합 뺄셈). `u` 와는 함께 못 쓴다.
 *
 * ## `\d` 도 다르다
 * Python `\d` 는 유니코드 Nd 전부 — `２５%`·`５０,０００원`(전각)이 잡힌다. `\p{Nd}` 로 옮겼다.
 */

import { PY_SP, pyStrip } from "../search/pystr.ts";
import { PY_WORD_CLASS } from "../pychar.ts";

/**
 * Python `\b` 그대로 — "한쪽은 word, 다른 쪽은 아님". 양쪽 lookbehind 실패가
 * 문자열 시작/끝을 자동으로 non-word 취급해 준다.
 *
 * `(?<!W)`(앞 경계) / `(?!W)`(뒤 경계) 로 줄여 쓰고 싶지만 **그건 패턴의 양끝 문자가
 * 반드시 word 일 때만 같다.** ISBN 패턴 `[\p{Nd}\-Xx]{10,17}` 은 `-` 로 끝날 수 있어
 * (`ISBN 1234567890-a` → Python 은 `1234567890-` 를 잡는다) 줄여 쓰면 갈린다.
 */
const B = `(?:(?<=${PY_WORD_CLASS})(?!${PY_WORD_CLASS})|(?<!${PY_WORD_CLASS})(?=${PY_WORD_CLASS}))`;
const B_BEFORE = B;
const B_AFTER = B;
/** Python `\d`(유니코드 Nd) · `\s`(Python 공백 집합). */
const D = "\\p{Nd}";
const S = `[${PY_SP}]`;

/** `v` 플래그로 만든다 — 집합 뺄셈이 들어 있다. */
const re = (src: string) => new RegExp(src, "gv");

/** 날짜 — `2024년 4월 30일` / `2024.04.30` / `2024-04` */
const DATE_PATTERNS = [
  re(`(${D}{4})${S}*년${S}*(${D}{1,2})${S}*월(?:${S}*(${D}{1,2})${S}*일)?`),
  re(`(?<!${D})(${D}{4})[.\\-\\/]${S}*(${D}{1,2})[.\\-\\/]${S}*(${D}{1,2})(?!${D})`),
  re(`(?<!${D})(${D}{4})[.\\-]${S}*(${D}{1,2})(?!${D})`),
];

/** 금액 — `50,000원` / `100만원` / `$100` / `₩1,000` */
const AMOUNT_PATTERNS = [
  re(`${B_BEFORE}${D}{1,3}(?:,${D}{3})+(?:\\.${D}+)?${S}*원${B_AFTER}`),
  re(`${B_BEFORE}${D}{1,4}(?:\\.${D}+)?${S}*(?:만|억|조|천)${S}*원${B_AFTER}`),
  re(`\\$${D}{1,3}(?:,${D}{3})*(?:\\.${D}+)?${B_AFTER}`),
  re(`₩${D}{1,3}(?:,${D}{3})*(?:\\.${D}+)?${B_AFTER}`),
];

/** 백분율 — `25%` / `1.5%` */
const PERCENT_PATTERN = re(`${B_BEFORE}${D}{1,3}(?:\\.${D}+)?${S}*%`);

/** 식별자 — ISSN / ISBN / `제709호` */
const IDENTIFIER_PATTERNS = [
  re(`${B_BEFORE}ISSN${S}+(${D}{4}-${D}{3}[${D}Xx])${B_AFTER}`),
  re(`${B_BEFORE}ISBN${S}+([${D}\\-Xx]{10,17})${B_AFTER}`),
  re(`제${S}*${D}+${S}*호`),
];

export interface ExtractedEntities {
  dates: string[];
  amounts: string[];
  percentages: string[];
  identifiers: string[];
}

/**
 * strip 후 빈 것 제외, **순서 보존** 중복 제거.
 *
 * `pyStrip` 을 `trim()` 으로 되돌려도 음성 대조가 0 건이었다. 케이스 부족이 아니라
 * **구조적 no-op** 이다 — 패턴 8 개가 전부 숫자·`$`·`₩`·`제` 로 시작해 숫자·`원`·`%`·
 * `호`·`[Nd Xx]` 로 끝나므로 매칭 결과가 공백으로 시작하거나 끝날 수 없다(무작위
 * 200,000 입력 × 30,587 매칭에서 `strip()` 이 값을 바꾼 경우 0 건). 원본 계약이라
 * 남겨 두되, 여기서 갈릴 일은 없다.
 */
function dedupPreserveOrder(items: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const it of items) {
    const s = pyStrip(it);
    if (s && !seen.has(s)) {
      seen.add(s);
      out.push(s);
    }
  }
  return out;
}

function collect(text: string, patterns: RegExp[], useGroup1 = false): string[] {
  if (!text) return [];
  const out: string[] = [];
  for (const p of patterns) {
    p.lastIndex = 0; // `g` 정규식은 상태를 갖는다 — 재사용 전에 반드시 되돌린다.
    for (const m of text.matchAll(p)) {
      // 원본 `m.group(1) if m.lastindex else m.group(0)` —
      // 그룹이 **매칭됐을 때만** 1 번을 쓴다.
      out.push(useGroup1 && m[1] !== undefined ? m[1] : m[0]);
    }
  }
  return dedupPreserveOrder(out);
}

export function extractDates(text: string): string[] {
  return collect(text, DATE_PATTERNS);
}
export function extractAmounts(text: string): string[] {
  return collect(text, AMOUNT_PATTERNS);
}
export function extractPercentages(text: string): string[] {
  return collect(text, [PERCENT_PATTERN]);
}
export function extractIdentifiers(text: string): string[] {
  return collect(text, IDENTIFIER_PATTERNS, true);
}

export function extractEntities(text: string): ExtractedEntities {
  return {
    dates: extractDates(text),
    amounts: extractAmounts(text),
    percentages: extractPercentages(text),
    identifiers: extractIdentifiers(text),
  };
}

/** 네 항목이 전부 비면 참. 호출부는 이때 `metadata.entities` 를 아예 안 넣는다. */
export function entitiesEmpty(e: ExtractedEntities): boolean {
  return !(e.dates.length || e.amounts.length || e.percentages.length ||
    e.identifiers.length);
}
