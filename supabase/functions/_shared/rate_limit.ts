/**
 * 일일 사용량 게이트 — `services/rate_limit.py` 포팅.
 *
 * `increment_usage_counter` RPC 로 **먼저 +1 한 뒤** 상한을 본다(increment-then-check).
 * 그래서 **검증 실패 요청도 카운터를 올린다** — FastAPI 가 dependency 를 파라미터 검증보다
 * 먼저 실행하기 때문이다. 2026-09-06 실측: `/answer` 에 무효 요청 10 건을 보내니 카운터가
 * 정확히 10 올랐다. `/me` 에서 본 "라우팅이 인증보다 먼저"와는 층이 다르다 —
 * **라우팅 → dependency(여기) → 파라미터 검증 → 핸들러** 순이다.
 *
 * ## 익명 키는 프록시 뒤에서 뭉친다 (이관 이전부터)
 * `X-Forwarded-For` 첫 항목을 키로 쓰는데, Cloudflare Worker 를 거치면 그 값이
 * **PoP 의 IP** 다. 실측: 직접 호출 `ip:121.131.211.110`, 프록시 경유 `ip:104.23.251.88`.
 * 즉 같은 PoP 을 쓰는 익명 사용자들이 하루 상한을 나눠 쓴다. **이관이 만든 문제가 아니라
 * 프록시를 세운 시점부터 그랬고**, 여기서 로직을 원본과 같게 두는 한 이관 전후 동작은 같다.
 * 고치려면 Worker 가 `CF-Connecting-IP` 를 `X-Forwarded-For` 로 넘겨야 한다(별도 결정).
 *
 * ## 전부 fail-open
 * RPC 가 실패하면 통과시킨다. DB 가 흔들릴 때 정상 사용자를 막지 않기 위해서다.
 *
 * ## 2026-09-15 — docs metric 은 없어졌다 (스펙 §4 S4)
 * 업로드의 일일 상한(`docs` 30건)과 보유 문서 수 상한은 자동 수집과 맞지 않아 버렸다.
 * 플랜 한도는 **저장 용량**으로 옮겨 `persist.ts` 안에서 보고(`me/quota.ts` 의
 * `makeStorageCheck`), 여기 남은 건 남용 방지용 `enforceUploadBurst`(분당)뿐이다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

import { getEffectivePlan, quotaActiveFor } from "./me/quota.ts";

export const METRIC_ANSWERS = "answers";

export class RateLimitError extends Error {
  constructor(readonly status: number, readonly detail: string) {
    super(detail);
    this.name = "RateLimitError";
  }
}

/** `_client_ip` — XFF 첫 항목 → 없으면 `"unknown"`. */
export function clientIp(req: Request): string {
  const xff = req.headers.get("X-Forwarded-For");
  if (xff) {
    const first = xff.split(",")[0].trim();
    if (first) return first;
  }
  // 원본은 여기서 `request.client.host` 를 보지만 Edge 에는 대응물이 없다.
  // ASGI 서버가 그 값을 채우는 경로는 프록시 뒤에서는 어차피 XFF 로 덮인다.
  return "unknown";
}

/** 로그인 → `user_id`, 익명 → `ip:<주소>`. */
export function buildUserKey(
  user: { userId: string; isAuthenticated: boolean },
  req: Request,
): string {
  return user.isAuthenticated ? user.userId : `ip:${clientIp(req)}`;
}

/** `YYYY-MM-DD` (UTC) — 원본 `datetime.now(timezone.utc).date()`. */
export function utcPeriodDate(nowMs: number): string {
  return new Date(nowMs).toISOString().slice(0, 10);
}

export interface RateLimitSettings {
  authEnabled: boolean;
  quotaEnforcementEnabled: boolean;
  ownerUserId: string | null;
  rateLimitAnswersPerDay: number;
}

function capForMetric(metric: string, s: RateLimitSettings): number {
  if (metric === METRIC_ANSWERS) return s.rateLimitAnswersPerDay;
  return 0; // 모르는 metric → 무제한
}

export interface EnforceDeps {
  client: SupabaseClient;
  now?: () => number;
}

/**
 * 카운터를 1 올리고 상한을 판정한다. 초과면 `RateLimitError`(402 또는 429).
 *
 * 판정 순서 — ① 플랜 quota(로그인·OWNER 제외, 402) ② abuse cap(익명 포함, 429).
 */
export async function enforceRateLimit(
  metric: string,
  req: Request,
  user: { userId: string; isAuthenticated: boolean },
  settings: RateLimitSettings,
  deps: EnforceDeps,
): Promise<void> {
  if (!settings.authEnabled) return; // 로컬 dev — 원본 동작 보존.

  const abuseCap = capForMetric(metric, settings);
  // 플랜 quota 판정은 `me/quota.ts` 가 단일 소스다 — 용량 검사·워커 게이트와 같은 규칙이어야
  // 한 사용자가 경로마다 다른 대접을 받지 않는다.
  const quotaActive = quotaActiveFor(user, settings);
  if (abuseCap <= 0 && !quotaActive) return; // 완전 무제한.

  const userKey = buildUserKey(user, req);
  const periodDate = utcPeriodDate((deps.now ?? Date.now)());

  let newCount: unknown;
  try {
    const { data, error } = await deps.client.rpc("increment_usage_counter", {
      p_user_key: userKey,
      p_metric: metric,
      p_period_date: periodDate,
    });
    if (error) throw new Error(error.message);
    newCount = data;
  } catch (e) {
    console.warn(`rate_limit RPC 실패 — fail-open (metric=${metric}):`, e);
    return;
  }

  // Python `isinstance(new_count, int)` — 불리언도 int 지만 RPC 가 돌려줄 일은 없다.
  const isInt = typeof newCount === "number" && Number.isInteger(newCount);

  if (quotaActive) {
    const plan = await getEffectivePlan(deps.client, user.userId);
    if (plan !== null) {
      if (
        metric === METRIC_ANSWERS && plan.answers_per_day > 0 && isInt &&
        (newCount as number) > plan.answers_per_day
      ) {
        throw new RateLimitError(
          402,
          `${plan.code} 플랜의 일일 답변 한도(${plan.answers_per_day}회)를 ` +
            "초과했습니다. 내일 다시 이용하시거나 Pro 로 업그레이드해 주세요.",
        );
      }
    }
  }

  if (isInt && abuseCap > 0 && (newCount as number) > abuseCap) {
    throw new RateLimitError(
      429,
      `일일 사용 한도(${abuseCap}회)를 초과했습니다. ` +
        "내일 다시 시도하시거나 Pro 로 업그레이드해 주세요.",
    );
  }
}

export const UPLOAD_BURST_PER_MINUTE = 60;

/** 분 단위 절삭 ISO — `upload_burst.minute`. */
export function minuteFloorIso(nowMs: number): string {
  return new Date(Math.floor(nowMs / 60_000) * 60_000).toISOString();
}

/**
 * 분당 업로드 남용 방지 — 로그인·기기 토큰 공통. 초과면 429. RPC 실패는 fail-open.
 * 플랜 quota 와 무관하게 전원에게 걸린다(owner 포함) — 오작동 에이전트가 무한 루프 돌 때를 위한 것이다.
 */
export async function enforceUploadBurst(
  user: { userId: string },
  deps: EnforceDeps,
): Promise<void> {
  const minute = minuteFloorIso((deps.now ?? Date.now)());
  let n: unknown;
  try {
    const { data, error } = await deps.client.rpc("increment_upload_burst", {
      p_user_key: user.userId,
      p_minute: minute,
    });
    if (error) throw new Error(error.message);
    n = data;
  } catch (e) {
    console.warn("upload_burst RPC 실패 — fail-open:", e);
    return;
  }
  if (typeof n === "number" && n > UPLOAD_BURST_PER_MINUTE) {
    throw new RateLimitError(
      429,
      `분당 업로드 한도(${UPLOAD_BURST_PER_MINUTE}건)를 초과했습니다. 잠시 후 다시 시도해 주세요.`,
    );
  }
}
