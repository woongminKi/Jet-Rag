/**
 * Python 의 문자 판정 메서드 3종 이식 — `str.isspace()` · `str.isdigit()` · `str.isalnum()`.
 *
 * `chunk.py` 의 `_looks_like_table_cell` 이 이 셋을 전부 쓴다:
 * ```python
 * non_ws     = sum(1 for c in stripped if not c.isspace())
 * digit_punct = sum(1 for c in stripped
 *                   if c.isdigit() or (not c.isalnum() and not c.isspace()))
 * ```
 * 세 판정이 조금만 어긋나도 표 셀 판정이 뒤집히고, 그러면 **섹션 병합 여부가 달라져
 * 청크 경계가 통째로 바뀐다.** JS 기본 문자 클래스로는 맞출 수 없어 전수 대조로 집합을 떴다.
 *
 * ## 전수 대조 결과 (2026-09-07, 0x0~0x10FFFF)
 * | Python | JS 후보 | 차이 |
 * |---|---|---|
 * | `isspace` | `/\s/u` | Python 만 `\x1C-\x1F`·`\x85` 5 자, JS 만 `\uFEFF` 1 자 |
 * | `isdigit` | `/\p{Nd}/u` | Python 만 128 자(`²³¹` 등), JS 만 80 자 |
 * | `isalnum` | `/[\p{L}\p{N}]/u` | **JS 만 5,004 자** (Python 은 부분집합) |
 *
 * `isalnum` 은 Python 집합을 통째로 박으면 747 개 범위지만, `[\p{L}\p{N}]` 에서
 * **27 개 범위만 빼면** 같아진다 — 그쪽을 골랐다.
 *
 * ## 이 차이는 유니코드 버전에서 온다
 * Deno 가 Python 보다 새 유니코드를 쓴다. 즉 **런타임이 올라가면 집합이 달라질 수 있다.**
 * 그래서 `pychar_test.ts` 가 전 코드포인트를 Python fixture 와 대조한다 — 조용히 갈리면
 * 그 테스트가 먼저 깨진다.
 */

import { PY_SP } from "./search/pystr.ts";

/** Python `str.isspace()` — `pystr.ts` 의 공백 집합과 같다(전수 확인). */
const SPACE_RE = new RegExp(`^[${PY_SP}]$`, "u");

export function pyIsSpace(ch: string): boolean {
  return ch.length > 0 && SPACE_RE.test(ch);
}

/** Python `str.isdigit()` 이 참인 코드포인트 범위. `\p{Nd}` 와 양방향으로 다르다. */
const DIGIT_RANGES: [number, number][] = [
  [0x30, 0x39],
  [0xB2, 0xB3],
  [0xB9, 0xB9],
  [0x660, 0x669],
  [0x6F0, 0x6F9],
  [0x7C0, 0x7C9],
  [0x966, 0x96F],
  [0x9E6, 0x9EF],
  [0xA66, 0xA6F],
  [0xAE6, 0xAEF],
  [0xB66, 0xB6F],
  [0xBE6, 0xBEF],
  [0xC66, 0xC6F],
  [0xCE6, 0xCEF],
  [0xD66, 0xD6F],
  [0xDE6, 0xDEF],
  [0xE50, 0xE59],
  [0xED0, 0xED9],
  [0xF20, 0xF29],
  [0x1040, 0x1049],
  [0x1090, 0x1099],
  [0x1369, 0x1371],
  [0x17E0, 0x17E9],
  [0x1810, 0x1819],
  [0x1946, 0x194F],
  [0x19D0, 0x19DA],
  [0x1A80, 0x1A89],
  [0x1A90, 0x1A99],
  [0x1B50, 0x1B59],
  [0x1BB0, 0x1BB9],
  [0x1C40, 0x1C49],
  [0x1C50, 0x1C59],
  [0x2070, 0x2070],
  [0x2074, 0x2079],
  [0x2080, 0x2089],
  [0x2460, 0x2468],
  [0x2474, 0x247C],
  [0x2488, 0x2490],
  [0x24EA, 0x24EA],
  [0x24F5, 0x24FD],
  [0x24FF, 0x24FF],
  [0x2776, 0x277E],
  [0x2780, 0x2788],
  [0x278A, 0x2792],
  [0xA620, 0xA629],
  [0xA8D0, 0xA8D9],
  [0xA900, 0xA909],
  [0xA9D0, 0xA9D9],
  [0xA9F0, 0xA9F9],
  [0xAA50, 0xAA59],
  [0xABF0, 0xABF9],
  [0xFF10, 0xFF19],
  [0x104A0, 0x104A9],
  [0x10A40, 0x10A43],
  [0x10D30, 0x10D39],
  [0x10E60, 0x10E68],
  [0x11052, 0x1105A],
  [0x11066, 0x1106F],
  [0x110F0, 0x110F9],
  [0x11136, 0x1113F],
  [0x111D0, 0x111D9],
  [0x112F0, 0x112F9],
  [0x11450, 0x11459],
  [0x114D0, 0x114D9],
  [0x11650, 0x11659],
  [0x116C0, 0x116C9],
  [0x11730, 0x11739],
  [0x118E0, 0x118E9],
  [0x11950, 0x11959],
  [0x11C50, 0x11C59],
  [0x11D50, 0x11D59],
  [0x11DA0, 0x11DA9],
  [0x11F50, 0x11F59],
  [0x16A60, 0x16A69],
  [0x16AC0, 0x16AC9],
  [0x16B50, 0x16B59],
  [0x1D7CE, 0x1D7FF],
  [0x1E140, 0x1E149],
  [0x1E2F0, 0x1E2F9],
  [0x1E4F0, 0x1E4F9],
  [0x1E950, 0x1E959],
  [0x1F100, 0x1F10A],
  [0x1FBF0, 0x1FBF9],
];

/** `[\p{L}\p{N}]` 에는 있는데 Python `isalnum()` 에는 **없는** 범위. */
const ALNUM_EXCESS: [number, number][] = [
  [0x1C89, 0x1C8A],
  [0xA7CB, 0xA7CD],
  [0xA7DA, 0xA7DC],
  [0x105C0, 0x105F3],
  [0x10D40, 0x10D65],
  [0x10D6F, 0x10D85],
  [0x10EC2, 0x10EC4],
  [0x11380, 0x11389],
  [0x1138B, 0x1138B],
  [0x1138E, 0x1138E],
  [0x11390, 0x113B5],
  [0x113B7, 0x113B7],
  [0x113D1, 0x113D1],
  [0x113D3, 0x113D3],
  [0x116D0, 0x116E3],
  [0x11BC0, 0x11BE0],
  [0x11BF0, 0x11BF9],
  [0x13460, 0x143FA],
  [0x16100, 0x1611D],
  [0x16130, 0x16139],
  [0x16D40, 0x16D6C],
  [0x16D70, 0x16D79],
  [0x18CFF, 0x18CFF],
  [0x1CCF0, 0x1CCF9],
  [0x1E5D0, 0x1E5ED],
  [0x1E5F0, 0x1E5FA],
  [0x2EBF0, 0x2EE5D],
];

function inRanges(cp: number, ranges: readonly [number, number][]): boolean {
  let lo = 0, hi = ranges.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const [a, b] = ranges[mid];
    if (cp < a) hi = mid - 1;
    else if (cp > b) lo = mid + 1;
    else return true;
  }
  return false;
}

export function pyIsDigit(ch: string): boolean {
  const cp = ch.codePointAt(0);
  return cp !== undefined && inRanges(cp, DIGIT_RANGES);
}

const ALNUM_RE = /^[\p{L}\p{N}]$/u;

export function pyIsAlnum(ch: string): boolean {
  if (!ALNUM_RE.test(ch)) return false;
  const cp = ch.codePointAt(0);
  return cp !== undefined && !inRanges(cp, ALNUM_EXCESS);
}

/**
 * Python `\w` — 실측 결과 **`isalnum()` + `_`** 와 정확히 같다(137,936 = 137,935 + 1).
 * JS `[\p{L}\p{N}_]` 와의 차이 5,004 자(27 범위)도 `ALNUM_EXCESS` 와 **동일**이라
 * 그대로 재사용한다.
 */
export function pyIsWord(ch: string): boolean {
  return ch === "_" || pyIsAlnum(ch);
}

/**
 * 정규식 안에서 Python `\b` 를 흉내낼 때 쓰는 **문자 클래스 조각**.
 *
 * Python `\b` 는 유니코드 `\w` 경계인데 JS `\b` 는 ASCII `\w` 경계다. 한국어 문서에서
 * 정면으로 갈린다 — 실측 6 건(`50,000원` 이 JS 에서 아예 안 잡힘, `약25%` 는 JS 에서만
 * 잡힘). 그래서 `\b` 를 lookaround 로 풀어 쓴다:
 *
 * ```
 * 패턴 앞의 \b  →  (?<!${PY_WORD_CLASS})
 * 패턴 뒤의 \b  →  (?!${PY_WORD_CLASS})
 * ```
 *
 * **`v` 플래그가 필요하다**(집합 뺄셈 `--`). Deno 지원을 확인했다.
 * `u` 플래그와는 함께 못 쓴다.
 */
export const PY_WORD_CLASS =
  "[[\\p{L}\\p{N}_]--[\u{1C89}-\u{1C8A}\u{A7CB}-\u{A7CD}\u{A7DA}-\u{A7DC}\u{105C0}-\u{105F3}\u{10D40}-\u{10D65}\u{10D6F}-\u{10D85}\u{10EC2}-\u{10EC4}\u{11380}-\u{11389}\u{1138B}\u{1138E}\u{11390}-\u{113B5}\u{113B7}\u{113D1}\u{113D3}\u{116D0}-\u{116E3}\u{11BC0}-\u{11BE0}\u{11BF0}-\u{11BF9}\u{13460}-\u{143FA}\u{16100}-\u{1611D}\u{16130}-\u{16139}\u{16D40}-\u{16D6C}\u{16D70}-\u{16D79}\u{18CFF}\u{1CCF0}-\u{1CCF9}\u{1E5D0}-\u{1E5ED}\u{1E5F0}-\u{1E5FA}\u{2EBF0}-\u{2EE5D}]]";
