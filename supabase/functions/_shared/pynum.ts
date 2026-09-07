/**
 * Python 수치 규약 중 JS 와 갈리는 것 — 여러 모듈이 쓰므로 한곳에 모았다.
 * (`_shared/search/pystr.ts` 의 문자열 판.)
 */

import { pyIsSpace } from "./pychar.ts";

/**
 * Python `round(x, nd)` — 은행가 반올림. 이진 표현을 정확히 꺼내 `BigInt` 로 비교하므로
 * 부동소수 오차 없이 원본과 같은 값이 나온다.
 *
 * `10^nd` 로 곱한 값이 `2^53` 을 넘으면 마지막 나눗셈에서 정밀도를 잃는다 — relevance 는
 * 0~1 에 `nd=4` 라 해당 없다.
 */
export function pyRound(x: number, nd: number): number {
  if (!Number.isFinite(x) || x === 0) return x;
  const neg = x < 0;
  const a = Math.abs(x);

  const view = new DataView(new ArrayBuffer(8));
  view.setFloat64(0, a);
  const bits = view.getBigUint64(0);
  const expBits = Number((bits >> 52n) & 0x7ffn);
  const mantBits = bits & 0xf_ffff_ffff_ffffn;
  // a = m × 2^e 로 정확히 분해한다 (지수부 0 은 비정규화 수).
  const m = expBits === 0 ? mantBits : mantBits | (1n << 52n);
  const e = expBits === 0 ? -1074 : expBits - 1075;

  const p = 10n ** BigInt(nd);
  let q: bigint;
  if (e >= 0) {
    q = m * p * (1n << BigInt(e)); // 이미 정수 — 반올림할 것이 없다
  } else {
    const k = BigInt(-e);
    const n = m * p;
    q = n >> k;
    const r = n - (q << k);
    const half = 1n << (k - 1n);
    // 정확히 절반이면 짝수 쪽으로 — 이게 Python 과 JS 가 갈리는 지점이다.
    if (r > half || (r === half && (q & 1n) === 1n)) q += 1n;
  }
  const out = Number(q) / Number(p);
  return neg ? -out : out;
}

/**
 * Python `f"{x:.<nd>f}"`. `toFixed` 로는 안 된다 — Python 은 정확한 십진 변환 후
 * **짝수 쪽 반올림**을 하고, `toFixed` 는 이진 근사값을 절반에서 위로 올린다.
 * (`(0.125).toFixed(2)` → "0.13", Python `f"{0.125:.2f}"` → "0.12")
 *
 * 비용 한도 메시지가 `warnings[]` 로 문서에 남으므로 눈에 보이는 값이다.
 */
export function pyFormatF(x: number, nd: number): string {
  if (Number.isNaN(x)) return "nan";
  if (!Number.isFinite(x)) return x > 0 ? "inf" : "-inf";
  const neg = x < 0 || Object.is(x, -0);
  const a = Math.abs(x);

  const view = new DataView(new ArrayBuffer(8));
  view.setFloat64(0, a);
  const bits = view.getBigUint64(0);
  const expBits = Number((bits >> 52n) & 0x7ffn);
  const mantBits = bits & 0xf_ffff_ffff_ffffn;
  const m = expBits === 0 ? mantBits : mantBits | (1n << 52n);
  const e = expBits === 0 ? -1074 : expBits - 1075;

  const p = 10n ** BigInt(nd);
  let q: bigint;
  if (e >= 0) {
    q = m * p * (1n << BigInt(e));
  } else {
    const k = BigInt(-e);
    const n = m * p;
    q = n >> k;
    const r = n - (q << k);
    const half = 1n << (k - 1n);
    if (r > half || (r === half && (q & 1n) === 1n)) q += 1n;
  }

  let digits = q.toString();
  if (nd > 0) {
    if (digits.length <= nd) digits = digits.padStart(nd + 1, "0");
    digits = `${digits.slice(0, digits.length - nd)}.${digits.slice(digits.length - nd)}`;
  }
  // Python 은 -0.0 도 "-0.0000" 으로 낸다.
  return neg ? `-${digits}` : digits;
}

/**
 * Python `float(v)` — 변환 못 하면 `null`(원본의 `TypeError`/`ValueError` 자리).
 *
 * JS `Number()` 를 그냥 쓰면 조용히 틀린다:
 * - `Number("")`, `Number("   ")` → 0 인데 Python 은 예외
 * - `Number("0x10")` → 16, `Number("0b11")` → 3 인데 Python 은 예외
 * - `Number("1_0")` → NaN 인데 Python 은 10.0 (밑줄 구분자 허용)
 * - `Number("infinity")` → NaN 인데 Python 은 inf
 * 비용 SUM 에 들어가는 값이라 하나만 어긋나도 한도 판정이 뒤집힌다.
 */
/**
 * CPython 이 `float(str)` 앞에 거는 `_PyUnicode_TransformDecimalAndSpaceToASCII` —
 * 유니코드 십진 숫자를 ASCII 숫자로, 유니코드 공백을 스페이스로 바꾼다.
 *
 * 그래서 `float("٣")` 이 3.0 이다. 대조에서 이 한 건이 걸려 알게 됐다.
 *
 * `\p{Nd}` 는 10자씩 연속 블록이라, 뒤로 최대 9 코드포인트만 훑으면 그 블록의 `0` 을
 * 찾을 수 있다.
 */
function toAsciiDecimal(s: string): string {
  let ascii = true;
  for (let i = 0; i < s.length; i++) {
    if (s.charCodeAt(i) > 0x7f) { ascii = false; break; }
  }
  if (ascii) return s; // ASCII 뿐이면 할 일이 없다
  let out = "";
  for (const ch of s) {
    const cp = ch.codePointAt(0)!;
    if (cp < 0x80) {
      out += ch;
    } else if (/\p{Nd}/u.test(ch)) {
      let zero = cp;
      while (zero > cp - 10 && /\p{Nd}/u.test(String.fromCodePoint(zero - 1))) zero--;
      out += String(cp - zero);
    } else if (pyIsSpace(ch)) {
      // JS `\s` 로는 부족하다 — U+0085(NEL) 를 안 잡는다. Python 집합을 그대로 쓴다.
      out += " ";
    } else {
      out += ch;
    }
  }
  return out;
}

/**
 * Python `int(str)` — 변환 못 하면 `null`. 10진수만 받는다.
 *
 * `parseInt` 과 다르다: `parseInt("12abc")` 는 12 지만 Python 은 예외고,
 * `parseInt("0x10")` 은 16 이지만 Python `int("0x10")` 도 예외다(밑수 인자 없이는).
 * 유니코드 십진 숫자·앞뒤 공백·밑줄 구분자는 `float()` 과 같은 규칙으로 받는다.
 */
export function pyInt(v: unknown): number | null {
  if (typeof v === "number") return Number.isInteger(v) ? v : Math.trunc(v);
  if (typeof v === "boolean") return v ? 1 : 0;
  if (typeof v !== "string") return null;
  const s = toAsciiDecimal(v).trim();
  const body = s.replace(/^[+-]/, "");
  const sign = s.startsWith("-") ? -1 : 1;
  if (!/^\d(?:_?\d)*$/.test(body)) return null;
  return sign * Number(body.replace(/_/g, ""));
}

export function pyFloat(v: unknown): number | null {
  if (typeof v === "number") return Number.isNaN(v) ? v : v;
  if (typeof v === "boolean") return v ? 1.0 : 0.0;
  if (typeof v !== "string") return null; // dict/list/None → TypeError
  // Python 은 앞뒤 공백만 허용한다(내부 공백은 불가).
  const s = toAsciiDecimal(v).trim();
  if (s === "") return null;
  const body = s.replace(/^[+-]/, "");
  const sign = s.startsWith("-") ? -1 : 1;
  const lower = body.toLowerCase();
  if (lower === "inf" || lower === "infinity") return sign * Infinity;
  if (lower === "nan") return NaN;
  // 밑줄은 숫자 사이에만 올 수 있다.
  if (body.includes("_")) {
    if (!/^(?!_)(?:\d(?:_?\d)*)?(?:\.(?:\d(?:_?\d)*)?)?(?:[eE][+-]?\d(?:_?\d)*)?$/.test(body)) {
      return null;
    }
  }
  const cleaned = body.replace(/_/g, "");
  // 10진 실수만 — 16/2/8진 리터럴은 Python 이 거부한다.
  if (!/^(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/.test(cleaned)) return null;
  return sign * Number(cleaned);
}
