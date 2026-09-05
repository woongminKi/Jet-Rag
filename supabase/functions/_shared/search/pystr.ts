/**
 * Python 문자열 규약 중 JS 와 갈리는 것들 — 여러 모듈이 같은 함정을 밟아서 한곳에 모았다.
 *
 * | 대상 | Python | JS |
 * |---|---|---|
 * | `re` 의 `\s` | `U+001C`~`U+001F`·`U+0085` 포함, `U+FEFF` 제외 | 반대 |
 * | `str.strip()` | `re` 의 `\s` 와 같은 집합(전수 확인) | `trim()` 은 `U+FEFF` 도 버린다 |
 * | `re` 의 `\d` | 유니코드 십진 숫자 전부(Nd, 680 자) | ASCII 만 |
 * | `int()` | Nd 문자를 값으로 받는다 | `Number()` 는 안 받는다 |
 *
 * 전부 2026-09-04~05 에 실측한 것이다. 한국어 업무 문서에서 자주 나오지는 않지만,
 * **판정 규칙이 곧 검색 결과**라 어긋나면 조용히 다른 답이 나온다.
 */

/** Python `re` 의 `\s`(str 패턴) 문자 집합 — 대괄호 없는 본문. */
export const PY_SP = " \\t\\n\\r\\f\\v\\u001c-\\u001f\\u0085\\u00a0\\u1680\\u2000-\\u200a" +
  "\\u2028\\u2029\\u202f\\u205f\\u3000";

/** `str.strip()` 대응. `trim()` 과 달리 `U+FEFF` 를 남긴다. */
export const PY_STRIP_RE = new RegExp(`^[${PY_SP}]+|[${PY_SP}]+$`, "gu");

/** 연속 공백 1 개로 접기 — `re.sub(r"\s+", " ", s)`. */
export const PY_SPACE_RUN_RE = new RegExp(`[${PY_SP}]+`, "gu");

/** Python `str.strip()`. */
export function pyStrip(s: string): string {
  return s.replace(PY_STRIP_RE, "");
}

/** Python `str.split()` — 연속 공백을 하나로 보고 양 끝 빈 토큰을 버린다. */
export function pySplit(s: string): string[] {
  return s.split(PY_SPACE_RUN_RE).filter((t) => t !== "");
}

/**
 * 유니코드 십진 숫자(카테고리 Nd)를 ASCII 로 옮긴다.
 *
 * Python `re` 의 `\d` 와 `int()` 가 둘 다 Nd 를 받으므로(`int("２０２５") == 2025`),
 * 숫자 패턴을 옮길 때 이 변환을 먼저 해야 같은 판정이 나온다. Nd 는 언제나 10 개씩
 * 연속된 구간으로만 나타나므로(구간 64 개 전수 확인) 구간의 `0` 까지 내려가면 값이 나온다.
 */
export function ndToAscii(s: string): string {
  if (!/\p{Nd}/u.test(s)) return s;
  return [...s].map((ch) => {
    if (!/^\p{Nd}$/u.test(ch)) return ch;
    const cp = ch.codePointAt(0)!;
    let base = cp;
    while (cp - base < 9 && base > 0 && /^\p{Nd}$/u.test(String.fromCodePoint(base - 1))) {
      base--;
    }
    return String(cp - base);
  }).join("");
}
