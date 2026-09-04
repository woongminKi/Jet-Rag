/**
 * `api/app/main.py` 의 `CORSMiddleware` 설정 포팅.
 *
 * ## 이 설정이 왜 이렇게 생겼나
 * 아키텍처 B(httpOnly 쿠키)라 `allow_credentials=True` 다. 그러면 CORS 스펙상 와일드카드
 * `*` origin 을 쓸 수 없어 **명시 origin 만** 허용하고, Vercel preview 도메인은
 * origin 마다 URL 이 새로 발급되므로 정규식으로 뚫어 둔다.
 *
 * ## 구현은 Starlette `CORSMiddleware` 를 그대로 따른다
 * 프레임워크가 하던 일이라 "대충 비슷하게"로 넘어가기 쉬운데, 헤더 하나가 달라지면
 * 브라우저가 응답을 통째로 버려 **네트워크 탭에만 보이고 코드에는 안 잡히는** 실패가 된다.
 * 아래 규칙은 전부 원본 소스에서 확인한 것이다:
 *
 * - `Origin` 헤더가 없으면 **아무 CORS 헤더도 붙이지 않는다** (미들웨어 자체를 통과).
 * - preflight 판정 = `OPTIONS` **이면서** `Access-Control-Request-Method` 가 있을 때.
 *   `OPTIONS` 만 오면 일반 응답 취급이다.
 * - origin 매칭은 `fullmatch` 다. `re.search` 로 착각하면 `https://evil.com/x.vercel.app`
 *   같은 값이 통과한다.
 * - `allow_methods` 에 `OPTIONS` 가 **없다**(GET, POST 뿐). preflight 가 요청한 메서드가
 *   목록 밖이면 400 이다.
 * - `allow_headers=["*"]` 이라 preflight 는 요청받은 헤더 목록을 **그대로 되비춘다**.
 * - 실패해도 400 응답에 CORS 헤더를 붙여 보낸다 — 브라우저가 이유를 보여주게 하려는 것.
 * - 일반 응답에서 `Vary` 는 **덮어쓰지 않고 덧붙인다**(`add_vary_header`).
 *
 * ## 라우팅 계층에 남는 요구사항 1건 (Task 1.6)
 * preflight 가 아닌 `OPTIONS` 요청은 CORS 를 통과해 **앱**으로 간다. FastAPI 는 GET/POST 만
 * 등록돼 있어 그때 **405** 를 낸다. Edge 함수도 같은 405 를 내야 응답이 갈리지 않는다
 * (패리티 검사기가 이 동작을 전제로 대조한다).
 */

export interface CorsSettings {
  corsOrigins: string[];
}

/** `app/main.py` 의 `allow_methods`. `OPTIONS` 가 없는 게 의도다. */
const ALLOW_METHODS = ["GET", "POST"];

/** Starlette 기본값. */
const MAX_AGE = "600";

/** `allow_origin_regex` — `re.fullmatch` 라 `^...$` 로 고정한다. */
const VERCEL_PREVIEW_ORIGIN = /^https:\/\/.*\.vercel\.app$/;

export function isAllowedOrigin(origin: string, settings: CorsSettings): boolean {
  if (VERCEL_PREVIEW_ORIGIN.test(origin)) return true;
  return settings.corsOrigins.includes(origin);
}

/** `OPTIONS` + `Access-Control-Request-Method` 가 있어야 preflight 다. */
export function isPreflight(req: Request): boolean {
  return req.method === "OPTIONS" && req.headers.has("Access-Control-Request-Method");
}

/**
 * preflight 응답. preflight 가 아니면 null.
 *
 * 실패해도 200 대신 400 을 주되 **CORS 헤더는 붙여서** 보낸다. 브라우저가 정책을 강제하므로
 * 상태코드 자체는 중요하지 않지만, 개발자 도구에 이유가 보이는 편이 낫다는 원본 주석의 판단이다.
 */
export function preflightResponse(req: Request, settings: CorsSettings): Response | null {
  const origin = req.headers.get("Origin");
  if (origin === null || !isPreflight(req)) return null;

  const requestedMethod = req.headers.get("Access-Control-Request-Method");
  const requestedHeaders = req.headers.get("Access-Control-Request-Headers");

  const headers = new Headers({
    "Vary": "Origin",
    "Access-Control-Allow-Methods": ALLOW_METHODS.join(", "),
    "Access-Control-Max-Age": MAX_AGE,
    "Access-Control-Allow-Credentials": "true",
  });
  const failures: string[] = [];

  if (isAllowedOrigin(origin, settings)) {
    headers.set("Access-Control-Allow-Origin", origin);
  } else {
    failures.push("origin");
  }

  if (requestedMethod === null || !ALLOW_METHODS.includes(requestedMethod)) {
    failures.push("method");
  }

  // `allow_headers=["*"]` — 요청받은 목록을 그대로 되비춘다.
  if (requestedHeaders !== null) {
    headers.set("Access-Control-Allow-Headers", requestedHeaders);
  }

  const body = failures.length ? `Disallowed CORS ${failures.join(", ")}` : "OK";
  headers.set("content-type", "text/plain; charset=utf-8");
  return new Response(body, { status: failures.length ? 400 : 200, headers });
}

/**
 * 일반 응답에 CORS 헤더를 붙인다. `Origin` 이 없으면 원본 그대로 돌려준다.
 *
 * origin 이 허용되지 않아도 `Access-Control-Allow-Credentials` 는 붙는다(원본과 동일).
 * `Access-Control-Allow-Origin` 이 없으므로 브라우저가 어차피 막는다.
 */
export function applyCorsHeaders(req: Request, response: Response, settings: CorsSettings): Response {
  const origin = req.headers.get("Origin");
  if (origin === null) return response;

  const headers = new Headers(response.headers);
  headers.set("Access-Control-Allow-Credentials", "true");

  if (isAllowedOrigin(origin, settings)) {
    headers.set("Access-Control-Allow-Origin", origin);
    // `add_vary_header` 는 덮어쓰지 않고 덧붙인다 — 캐시가 다른 origin 응답을 섞지 않도록.
    const vary = headers.get("Vary");
    headers.set("Vary", vary ? `${vary}, Origin` : "Origin");
  }

  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}
