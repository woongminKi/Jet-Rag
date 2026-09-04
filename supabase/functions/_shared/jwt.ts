/**
 * `api/app/auth/jwt_verify.py` 포팅 — Supabase JWT 로컬 검증.
 *
 * ## 운영 경로는 ES256/JWKS 다
 * 프로젝트 JWKS 실측(2026-09-04): `{kty: "EC", alg: "ES256", crv: "P-256", use: "sig"}`.
 * 신규 Supabase 프로젝트는 ECC(P-256) signing key 가 기본이라 **HS256 전용 구현은 아예 못 쓴다.**
 * HS256 은 레거시 프로젝트 호환 경로로만 남긴다.
 *
 * ## 검증 항목 (원본과 동일)
 * - 서명 (대칭 secret 또는 JWKS 공개키)
 * - `exp` 만료 — leeway 0
 * - `aud === "authenticated"` (Supabase 발급 토큰 고정값)
 * - 필수 claim `exp` / `sub`
 * - 허용 알고리즘은 **설정값 하나로 고정**한다. 목록을 넓히면 토큰이 헤더의 `alg` 를 바꿔
 *   약한 알고리즘으로 검증받는 길이 열린다.
 *
 * ## 실패 메시지까지 원본과 맞춘다
 * 이 문자열이 그대로 401 detail 로 사용자에게 나간다. 바꾸면 프론트 분기나 안내가 달라진다.
 *
 * ## 키 해석을 검증보다 **먼저** 한다
 * 원본은 `_resolve_asymmetric_key` 에서 키를 먼저 가져오므로, 토큰이 깨져 있으면
 * "JWKS 공개키 조회에 실패했습니다." 가 나온다. jose 의 `jwtVerify` 에 JWKS 함수를 그대로
 * 넘기면 토큰 파싱이 먼저 실패해 "토큰 검증에 실패했습니다." 가 나와 **메시지가 갈린다.**
 * 그래서 헤더를 먼저 디코드해 키를 해석하고, 해석된 키를 검증에 넘긴다.
 */

import {
  createRemoteJWKSet,
  decodeProtectedHeader,
  errors as joseErrors,
  type JWTPayload,
  jwtVerify,
  type JWTVerifyGetKey,
} from "jose";

/** Supabase access token 의 고정 audience claim. */
const SUPABASE_AUDIENCE = "authenticated";

/** 로컬 검증을 지원하는 대칭 알고리즘. 원본 화이트리스트와 동일. */
const SYMMETRIC_ALGORITHMS = new Set(["HS256", "HS384", "HS512"]);

/** 비대칭 — JWKS 공개키 경로. ES256 이 Supabase 기본, RS256 은 마이그레이션 호환. */
const ASYMMETRIC_ALGORITHMS = new Set(["ES256", "ES384", "ES512", "RS256", "RS384", "RS512"]);

/** 검증 실패 단일 예외. 호출부가 401 로 변환한다. */
export class JWTValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "JWTValidationError";
  }
}

/** `Settings` 전체가 아니라 이 모듈이 쓰는 값만 받는다 — 테스트와 의존을 좁히기 위해. */
export interface JwtSettings {
  supabaseJwtAlgorithm: string;
  supabaseJwtSecret: string | null;
  supabaseJwksUrl: string | null;
}

export interface VerifiedToken {
  /** `sub` claim = Supabase user UUID (격리 키). */
  userId: string;
  email: string | null;
}

/**
 * JWKS URL 별 원격 키셋 캐시.
 *
 * 원본의 `@lru_cache(maxsize=4)` 대응물이다. `_shared` 에 상태를 두지 않는 게 원칙이지만
 * 여기는 예외로 둔다 — 캐시가 없으면 **요청마다 JWKS 를 fetch** 해 지연과 rate limit 을 만든다.
 * 담기는 건 공개키뿐이라 인스턴스 간 불일치가 생겨도 위험이 없고, `kid` 가 안 맞으면
 * jose 가 알아서 다시 받아온다.
 */
const jwksCache = new Map<string, JWTVerifyGetKey>();
const JWKS_CACHE_MAX = 4;

function getJwks(url: string): JWTVerifyGetKey {
  const cached = jwksCache.get(url);
  if (cached) return cached;
  const created = createRemoteJWKSet(new URL(url));
  // maxsize 4 — URL 이 늘어날 일이 없지만 원본과 같은 상한을 둔다.
  if (jwksCache.size >= JWKS_CACHE_MAX) jwksCache.delete(jwksCache.keys().next().value!);
  jwksCache.set(url, created);
  return created;
}

/** 대칭 secret. 미설정은 운영 설정 오류라 fail-fast. */
function resolveSymmetricKey(settings: JwtSettings): Uint8Array {
  const secret = settings.supabaseJwtSecret;
  if (!secret) {
    throw new JWTValidationError("JWT secret 이 설정되지 않았습니다 (서버 설정 오류).");
  }
  return new TextEncoder().encode(secret);
}

/**
 * JWKS 에서 토큰의 `kid` 에 맞는 공개키. URL 미설정·네트워크 실패·`kid` 미매칭·
 * 토큰 형식 오류를 **전부** 조회 실패로 흡수한다(원본과 동일한 경계).
 */
async function resolveAsymmetricKey(token: string, settings: JwtSettings): Promise<CryptoKey> {
  const url = settings.supabaseJwksUrl;
  if (!url) {
    throw new JWTValidationError("JWKS URL 이 설정되지 않았습니다 (비대칭 JWT 검증 불가).");
  }
  try {
    const { alg, kid } = decodeProtectedHeader(token);
    // `alg` 가 없는 헤더는 키를 고를 수 없다 — 원본도 이 지점에서 조회 실패로 떨어진다.
    if (!alg) throw new Error("protected header 에 alg 가 없습니다");
    const jwks = getJwks(url);
    // 두 번째 인자(서명 본문)는 원격 JWKS 가 쓰지 않는다. kid/alg 만으로 키를 고른다.
    const header = { alg, kid } as Parameters<JWTVerifyGetKey>[0];
    return (await jwks(header, { payload: "", signature: "" })) as CryptoKey;
  } catch (e) {
    if (e instanceof JWTValidationError) throw e;
    throw new JWTValidationError("JWKS 공개키 조회에 실패했습니다.");
  }
}

async function resolveSigningKey(token: string, settings: JwtSettings): Promise<Uint8Array | CryptoKey> {
  const alg = settings.supabaseJwtAlgorithm;
  if (SYMMETRIC_ALGORITHMS.has(alg)) return resolveSymmetricKey(settings);
  if (ASYMMETRIC_ALGORITHMS.has(alg)) return await resolveAsymmetricKey(token, settings);
  throw new JWTValidationError(`JWT 알고리즘 '${alg}' 은 지원되지 않습니다.`);
}

/** jose 의 세분화된 예외를 원본과 같은 메시지로 접는다. */
function toValidationError(e: unknown): JWTValidationError {
  if (e instanceof joseErrors.JWTExpired) {
    return new JWTValidationError("토큰이 만료되었습니다.");
  }
  // `aud` 는 **불일치**와 **누락**을 갈라야 한다. 원본에서 불일치는 InvalidAudienceError 지만
  // 누락은 MissingRequiredClaimError 라 일반 실패 메시지로 떨어진다.
  // jose 는 둘 다 JWTClaimValidationFailed 로 던지고 `reason` 으로 구분한다
  // (실측: 불일치 = "check_failed", 누락 = "missing").
  if (
    e instanceof joseErrors.JWTClaimValidationFailed && e.claim === "aud" &&
    e.reason === "check_failed"
  ) {
    return new JWTValidationError("토큰 audience 가 유효하지 않습니다.");
  }
  // 서명 불일치·형식 오류·필수 claim 누락 등 나머지 전부.
  return new JWTValidationError("토큰 검증에 실패했습니다.");
}

/**
 * JWT 를 검증하고 핵심 claim 을 돌려준다. 실패는 전부 `JWTValidationError`.
 */
export async function verifyJwt(token: string, settings: JwtSettings): Promise<VerifiedToken> {
  if (!token) throw new JWTValidationError("토큰이 비어 있습니다.");

  const key = await resolveSigningKey(token, settings);

  let payload: JWTPayload;
  try {
    const result = await jwtVerify(token, key, {
      algorithms: [settings.supabaseJwtAlgorithm],
      audience: SUPABASE_AUDIENCE,
      requiredClaims: ["exp", "sub"],
    });
    payload = result.payload;
  } catch (e) {
    throw toValidationError(e);
  }

  const userId = payload.sub;
  // jose 의 requiredClaims 는 빈 문자열을 "있음" 으로 본다 — 쓸 수 있는 값인지는 여기서 본다.
  if (typeof userId !== "string" || !userId) {
    throw new JWTValidationError("토큰에 사용자 식별자(sub)가 없습니다.");
  }

  const email = payload.email;
  return { userId, email: typeof email === "string" ? email : null };
}
