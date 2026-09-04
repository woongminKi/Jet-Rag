/**
 * FastAPI `HTTPException` → `Response` 매핑.
 *
 * 프론트가 오류 본문의 모양에 의존한다. FastAPI 는 `{"detail": ...}` 로 내보내므로
 * 형태를 그대로 맞춘다 — 키 이름이 바뀌면 프론트의 오류 표시가 조용히 빈칸이 된다.
 *
 * 처리되지 않은 예외는 500 `{"detail": "Internal Server Error"}` 다. 원인 문자열을 그대로
 * 흘리지 않는다 — 스택이나 내부 경로가 응답에 실리면 안 된다.
 */

import { AuthError } from "./current_user.ts";

export function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

/** FastAPI 의 `HTTPException` 과 같은 본문. */
export function detailResponse(
  status: number,
  detail: string,
  headers: Record<string, string> = {},
): Response {
  return jsonResponse({ detail }, status, headers);
}

/**
 * 핸들러에서 올라온 예외를 응답으로 바꾼다.
 * `AuthError` 는 상태·detail·헤더를 그대로 쓰고, 나머지는 500 으로 접는다.
 */
export function toResponse(e: unknown): Response {
  if (e instanceof AuthError) {
    return detailResponse(e.status, e.detail, e.headers);
  }
  // 내부 사정을 밖으로 내보내지 않는다. 진단은 로그로 한다.
  console.error("처리되지 않은 예외:", e);
  return detailResponse(500, "Internal Server Error");
}

/** 등록되지 않은 메서드 — FastAPI 라우터와 같은 응답. */
export function methodNotAllowed(): Response {
  return detailResponse(405, "Method Not Allowed");
}

/** 등록되지 않은 경로 — FastAPI 와 같은 응답. */
export function notFound(): Response {
  return detailResponse(404, "Not Found");
}
