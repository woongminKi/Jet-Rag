/**
 * 실시간 감시 — `Deno.watchFs(dirs, { recursive: true })`.
 *
 * ## 디바운스가 먼저, 안정성 판정이 그다음
 * 브라우저 다운로드 하나가 `create` 1 + `modify` 수십 개를 낸다. 디바운스로 "조용해졌다"를
 * 본 뒤, `gate.isStable` 로 "크기가 안 변한다"를 확인한다. 둘 중 하나만으로는 부족하다 —
 * 느린 네트워크에서는 modify 사이 간격이 디바운스보다 길어질 수 있다.
 *
 * ## remove 를 보지 않는다
 * 삭제 동기화는 범위 밖이다(스펙 §1). 로컬에서 지워도 Jet-Rag 문서는 남는다.
 *
 * ## 콜백은 **폴더 경로도 받는다**
 * 맥 FSEvents 는 감시 루트 폴더 자체의 create/modify 를 파일 이벤트보다 먼저 흘린다
 * (2026-09-15 실측). 여기서 거르려면 stat 이 필요해 순수함을 잃으므로, 거르는 쪽은
 * 호출자다 — `run.ts` 의 `ingest` 가 `st.isFile` 로 닫는다.
 */

import { isTempName } from "./gate.ts";

const INTERESTING = new Set(["create", "modify", "rename"]);

/** 이벤트 → 다룰 경로. 임시·숨김 파일은 여기서 버린다. */
export function fsEventPaths(ev: { kind: string; paths: string[] }): string[] {
  if (!INTERESTING.has(ev.kind)) return [];
  return ev.paths.filter((p) => !isTempName(p));
}

/** 경로별 마지막 이벤트 후 `delayMs` 동안 조용하면 flush 한다. */
export class Debouncer {
  // Deno 2.8 의 setTimeout 반환형은 number 가 아니다(node 호환 Timeout). 런타임 값을 그대로 담는다.
  readonly #timers = new Map<string, ReturnType<typeof setTimeout>>();

  constructor(private readonly delayMs: number, private readonly flush: (path: string) => void) {}

  push(path: string): void {
    const prev = this.#timers.get(path);
    if (prev !== undefined) clearTimeout(prev);
    const id = setTimeout(() => {
      this.#timers.delete(path);
      this.flush(path);
    }, this.delayMs);
    this.#timers.set(path, id);
  }

  get size(): number {
    return this.#timers.size;
  }

  dispose(): void {
    for (const id of this.#timers.values()) clearTimeout(id);
    this.#timers.clear();
  }
}

export interface WatchOptions {
  debounceMs?: number;
  signal?: AbortSignal;
  onError?: (dir: string, err: Error) => void;
}

/**
 * `dirs` 를 재귀 감시하며 조용해진 경로를 `onPath` 로 넘긴다.
 * `signal` 로 중단한다. 폴더 하나가 없어도 나머지는 계속 감시한다.
 */
export async function watchLoop(
  dirs: string[],
  onPath: (path: string) => void,
  opts: WatchOptions = {},
): Promise<void> {
  const debouncer = new Debouncer(opts.debounceMs ?? 1500, onPath);
  const alive: string[] = [];
  for (const d of dirs) {
    try {
      await Deno.stat(d);
      alive.push(d);
    } catch (e) {
      opts.onError?.(d, e as Error);
    }
  }
  if (alive.length === 0) {
    debouncer.dispose();
    return;
  }
  const watcher = Deno.watchFs(alive, { recursive: true });
  const stop = () => {
    try {
      watcher.close();
    } catch {
      // 이미 닫혔으면 무시.
    }
  };
  opts.signal?.addEventListener("abort", stop, { once: true });
  try {
    for await (const ev of watcher) {
      for (const p of fsEventPaths(ev)) debouncer.push(p);
    }
  } catch (e) {
    // close() 가 루프를 BadResource 로 끊는다 — 정상 종료다.
    if (opts.signal?.aborted !== true) opts.onError?.(alive.join(","), e as Error);
  } finally {
    opts.signal?.removeEventListener("abort", stop);
    debouncer.dispose();
  }
}
