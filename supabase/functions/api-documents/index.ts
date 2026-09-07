/**
 * `api-documents` — 업로드 경로. **지금은 `POST /documents` 하나뿐이다.**
 *
 * 이걸 열어야 인제스트 사슬의 입구가 생긴다. 여기까지 오면
 * `업로드 → extract → chunk → load → embed` 가 pg_cron 으로 끝까지 돈다.
 *
 * ## 나머지 `/documents/*` 는 아직 Railway 다
 * 목록·상세·삭제·재인제스트·URL 업로드는 안 옮겼다. 프록시가 `POST /documents` 만
 * 이쪽으로 보내고 나머지는 그대로 Railway 로 간다 — 라우팅 규칙이 메서드까지 봐야 한다.
 *
 * ## 인증·상한은 원본 순서 그대로
 * 원본은 `require_authenticated_user` → `check_rate_limit("docs")` 를 **의존성**으로
 * 걸어 본문보다 먼저 돌린다. 무효한 파일이어도 상한 카운터는 올라간다. 그 순서를 지킨다.
 */

import { loadSettings } from "../_shared/config.ts";
import { applyCorsHeaders, preflightResponse } from "../_shared/cors.ts";
import { createServiceClient } from "../_shared/db.ts";
import { getCurrentUser, requireAuthenticatedUser } from "../_shared/current_user.ts";
import { jsonResponse, methodNotAllowed, notFound, toResponse } from "../_shared/errors.ts";
import { enforceRateLimit, METRIC_DOCS, RateLimitError } from "../_shared/rate_limit.ts";
import { handleUpload } from "../_shared/documents/upload.ts";

const FUNCTION_PREFIX = "/api-documents";

function resolvePath(req: Request): string {
  const forwarded = req.headers.get("X-Forwarded-Path");
  const path = forwarded ?? new URL(req.url).pathname;
  const stripped = !forwarded && path.startsWith(FUNCTION_PREFIX)
    ? path.slice(FUNCTION_PREFIX.length) || "/"
    : path;
  return stripped.length > 1 && stripped.endsWith("/") ? stripped.slice(0, -1) : stripped;
}

Deno.serve(async (req: Request) => {
  const settings = loadSettings();
  try {
    const pre = preflightResponse(req, settings);
    if (pre) return pre;

    const path = resolvePath(req);
    if (path !== "/documents" && path !== "/") return notFound();
    if (req.method !== "POST") return methodNotAllowed();

    // ① 인증 — 쓰기는 로그인 필수(수익화 W1).
    const user = requireAuthenticatedUser(await getCurrentUser(req, settings));
    const client = createServiceClient(settings);

    // ② 일일 상한(수익화 W2). **본문 검증보다 먼저**다 — 원본이 의존성으로 걸어 뒀다.
    try {
      await enforceRateLimit(METRIC_DOCS, req, user, settings, { client });
    } catch (e) {
      if (e instanceof RateLimitError) {
        return applyCorsHeaders(req, jsonResponse({ detail: e.detail }, e.status), settings);
      }
      throw e;
    }

    let form: FormData;
    try {
      form = await req.formData();
    } catch {
      return applyCorsHeaders(
        req,
        jsonResponse({ detail: "multipart/form-data 본문이 필요합니다." }, 422),
        settings,
      );
    }

    const result = await handleUpload(form, {
      client,
      bucket: settings.supabaseStorageBucket,
      userId: user.userId,
    });
    return applyCorsHeaders(req, jsonResponse(result.body, result.status), settings);
  } catch (e) {
    return applyCorsHeaders(req, toResponse(e), settings);
  }
});
