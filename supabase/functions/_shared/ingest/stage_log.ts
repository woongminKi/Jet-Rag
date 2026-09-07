/**
 * `ingest/jobs.py` 의 `begin_stage` / `end_stage` 포팅 — `ingest_logs` 1 행 모델.
 *
 * ## 이게 없으면 두 기능이 조용히 죽는다
 * Edge 는 지금까지 `ingest_logs` 를 **읽기만 하고 쓰지 않았다**. 그래서
 * - `GET /documents/{id}/status?include_logs=true` 가 늘 빈 배열
 * - `eta.ts` 가 stage 별 median 을 못 구해 항상 cold-start 추정으로 떨어진다
 *
 * ## 시계를 한쪽으로 모은다
 * 원본 주석 그대로다 — `started_at` 을 DB `DEFAULT now()` 에 맡기면 애플리케이션이 쓰는
 * `finished_at` 과 시계 소스가 달라 `started_at > finished_at` 역전이 실제로 관찰됐다.
 * 양쪽 다 호출자가 넘긴 시각을 쓴다.
 *
 * ## 실패해도 인제스트를 막지 않는다
 * 기록은 부가 기능이다. 여기서 던지면 잘 끝난 작업이 실패로 뒤집힌다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

import { pyIsoUtc } from "../pytime.ts";

const TABLE = "ingest_logs";

/** 원본과 같은 허용 집합. 그 외 값은 원본이 `ValueError` 를 낸다. */
const END_STATUSES = new Set(["succeeded", "failed", "skipped"]);

/** 시작 기록. 실패하면 `null` — 그 뒤 `endStage` 는 아무것도 안 한다. */
export async function beginStage(
  client: SupabaseClient,
  jobId: string,
  stage: string,
  nowMs: number,
): Promise<number | null> {
  try {
    const { data, error } = await client
      .from(TABLE)
      .insert({
        job_id: jobId,
        stage,
        status: "started",
        started_at: pyIsoUtc(nowMs),
      })
      .select("id")
      .single();
    if (error) throw new Error(error.message);
    return Number((data as { id: number }).id);
  } catch (e) {
    console.warn(`ingest_logs begin 실패 (graceful): ${e} (job=${jobId}, stage=${stage})`);
    return null;
  }
}

export async function endStage(
  client: SupabaseClient,
  logId: number | null,
  opts: {
    status: "succeeded" | "failed" | "skipped";
    errorMsg?: string | null;
    durationMs?: number | null;
    nowMs: number;
  },
): Promise<void> {
  if (logId === null) return;
  if (!END_STATUSES.has(opts.status)) {
    throw new Error(`end_stage 에 허용되지 않는 status: ${opts.status}`);
  }
  const payload: Record<string, unknown> = {
    status: opts.status,
    finished_at: pyIsoUtc(opts.nowMs),
  };
  // 원본은 `is not None` 일 때만 넣는다 — 안 넣으면 컬럼이 기존 값을 유지한다.
  if (opts.errorMsg !== undefined && opts.errorMsg !== null) {
    payload["error_msg"] = opts.errorMsg;
  }
  if (opts.durationMs !== undefined && opts.durationMs !== null) {
    payload["duration_ms"] = opts.durationMs;
  }
  try {
    const { error } = await client.from(TABLE).update(payload).eq("id", logId);
    if (error) throw new Error(error.message);
  } catch (e) {
    console.warn(`ingest_logs end 실패 (graceful): ${e} (log=${logId})`);
  }
}
