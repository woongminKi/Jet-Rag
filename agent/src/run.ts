/**
 * 틱 루프 — 모듈을 묶는 곳. 여기에는 **정책이 없다**(전부 scheduler 에 있다).
 *
 * 한 틱에서 하는 일, 순서대로:
 *   ① 6시간 지난 한도대기를 업로드 줄로 되돌린다
 *   ② precheck 안 한 것 200개를 서버에 물어 `existing` 을 전송 없이 닫는다
 *   ③ pickBatch 만큼 업로드한다 (실시간 2 · 백로그 1)
 *   ④ `registered` 50개의 잡 상태를 묻는다
 *
 * ## 401/403 이면 멈춘다
 * 토큰이 폐기됐는데 계속 두드리면 로그만 쌓이고 사용자는 왜 안 올라가는지 모른다.
 * 루프를 끝내고 이유를 로그·원장·표준출력에 남긴다.
 *
 * ## 429 는 전역 일시정지
 * 분당 60건 상한의 키가 user_id 라 웹·다른 기기와 공유한다(스펙 §10). 파일 하나만
 * 미루면 다음 파일이 곧바로 또 429 를 맞는다.
 */

import { type ApiClient, ApiError, QUOTA_WAIT_MS } from "./client.ts";
import type { AgentConfig } from "./config.ts";
import { baseName, gateEntry, isStable } from "./gate.ts";
import { sha256File } from "./hash.ts";
import type { Ledger, QueueKind } from "./ledger.ts";
import type { FileLogger } from "./log.ts";
import { formatBytes, scanBacklog, summarize } from "./scanner.ts";
import { pickBatch, planJob, planPrecheck, planUpload, type Transition } from "./scheduler.ts";
import { watchLoop } from "./watcher.ts";

export interface RunDeps {
  cfg: AgentConfig;
  ledger: Ledger;
  client: ApiClient;
  logger: FileLogger;
  signal?: AbortSignal;
  tickMs?: number;
  /** 한 틱만 돌고 끝낸다 — 테스트·E2E 용. */
  once?: boolean;
  /** 백로그 스캔을 건너뛴다(이미 `init` 에서 넣은 경우). */
  skipBacklog?: boolean;
}

const BACKLOG_SCAN_INTERVAL_MS = 24 * 60 * 60 * 1000;

export class FatalAuthError extends Error {}

export async function runAgent(deps: RunDeps): Promise<void> {
  const { cfg, ledger, client, logger } = deps;
  const tickMs = deps.tickMs ?? 2000;
  const now0 = Date.now();

  const reset = ledger.resetInFlight(now0);
  if (reset > 0) await logger.write("info", `진행 중이던 업로드 ${reset}건을 대기로 되돌렸습니다.`);
  await logger.write("info", `감시 시작: ${cfg.watch_dirs.join(", ")}`);

  let pauseUntil = 0;
  const ac = new AbortController();
  deps.signal?.addEventListener("abort", () => ac.abort(), { once: true });

  const ingest = async (path: string, queue: QueueKind) => {
    let st: Deno.FileInfo;
    try {
      st = await Deno.stat(path);
    } catch {
      return;
    }
    if (!st.isFile) return;
    const g = gateEntry({ path, size: st.size });
    if (!g.ok) {
      if (g.reason !== "temp") await logger.write("info", `제외(${g.reason}): ${path} — ${g.detail}`);
      return;
    }
    if (!(await isStable(path))) {
      await logger.write("info", `아직 쓰는 중으로 보여 건너뜁니다(다음 이벤트에 다시 봅니다): ${path}`);
      return;
    }
    const sha = await sha256File(path);
    const state = ledger.discover(
      { sha256: sha, path, size: st.size, mtime: st.mtime?.getTime() ?? Date.now(), queue },
      Date.now(),
    );
    if (state === "pending") {
      await logger.write("info", `발견(${queue}): ${path} (${formatBytes(st.size)})`);
    }
  };

  // 실시간 감시 — 백그라운드로 돈다.
  const watching = deps.once ? Promise.resolve() : watchLoop(cfg.watch_dirs, (p) => {
    ingest(p, "live").catch((e) => logger.write("error", `감시 처리 실패 ${p}: ${(e as Error).message}`));
  }, {
    signal: ac.signal,
    onError: (dir, err) => logger.write("error", `감시 불가 ${dir}: ${err.message}`),
  });

  // 백로그 — 하루 1회.
  const lastScan = Number(ledger.getMeta("backlog_scanned_at") ?? "0");
  if (!deps.skipBacklog && cfg.backlog_days > 0 && Date.now() - lastScan > BACKLOG_SCAN_INTERVAL_MS) {
    const found = await scanBacklog(cfg.watch_dirs, {
      days: cfg.backlog_days,
      onSkip: (p, r) => logger.write("info", `백로그 제외(${r}): ${p}`),
    });
    const s = summarize(found);
    await logger.write("info", `백로그 스캔: ${s.count}개, ${formatBytes(s.bytes)}`);
    for (const c of found) {
      const sha = await sha256File(c.path);
      ledger.discover(
        { sha256: sha, path: c.path, size: c.size, mtime: c.mtime, queue: "backlog" },
        Date.now(),
      );
    }
    ledger.setMeta("backlog_scanned_at", String(Date.now()));
  }

  const apply = (sha: string, t: Transition, now: number) => {
    switch (t.state) {
      case "done":
        ledger.markDone(sha, t.docId ?? null, now);
        break;
      case "registered":
        // docId/jobId 가 없으면(= 폴링 전이) 기존 값을 유지한다 — 덮어쓰면 추적이 끊긴다.
        ledger.markRegistered(sha, t.docId ?? null, t.jobId ?? null, t.nextAttemptAt, now, t.lastError);
        if (t.lastError) ledger.event("info", `${sha.slice(0, 8)} ${t.lastError}`, now);
        break;
      case "excluded":
        ledger.markExcluded(sha, "server", t.lastError ?? "", now);
        break;
      case "quota_wait":
        ledger.markQuotaWait(sha, t.lastError ?? "", t.nextAttemptAt, now);
        break;
      case "failed":
        ledger.markFailed(sha, t.lastError ?? "", now);
        break;
      case "pending":
        if (t.countAttempt) ledger.markRetry(sha, t.lastError ?? "", t.nextAttemptAt, now);
        else ledger.requeue(sha, now);
        break;
      case "uploading":
        break;
    }
  };

  try {
    do {
      const now = Date.now();
      if (now < pauseUntil) {
        await sleep(Math.min(tickMs, pauseUntil - now), ac.signal);
        continue;
      }

      // ① 한도대기 복귀
      for (const row of ledger.dueQuotaWait(now)) {
        ledger.requeue(row.sha256, now);
        await logger.write("info", `한도대기 해제, 재시도: ${row.path}`);
      }

      // ② precheck
      const unchecked = ledger.claimUnchecked(200, now);
      if (unchecked.length > 0) {
        try {
          const results = await client.precheck(unchecked.map((r) => r.sha256));
          ledger.setMeta("last_contact", new Date().toISOString());
          const prechecked: string[] = [];
          for (const row of unchecked) {
            const entry = results[row.sha256];
            if (!entry) continue;
            const t = planPrecheck(entry.state, entry.doc_id, now);
            if (t.state === "done") {
              ledger.markDone(row.sha256, t.docId ?? null, now);
              await logger.write("info", `이미 있음(전송 없음): ${row.path}`);
            }
            prechecked.push(row.sha256);
          }
          ledger.markPrechecked(prechecked, now);
        } catch (e) {
          pauseUntil = await handleApiError(e as Error, logger, ledger, now);
          if (pauseUntil < 0) throw new FatalAuthError((e as Error).message);
        }
      }

      // ③ 업로드
      const batch = pickBatch({
        livePending: ledger.countPending("live", now),
        liveInFlight: ledger.countInFlight("live"),
        backlogPending: ledger.countPending("backlog", now),
        backlogInFlight: ledger.countInFlight("backlog"),
      });
      const claimed = [
        ...(batch.live > 0 ? ledger.claimUpload("live", batch.live, now) : []),
        ...(batch.backlog > 0 ? ledger.claimUpload("backlog", batch.backlog, now) : []),
      ];
      const fatals: string[] = [];
      await Promise.all(claimed.map(async (row) => {
        let bytes: Uint8Array;
        try {
          bytes = await Deno.readFile(row.path);
        } catch {
          ledger.markExcluded(row.sha256, "gone", `로컬 파일이 없습니다: ${row.path}`, Date.now());
          return;
        }
        const outcome = await client.upload({ name: baseName(row.path), bytes });
        const t2 = Date.now();
        ledger.setMeta("last_contact", new Date(t2).toISOString());
        const t = planUpload(outcome, row.attempts, t2);
        apply(row.sha256, t, t2);
        if (outcome.kind === "retry" && outcome.retryAfterMs !== null) {
          pauseUntil = Math.max(pauseUntil, t2 + outcome.retryAfterMs);
        }
        if (t.fatal) fatals.push(`${t.fatal}: ${t.lastError}`);
        const label = outcome.kind === "accepted" ? (outcome.duplicated ? "중복" : "등록") : outcome.kind;
        await logger.write(
          outcome.kind === "accepted" ? "info" : "warn",
          `업로드 ${label}: ${row.path}${t.lastError ? ` — ${t.lastError}` : ""}`,
        );
      }));
      if (fatals.length > 0) throw new FatalAuthError(fatals[0]);

      // ④ 잡 상태 폴링
      const polls = ledger.duePolls(50, Date.now());
      if (polls.length > 0) {
        try {
          const statuses = await client.batchStatus(polls.map((r) => r.doc_id ?? ""));
          const t3 = Date.now();
          ledger.setMeta("last_contact", new Date(t3).toISOString());
          for (const row of polls) {
            const t = planJob(statuses.get(row.doc_id ?? "") ?? null, t3);
            apply(row.sha256, t, t3);
            if (t.state === "done") await logger.write("info", `처리 완료: ${row.path}`);
            if (t.state === "failed") await logger.write("warn", `처리 실패: ${row.path} — ${t.lastError}`);
          }
        } catch (e) {
          pauseUntil = await handleApiError(e as Error, logger, ledger, Date.now());
          if (pauseUntil < 0) throw new FatalAuthError((e as Error).message);
        }
      }

      if (deps.once) break;
      await sleep(tickMs, ac.signal);
    } while (!ac.signal.aborted);
  } finally {
    ac.abort();
    await watching;
  }
}

/** 일시정지 시각을 돌려준다. `-1` 이면 치명(중단). */
async function handleApiError(
  e: Error,
  logger: FileLogger,
  ledger: Ledger,
  now: number,
): Promise<number> {
  if (e instanceof ApiError) {
    if (e.kind === "auth") {
      await logger.write(
        "error",
        `기기 토큰이 무효입니다(401). 설정 → 연결된 기기에서 새로 발급하세요: ${e.message}`,
      );
      ledger.event("error", "401 기기 토큰 무효", now);
      return -1;
    }
    if (e.kind === "forbidden") {
      await logger.write("error", `권한 범위를 벗어난 요청(403): ${e.message}`);
      ledger.event("error", "403 스코프 밖", now);
      return -1;
    }
    if (e.kind === "retry") {
      const wait = e.retryAfterMs ?? 30_000;
      await logger.write("warn", `서버가 바쁩니다(${e.status}). ${Math.round(wait / 1000)}초 뒤 재시도.`);
      return now + wait;
    }
    await logger.write("warn", `요청 거절(${e.status}): ${e.message}`);
    return now + 60_000;
  }
  await logger.write("warn", `통신 실패: ${e.message}`);
  return now + 30_000;
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const id = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => {
      clearTimeout(id);
      resolve();
    }, { once: true });
  });
}

export { QUOTA_WAIT_MS };
