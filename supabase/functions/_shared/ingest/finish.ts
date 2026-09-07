/**
 * `jobs.finish_job` 포팅 — 잡을 완료로 마감한다.
 *
 * 사슬의 **끝에서만** 부른다. 지금은 `dedup`(또는 `doc_embed` 가 건너뛴 경우 `finish`)이다.
 * 원본도 `run_pipeline` 의 마지막 줄에서 한 번 부른다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

import { pyIsoUtc } from "../pytime.ts";

export async function finishJob(
  client: SupabaseClient,
  jobId: string,
  nowMs: number,
): Promise<void> {
  const { error } = await client
    .from("ingest_jobs")
    .update({
      status: "completed",
      current_stage: "done",
      finished_at: pyIsoUtc(nowMs),
    })
    .eq("id", jobId);
  if (error) throw new Error(`잡 마감 실패: ${error.message}`);
}
