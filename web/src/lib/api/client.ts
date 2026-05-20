const BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000';

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
  ) {
    super(`[${status}] ${detail}`);
    this.name = 'ApiError';
  }
}

/**
 * D1 Phase B (plan §1.1) — 컨텍스트별 인증 첨부.
 * - 브라우저: credentials:'include' → 브라우저가 httpOnly 쿠키 자동 첨부 (진짜 httpOnly).
 * - 서버(RSC/action): 모듈 상단의 setServerTokenResolver 로 등록된 resolver 가 있으면
 *   쿠키에서 access_token 을 읽어 Authorization: Bearer 로 forward.
 *
 * resolver 주입 패턴 — client.ts 는 client component 에도 import 되므로 next/headers 를
 * 직접 import 할 수 없다(client 번들 누수). 서버 전용 모듈(`server.ts`)이 자신을 import
 * 할 때 resolver 를 등록한다. graceful — resolver 미등록/실패 시 무인증 fallback
 * (백엔드 auth_enabled=false 와 호환, plan §2).
 */
type ServerTokenResolver = () => Promise<string | null>;

let _serverTokenResolver: ServerTokenResolver | null = null;

export function setServerTokenResolver(resolver: ServerTokenResolver): void {
  _serverTokenResolver = resolver;
}

async function buildAuthInit(init?: RequestInit): Promise<RequestInit> {
  if (typeof window === 'undefined') {
    // 서버 컨텍스트 — 등록된 resolver 로 토큰 추출 후 Bearer forward.
    if (_serverTokenResolver) {
      try {
        const token = await _serverTokenResolver();
        if (token) {
          return {
            ...init,
            headers: {
              ...(init?.headers ?? {}),
              Authorization: `Bearer ${token}`,
            },
          };
        }
      } catch {
        // resolver 실패 — 무인증 fallback.
      }
    }
    return init ?? {};
  }
  // 브라우저 컨텍스트 — httpOnly 쿠키 자동 첨부.
  return { ...init, credentials: 'include' };
}

export async function apiGet<T>(path: string, init?: RequestInit): Promise<T> {
  const authInit = await buildAuthInit(init);
  const res = await fetch(`${BASE_URL}${path}`, {
    ...authInit,
    headers: { Accept: 'application/json', ...(authInit.headers ?? {}) },
    cache: 'no-store',
  });
  if (!res.ok) {
    throw new ApiError(res.status, await safeReadDetail(res));
  }
  return res.json() as Promise<T>;
}

export async function apiPostFormData<T>(
  path: string,
  formData: FormData,
): Promise<T> {
  const authInit = await buildAuthInit();
  const res = await fetch(`${BASE_URL}${path}`, {
    ...authInit,
    method: 'POST',
    body: formData,
    headers: { ...(authInit.headers ?? {}) },
    cache: 'no-store',
  });
  if (!res.ok) {
    throw new ApiError(res.status, await safeReadDetail(res));
  }
  return res.json() as Promise<T>;
}

/**
 * Body 없는 단순 POST. reingest 처럼 path 만으로 동작이 결정되는 엔드포인트용.
 * Content-Length: 0 을 명시해 일부 프록시가 빈 POST 를 거절하지 않도록 한다.
 */
export async function apiPost<T>(path: string): Promise<T> {
  const authInit = await buildAuthInit();
  const res = await fetch(`${BASE_URL}${path}`, {
    ...authInit,
    method: 'POST',
    headers: {
      Accept: 'application/json',
      'Content-Length': '0',
      ...(authInit.headers ?? {}),
    },
    cache: 'no-store',
  });
  if (!res.ok) {
    throw new ApiError(res.status, await safeReadDetail(res));
  }
  return res.json() as Promise<T>;
}

async function safeReadDetail(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body?.detail === 'string') return body.detail;
    return JSON.stringify(body);
  } catch {
    return res.statusText;
  }
}
