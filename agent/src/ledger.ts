/**
 * 로컬 원장 — `~/.jetrag/ledger.sqlite`.
 *
 * ## 정체성은 sha256 이다
 * 경로가 아니다. 이름을 바꾸거나 폴더를 옮겨도 같은 파일이므로 `path` 만 갱신하고
 * 상태는 유지한다. 그래서 `files.sha256` 이 PK 다.
 *
 * ## precheck 는 상태가 아니라 플래그다
 * "precheck 끝났지만 아직 업로드 안 함"을 별도 상태로 두면 재시도·백오프 전이가
 * 상태 × 플래그로 두 배가 된다. `state='pending' + precheck_done` 조합으로 둔다.
 *
 * ## 제외·실패는 조용히 사라지지 않는다 (스펙 §5.2)
 * `excluded`·`failed`·`quota_wait` 는 전부 `last_error` 에 사유를 남기고 `status` 에 보인다.
 *
 * ## 손상 복구
 * 열기 실패 시 `.bak` 으로 밀고 빈 원장을 만든다. 서버 precheck 가 있어 재스캔해도
 * 중복 업로드는 생기지 않는다 — 원장을 잃는 비용은 "다시 해싱"뿐이다.
 */

import { DatabaseSync } from "node:sqlite";

export type FileState =
  | "pending"
  | "uploading"
  | "registered"
  | "done"
  | "failed"
  | "excluded"
  | "quota_wait";
export type QueueKind = "live" | "backlog";

export interface FileRow {
  sha256: string;
  path: string;
  size: number;
  mtime: number;
  state: FileState;
  queue: QueueKind;
  precheck_done: number;
  doc_id: string | null;
  job_id: string | null;
  attempts: number;
  next_attempt_at: number;
  last_error: string | null;
  updated_at: number;
}

export interface DiscoverInput {
  sha256: string;
  path: string;
  size: number;
  mtime: number;
  queue: QueueKind;
}

const SCHEMA = `
CREATE TABLE IF NOT EXISTS files (
  sha256          TEXT PRIMARY KEY,
  path            TEXT NOT NULL,
  size            INTEGER NOT NULL,
  mtime           INTEGER NOT NULL,
  state           TEXT NOT NULL,
  queue           TEXT NOT NULL DEFAULT 'live',
  precheck_done   INTEGER NOT NULL DEFAULT 0,
  doc_id          TEXT,
  job_id          TEXT,
  attempts        INTEGER NOT NULL DEFAULT 0,
  next_attempt_at INTEGER NOT NULL DEFAULT 0,
  last_error      TEXT,
  updated_at      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS files_state_next ON files (state, next_attempt_at);
CREATE INDEX IF NOT EXISTS files_queue_state ON files (queue, state);
CREATE TABLE IF NOT EXISTS events (
  id    INTEGER PRIMARY KEY AUTOINCREMENT,
  ts    INTEGER NOT NULL,
  level TEXT NOT NULL,
  msg   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_ts ON events (ts DESC);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
`;

const ALL_STATES: FileState[] = [
  "pending",
  "uploading",
  "registered",
  "done",
  "failed",
  "excluded",
  "quota_wait",
];

export class Ledger {
  readonly #db: DatabaseSync;

  private constructor(db: DatabaseSync) {
    this.#db = db;
    this.#db.exec("PRAGMA journal_mode = WAL");
    this.#db.exec("PRAGMA busy_timeout = 5000");
    this.#db.exec(SCHEMA);
  }

  static openMemory(): Ledger {
    return new Ledger(new DatabaseSync(":memory:"));
  }

  /** 열기 실패 시 `.bak` 으로 밀고 새로 만든다. */
  static open(path: string): Ledger {
    // SQLite 는 파일을 지연 검증한다 — 헤더가 깨진 파일도 생성자는 통과하고 첫 문장에서 터진다.
    // 그때 핸들은 이미 열려 있다. 윈도우는 열린 핸들이 있으면 rename·remove 가 막히므로
    // 밀어내기 전에 반드시 닫는다.
    let db: DatabaseSync | null = null;
    try {
      db = new DatabaseSync(path);
      return new Ledger(db);
    } catch (e) {
      if (db !== null) {
        try {
          db.close();
        } catch { /* 이미 닫혔거나 닫을 수 없는 상태 — 밀어내기를 막지 않는다. */ }
      }
      try {
        Deno.renameSync(path, `${path}.bak`);
      } catch {
        // 옮기지도 못하면 지운다 — 원장 하나 때문에 에이전트가 영영 안 뜨면 안 된다.
        try {
          Deno.removeSync(path);
        } catch { /* 무시 */ }
      }
      console.error(`원장을 열 수 없어 새로 만듭니다 (${(e as Error).message}). 이전 파일: ${path}.bak`);
      return new Ledger(new DatabaseSync(path));
    }
  }

  close(): void {
    this.#db.close();
  }

  get(sha256: string): FileRow | null {
    const row = this.#db.prepare("SELECT * FROM files WHERE sha256 = ?").get(sha256);
    return row ? ({ ...row } as unknown as FileRow) : null;
  }

  /**
   * 파일 발견. 이미 있으면 경로·크기·mtime 만 갱신하고 **상태는 건드리지 않는다**.
   * 큐는 backlog → live 승격만 한다(사용자가 방금 만진 파일이 우선).
   */
  discover(f: DiscoverInput, now: number): FileState {
    const existing = this.get(f.sha256);
    if (existing) {
      const queue: QueueKind = existing.queue === "live" || f.queue === "live" ? "live" : "backlog";
      this.#db.prepare(
        "UPDATE files SET path = ?, size = ?, mtime = ?, queue = ?, updated_at = ? WHERE sha256 = ?",
      ).run(f.path, f.size, f.mtime, queue, now, f.sha256);
      return existing.state;
    }
    this.#db.prepare(
      `INSERT INTO files (sha256, path, size, mtime, state, queue, precheck_done, attempts, next_attempt_at, updated_at)
       VALUES (?, ?, ?, ?, 'pending', ?, 0, 0, 0, ?)`,
    ).run(f.sha256, f.path, f.size, f.mtime, f.queue, now);
    return "pending";
  }

  /** precheck 대상 — live 우선, 같은 큐면 최신순. */
  claimUnchecked(limit: number, now: number): FileRow[] {
    return this.#db.prepare(
      `SELECT * FROM files
        WHERE state = 'pending' AND precheck_done = 0 AND next_attempt_at <= ?
        ORDER BY (queue = 'live') DESC, mtime DESC
        LIMIT ?`,
    ).all(now, limit).map((r) => ({ ...r } as unknown as FileRow));
  }

  markPrechecked(shas: string[], now: number): void {
    const stmt = this.#db.prepare("UPDATE files SET precheck_done = 1, updated_at = ? WHERE sha256 = ?");
    for (const s of shas) stmt.run(now, s);
  }

  /** 업로드 대상을 잡아 `uploading` 으로 표시한다(같은 파일 두 번 집는 것 방지). */
  claimUpload(queue: QueueKind, limit: number, now: number): FileRow[] {
    const rows = this.#db.prepare(
      `SELECT * FROM files
        WHERE state = 'pending' AND precheck_done = 1 AND queue = ? AND next_attempt_at <= ?
        ORDER BY mtime DESC
        LIMIT ?`,
    ).all(queue, now, limit).map((r) => ({ ...r } as unknown as FileRow));
    const stmt = this.#db.prepare("UPDATE files SET state = 'uploading', updated_at = ? WHERE sha256 = ?");
    for (const r of rows) stmt.run(now, r.sha256);
    return rows.map((r) => ({ ...r, state: "uploading" as FileState }));
  }

  countPending(queue: QueueKind, now: number): number {
    const row = this.#db.prepare(
      `SELECT COUNT(*) AS n FROM files
        WHERE state = 'pending' AND precheck_done = 1 AND queue = ? AND next_attempt_at <= ?`,
    ).get(queue, now) as { n: number };
    return Number(row.n);
  }

  countInFlight(queue: QueueKind): number {
    const row = this.#db.prepare(
      "SELECT COUNT(*) AS n FROM files WHERE state = 'uploading' AND queue = ?",
    ).get(queue) as { n: number };
    return Number(row.n);
  }

  /**
   * 등록·폴링 재예약 공용.
   *
   * `docId`/`jobId` 에 null 을 넘기면 **기존 값을 유지한다**(COALESCE). 폴링 전이는
   * `planJob` 이 만드는데 거기엔 doc_id 가 없다 — 그대로 덮어쓰면 첫 폴링에 doc_id 가
   * 날아가 그 문서는 영영 완료되지 않는다(2026-09-15 run_test 가 잡은 버그).
   * `lastError` 는 `deferred_quota` 처럼 "비종단이지만 사유는 보여야 하는" 경우에 쓴다.
   */
  markRegistered(
    sha256: string,
    docId: string | null,
    jobId: string | null,
    nextPollAt: number,
    now: number,
    lastError: string | null = null,
  ): void {
    this.#db.prepare(
      `UPDATE files SET state = 'registered', doc_id = COALESCE(?, doc_id), job_id = COALESCE(?, job_id),
         last_error = ?, next_attempt_at = ?, updated_at = ? WHERE sha256 = ?`,
    ).run(docId, jobId, lastError, nextPollAt, now, sha256);
  }

  markDone(sha256: string, docId: string | null, now: number): void {
    this.#db.prepare(
      "UPDATE files SET state = 'done', doc_id = COALESCE(?, doc_id), last_error = NULL, next_attempt_at = 0, updated_at = ? WHERE sha256 = ?",
    ).run(docId, now, sha256);
  }

  markRetry(sha256: string, error: string, nextAttemptAt: number, now: number): void {
    this.#db.prepare(
      `UPDATE files SET state = 'pending', attempts = attempts + 1, last_error = ?,
         next_attempt_at = ?, updated_at = ? WHERE sha256 = ?`,
    ).run(error, nextAttemptAt, now, sha256);
  }

  markExcluded(sha256: string, code: string, detail: string, now: number): void {
    this.#db.prepare(
      "UPDATE files SET state = 'excluded', last_error = ?, next_attempt_at = 0, updated_at = ? WHERE sha256 = ?",
    ).run(`[${code}] ${detail}`, now, sha256);
  }

  markQuotaWait(sha256: string, detail: string, retryAt: number, now: number): void {
    this.#db.prepare(
      "UPDATE files SET state = 'quota_wait', last_error = ?, next_attempt_at = ?, updated_at = ? WHERE sha256 = ?",
    ).run(detail, retryAt, now, sha256);
  }

  markFailed(sha256: string, detail: string, now: number): void {
    this.#db.prepare(
      "UPDATE files SET state = 'failed', last_error = ?, next_attempt_at = 0, updated_at = ? WHERE sha256 = ?",
    ).run(detail, now, sha256);
  }

  /** 서버 잡 상태를 물어볼 행. `doc_id` 가 있는 `registered` 만. */
  duePolls(limit: number, now: number): FileRow[] {
    return this.#db.prepare(
      `SELECT * FROM files WHERE state = 'registered' AND doc_id IS NOT NULL AND next_attempt_at <= ?
        ORDER BY next_attempt_at ASC LIMIT ?`,
    ).all(now, limit).map((r) => ({ ...r } as unknown as FileRow));
  }

  /** 6시간이 지난 한도대기 — 다시 업로드 줄에 세운다. */
  dueQuotaWait(now: number): FileRow[] {
    return this.#db.prepare(
      "SELECT * FROM files WHERE state = 'quota_wait' AND next_attempt_at <= ? ORDER BY mtime DESC",
    ).all(now).map((r) => ({ ...r } as unknown as FileRow));
  }

  requeue(sha256: string, now: number): void {
    this.#db.prepare(
      "UPDATE files SET state = 'pending', next_attempt_at = 0, updated_at = ? WHERE sha256 = ?",
    ).run(now, sha256);
  }

  /** 프로세스가 죽어 남은 `uploading` 을 되돌린다. precheck 결과는 보존. */
  resetInFlight(now: number): number {
    const before = this.#db.prepare("SELECT COUNT(*) AS n FROM files WHERE state = 'uploading'").get() as {
      n: number;
    };
    this.#db.prepare(
      "UPDATE files SET state = 'pending', next_attempt_at = 0, updated_at = ? WHERE state = 'uploading'",
    ).run(now);
    return Number(before.n);
  }

  counts(): Record<FileState, number> {
    const out = Object.fromEntries(ALL_STATES.map((s) => [s, 0])) as Record<FileState, number>;
    for (const r of this.#db.prepare("SELECT state, COUNT(*) AS n FROM files GROUP BY state").all()) {
      const row = r as unknown as { state: FileState; n: number };
      out[row.state] = Number(row.n);
    }
    return out;
  }

  /** `status` 가 보여줄 최근 문제 — 제외·실패·한도대기. */
  recentProblems(limit: number): FileRow[] {
    return this.#db.prepare(
      `SELECT * FROM files WHERE state IN ('excluded', 'failed', 'quota_wait')
        ORDER BY updated_at DESC LIMIT ?`,
    ).all(limit).map((r) => ({ ...r } as unknown as FileRow));
  }

  event(level: string, msg: string, ts: number = Date.now()): void {
    this.#db.prepare("INSERT INTO events (ts, level, msg) VALUES (?, ?, ?)").run(ts, level, msg);
  }

  recentEvents(limit: number): { ts: number; level: string; msg: string }[] {
    return this.#db.prepare("SELECT ts, level, msg FROM events ORDER BY ts DESC, id DESC LIMIT ?")
      .all(limit).map((r) => ({ ...r } as unknown as { ts: number; level: string; msg: string }));
  }

  getMeta(key: string): string | null {
    const row = this.#db.prepare("SELECT value FROM meta WHERE key = ?").get(key) as
      | { value: string }
      | undefined;
    return row ? row.value : null;
  }

  setMeta(key: string, value: string): void {
    this.#db.prepare(
      "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
    ).run(key, value);
  }
}
