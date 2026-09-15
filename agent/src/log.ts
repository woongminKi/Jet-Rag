/**
 * `~/.jetrag/agent.log` — 5MB × 3 회전.
 *
 * 트레이 UI 가 없으므로 이 파일과 `status` 명령이 관측의 전부다(스펙 §5.6).
 * 쓰기는 append 로만 하고, 크기가 넘으면 `.1 → .2 → .3` 으로 밀고 `.3` 은 버린다.
 */

export interface LoggerOptions {
  maxBytes?: number;
  keep?: number;
}

export type LogLevel = "info" | "warn" | "error";

export class FileLogger {
  readonly #path: string;
  readonly #maxBytes: number;
  readonly #keep: number;
  #size = -1;

  constructor(path: string, opts: LoggerOptions = {}) {
    this.#path = path;
    this.#maxBytes = opts.maxBytes ?? 5 * 1024 * 1024;
    this.#keep = opts.keep ?? 3;
  }

  async write(level: LogLevel, msg: string): Promise<void> {
    const line = `${new Date().toISOString()} ${level.toUpperCase().padEnd(5)} ${msg}\n`;
    const bytes = new TextEncoder().encode(line);
    if (this.#size < 0) this.#size = await this.#currentSize();
    if (this.#size + bytes.length > this.#maxBytes) await this.#rotate();
    await Deno.writeFile(this.#path, bytes, { append: true, create: true });
    this.#size += bytes.length;
  }

  /** 지금은 버퍼가 없어 할 일이 없다. 인터페이스만 맞춰 둔다. */
  close(): Promise<void> {
    return Promise.resolve();
  }

  async #currentSize(): Promise<number> {
    try {
      return (await Deno.stat(this.#path)).size;
    } catch {
      return 0;
    }
  }

  async #rotate(): Promise<void> {
    for (let i = this.#keep; i >= 1; i--) {
      const from = i === 1 ? this.#path : `${this.#path}.${i - 1}`;
      const to = `${this.#path}.${i}`;
      try {
        await Deno.rename(from, to);
      } catch {
        // 없으면 넘어간다. 회전 실패로 로깅 자체가 죽으면 안 된다.
      }
    }
    try {
      await Deno.remove(`${this.#path}.${this.#keep + 1}`);
    } catch {
      // 없으면 넘어간다.
    }
    this.#size = 0;
  }
}

/** `logs` 명령용 — 마지막 n 줄. 파일이 없으면 빈 배열. */
export async function tailLines(path: string, n: number): Promise<string[]> {
  let text: string;
  try {
    text = await Deno.readTextFile(path);
  } catch {
    return [];
  }
  const lines = text.split("\n").filter((l) => l !== "");
  return lines.slice(Math.max(0, lines.length - n));
}
