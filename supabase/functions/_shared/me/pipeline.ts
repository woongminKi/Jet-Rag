/**
 * `/me/*` 조립 — `routers/me.py` 포팅.
 *
 * ## 이 라우터는 전부 인증 필수다
 * 원본은 라우터 레벨에 `Depends(require_authenticated_user)` 를 걸어 **네 엔드포인트
 * 모두** 익명을 막는다(운영 실측: 401 `{"detail":"로그인이 필요합니다."}`).
 * `/search`·`/stats` 가 익명을 통과시키는 것과 다르다 — 여기를 열어 두면 익명 방문자가
 * owner 컨텍스트로 주소를 발급·회전시킬 수 있다.
 *
 * ## `/me/plan` 의 모양은 2026-09-15 에 바뀌었다 (스펙 §4 S4)
 * 문서 수 상한·보유 문서 수 대신 `storage`·`vision_pages`·`answers` 세 묶음이다.
 * 옛 모양을 기대하는 Python 대조 하네스(`verify_me_quota_parity.py`)는 폐기 대상이다.
 *
 * ## `/me/plan` 만 503 을 낼 수 있다
 * 플랜 조회가 실패하면(=`plans` 에 코드가 없거나 DB 장애) 빈 값을 보내지 않고 503 이다.
 * 나머지 셋은 fail-open 이라 free·none 으로 떨어진다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

import {
  getEffectivePlan,
  getSubscriptionView,
  getTodaysCount,
  kstMonthStartIso,
  storageUsedBytes,
  visionPagesUsedMonth,
} from "./quota.ts";
import { type AddressRow, buildAddress, getOrCreateAddress, rotateAddress } from "./email_ingest.ts";

export class MeHttpError extends Error {
  constructor(readonly status: number, readonly detail: string) {
    super(detail);
    this.name = "MeHttpError";
  }
}

export interface MeDeps {
  client: SupabaseClient;
  /** 이메일 주소 도메인 — `JETRAG_EMAIL_INGEST_DOMAIN`. */
  emailIngestDomain: string;
  now?: () => number;
}

export async function buildPlan(
  userId: string,
  deps: MeDeps,
): Promise<Record<string, unknown>> {
  const nowMs = (deps.now ?? Date.now)();
  const plan = await getEffectivePlan(deps.client, userId);
  if (plan === null) {
    throw new MeHttpError(
      503,
      "플랜 정보를 불러올 수 없습니다. 잠시 후 다시 시도해 주세요.",
    );
  }
  return {
    plan_code: plan.code,
    storage: {
      // 조회 실패는 `null` 인데 0 으로 접는다 — 장애가 "안 쓴 것" 으로 보인다(원본 관행).
      used_bytes: (await storageUsedBytes(deps.client, userId)) ?? 0,
      limit_bytes: plan.storage_bytes_limit,
    },
    vision_pages: {
      used: (await visionPagesUsedMonth(deps.client, userId, nowMs)) ?? 0,
      limit: plan.vision_pages_per_month,
      // 화면이 "이번 달" 의 기준을 말할 수 있어야 한다 — KST 1일 00:00 이다.
      period_start: kstMonthStartIso(nowMs),
    },
    answers: {
      per_day: plan.answers_per_day,
      used_today: await getTodaysCount(deps.client, userId, "answers", nowMs),
    },
  };
}

export async function buildSubscription(
  userId: string,
  deps: MeDeps,
): Promise<Record<string, unknown>> {
  const view = await getSubscriptionView(deps.client, userId);
  return {
    plan_code: view.plan_code,
    status: view.status,
    current_period_end: view.current_period_end,
  };
}

/** 주소 응답 — 플랜을 한 번 더 조회해 `pro` 여부를 붙인다(원본 `_address_response`). */
async function addressResponse(
  row: AddressRow,
  userId: string,
  deps: MeDeps,
): Promise<Record<string, unknown>> {
  const plan = await getEffectivePlan(deps.client, userId);
  return {
    address: buildAddress(row.token, deps.emailIngestDomain),
    pro: plan !== null && plan.code === "pro",
    plan_code: plan !== null ? plan.code : "unknown",
  };
}

/** GET 이지만 주소가 없으면 발급하고 `owner_email` 도 갱신한다 — 원본 그대로다. */
export async function buildEmailIngest(
  userId: string,
  userEmail: string | null,
  deps: MeDeps,
): Promise<Record<string, unknown>> {
  const row = await getOrCreateAddress(deps.client, userId, userEmail);
  return addressResponse(row, userId, deps);
}

/** **파괴적** — 구 주소가 즉시 무효가 된다. */
export async function buildEmailIngestRotate(
  userId: string,
  userEmail: string | null,
  deps: MeDeps,
): Promise<Record<string, unknown>> {
  const row = await rotateAddress(
    deps.client,
    userId,
    userEmail,
    (deps.now ?? Date.now)(),
  );
  return addressResponse(row, userId, deps);
}
