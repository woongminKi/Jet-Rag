/**
 * 상태 전이 — **순수 함수만** 있다. 시계도 난수도 인자로 받는다.
 *
 * 스펙 §5.2 의 상태 기계를 그대로 옮긴 것이고, 테스트가 그 표의 실행 가능한 사본이다.
 * I/O 를 섞으면 "429 를 8번 맞으면 어떻게 되는가"를 시험하는 데 8분이 든다.
 *
 * ## 백로그는 실시간이 완전히 빌 때만 돈다
 * 서버 큐 처리량 실측이 **시간당 약 5건**(2026-09-15, 건당 평균 701s)이다. 병목이
 * 서버라서 에이전트 동시성을 올려도 전체 처리량은 안 늘고, 사용자가 방금 넣은 파일만
 * 백로그 137건 뒤에 선다. 그래서 실시간 2 · 백로그 1 + 유휴 조건이다.
 */

import {
  backoffMs,
  type JobInfo,
  POLL_INTERVAL_MS,
  type PrecheckState,
  QUOTA_WAIT_MS,
  RETRY,
  type UploadOutcome,
} from "./client.ts";
import type { FileState } from "./ledger.ts";

export interface Transition {
  state: FileState;
  nextAttemptAt: number;
  lastError: string | null;
  docId?: string | null;
  jobId?: string | null;
  /** 설정·토큰 문제 — 재시도로 풀리지 않는다. 루프가 멈추고 사용자에게 알린다. */
  fatal?: "auth" | "forbidden";
  /** attempts 를 올려야 하는가. 401/403 처럼 파일 잘못이 아닌 경우는 false. */
  countAttempt: boolean;
}

export const CONCURRENCY = { live: 2, backlog: 1 } as const;

export function planUpload(
  outcome: UploadOutcome,
  attempts: number,
  now: number,
  rand: () => number = Math.random,
): Transition {
  switch (outcome.kind) {
    case "accepted":
      if (outcome.duplicated) {
        return {
          state: "done",
          nextAttemptAt: 0,
          lastError: null,
          docId: outcome.docId,
          jobId: null,
          countAttempt: false,
        };
      }
      return {
        state: "registered",
        nextAttemptAt: now + POLL_INTERVAL_MS,
        lastError: null,
        docId: outcome.docId,
        jobId: outcome.jobId,
        countAttempt: false,
      };
    case "excluded":
      return {
        state: "excluded",
        nextAttemptAt: 0,
        lastError: `[${outcome.code}] ${outcome.detail}`,
        countAttempt: false,
      };
    case "quota":
      return {
        state: "quota_wait",
        nextAttemptAt: now + QUOTA_WAIT_MS,
        lastError: `저장 용량 한도 (${outcome.used}/${outcome.limit}) — ${outcome.detail}`,
        countAttempt: false,
      };
    case "auth":
    case "forbidden":
      // 파일 잘못이 아니다. attempts 를 태우면 토큰을 고친 뒤에도 이미 소진돼 있다.
      return {
        state: "pending",
        nextAttemptAt: now,
        lastError: outcome.detail,
        fatal: outcome.kind,
        countAttempt: false,
      };
    case "retry": {
      const next = attempts + 1;
      if (next > RETRY.maxAttempts) {
        return {
          state: "failed",
          nextAttemptAt: 0,
          lastError: `${RETRY.maxAttempts}회 재시도 실패: ${outcome.detail}`,
          countAttempt: true,
        };
      }
      const delay = outcome.retryAfterMs ?? backoffMs(next, rand);
      return {
        state: "pending",
        nextAttemptAt: now + delay,
        lastError: outcome.detail,
        countAttempt: true,
      };
    }
  }
}

export function planPrecheck(state: PrecheckState, docId: string | undefined, now: number): Transition {
  if (state === "existing") {
    return { state: "done", nextAttemptAt: 0, lastError: null, docId: docId ?? null, countAttempt: false };
  }
  // `failed` 는 서버에 실패 흔적이 남은 행 — 같은 문서에 재시도를 거는 게 맞다(persist 의 retried 분기).
  return { state: "pending", nextAttemptAt: now, lastError: null, countAttempt: false };
}

export function planJob(job: JobInfo | null, now: number): Transition {
  const status = job?.status ?? null;
  switch (status) {
    case "completed":
      return { state: "done", nextAttemptAt: 0, lastError: null, countAttempt: false };
    case "failed":
      return {
        state: "failed",
        nextAttemptAt: 0,
        lastError: `서버 처리 실패${job?.current_stage ? `(${job.current_stage})` : ""}: ${
          job?.error_msg ?? "사유 없음"
        }`,
        countAttempt: false,
      };
    case "cancelled":
      return { state: "failed", nextAttemptAt: 0, lastError: "서버에서 취소됨", countAttempt: false };
    case "deferred_quota":
      // **비종단**이다. 월 Vision 페이지 한도 — 월초 cron 이 재투입한다(스펙 §10).
      return {
        state: "registered",
        nextAttemptAt: now + QUOTA_WAIT_MS,
        lastError: "한도 대기 중 (월 Vision 페이지)",
        countAttempt: false,
      };
    default:
      // queued · running · 잡 없음 · 모르는 값 → 계속 본다.
      return {
        state: "registered",
        nextAttemptAt: now + POLL_INTERVAL_MS,
        lastError: null,
        countAttempt: false,
      };
  }
}

export interface QueueState {
  livePending: number;
  liveInFlight: number;
  backlogPending: number;
  backlogInFlight: number;
}

export function pickBatch(
  s: QueueState,
  limits: { live: number; backlog: number } = CONCURRENCY,
): {
  live: number;
  backlog: number;
} {
  const live = Math.max(0, Math.min(limits.live - s.liveInFlight, s.livePending));
  const idle = s.livePending === 0 && s.liveInFlight === 0;
  const backlog = idle ? Math.max(0, Math.min(limits.backlog - s.backlogInFlight, s.backlogPending)) : 0;
  return { live, backlog };
}
