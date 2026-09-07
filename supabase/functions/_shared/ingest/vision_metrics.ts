/**
 * `api/app/services/vision_metrics.py` 의 `record_call` 포팅 — `vision_usage_log` 적재.
 *
 * ## in-memory 카운터는 옮기지 않는다
 * 원본은 `_total_calls` 같은 모듈 전역 카운터를 들고 `/stats` 가 그걸 읽는다. 상주
 * 프로세스 전제다. Edge 는 인스턴스 재사용이 보장되지 않아 그 값이 의미가 없고,
 * **이미 `_shared/stats/sources.ts` 가 같은 수치를 `vision_usage_log` 에서 뽑도록
 * 이관돼 있다.** 그러니 여기서는 DB 적재만 하면 된다 — 누락이 아니라 판단이다.
 *
 * ## fire-and-forget 을 못 한다
 * 원본은 `ThreadPoolExecutor` 로 던지고 즉시 반환해 호출자 latency 를 0 으로 만든다.
 * Edge 에는 BackgroundTasks 가 없어 await 해야 한다. 다만 이 insert 는 vision 호출
 * 1~3초 I/O 뒤에 붙는 수십 ms 고, I/O 는 CPU 예산(2초)에 안 잡힌다.
 *
 * ## 실패는 삼킨다
 * 마이그 005/014 미적용이면 insert 가 깨진다. 그래도 인제스트는 계속 간다 —
 * 원본과 같다. 첫 1회만 warn.
 */

import type { SupabaseClient } from "@supabase/supabase-js";
import { pyInt } from "../pynum.ts";
import { pyIsoUtc } from "../pytime.ts";

/** W16 Day 4 #90 — enum 강제. 목록에 없으면 `null` 로 떨어뜨린다. */
const VALID_SOURCE_TYPES = new Set([
  "image",
  "pdf_scan",
  "pptx_rerouting",
  "pptx_augment",
  "pdf_vision_enrich",
  "multimodal_judge",
  "acceptable_judge",
]);

const ERROR_MSG_MAX_LEN_DEFAULT = 200;

/** 마이그 014 컬럼과 1:1. 그 외 usage 키는 무시한다(forward-compat). */
const USAGE_KEYS = [
  "prompt_tokens",
  "image_tokens",
  "output_tokens",
  "thinking_tokens",
  "estimated_cost",
  "model_used",
] as const;

let firstPersistWarnLogged = false;

/** 테스트용 — 첫 warn 플래그 초기화. */
export function resetFirstPersistWarnForTest(): void {
  firstPersistWarnLogged = false;
}

/** 원본 `_error_msg_max_len` — 잘못된 값(음수·비숫자)이면 기본값. */
export function errorMsgMaxLen(env: Record<string, string | undefined>): number {
  const raw = env["JET_RAG_VISION_ERROR_MSG_MAX_LEN"];
  if (raw === undefined) return ERROR_MSG_MAX_LEN_DEFAULT;
  const n = pyInt(raw);
  if (n === null) return ERROR_MSG_MAX_LEN_DEFAULT;
  return n > 0 ? n : ERROR_MSG_MAX_LEN_DEFAULT;
}

/** 원본 `_normalize_source_type` — 무효값은 null + warn. */
export function normalizeSourceType(value: string | null | undefined): string | null {
  if (value === null || value === undefined) return null;
  if (VALID_SOURCE_TYPES.has(value)) return value;
  console.warn(
    `vision_metrics.record_call source_type=${JSON.stringify(value)} 무효 — ` +
      `None 으로 fallback. 허용값: ${[...VALID_SOURCE_TYPES].sort().join(", ")}`,
  );
  return null;
}

/** 원본 `_extract_usage_columns` — usage 가 dict 가 아니면 전 컬럼 null. */
export function extractUsageColumns(
  usage: Record<string, unknown> | null | undefined,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  const ok = usage !== null && usage !== undefined && typeof usage === "object" &&
    !Array.isArray(usage);
  for (const k of USAGE_KEYS) out[k] = ok ? (usage as Record<string, unknown>)[k] ?? null : null;
  return out;
}

export interface RecordCallOpts {
  success: boolean;
  quotaExhausted?: boolean;
  errorMsg?: string | null;
  sourceType?: string | null;
  usage?: Record<string, unknown> | null;
  docId?: string | null;
  page?: number | null;
  retryAttempt?: number | null;
}

/**
 * vision 호출 1건 기록. `called_at` 은 호출자가 넘긴 시각을 쓴다(테스트 결정성).
 *
 * `error_msg` 는 기본 200자에서 자른다 — 원본은 `(error_msg or "")[:n] or None` 이라
 * 빈 문자열이 `null` 이 된다. 그 동작까지 같다.
 *
 * ## 실패 사유를 **돌려준다**
 * 원본은 삼키고 끝이다. 그런데 이 테이블이 비면 `budget_guard` 의 비용 SUM 이 영원히
 * 0 이 되고 **한도가 절대 발동하지 않는다** — 지출이 무제한이 된다는 뜻이다. 그런
 * 고장이 조용하면 안 된다. 인제스트는 원본처럼 계속 진행하되(던지지 않는다), 사유는
 * 호출자에게 넘겨 아티팩트에 남긴다.
 *
 * @returns 실패 사유. 성공이거나 비활성이면 `null`.
 */
export async function recordCall(
  client: SupabaseClient,
  env: Record<string, string | undefined>,
  nowMs: number,
  opts: RecordCallOpts,
): Promise<string | null> {
  if (env["JET_RAG_METRICS_PERSIST_ENABLED"] === "0") return null;

  const truncated = (opts.errorMsg ?? "").slice(0, errorMsgMaxLen(env));
  const row: Record<string, unknown> = {
    called_at: pyIsoUtc(nowMs),
    success: opts.success,
    error_msg: truncated === "" ? null : truncated,
    quota_exhausted: opts.quotaExhausted ?? false,
    source_type: normalizeSourceType(opts.sourceType),
    doc_id: opts.docId ?? null,
    page: opts.page ?? null,
    retry_attempt: opts.retryAttempt ?? null,
    ...extractUsageColumns(opts.usage),
  };
  try {
    const { error } = await client.from("vision_usage_log").insert(row);
    if (error) {
      throw new Error(
        `${error.message}` +
          `${error.details ? ` | details=${error.details}` : ""}` +
          `${error.hint ? ` | hint=${error.hint}` : ""}` +
          `${error.code ? ` | code=${error.code}` : ""}`,
      );
    }
    return null;
  } catch (err) {
    const msg = String(err);
    if (!firstPersistWarnLogged) {
      firstPersistWarnLogged = true;
      console.warn(
        `vision_usage_log insert 실패 (graceful): ${msg} — ` +
          `마이그 005/014 적용 후 자동 회복.`,
      );
    } else {
      console.debug(`vision_usage_log insert 실패: ${msg}`);
    }
    return msg;
  }
}
