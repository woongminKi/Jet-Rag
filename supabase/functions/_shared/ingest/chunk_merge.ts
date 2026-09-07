/**
 * 짧은 섹션 병합 — `chunk.py` 의 `_merge_short_sections` · `_looks_like_table_cell` 포팅.
 *
 * ## 왜 이 단계가 까다로운가
 * 표 셀 판정 하나가 뒤집히면 **병합 여부가 달라지고 청크 경계가 통째로 바뀐다.**
 * 그 판정이 Python 문자 메서드 3종에 의존하는데 JS 기본 클래스로는 못 맞춘다 —
 * 그래서 `pychar.ts` 를 전수 대조로 먼저 만들었다.
 *
 * ```python
 * non_ws      = sum(1 for c in stripped if not c.isspace())
 * digit_punct = sum(1 for c in stripped
 *                   if c.isdigit() or (not c.isalnum() and not c.isspace()))
 * return digit_punct / non_ws >= 0.5
 * ```
 *
 * ## 길이는 코드포인트
 * `len(stripped) >= 30` · `len(buf.text) < 200` · `+2 <= 1000` 전부 Python `len()` 이다.
 */

import { pyStrip } from "../search/pystr.ts";
import { pyIsAlnum, pyIsDigit, pyIsSpace } from "../pychar.ts";
import type { ExtractedSection } from "./hwp_extract.ts";

/** `_MIN_MERGE_SIZE` · `_MAX_SIZE`. */
export const MIN_MERGE_SIZE = 200;
export const MAX_SIZE = 1000;

/** 표 셀 판정에 쓰는 길이 임계(`len(stripped) >= 30` 이면 본문으로 본다). */
const TABLE_CELL_MAX_LEN = 30;
const DIGIT_PUNCT_RATIO = 0.5;

function cpLen(s: string): number {
  let n = 0;
  for (const _ of s) n++;
  return n;
}

/**
 * 표 셀로 의심되는 짧은 조각인지. 인접 본문과의 **병합을 막는** 용도다.
 *
 * `chunk_filter` 의 `table_noise` 와 책임이 다르다 — 저쪽은 검색 제외용이다.
 */
export function looksLikeTableCell(text: string): boolean {
  if (text.includes(" | ")) return true;
  const stripped = pyStrip(text);
  if (cpLen(stripped) >= TABLE_CELL_MAX_LEN) return false;

  let nonWs = 0;
  let digitPunct = 0;
  for (const ch of stripped) {
    const space = pyIsSpace(ch);
    if (!space) nonWs++;
    // 원본 그대로 — `isdigit` 이거나, (alnum 이 아니고 공백도 아닌) 문자.
    if (pyIsDigit(ch) || (!pyIsAlnum(ch) && !space)) digitPunct++;
  }
  if (nonWs === 0) return false;
  return digitPunct / nonWs >= DIGIT_PUNCT_RATIO;
}

/**
 * 짧은 섹션을 앞뒤로 붙인다.
 *
 * 병합 조건 3가지가 **모두** 참이어야 한다 — 앞 조각이 `MIN_MERGE_SIZE` 미만이고,
 * 같은 페이지이고, 합쳐도 `MAX_SIZE` 이하(구분자 `\n\n` 2자 포함).
 *
 * 병합 시:
 * - `section_title` 은 **뒤쪽 우선**(`section.section_title || buf.section_title`).
 *   앞이 title 없는 짧은 조각일 가능성이 높아서다.
 * - `bbox` 는 `null` — 합성이 근사가 어렵다.
 * - `metadata` 는 dict merge 이고 **뒤쪽이 이긴다**.
 */
export function mergeShortSections(sections: ExtractedSection[]): ExtractedSection[] {
  const merged: ExtractedSection[] = [];
  let buf: ExtractedSection | null = null;
  for (const section of sections) {
    if (buf === null) {
      buf = section;
      continue;
    }
    // 표 셀 의심 조각은 본문과 섞이면 의미가 오염된다 — 어느 쪽이든 걸리면 끊는다.
    if (looksLikeTableCell(buf.text) || looksLikeTableCell(section.text)) {
      merged.push(buf);
      buf = section;
      continue;
    }
    const canMerge = cpLen(buf.text) < MIN_MERGE_SIZE &&
      buf.page === section.page &&
      cpLen(buf.text) + cpLen(section.text) + 2 <= MAX_SIZE;
    if (canMerge) {
      buf = {
        text: `${buf.text}\n\n${section.text}`,
        page: buf.page,
        section_title: section.section_title || buf.section_title,
        bbox: null,
        metadata: { ...buf.metadata, ...section.metadata },
      };
    } else {
      merged.push(buf);
      buf = section;
    }
  }
  if (buf !== null) merged.push(buf);
  return merged;
}
