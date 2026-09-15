/**
 * 백로그 스캔 — 설치 시점에 이미 폴더에 있던 파일을 찾는다 (스펙 §5.3, 결정 D9).
 *
 * ## 최신순인 이유
 * 사용자가 지금 찾을 가능성이 높은 건 최근 문서다. 서버 큐가 시간당 5건이라
 * 137건이면 하루가 걸린다 — 그 하루 동안 위에서부터 쓸모 있는 순서로 올라가야 한다.
 *
 * ## 숨김 폴더는 통째로 건너뛴다
 * `.git`·`.Trash`·동기화 캐시가 여기 산다. 들어가면 스캔이 몇 분씩 걸리고 올릴 것도 없다.
 *
 * ## 심볼릭 링크를 따라가지 않는다
 * 순환이 생기고, 지정 폴더 밖을 읽게 된다(보안 페르소나 합의: 전체 스캔 금지).
 */

import { gateEntry } from "./gate.ts";

export interface Candidate {
  path: string;
  size: number;
  mtime: number;
}

export interface ScanOptions {
  days: number;
  now?: number;
  maxDepth?: number;
  /** 폭주 방지. 넘으면 거기서 멈춘다. */
  maxEntries?: number;
  onSkip?: (path: string, reason: string) => void;
}

const DAY_MS = 86_400_000;

export async function scanBacklog(dirs: string[], opts: ScanOptions): Promise<Candidate[]> {
  if (opts.days <= 0) return [];
  const now = opts.now ?? Date.now();
  const cutoff = now - opts.days * DAY_MS;
  const maxDepth = opts.maxDepth ?? 8;
  const maxEntries = opts.maxEntries ?? 200_000;
  const out: Candidate[] = [];
  let seen = 0;

  const stack: { dir: string; depth: number }[] = dirs.map((d) => ({ dir: d, depth: 0 }));
  while (stack.length > 0) {
    const { dir, depth } = stack.pop()!;
    if (depth > maxDepth) continue;
    let entries: Deno.DirEntry[];
    try {
      entries = [];
      for await (const e of Deno.readDir(dir)) entries.push(e);
    } catch (e) {
      // 없는 폴더·권한 없음 — 나머지 폴더는 계속 본다.
      opts.onSkip?.(dir, `읽기 실패: ${(e as Error).message}`);
      continue;
    }
    for (const e of entries) {
      if (seen++ > maxEntries) return sortNewest(out);
      if (e.isSymlink) continue;
      if (e.name.startsWith(".")) continue; // 숨김 파일·폴더 전부
      const path = `${dir}/${e.name}`;
      if (e.isDirectory) {
        stack.push({ dir: path, depth: depth + 1 });
        continue;
      }
      let st: Deno.FileInfo;
      try {
        st = await Deno.stat(path);
      } catch {
        continue; // 스캔 중 사라진 파일
      }
      const mtime = st.mtime?.getTime() ?? 0;
      if (mtime < cutoff) continue;
      const g = gateEntry({ path, size: st.size });
      if (!g.ok) {
        // `temp` 는 소음이라 알리지 않는다. 나머지(확장자·크기)는 사용자가 알아야 한다.
        if (g.reason !== "temp") opts.onSkip?.(path, g.reason);
        continue;
      }
      out.push({ path, size: st.size, mtime });
    }
  }
  return sortNewest(out);
}

function sortNewest(list: Candidate[]): Candidate[] {
  return list.sort((a, b) => b.mtime - a.mtime);
}

export function summarize(list: Candidate[]): { count: number; bytes: number } {
  return { count: list.length, bytes: list.reduce((n, c) => n + c.size, 0) };
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n}B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)}KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)}MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(1)}GB`;
}
