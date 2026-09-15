/**
 * 월 Vision 페이지 게이트 — Vision 을 부를 수 있는 단계 앞에서 사용자의 이번 달 페이지 수를 본다.
 *
 * 한 곳(워커)에서만 판정한다. 핸들러 넷(scan·vision·vision_missing·extract 의 image/pptx)에
 * 각자 넣으면 네 벌이 된다. 초과면 잡을 `deferred_quota` 로 보류하고 페이로드를 남긴다 —
 * 실패가 아니다. 매일 00:00 KST cron(`vision_quota_release`)이 되돌리고, 여전히 초과면 다시 보류된다.
 *
 * 기존 USD 예산 가드(`budget_guard.ts`)는 **전역** 비용 방어라 그대로 둔다. 이건 **사용자별** 계량이다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

import { type PlanLimits, quotaActiveFor, type QuotaSettings, visionPagesUsedMonth } from "../me/quota.ts";
import type { TaskPayload } from "./worker.ts";

export const VISION_STAGES = new Set(["scan", "vision", "vision_missing", "extract"]);
const EXTRACT_VISION_DOC_TYPES = new Set(["image", "pptx"]);

export type GateResult = { defer: false } | { defer: true; reason: string };
export type QuotaGate = (task: TaskPayload) => Promise<GateResult>;

export interface QuotaGateDeps {
  client: SupabaseClient;
  settings: QuotaSettings;
  getPlan: (userId: string) => Promise<PlanLimits | null>;
  nowMs?: () => number;
}

export function makeQuotaGate(deps: QuotaGateDeps): QuotaGate {
  const now = deps.nowMs ?? (() => Date.now());
  return async (task) => {
    if (!VISION_STAGES.has(task.stage)) return { defer: false };
    // extract 는 첫 창에서만 본다. 중간 창을 막으면 산출물이 반만 남는다.
    if (task.stage === "extract" && (task.from ?? 0) > 0) return { defer: false };

    const { data, error } = await deps.client
      .from("documents")
      .select("user_id, doc_type")
      .eq("id", task.doc_id)
      .limit(1);
    if (error) {
      console.warn(`quota_gate documents 조회 실패 — 통과 (doc=${task.doc_id}): ${error.message}`);
      return { defer: false };
    }
    const doc = (data ?? [])[0] as { user_id: string; doc_type: string } | undefined;
    if (!doc) return { defer: false };
    if (task.stage === "extract" && !EXTRACT_VISION_DOC_TYPES.has(doc.doc_type)) {
      return { defer: false };
    }

    const user = { userId: doc.user_id, isAuthenticated: true };
    if (!quotaActiveFor(user, deps.settings)) return { defer: false };
    const plan = await deps.getPlan(doc.user_id);
    if (plan === null || plan.vision_pages_per_month <= 0) return { defer: false };
    const used = await visionPagesUsedMonth(deps.client, doc.user_id, now());
    if (used === null) return { defer: false };
    if (used >= plan.vision_pages_per_month) {
      return {
        defer: true,
        reason:
          `${plan.code} 플랜의 이번 달 Vision 페이지 한도 도달 (${used}/${plan.vision_pages_per_month}) — 다음 달에 이어서 처리`,
      };
    }
    return { defer: false };
  };
}
