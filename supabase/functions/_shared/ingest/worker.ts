/**
 * 인제스트 워커의 **드레인 루프** — 큐에서 작업을 꺼내 핸들러로 넘기고 정리한다.
 *
 * HTTP 껍데기(`api-ingest-worker`)와 분리한 이유는 지금까지와 같다: 패리티 검사기가
 * 토큰·네트워크 없이 in-process 로 돌릴 수 있어야 한다.
 *
 * ## 왜 큐인가
 * Edge 는 **요청당 CPU 2초**다. 현행처럼 9단계를 한 번에 돌 수 없다. 작업 1건씩 처리하고,
 * 남은 일은 큐에 다시 넣는다. 작업 단위는 실측으로 정했다 — vision 래스터화가 붙는 PDF
 * 페이지는 최악 443ms 라 **요청당 1~2 페이지**, 텍스트만이면 10 페이지.
 *
 * ## 아직 핸들러가 없다 (의도적)
 * `extract` 를 페이지 단위로 쪼개려면 **중간 산출물을 어딘가 둬야 한다.** 청킹이
 * `_merge_short_sections` 로 **인접 섹션을 병합**하기 때문에(원본 `chunk.py:96`), 페이지별로
 * 따로 청킹하면 경계에서 병합이 안 일어나 청크가 달라진다. 즉 "페이지 추출 → 전부 모아
 * 청킹" 이어야 하고, 그 중간 텍스트를 보관할 자리가 필요하다. 그 설계 전에 핸들러를 쓰면
 * 되돌리게 된다.
 *
 * 그래서 이 골격은 **큐 왕복·재시도·상태 전이만** 책임진다. 핸들러는 주입받는다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

/** 큐에서 꺼낸 작업 1건. `ingest_queue_read` 의 반환 모양과 같다. */
export interface QueueMessage {
  msg_id: number;
  read_ct: number;
  enqueued_at: string;
  vt: string;
  message: TaskPayload;
}

/** 큐에 싣는 페이로드. **상태는 `ingest_jobs` 가 갖고, 메시지는 "무엇을 할지"만 담는다.** */
export interface TaskPayload {
  job_id: string;
  doc_id: string;
  stage: string;
  /** 페이지·청크 범위 시작 (해당 stage 만 쓴다). */
  from?: number;
  /** 처리 개수. CPU 예산으로 정한다. */
  count?: number;
}

/**
 * 재시도 한도. `read_ct` 가 이 값을 넘으면 더 돌리지 않고 보관한다.
 *
 * 3 인 근거는 약하다 — 현행 `ingest_jobs.attempts` 가 전부 1 이라 참고할 실적이 없다.
 * 큐가 돌기 시작하면 실패 사유별 분포를 보고 조정한다. **지금 값은 잠정이다.**
 */
export const MAX_ATTEMPTS = 3;

/** 작업을 처리한다. 던지면 재시도 대상이 된다. */
export type TaskHandler = (task: TaskPayload, msg: QueueMessage) => Promise<void>;

export interface WorkerDeps {
  client: SupabaseClient;
  /** stage 이름 → 핸들러. 없는 stage 는 **독약 메시지**로 보고 즉시 보관한다. */
  handlers: Record<string, TaskHandler>;
  /** visibility timeout(초). 처리 중 죽어도 이 시간 뒤 다시 보인다. */
  vtSeconds?: number;
  /** 한 번에 꺼낼 작업 수. */
  batch?: number;
  now?: () => number;
}

export interface DrainResult {
  read: number;
  ok: number;
  retried: number;
  archived: number;
  errors: { msg_id: number; stage: string; error: string }[];
}

/**
 * Edge wall clock 은 백그라운드 400s 다. vt 를 그보다 **크게** 잡아야, 처리 중 함수가
 * 죽었을 때 같은 작업이 두 번 도는 구간이 안 생긴다.
 */
const DEFAULT_VT_SECONDS = 600;
const DEFAULT_BATCH = 1;

/** 잡 상태를 갱신한다. 실패해도 드레인은 멈추지 않는다 — 큐 정리가 우선이다. */
async function touchJob(
  client: SupabaseClient,
  jobId: string,
  patch: Record<string, unknown>,
): Promise<void> {
  try {
    await client.from("ingest_jobs").update(patch).eq("id", jobId);
  } catch (e) {
    console.warn(`ingest_jobs 갱신 실패 (job=${jobId}):`, e);
  }
}

export async function drainOnce(deps: WorkerDeps): Promise<DrainResult> {
  const vt = deps.vtSeconds ?? DEFAULT_VT_SECONDS;
  const qty = deps.batch ?? DEFAULT_BATCH;
  const nowIso = () => new Date((deps.now ?? Date.now)()).toISOString();

  const { data, error } = await deps.client.rpc("ingest_queue_read", {
    vt_seconds: vt,
    qty,
  });
  if (error) throw new Error(`ingest_queue_read 실패: ${error.message}`);

  const msgs = (data ?? []) as QueueMessage[];
  const out: DrainResult = { read: msgs.length, ok: 0, retried: 0, archived: 0, errors: [] };

  for (const msg of msgs) {
    const task = msg.message;
    const stage = task?.stage ?? "(없음)";
    const handler = deps.handlers[stage];

    // 처리할 수 없는 메시지를 큐에 남기면 vt 마다 되살아나 영원히 돈다.
    if (!handler) {
      await deps.client.rpc("ingest_queue_archive", { message_id: msg.msg_id });
      out.archived++;
      out.errors.push({ msg_id: msg.msg_id, stage, error: "핸들러 없음 — 보관" });
      if (task?.job_id) {
        await touchJob(deps.client, task.job_id, {
          status: "failed",
          error_msg: `처리기 없는 stage: ${stage}`,
          finished_at: nowIso(),
        });
      }
      continue;
    }

    // `read_ct` 는 pgmq 가 세는 **배달 횟수**다. 한도를 넘으면 지우지 않고 보관한다 —
    // 사후 분석이 가능해야 한다.
    if (msg.read_ct > MAX_ATTEMPTS) {
      await deps.client.rpc("ingest_queue_archive", { message_id: msg.msg_id });
      out.archived++;
      out.errors.push({ msg_id: msg.msg_id, stage, error: `재시도 ${msg.read_ct}회 초과 — 보관` });
      await touchJob(deps.client, task.job_id, {
        status: "failed",
        error_msg: `${stage} 재시도 한도 초과 (${msg.read_ct}회)`,
        finished_at: nowIso(),
      });
      continue;
    }

    try {
      await touchJob(deps.client, task.job_id, {
        status: "running",
        current_stage: stage,
        last_heartbeat_at: nowIso(),
      });
      await handler(task, msg);
      await deps.client.rpc("ingest_queue_delete", { message_id: msg.msg_id });
      out.ok++;
    } catch (e) {
      // **지우지 않는다.** vt 가 지나면 다시 보이고, read_ct 가 올라간다.
      out.retried++;
      const detail = e instanceof Error ? e.message : String(e);
      out.errors.push({ msg_id: msg.msg_id, stage, error: detail });
      console.error(`작업 실패 (stage=${stage}, msg=${msg.msg_id}):`, e);
      await touchJob(deps.client, task.job_id, {
        error_msg: detail.slice(0, 500),
        last_heartbeat_at: nowIso(),
      });
    }
  }
  return out;
}
