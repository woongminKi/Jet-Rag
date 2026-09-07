/**
 * `dedup` 작업 핸들러 — 문서 임베딩으로 Tier 2/3 중복을 찾는다. **사슬의 끝**이다.
 *
 * 여기서 잡을 마감한다(원본 `run_pipeline` 의 `finish_job` 자리).
 */

import type { SupabaseClient } from "@supabase/supabase-js";

import { runDedupStage } from "../dedup.ts";
import { finishJob } from "../finish.ts";
import type { HandlerOutcome, TaskHandler, TaskPayload } from "../worker.ts";

export interface DedupHandlerDeps {
  client: SupabaseClient;
  defaultUserId: string;
  nowMs?: () => number;
}

export function makeDedupHandler(deps: DedupHandlerDeps): TaskHandler {
  const now = deps.nowMs ?? (() => Date.now());
  return async (task: TaskPayload): Promise<HandlerOutcome> => {
    const r = await runDedupStage(
      { client: deps.client, defaultUserId: deps.defaultUserId },
      task.doc_id,
    );
    await finishJob(deps.client, task.job_id, now());
    if (r.skipped) {
      console.info(`dedup: doc=${task.doc_id} ${r.reason}`);
      // 원본은 `skip_stage` 로 남기지만 워커 로그는 succeeded/failed 만 받는다.
      // 사유는 error_msg 에 남겨 `/status?include_logs` 에서 보이게 한다.
      return { logStatus: "succeeded", logError: r.reason ?? null };
    }
    return {};
  };
}

/** `doc_embed` 가 벡터를 못 채웠을 때 오는 자리 — 마감만 한다. */
export function makeFinishHandler(deps: DedupHandlerDeps): TaskHandler {
  const now = deps.nowMs ?? (() => Date.now());
  return async (task: TaskPayload) => {
    await finishJob(deps.client, task.job_id, now());
  };
}
