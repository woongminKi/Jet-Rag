/**
 * `tag_summarize` 작업 핸들러 — 태그·요약을 LLM 으로 만들어 문서에 저장한다.
 *
 * 원본 파이프라인 자리: `chunk → chunk_filter → content_gate → **tag_summarize** → load`.
 * Edge 는 앞의 둘을 `chunk` 안에서 처리하므로 여기가 `chunk` 다음이다.
 *
 * ## 별도 스테이지로 뺀 이유
 * LLM 을 두 번 부른다(수 초). `chunk` 안에 넣으면 그 태스크의 벽시계가 길어져 큐가
 * 막힌다. I/O 라 CPU 예산에는 안 잡히지만 단계를 나누는 편이 진행 표시도 정확하다.
 *
 * ## 실패해도 다음으로 간다
 * LLM 이 죽어도 `load` 는 넣는다 — 태그·요약이 없을 뿐 문서는 검색 가능해야 한다.
 * 다만 **둘 다 실패**하면 스테이지 로그를 `failed` 로 남긴다(원본과 같다).
 */

import type { SupabaseClient } from "@supabase/supabase-js";

import { loadRawText } from "../raw_text.ts";
import { runTagSummarizeStage } from "../tag_summarize.ts";
import type { HandlerOutcome, TaskHandler, TaskPayload } from "../worker.ts";

/** 요약이 보는 최대 길이. 그보다 더 읽을 이유가 없다. */
const MAX_INPUT_CHARS = 12000;

export interface TagSummarizeHandlerDeps {
  client: SupabaseClient;
  env?: Record<string, string | undefined>;
  /** 테스트 주입. */
  complete?: Parameters<typeof runTagSummarizeStage>[0]["complete"];
  rawText?: (jobId: string) => Promise<string>;
}

export function makeTagSummarizeHandler(deps: TagSummarizeHandlerDeps): TaskHandler {
  const env = deps.env ?? Deno.env.toObject();

  return async (task: TaskPayload): Promise<HandlerOutcome> => {
    const rawText = deps.rawText
      ? await deps.rawText(task.job_id)
      : await loadRawText(deps.client, task.job_id, MAX_INPUT_CHARS);

    const r = await runTagSummarizeStage(
      { client: deps.client, env, complete: deps.complete },
      task.doc_id,
      rawText,
    );

    const { error } = await deps.client.rpc("ingest_queue_send", {
      payload: { job_id: task.job_id, doc_id: task.doc_id, stage: "load", from: 0 },
    });
    if (error) throw new Error(`load enqueue 실패: ${error.message}`);

    // 원본: 둘 다 없을 때만 failed. 하나라도 있으면 succeeded 이되 사유는 남긴다.
    if (r.errors.length === 0) return {};
    const bothFailed = r.tags === null && r.summary === null;
    return {
      logStatus: bothFailed ? "failed" : "succeeded",
      logError: r.errors.join(" | "),
    };
  };
}
