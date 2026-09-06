/**
 * Python `datetime.isoformat()` 과 같은 **형식**의 UTC 문자열.
 *
 * 규칙이 하나 있다 — **마이크로초가 0 이면 소수부를 통째로 생략한다.**
 * `toISOString()` 은 언제나 `.000Z` 를 붙이므로 그대로 쓰면 어긋난다.
 * JS 는 밀리초까지만 있어서 뒤 3 자리는 항상 0 이다.
 *
 * ```
 * 마이크로초 0  → 2026-09-10T00:26:40+00:00
 * 마이크로초 有 → 2026-09-10T00:26:40.123000+00:00
 * ```
 *
 * `datetime.now()` 로 만든 값이 정확히 0 마이크로초일 일은 드물지만, 고정 시각을 쓰는
 * 검증에서는 바로 드러난다 — 실제로 `/me` 대조에서 잡혔다.
 */
export function pyIsoUtc(ms: number): string {
  const iso = new Date(ms).toISOString(); // ...THH:MM:SS.mmmZ
  const body = iso.slice(0, -1); // Z 제거
  const [head, frac] = body.split(".");
  if (!frac || frac === "000") return `${head}+00:00`;
  return `${head}.${frac}000+00:00`;
}
