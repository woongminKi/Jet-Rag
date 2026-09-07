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
 * ## 지금은 `extract` → `chunk` 까지다
 * extract 는 HWP(통째로)와 PDF(페이지 단위)를 처리하고 다음 작업을 큐에 넣는다.
 * PDF 는 `current_title` 이 문서 전체 sticky 라 **순차**여야 해서, 범위를 한꺼번에
 * 넣지 않고 직전 범위가 끝날 때 다음 하나만 넣는다.
 * chunk 는 extract 산출물을 전부 모아 청크 레코드를 만들고 `CHUNKS_PER_ARTIFACT` 개씩
 * 쪼개 저장한다. load 는 그 part 를 하나씩 `chunks` 테이블에 upsert 하고, 마지막에
 * embed 를 넣는다. embed 는 `dense_vec` 이 NULL 인 청크를 BGE-M3 로 채운다.
 * 거기서 멈춘다 — `tag_summarize` 이후 핸들러가 없어서 넣는 순간 archive + 잡 failed 다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

import { loadSettings } from "../_shared/config.ts";
import { createServiceClient } from "../_shared/db.ts";
import { jsonResponse, methodNotAllowed, notFound, toResponse } from "../_shared/errors.ts";
import { drainLoop, type TaskHandler } from "../_shared/ingest/worker.ts";
import { makeChunkHandler } from "../_shared/ingest/handlers/chunk.ts";
import { makeEmbedHandler } from "../_shared/ingest/handlers/embed.ts";
import { makeExtractHandler } from "../_shared/ingest/handlers/extract.ts";
import { makeLoadHandler } from "../_shared/ingest/handlers/load.ts";
import { makeVisionHandler } from "../_shared/ingest/handlers/vision.ts";

const FUNCTION_PREFIX = "/api-ingest-worker";

/**
 * stage → 핸들러.
 *
 * `extract` · `chunk` · `load` · `embed` 넷이다. 그 뒤(`chunk_filter` · `tag_summarize` …)는
 * 아직 안 옮겼고, **없는 stage 를 큐에 넣으면 즉시 archive + 잡 failed** 가 되므로
 * (`worker.ts` 계약) embed 핸들러는 남은 청크가 없으면 아무것도 enqueue 하지 않는다.
 */
function buildHandlers(
  settings: { supabaseStorageBucket: string },
  client: SupabaseClient,
): Record<string, TaskHandler> {
  return {
    extract: makeExtractHandler({ client, bucket: settings.supabaseStorageBucket }),
    vision: makeVisionHandler({ client, bucket: settings.supabaseStorageBucket }),
    chunk: makeChunkHandler({ client }),
    load: makeLoadHandler({ client }),
    embed: makeEmbedHandler({ client, token: Deno.env.get("DEEPINFRA_API_TOKEN") ?? "" }),
  };
}

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
    const budget = Number(url.searchParams.get("budget") ?? "");
    const client = createServiceClient(settings);
    // 예산이 남는 동안 반복한다. 작업이 순차 의존이라 batch 를 키워도 소용이 없다 —
    // 다음 작업은 직전 작업이 끝나야 큐에 들어간다.
    const result = await drainLoop({
      client,
      handlers: buildHandlers(settings, client),
      batch: Number.isFinite(batch) && batch > 0 ? Math.min(batch, 10) : 1,
      budgetMs: Number.isFinite(budget) && budget > 0 ? Math.min(budget, 20_000) : undefined,
    });
    return jsonResponse(result);
  } catch (e) {
    return toResponse(e);
  }
});
