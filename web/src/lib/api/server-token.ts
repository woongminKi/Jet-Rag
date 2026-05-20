import { cookies } from 'next/headers';

/**
 * D1 Phase B — 서버 컨텍스트에서 jetrag-api 로 forward 할 access_token 추출 (plan §1.1).
 *
 * next/headers cookies() 로 Supabase auth 쿠키(`sb-<ref>-auth-token`)를 읽어
 * access_token 을 꺼낸다. server 전용 — client.ts 의 서버 분기에서만 비정적
 * dynamic import 로 로드돼 client 번들에 포함되지 않는다 (Turbopack 추적 회피).
 *
 * 쿠키 형식(@supabase/ssr 0.10.x): `base64-` prefix 면 base64url 디코드 → JSON,
 * 아니면 그대로 JSON. 3180자 초과 시 `.0`/`.1` 청크 분할(순서대로 concat).
 * JSON 의 access_token 이 JWT. graceful — 어떤 실패도 null.
 */

const MAX_CHUNKS = 16;

function deriveProjectRef(url: string): string | null {
  try {
    const host = new URL(url).hostname;
    const ref = host.split('.')[0];
    return ref || null;
  } catch {
    return null;
  }
}

function joinChunked(map: Record<string, string>, base: string): string | null {
  if (map[base] !== undefined) return map[base];
  const parts: string[] = [];
  for (let i = 0; i < MAX_CHUNKS; i++) {
    const chunk = map[`${base}.${i}`];
    if (chunk === undefined) break;
    parts.push(chunk);
  }
  return parts.length ? parts.join('') : null;
}

function decodeValue(raw: string): unknown {
  let payload = raw;
  if (payload.startsWith('base64-')) {
    try {
      const encoded = payload.slice('base64-'.length);
      const padded = encoded + '='.repeat((4 - (encoded.length % 4)) % 4);
      const normalized = padded.replace(/-/g, '+').replace(/_/g, '/');
      payload = Buffer.from(normalized, 'base64').toString('utf-8');
    } catch {
      return null;
    }
  }
  try {
    return JSON.parse(payload);
  } catch {
    return null;
  }
}

export async function getServerForwardToken(): Promise<string | null> {
  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
  if (!supabaseUrl) return null;
  const projectRef = deriveProjectRef(supabaseUrl);
  if (!projectRef) return null;

  try {
    const cookieStore = await cookies();
    const map: Record<string, string> = {};
    for (const { name, value } of cookieStore.getAll()) {
      map[name] = value;
    }
    const raw = joinChunked(map, `sb-${projectRef}-auth-token`);
    if (raw === null) return null;
    const decoded = decodeValue(raw);
    if (decoded && typeof decoded === 'object' && !Array.isArray(decoded)) {
      const token = (decoded as { access_token?: unknown }).access_token;
      return typeof token === 'string' && token ? token : null;
    }
    if (Array.isArray(decoded) && typeof decoded[0] === 'string') {
      return decoded[0] || null;
    }
    return null;
  } catch {
    return null;
  }
}
