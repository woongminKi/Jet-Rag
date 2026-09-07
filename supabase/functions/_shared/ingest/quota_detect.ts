/**
 * `services/quota.is_quota_exhausted` 포팅 — Gemini quota 초과를 알아본다.
 *
 * 이 판정이 켜지면 호출자가 **남은 호출을 통째로 건너뛴다**(PPTX 는 다음 슬라이드,
 * tag_summarize 는 요약). 잘못 켜지면 멀쩡한 문서를 덜 읽고, 안 켜지면 이미 막힌
 * API 를 cap 만큼 계속 두드린다.
 *
 * ## 3 단계 판정 — 순서가 의미다
 * 1. **예외 클래스 이름** — `ResourceExhausted`(google.api_core) · `TooManyRequests`.
 *    SDK 응답 형식과 무관해 가장 정확하다.
 * 2. **`status_code` / `code` 속성이 429**.
 * 3. **메시지 문자열** — `RESOURCE_EXHAUSTED` · `429` · `QUOTA`. 1·2 가 놓쳤을 때의 안전망.
 *
 * ## 대소문자 규칙이 항목마다 다르다
 * `RESOURCE_EXHAUSTED` 와 `QUOTA` 는 **대문자로 바꾼 뒤** 찾고, `429` 는 **원문**에서
 * 찾는다. 숫자는 대소문자가 없으니 결과는 같지만, 원본을 그대로 옮긴다.
 */

const QUOTA_EXCEPTION_NAMES = new Set(["ResourceExhausted", "TooManyRequests"]);

export function isQuotaExhausted(errorOrMsg: unknown): boolean {
  let msg: string;
  if (errorOrMsg instanceof Error) {
    // ① 클래스 이름. JS 는 `name` 이 그 자리다(`e.constructor.name` 은 minify 에 약하다).
    if (QUOTA_EXCEPTION_NAMES.has(errorOrMsg.name)) return true;
    // ② HTTP 상태 속성.
    const any = errorOrMsg as unknown as Record<string, unknown>;
    for (const attr of ["status_code", "statusCode", "code", "status"]) {
      if (any[attr] === 429) return true;
    }
    msg = errorOrMsg.message;
  } else if (typeof errorOrMsg === "string") {
    msg = errorOrMsg;
  } else if (errorOrMsg === null || errorOrMsg === undefined) {
    return false;
  } else {
    msg = String(errorOrMsg);
  }

  if (!msg) return false;
  const upper = msg.toUpperCase();
  return upper.includes("RESOURCE_EXHAUSTED") || msg.includes("429") ||
    upper.includes("QUOTA");
}
