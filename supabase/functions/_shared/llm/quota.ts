/**
 * `services/quota.is_quota_exhausted` 포팅 — quota 소진을 알아본다.
 *
 * 원본은 3 단계로 본다.
 * 1. 예외 **클래스 이름** 화이트리스트 (google SDK 표준 타입)
 * 2. `status_code` / `code` 속성이 429
 * 3. 메시지에 `RESOURCE_EXHAUSTED` / `429` / `QUOTA`
 *
 * Edge 는 SDK 를 안 쓰고 REST 를 직접 부르므로 1 번은 성립하지 않는다. 대신 우리 쪽
 * `complete` 가 **상태 코드를 메시지에 넣어** 던지므로 3 번이 그 자리를 메운다.
 * `429` 만 대문자화 없이 원문에서 찾는 것도 원본 그대로다.
 */
export function isQuotaExhausted(e: unknown): boolean {
  if (e && typeof e === "object") {
    const anyE = e as Record<string, unknown>;
    for (const attr of ["status_code", "code"]) {
      if (anyE[attr] === 429) return true;
    }
  }
  const msg = e instanceof Error ? e.message : String(e ?? "");
  if (!msg) return false;
  const upper = msg.toUpperCase();
  return upper.includes("RESOURCE_EXHAUSTED") || msg.includes("429") ||
    upper.includes("QUOTA");
}
