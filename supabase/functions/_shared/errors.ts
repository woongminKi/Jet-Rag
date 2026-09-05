/**
 * FastAPI `HTTPException` → `Response` 매핑.
 *
 * 프론트가 오류 본문의 모양에 의존한다. FastAPI 는 `{"detail": ...}` 로 내보내므로
 * 형태를 그대로 맞춘다 — 키 이름이 바뀌면 프론트의 오류 표시가 조용히 빈칸이 된다.
 *
 * **처리되지 않은 예외만 모양이 다르다.** FastAPI 는 그때 JSON 이 아니라 평문을 쓴다 —
 * 운영 실측(2026-09-05): `content-type: text/plain; charset=utf-8`, 본문 `Internal Server Error`.
 * 처음엔 여기서도 `{"detail": ...}` 를 냈는데, 배포 후 HTTP 로 대조하다 차이를 발견했다.
 * 어느 쪽이든 프론트는 500 으로 처리하겠지만, 이관은 동작을 맞추는 일이라 평문으로 맞췄다.
 * 어느 경우든 원인 문자열은 흘리지 않는다 — 스택이나 내부 경로가 응답에 실리면 안 된다.
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
  return internalServerError();
}

/**
 * 처리되지 않은 예외 — FastAPI 의 기본 500 과 같은 **평문** 응답.
 * 여기만 `{"detail": ...}` 이 아니다(위 docstring 참조).
 */
export function internalServerError(): Response {
  return new Response("Internal Server Error", {
    status: 500,
    headers: { "content-type": "text/plain; charset=utf-8" },
  });
}

/** 등록되지 않은 메서드 — FastAPI 라우터와 같은 응답. */
export function methodNotAllowed(): Response {
  return detailResponse(405, "Method Not Allowed");
}

/** 등록되지 않은 경로 — FastAPI 와 같은 응답. */
export function notFound(): Response {
  return detailResponse(404, "Not Found");
}
