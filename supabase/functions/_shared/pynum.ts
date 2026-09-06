/**
 * Python 수치 규약 중 JS 와 갈리는 것 — 여러 모듈이 쓰므로 한곳에 모았다.
 * (`_shared/search/pystr.ts` 의 문자열 판.)
 */

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
