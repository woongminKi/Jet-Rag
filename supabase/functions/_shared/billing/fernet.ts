/**
 * Fernet (spec v0x80) — `services/billing_crypto.py` 의 `cryptography.fernet.Fernet` 자리.
 *
 * `subscriptions.billing_key` 에는 **Fernet 암호문만** 들어간다(평문 SID 금지).
 * Python 이 만든 암호문을 Edge 가 읽고, Edge 가 만든 걸 Python 이 읽을 수 있어야 한다 —
 * 이관 중에는 양쪽이 같은 행을 본다.
 *
 * ## 토큰 구조 (Fernet 명세)
 * ```
 * base64url( 0x80 | timestamp(8B, big-endian) | IV(16B) | ct | HMAC-SHA256(32B) )
 * ```
 * - 키는 base64url 32 바이트 → 앞 16B = 서명키, 뒤 16B = 암호화키
 * - ct = AES-128-CBC, PKCS7 패딩
 * - HMAC 은 `0x80 | timestamp | IV | ct` 전체에 대해 **서명키**로 건다
 *
 * ## 바이트 일치는 애초에 불가능하다
 * IV 가 난수고 timestamp 가 현재 시각이라 같은 입력도 매번 다른 토큰이 된다.
 * 그래서 검증은 **교차 복호화**로 한다 — Python 이 만든 걸 여기서 풀고, 여기서 만든 걸
 * Python 이 푼다. `verify_fernet_parity.py` 가 양방향을 다 돌린다.
 *
 * ## `ttl` 을 쓰지 않는다
 * 원본이 `decrypt(token)` 만 부른다. `ttl=None` 이면 CPython 쪽도 시각 검사를 아예
 * 건너뛴다 — 여기서 만료를 넣으면 **원본이 푸는 토큰을 우리가 거부하게 된다.**
 */

const VERSION = 0x80;

export class FernetError extends Error {}

function b64urlDecode(s: string): Uint8Array {
  const norm = s.replace(/-/g, "+").replace(/_/g, "/");
  const pad = norm.length % 4 === 0 ? norm : norm + "=".repeat(4 - (norm.length % 4));
  const bin = atob(pad);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function b64urlEncode(b: Uint8Array): string {
  let s = "";
  for (const x of b) s += String.fromCharCode(x);
  // Python `urlsafe_b64encode` 는 `=` 패딩을 **남긴다**. 떼면 원본과 다른 문자열이 된다.
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_");
}

interface Keys {
  signing: CryptoKey;
  encryption: CryptoKey;
}

/** `Fernet(key)` — base64url 32 바이트가 아니면 던진다(원본도 `ValueError`). */
export async function importFernetKey(key: string): Promise<Keys> {
  let raw: Uint8Array;
  try {
    raw = b64urlDecode(key.trim());
  } catch {
    throw new FernetError("Fernet 키가 base64url 이 아니다");
  }
  if (raw.length !== 32) {
    throw new FernetError(`Fernet 키는 32 바이트여야 한다 (받은 값 ${raw.length})`);
  }
  const signing = await crypto.subtle.importKey(
    "raw",
    raw.slice(0, 16),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign", "verify"],
  );
  const encryption = await crypto.subtle.importKey(
    "raw",
    raw.slice(16, 32),
    { name: "AES-CBC" },
    false,
    ["encrypt", "decrypt"],
  );
  return { signing, encryption };
}

/** 시각·IV 를 주입할 수 있다 — 테스트가 결정적으로 돌게 하려는 것뿐이다. */
export interface EncryptOpts {
  nowSeconds?: number;
  iv?: Uint8Array;
}

export async function fernetEncrypt(
  key: string,
  plaintext: string,
  opts: EncryptOpts = {},
): Promise<string> {
  const { signing, encryption } = await importFernetKey(key);
  const iv = opts.iv ?? crypto.getRandomValues(new Uint8Array(16));
  if (iv.length !== 16) throw new FernetError("IV 는 16 바이트여야 한다");
  const ts = Math.floor(opts.nowSeconds ?? Date.now() / 1000);

  const ct = new Uint8Array(
    await crypto.subtle.encrypt(
      { name: "AES-CBC", iv: iv as unknown as BufferSource },
      encryption,
      new TextEncoder().encode(plaintext),
    ),
  );

  const body = new Uint8Array(1 + 8 + 16 + ct.length);
  body[0] = VERSION;
  // 8 바이트 big-endian. 2038 년을 넘기므로 `BigInt` 로 쓴다.
  new DataView(body.buffer).setBigUint64(1, BigInt(ts), false);
  body.set(iv, 9);
  body.set(ct, 25);

  const mac = new Uint8Array(
    await crypto.subtle.sign("HMAC", signing, body as unknown as BufferSource),
  );
  const token = new Uint8Array(body.length + 32);
  token.set(body, 0);
  token.set(mac, body.length);
  return b64urlEncode(token);
}

export async function fernetDecrypt(key: string, token: string): Promise<string> {
  const { signing, encryption } = await importFernetKey(key);
  let raw: Uint8Array;
  try {
    raw = b64urlDecode(token.trim());
  } catch {
    throw new FernetError("토큰이 base64url 이 아니다");
  }
  // 최소 길이: 버전 1 + 시각 8 + IV 16 + 블록 16 + MAC 32
  if (raw.length < 73) throw new FernetError("토큰이 너무 짧다");
  if (raw[0] !== VERSION) throw new FernetError(`알 수 없는 Fernet 버전: ${raw[0]}`);

  const body = raw.subarray(0, raw.length - 32);
  const mac = raw.subarray(raw.length - 32);
  const ok = await crypto.subtle.verify(
    "HMAC",
    signing,
    mac as unknown as BufferSource,
    body as unknown as BufferSource,
  );
  // `verify` 자체가 상수 시간 비교다 — 직접 바이트를 비교하지 않는다.
  if (!ok) throw new FernetError("HMAC 불일치 — 위조되었거나 키가 다르다");

  const iv = body.subarray(9, 25);
  const ct = body.subarray(25);
  if (ct.length === 0 || ct.length % 16 !== 0) {
    throw new FernetError("암호문 길이가 블록 크기의 배수가 아니다");
  }
  let plain: ArrayBuffer;
  try {
    plain = await crypto.subtle.decrypt(
      { name: "AES-CBC", iv: iv as unknown as BufferSource },
      encryption,
      ct as unknown as BufferSource,
    );
  } catch {
    // WebCrypto 가 PKCS7 패딩 검사까지 한다. 원본은 여기서 `InvalidToken` 이다.
    throw new FernetError("복호화 실패 — 패딩이 깨졌거나 키가 다르다");
  }
  return new TextDecoder().decode(plain);
}
