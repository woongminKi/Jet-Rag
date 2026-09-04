/**
 * `api/app/auth/cookie_token.py` 포팅 — Supabase auth 쿠키에서 access_token 추출.
 *
 * ## 왜 쿠키를 직접 파싱하나
 * 프론트(`@supabase/ssr`)가 세션을 httpOnly 쿠키 `sb-<project-ref>-auth-token` 에 저장하고,
 * 브라우저 직접 호출은 `credentials: 'include'` 로 **쿠키만** 보낸다. Authorization 헤더가
 * 없는 그 경로를 살리려면 백엔드가 쿠키에서 JWT 를 꺼내야 한다.
 *
 * ## 쿠키 형식 (`@supabase/ssr` 0.10.x)
 * - 이름: `sb-<project-ref>-auth-token`. project-ref 는 `SUPABASE_URL` hostname 의 첫 segment.
 * - 값이 3,180자를 넘으면 `.0` / `.1` … 로 쪼개진다. 순서대로 이어 붙여 복원한다.
 * - 복원값이 `base64-` 로 시작하면 그 뒤를 base64url 디코드 → JSON. 아니면 그대로 JSON.
 * - JSON 의 `access_token` 이 검증 대상. 일부 버전은 토큰 **배열**을 저장한다.
 *
 * ## 실패는 전부 null
 * 어떤 입력 오류도 예외를 던지지 않는다(원본과 동일). 호출부가 401 또는 익명 fallback 으로
 * 바꾼다. 여기서 던지면 쿠키가 깨진 사용자에게 500 이 나간다.
 *
 * ## 원본과 의도적으로 다른 지점 1건 — 공백이 섞인 base64
 * Python 은 **공백을 포함한 길이로 padding 을 계산**한 뒤 `binascii` 가 그 공백을 버린다.
 * 그래서 성공 여부가 우연히 결정된다 — 실측(2026-09-04): 같은 값에 공백 2개를 넣으면
 * 디코드에 성공하고, 개행 1개를 넣으면 실패한다. 설계된 계약이 아니라 부작용이다.
 *
 * 여기서는 **공백류를 먼저 제거한 뒤 padding 을 계산**해 결정론적으로 만든다. 결과적으로
 * 손상된 값에 대해 Python 보다 관대해질 수 있는데, 그 방향이 안전하다 — 병행 운용 중
 * "Railway 는 로그인 상태인데 Edge 에서는 익명" 이 되는 쪽이 훨씬 나쁘고, 어차피 꺼낸 JWT 는
 * 직후에 서명 검증을 거치므로 통과시킨다고 권한이 생기지 않는다.
 * `verify_cookie_token_parity.py` 가 이 차이를 "허용된 차이"로 고정해 감시한다.
 */

const COOKIE_NAME_PREFIX = "sb-";
const COOKIE_NAME_SUFFIX = "-auth-token";
const BASE64_VALUE_PREFIX = "base64-";

/** 분할 쿠키 인덱스 상한 (방어). ssr 은 3,180자/청크라 정상 세션은 수 청크 이내다. */
const MAX_COOKIE_CHUNKS = 16;

/**
 * `SUPABASE_URL` → project-ref. 유도 불가면 null.
 *
 * `https://abcd1234.supabase.co` → `abcd1234`.
 * 스킴이 없으면 Python `urlsplit` 이 netloc 을 못 잡아 None 이므로 여기서도 null 이다.
 *
 * 단 `//host` 형태(scheme-relative)는 Python 이 netloc 으로 인식한다. `URL` 생성자는 base 가
 * 없으면 던지므로 **그 경우에만** 더미 스킴을 붙인다. 무조건 base 를 주면 `"not a url"` 같은
 * 상대 경로까지 해석돼 엉뚱한 호스트가 나온다.
 */
export function deriveProjectRef(supabaseUrl: string | null | undefined): string | null {
  if (!supabaseUrl) return null;
  let host: string;
  try {
    host = supabaseUrl.startsWith("//")
      ? new URL(`http:${supabaseUrl}`).hostname
      : new URL(supabaseUrl).hostname;
  } catch {
    return null;
  }
  if (!host) return null;
  const ref = host.split(".", 1)[0];
  return ref || null;
}

/**
 * 단일 쿠키가 있으면 그대로, 없으면 `.0` 부터 **연속된** 인덱스만 이어 붙인다.
 * 구멍을 만나면 거기서 멈춘다 — 건너뛰고 이으면 순서가 어긋난 값이 만들어진다.
 */
function joinChunkedCookie(cookies: Record<string, string>, baseName: string): string | null {
  const single = cookies[baseName];
  // 빈 문자열도 "있는 값"이다 — 원본의 `is not None` 과 같게 취급해야 한다.
  if (single !== undefined) return single;

  const parts: string[] = [];
  for (let i = 0; i < MAX_COOKIE_CHUNKS; i++) {
    const chunk = cookies[`${baseName}.${i}`];
    if (chunk === undefined) break;
    parts.push(chunk);
  }
  return parts.length ? parts.join("") : null;
}

/** base64url(패딩 유무 무관) → UTF-8 문자열. 실패는 null. */
function decodeBase64Url(encoded: string): string | null {
  try {
    // 공백류를 **먼저** 제거한 뒤 padding 을 계산한다 — §원본과 의도적으로 다른 지점 참조.
    const b64 = encoded.replace(/\s+/g, "").replace(/-/g, "+").replace(/_/g, "/");
    const padded = b64 + "=".repeat((4 - (b64.length % 4)) % 4);
    const bin = atob(padded);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    // fatal: true — 원본의 UnicodeDecodeError 와 같게 깨진 UTF-8 을 거부한다.
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    return null;
  }
}

/** 쿠키 원본값 → JSON 값. `base64-` prefix 면 선해독. 실패는 null. */
function decodeCookieValue(raw: string): unknown {
  let payload = raw;
  if (payload.startsWith(BASE64_VALUE_PREFIX)) {
    const decoded = decodeBase64Url(payload.slice(BASE64_VALUE_PREFIX.length));
    if (decoded === null) return null;
    payload = decoded;
  }
  try {
    return JSON.parse(payload);
  } catch {
    return null;
  }
}

/**
 * Supabase auth 쿠키에서 access_token(JWT) 추출. 실패는 전부 null.
 *
 * @param cookies    요청 쿠키 (이름 → 값)
 * @param projectRef `deriveProjectRef` 결과. 비었으면 추출 불가
 */
export function extractAccessToken(
  cookies: Record<string, string>,
  projectRef: string | null | undefined,
): string | null {
  if (!projectRef) return null;

  const baseName = `${COOKIE_NAME_PREFIX}${projectRef}${COOKIE_NAME_SUFFIX}`;
  const raw = joinChunkedCookie(cookies, baseName);
  if (raw === null) return null;

  const decoded = decodeCookieValue(raw);

  // **배열을 먼저 본다** — JS 에서 배열의 typeof 도 "object" 라 순서를 바꾸면
  // 배열 형식 세션이 dict 분기로 들어가 조용히 null 이 된다.
  if (Array.isArray(decoded)) {
    const first = decoded[0];
    return typeof first === "string" && first ? first : null;
  }
  if (decoded !== null && typeof decoded === "object") {
    const token = (decoded as Record<string, unknown>).access_token;
    return typeof token === "string" && token ? token : null;
  }
  return null;
}
