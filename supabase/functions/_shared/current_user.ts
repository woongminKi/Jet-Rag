/**
 * `api/app/auth/dependencies.py` 포팅 — 호출자 식별 3-way 분기 + 권한 게이트.
 *
 * ## 3-way 분기 (수익화 W1 의 데모 병행 모드)
 * | 조건 | user_id | isAuthenticated | 의미 |
 * |---|---|---|---|
 * | `authEnabled=false` | `defaultUserId` | **true** | 로컬 dev / 단일 사용자 — 쓰기까지 허용 |
 * | 토큰 없음 | `ownerUserId ?? defaultUserId` | **false** | 익명 데모 — owner 문서 read-only |
 * | 유효 JWT | 토큰의 `sub` | true | 본인 격리 컨텍스트 |
 * | 무효 JWT | — | — | **401** (조용한 데모 강등 금지) |
 *
 * 마지막 줄이 중요하다. 무효 토큰을 익명으로 강등하면 "로그인했는데 남의 데이터가 보이는"
 * 상태가 조용히 만들어진다. 원본이 명시적 실패를 택한 이유다.
 *
 * ## 익명 fallback 은 owner 컨텍스트다
 * 그래서 **모든 쓰기 엔드포인트에 `requireAuthenticatedUser` 를 걸어야 한다.** 안 걸면
 * 익명 방문자가 owner 데이터에 쓴다. 원본 주석의 경고를 그대로 옮긴다.
 *
 * ## JWT 실패 메시지는 밖으로 나가지 않는다
 * `jwt.ts` 의 세분화된 메시지("토큰이 만료되었습니다." 등)는 여기서 삼켜지고 401 detail 은
 * 항상 "인증이 필요합니다." 다. 원본과 같다 — 어떤 토큰이 왜 틀렸는지 공격자에게 알려주지 않는다.
 *
 * ## 쿠키 파싱은 이 계층의 새 책임이다
 * Python 은 Starlette 이 `Cookie` 헤더를 파싱해 `request.cookies` 로 준다. Edge 에는 그게
 * 없어 직접 파싱하는데, 그 규칙이 어긋나면 세션이 조용히 안 잡힌다.
 * `cookieParser` 는 Starlette 의 `cookie_parser` + `http.cookies._unquote` 를 그대로 옮겼다.
 */

import { deriveProjectRef, extractAccessToken } from "./cookie_token.ts";
import { type JwtSettings, JWTValidationError, verifyJwt } from "./jwt.ts";

const BEARER_PREFIX = "Bearer ";

export interface AuthSettings extends JwtSettings {
  supabaseUrl: string;
  authEnabled: boolean;
  defaultUserId: string;
  ownerUserId: string | null;
}

/** 요청 호출자. `userId` 가 격리 키다 (RPC user_id_arg / documents.user_id 필터). */
export interface CurrentUser {
  userId: string;
  email: string | null;
  /** false = 익명 데모 방문자 (owner read-only). 쓰기 게이트가 막아야 한다. */
  isAuthenticated: boolean;
}

/** HTTP 상태와 detail 을 담은 인증/권한 실패. `errors.ts` 가 Response 로 바꾼다. */
export class AuthError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly headers: Record<string, string>;

  constructor(status: number, detail: string, headers: Record<string, string> = {}) {
    super(detail);
    this.name = "AuthError";
    this.status = status;
    this.detail = detail;
    this.headers = headers;
  }
}

/* ------------------------------------------------------------------ 쿠키 파싱 */

const OCTAL_PATT = /\\[0-3][0-7][0-7]/;
const QUOTE_PATT = /\\./;

/**
 * Python `http.cookies._unquote` 포팅.
 *
 * 따옴표로 감싼 값만 처리한다. 안에서 `\\OOO`(8진) 과 `\\x` 이스케이프를 푼다.
 * 브라우저가 이렇게 보내는 일은 드물지만, 규칙이 다르면 그 사용자만 세션이 안 잡힌다.
 */
function unquote(value: string): string {
  if (value.length < 2) return value;
  if (!value.startsWith('"') || !value.endsWith('"')) return value;

  const s = value.slice(1, -1);
  let i = 0;
  const n = s.length;
  const res: string[] = [];
  while (i >= 0 && i < n) {
    const rest = s.slice(i);
    const oMatch = OCTAL_PATT.exec(rest);
    const qMatch = QUOTE_PATT.exec(rest);
    if (!oMatch && !qMatch) {
      res.push(s.slice(i));
      break;
    }
    const oStart = oMatch ? i + oMatch.index : -1;
    const qStart = qMatch ? i + qMatch.index : -1;
    // 8진 이스케이프가 없거나 따옴표 이스케이프가 더 앞이면 후자를 먼저 처리한다.
    if (!oMatch || (qMatch && qStart < oStart)) {
      res.push(s.slice(i, qStart), s[qStart + 1]);
      i = qStart + 2;
    } else {
      res.push(s.slice(i, oStart), String.fromCharCode(parseInt(s.slice(oStart + 1, oStart + 4), 8)));
      i = oStart + 4;
    }
  }
  return res.join("");
}

/**
 * `Cookie` 헤더 → 이름/값 맵. Starlette `cookie_parser` 와 동일한 규칙:
 * `;` 로 나누고, `=` 가 없으면 이름을 빈 문자열로 두고, 양쪽을 trim 하고,
 * **같은 이름이 여러 번 오면 마지막이 이긴다.**
 */
export function cookieParser(cookieHeader: string | null | undefined): Record<string, string> {
  const out: Record<string, string> = {};
  if (!cookieHeader) return out;
  for (const chunk of cookieHeader.split(";")) {
    const eq = chunk.indexOf("=");
    let key: string;
    let val: string;
    if (eq >= 0) {
      key = chunk.slice(0, eq);
      val = chunk.slice(eq + 1);
    } else {
      // 이름 없는 쿠키도 브라우저가 보낸다 — Starlette 과 같게 빈 이름으로 담는다.
      key = "";
      val = chunk;
    }
    key = key.trim();
    val = val.trim();
    if (key || val) out[key] = unquote(val);
  }
  return out;
}

/* ------------------------------------------------------------------ 토큰 추출 */

/** `Authorization: Bearer <token>`. 형식이 아니거나 토큰이 비면 null. */
export function extractBearerToken(req: Request): string | null {
  const header = req.headers.get("Authorization");
  // 원본은 `startswith("Bearer ")` 라 대소문자를 구분한다. 같게 둔다.
  if (!header || !header.startsWith(BEARER_PREFIX)) return null;
  const token = header.slice(BEARER_PREFIX.length).trim();
  return token || null;
}

/** Supabase auth 쿠키에서 access_token. 어떤 실패도 null. */
export function extractCookieToken(req: Request, settings: AuthSettings): string | null {
  const cookies = cookieParser(req.headers.get("Cookie"));
  if (!Object.keys(cookies).length) return null;
  const projectRef = deriveProjectRef(settings.supabaseUrl);
  if (!projectRef) return null;
  return extractAccessToken(cookies, projectRef);
}

/* ------------------------------------------------------------------ 3-way 분기 */

export async function getCurrentUser(req: Request, settings: AuthSettings): Promise<CurrentUser> {
  if (!settings.authEnabled) {
    // 로컬 dev 무중단 — 기존 단일 사용자 동작 보존. 쓰기까지 허용된다.
    return { userId: settings.defaultUserId, email: null, isAuthenticated: true };
  }

  const token = extractBearerToken(req) ?? extractCookieToken(req, settings);
  if (token === null) {
    return {
      userId: settings.ownerUserId || settings.defaultUserId,
      email: null,
      isAuthenticated: false,
    };
  }

  try {
    const verified = await verifyJwt(token, settings);
    return { userId: verified.userId, email: verified.email, isAuthenticated: true };
  } catch (e) {
    if (e instanceof JWTValidationError) {
      // 세부 사유는 밖으로 내보내지 않는다 — 원본과 같다.
      throw new AuthError(401, "인증이 필요합니다.", { "WWW-Authenticate": "Bearer" });
    }
    throw e;
  }
}

/* ------------------------------------------------------------------ 게이트 */

/**
 * 쓰기 엔드포인트 게이트 — 익명 데모 방문자를 401 로 막는다.
 * 익명 fallback 이 owner 컨텍스트라 이 게이트가 없으면 owner 데이터에 쓰기가 가능해진다.
 */
export function requireAuthenticatedUser(user: CurrentUser): CurrentUser {
  if (!user.isAuthenticated) {
    throw new AuthError(401, "로그인이 필요합니다.", { "WWW-Authenticate": "Bearer" });
  }
  return user;
}

/**
 * admin 게이트.
 * - `authEnabled=false`: 통과 (기존 단일 사용자 동작 보존)
 * - `ownerUserId` 미설정: **전면 403** — 운영자를 지정하지 않았으면 아무도 통과시키지 않는다
 * - 호출자 ≠ owner: 403
 */
export function requireAdmin(user: CurrentUser, settings: AuthSettings): CurrentUser {
  if (!settings.authEnabled) return user;
  if (!user.isAuthenticated || !settings.ownerUserId || user.userId !== settings.ownerUserId) {
    throw new AuthError(403, "운영자 권한이 필요합니다.");
  }
  return user;
}
