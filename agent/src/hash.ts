/** sha256 hex — 서버 `persist.ts` 의 `sha256Hex` 와 같은 계산. */
export async function sha256Hex(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", bytes as unknown as BufferSource);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/**
 * 파일 전체를 읽어 해시한다.
 *
 * 스트리밍이 아니다 — gate 가 이미 50MB 를 넘긴 파일을 걸러내므로 상한이 50MB 이고,
 * 업로드에도 같은 바이트가 필요하다. 나눠 읽으면 두 번 읽게 된다.
 */
export async function sha256File(path: string): Promise<string> {
  return await sha256Hex(await Deno.readFile(path));
}
