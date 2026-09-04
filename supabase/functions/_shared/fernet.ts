/**
 * Fernet (Python `cryptography.fernet`) 호환 구현 — Web Crypto 만 사용.
 *
 * ## 왜 필요한가
 * `subscriptions.billing_key` 에 저장된 카카오페이 SID 는 `app/services/billing_crypto.py` 가
 * Fernet 으로 암호화한 값이다. Deno 가 이걸 못 읽으면 **기존 구독자 전원의 정기결제가 끊긴다.**
 * 그래서 새 포맷을 만드는 게 아니라 **기존 암호문을 그대로 복호**할 수 있어야 한다.
 *
 * ## 포맷 (spec: fernet-spec 0x80)
 * ```
 *   token = urlsafe-b64( version(1B=0x80) | timestamp(8B, big-endian, 초) |
 *                        IV(16B) | ciphertext(16의 배수) | HMAC-SHA256(32B) )
 *   key   = urlsafe-b64( signingKey(16B) | encryptionKey(16B) )   // 총 32B
 *   암호  = AES-128-CBC + PKCS7 패딩
 *   HMAC  = HMAC-SHA256(signingKey, version|timestamp|IV|ciphertext)   // 서명 대상에 HMAC 자신은 제외
 * ```
 *
 * ## HMAC 검증은 선택이 아니다
 * 플랜 초안의 스파이크 코드는 HMAC 을 건너뛰었다. 그대로 두면 **위조 토큰을 그대로 복호**한다
 * (공격자가 ciphertext 를 바꿔 넣어도 통과). 여기서는 `crypto.subtle.verify` 로 검증한다 —
 * 직접 바이트 비교하면 타이밍 공격 표면이 생기므로 verify 에 맡긴다.
 */

export class FernetError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "FernetError";
  }
}

const VERSION = 0x80;
const HMAC_LEN = 32;
const IV_LEN = 16;
const HEADER_LEN = 1 + 8 + IV_LEN; // version + timestamp + IV

function b64urlDecode(s: string): Uint8Array {
  const norm = s.replace(/-/g, "+").replace(/_/g, "/");
  const padded = norm + "=".repeat((4 - (norm.length % 4)) % 4);
  const bin = atob(padded);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function b64urlEncode(bytes: Uint8Array): string {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  // Python 의 `base64.urlsafe_b64encode` 는 패딩(`=`)을 유지한다. 떼면 토큰이 달라진다.
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_");
}

/**
 * Deno 의 lib.dom 타입은 `Uint8Array<ArrayBufferLike>` 를 `BufferSource` 로 받지 않는다.
 * 런타임 동작과 무관한 타입 문제라 Web Crypto 호출 지점에서만 좁힌다.
 */
function buf(u: Uint8Array): BufferSource {
  return u as unknown as BufferSource;
}

function concat(...parts: Uint8Array[]): Uint8Array {
  const total = parts.reduce((n, p) => n + p.byteLength, 0);
  const out = new Uint8Array(total);
  let off = 0;
  for (const p of parts) {
    out.set(p, off);
    off += p.byteLength;
  }
  return out;
}

interface Keys {
  signing: CryptoKey;
  encryption: CryptoKey;
}

async function importKeys(key: string, usage: "encrypt" | "decrypt"): Promise<Keys> {
  const raw = b64urlDecode(key);
  if (raw.byteLength !== 32) {
    throw new FernetError(`Fernet 키는 32바이트여야 한다 (받은 값: ${raw.byteLength}B)`);
  }
  const signingRaw = raw.slice(0, 16);
  const encryptionRaw = raw.slice(16, 32);
  const signing = await crypto.subtle.importKey(
    "raw",
    buf(signingRaw),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign", "verify"],
  );
  const encryption = await crypto.subtle.importKey("raw", buf(encryptionRaw), "AES-CBC", false, [usage]);
  return { signing, encryption };
}

/** 기존 Fernet 토큰 → 평문. HMAC 불일치·버전 불일치·TTL 초과는 전부 예외다. */
export async function decryptFernet(
  key: string,
  token: string,
  opts: { ttlSeconds?: number; nowSeconds?: number } = {},
): Promise<{ plaintext: string; timestamp: number }> {
  const blob = b64urlDecode(token);
  if (blob.byteLength < HEADER_LEN + HMAC_LEN) {
    throw new FernetError(`토큰이 너무 짧다 (${blob.byteLength}B)`);
  }
  if (blob[0] !== VERSION) {
    throw new FernetError(`지원하지 않는 버전: 0x${blob[0].toString(16)}`);
  }

  const signed = blob.slice(0, blob.byteLength - HMAC_LEN);
  const mac = blob.slice(blob.byteLength - HMAC_LEN);
  const { signing, encryption } = await importKeys(key, "decrypt");

  const valid = await crypto.subtle.verify("HMAC", signing, buf(mac), buf(signed));
  if (!valid) throw new FernetError("HMAC 불일치 — 위조되었거나 키가 다르다");

  // timestamp 는 8바이트 big-endian. Number 로 읽으면 2^53 을 넘을 수 없어 안전하다(초 단위).
  const view = new DataView(blob.buffer, blob.byteOffset + 1, 8);
  const timestamp = Number(view.getBigUint64(0, false));

  if (opts.ttlSeconds !== undefined) {
    const now = opts.nowSeconds ?? Math.floor(Date.now() / 1000);
    if (timestamp + opts.ttlSeconds < now) throw new FernetError("토큰 만료 (TTL 초과)");
    // 미래 토큰도 거부한다 — Python 구현과 같은 기준(시계 어긋남 허용치 60초).
    if (timestamp > now + 60) throw new FernetError("토큰 timestamp 가 미래다");
  }

  const iv = blob.slice(9, 9 + IV_LEN);
  const ct = blob.slice(HEADER_LEN, blob.byteLength - HMAC_LEN);
  let pt: ArrayBuffer;
  try {
    // Web Crypto 의 AES-CBC 는 PKCS7 언패딩을 자동으로 한다.
    pt = await crypto.subtle.decrypt({ name: "AES-CBC", iv: buf(iv) }, encryption, buf(ct));
  } catch {
    throw new FernetError("복호 실패 — 패딩이 깨졌거나 키가 다르다");
  }
  return { plaintext: new TextDecoder().decode(pt), timestamp };
}

/**
 * 평문 → Fernet 토큰. Python 쪽이 그대로 복호할 수 있어야 한다
 * (이관 도중에는 Python 과 Deno 가 같은 컬럼을 함께 읽고 쓴다).
 *
 * `iv`/`nowSeconds` 는 테스트에서 결정론적 벡터를 만들기 위한 주입점이다. 운영에서는 넘기지 않는다.
 */
export async function encryptFernet(
  key: string,
  plaintext: string,
  opts: { iv?: Uint8Array; nowSeconds?: number } = {},
): Promise<string> {
  const { signing, encryption } = await importKeys(key, "encrypt");
  const iv = opts.iv ?? crypto.getRandomValues(new Uint8Array(IV_LEN));
  const ts = BigInt(opts.nowSeconds ?? Math.floor(Date.now() / 1000));

  const tsBytes = new Uint8Array(8);
  new DataView(tsBytes.buffer).setBigUint64(0, ts, false);

  const ct = new Uint8Array(
    await crypto.subtle.encrypt(
      { name: "AES-CBC", iv: buf(iv) },
      encryption,
      buf(new TextEncoder().encode(plaintext)),
    ),
  );
  const signed = concat(new Uint8Array([VERSION]), tsBytes, iv, ct);
  const mac = new Uint8Array(await crypto.subtle.sign("HMAC", signing, buf(signed)));
  return b64urlEncode(concat(signed, mac));
}
