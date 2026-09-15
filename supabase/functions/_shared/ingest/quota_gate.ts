/**
 * 월 Vision 페이지 게이트 — Vision 을 부를 수 있는 단계 앞에서 사용자의 이번 달 페이지 수를 본다.
 *
 * 한 곳(워커)에서만 판정한다. 핸들러 넷(scan·vision·vision_missing·extract 의 image/pptx)에
 * 각자 넣으면 네 벌이 된다. 초과면 잡을 `deferred_quota` 로 보류하고 페이로드를 남긴다 —
 * 실패가 아니다. 매일 00:00 KST cron(`vision_quota_release`)이 되돌리고, 여전히 초과면 다시 보류된다.
 *
 * 기존 USD 예산 가드(`budget_guard.ts`)는 **전역** 비용 방어라 그대로 둔다. 이건 **사용자별** 계량이다.
 *
 * ## 한도는 정확히 지켜지지 않는다 — 태스크 단위로만 본다
 * 검사는 **태스크에 들어가기 전** 1 회다. 태스크 안에서 몇 장을 쓰는지는 보지 않는다.
 * - `scan`·`vision`: 태스크당 `VISION_PAGES_PER_TASK`(4) 페이지라 초과분은 최대 3 장이다.
 * - `extract` 의 **PPTX 는 덱 한 벌이 통째로 한 태스크**다. 100 장짜리를 한도 1 장 남기고
 *   시작하면 99 장을 넘겨 쓴다. 지금은 막지 않는다 — 크기로 미리 거르려면 파일을 열어야
 *   하고(그 자체가 CPU 예산을 먹는다), 실제 비용 폭주는 전역 USD 가드가 잡는다.
 * 즉 여기는 **요금 방어선이 아니라 계량**이다. 정확한 차단이 필요해지면 그때
 * 페이지 단위 차감(선차감 후 정산)으로 바꾼다.
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

interface DocOwner {
  user_id: string;
  doc_type: string;
}

export function makeQuotaGate(deps: QuotaGateDeps): QuotaGate {
  const now = deps.nowMs ?? (() => Date.now());
  /**
   * doc_id → 소유자·타입 캐시.
   *
   * 게이트 인스턴스는 **드레인 요청 1 회당 하나** 만들어지므로 캐시 수명도 cron 한 틱이다.
   * 그 안에서 같은 문서의 태스크가 연달아 도는 게 정상 패턴이라(PDF 한 장씩 vision),
   * 이 캐시 하나가 태스크마다 붙던 select 를 문서당 1 회로 줄인다. 요청이 끝나면 통째로
   * 버려지므로 소유자 변경 같은 건 다음 틱에 반영된다 — 어차피 바뀔 일이 없는 값이다.
   */
  const docCache = new Map<string, Promise<DocOwner | null>>();

  const loadDoc = (docId: string): Promise<DocOwner | null> => {
    const hit = docCache.get(docId);
    if (hit) return hit;
    const p = (async () => {
      const { data, error } = await deps.client
        .from("documents")
        .select("user_id, doc_type")
        .eq("id", docId)
        .limit(1);
      if (error) {
        console.warn(`quota_gate documents 조회 실패 — 통과 (doc=${docId}): ${error.message}`);
        return null;
      }
      return ((data ?? [])[0] as DocOwner | undefined) ?? null;
    })();
    docCache.set(docId, p);
    return p;
  };

  return async (task) => {
    if (!VISION_STAGES.has(task.stage)) return { defer: false };
    // extract 는 첫 창에서만 본다. 중간 창을 막으면 산출물이 반만 남는다.
    if (task.stage === "extract" && (task.from ?? 0) > 0) return { defer: false };

    const doc = await loadDoc(task.doc_id);
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
