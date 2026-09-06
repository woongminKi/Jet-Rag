/**
 * `api-ingest-worker` — 큐 드레인 엔드포인트. **pg_cron 이 pg_net 으로 부른다**(마이그 027).
 *
 * 로직은 `_shared/ingest/worker.ts` 에 있다. 여기서는 인증과 응답만 한다.
 *
 * ## 왜 service_role 만인가
 * 이 엔드포인트는 큐를 소비한다. 아무나 부르면 남의 작업을 가로채거나(vt 동안 안 보인다)
 * 큐를 고갈시킬 수 있다. `ingest_queue_*` 래퍼도 service_role 에게만 EXECUTE 를 줬으므로
 * DB 층에서도 막히지만, **함수 층에서 먼저 끊는다**.
 *
 * ## 핸들러는 아직 비어 있다
 * `extract` 를 페이지 단위로 쪼개려면 중간 산출물 자리가 필요하다(청킹이 인접 섹션을
 * 병합하기 때문 — `worker.ts` 헤더 참조). 그 설계 전에는 등록하지 않는다.
 * 지금 이 함수는 **큐 왕복과 재시도 경로가 실제로 도는지**를 확인하는 용도다.
 */

import { loadSettings } from "../_shared/config.ts";
import { createServiceClient } from "../_shared/db.ts";
import { jsonResponse, methodNotAllowed, notFound, toResponse } from "../_shared/errors.ts";
import { drainOnce, type TaskHandler } from "../_shared/ingest/worker.ts";

const FUNCTION_PREFIX = "/api-ingest-worker";

/** stage → 핸들러. **의도적으로 비어 있다** (위 주석 참조). */
const HANDLERS: Record<string, TaskHandler> = {};

function resolvePath(req: Request): string {
  const forwarded = req.headers.get("X-Forwarded-Path");
  const path = forwarded ?? new URL(req.url).pathname;
  const stripped = !forwarded && path.startsWith(FUNCTION_PREFIX)
    ? path.slice(FUNCTION_PREFIX.length) || "/"
    : path;
  return stripped.length > 1 && stripped.endsWith("/") ? stripped.slice(0, -1) : stripped;
}

/** service_role 판정. 새 형식(`sb_secret_…`)과 구 형식(JWT) 둘 다 온다. */
function isServiceRole(req: Request): boolean {
  const auth = req.headers.get("Authorization")?.replace(/^Bearer\s+/i, "") ?? "";
  if (!auth) return false;
  const expected = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
  if (expected && auth === expected) return true;
  // 구 형식 키는 JWT 라 role 클레임으로 판정한다.
  if (auth.startsWith("eyJ")) {
    try {
      const p = JSON.parse(
        atob(auth.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")),
      );
      return p.role === "service_role";
    } catch {
      return false;
    }
  }
  return false;
}

Deno.serve(async (req: Request) => {
  try {
    if (resolvePath(req) !== "/drain") return notFound();
    if (req.method !== "POST") return methodNotAllowed();
    if (!isServiceRole(req)) return jsonResponse({ detail: "service_role 전용입니다." }, 403);

    const settings = loadSettings();
    const url = new URL(req.url);
    const batch = Number(url.searchParams.get("batch") ?? "1");
    const result = await drainOnce({
      client: createServiceClient(settings),
      handlers: HANDLERS,
      batch: Number.isFinite(batch) && batch > 0 ? Math.min(batch, 10) : 1,
    });
    return jsonResponse(result);
  } catch (e) {
    return toResponse(e);
  }
});
