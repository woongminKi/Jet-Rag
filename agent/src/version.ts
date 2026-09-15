/** 바이너리·UA·릴리스 태그가 공유하는 단일 버전. `deno.json` 의 version 과 같이 올린다. */
export const VERSION = "0.1.0";

/** `JetRag-Agent/0.1.0 (darwin)` — Cloudflare 가 `Python-urllib` 을 403 으로 막는다(스펙 §2). */
export function userAgent(os: string = Deno.build.os): string {
  return `JetRag-Agent/${VERSION} (${os})`;
}
