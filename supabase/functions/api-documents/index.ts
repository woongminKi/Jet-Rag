/**
 * `api-documents` — 업로드 + 읽기 3종.
 *
 * | 라우트 | 상태 |
 * |---|---|
 * | `POST /documents` | 업로드 — 인제스트 사슬의 입구 |
 * | `GET /documents` | 목록 |
 * | `GET /documents/{id}` | 상세 |
 * | `GET /documents/{id}/status` | 잡 상태(+`include_logs`) |
 *
 * | `GET /documents/active` | 진행 중·실패 문서 (프런트 폴러) |
 * | `GET /documents/batch-status` | 여러 문서 상태 일괄 |
 *
 * ## 아직 Railway 인 것
 * `POST /documents/url` · `reingest` 2 종 (쓰기 3 개).
 *
 * ## 읽기에는 인증 게이트를 걸지 않는다
 * 토큰이 없으면 `getCurrentUser` 가 owner 컨텍스트(`isAuthenticated: false`)를 준다 —
 * 수익화 W1 의 "데모 병행" 설계다. 쓰기(`POST`)에만 `requireAuthenticatedUser` 를 건다.
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
import {
  getDocument,
  getDocumentStatus,
  listDocuments,
  parseBoolParam,
  type ValidationItem,
} from "../_shared/documents/read.ts";
import { batchStatus, listActiveDocuments } from "../_shared/documents/active.ts";

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
    const url = new URL(req.url);
    const client = createServiceClient(settings);
    // 읽기·쓰기 공통으로 호출자를 먼저 정한다. 토큰이 없으면 owner 컨텍스트다.
    const caller = await getCurrentUser(req, settings);

    // ---- 읽기 3종 — 인증 게이트 없음(데모 병행) ----
    if (req.method === "GET") {
      if (path === "/documents" || path === "/") {
        const r = await listDocuments(client, caller.userId, url.searchParams);
        return applyCorsHeaders(req, jsonResponse(r.body, r.status), settings);
      }
      // **`/active` 와 `/batch-status` 를 `{doc_id}` 보다 먼저 본다.** 순서가 반대면
      // 상세 라우트가 이 둘을 문서 id 로 오인해 404 를 낸다.
      if (path === "/documents/active") {
        const r = await listActiveDocuments(client, caller.userId, url.searchParams);
        return applyCorsHeaders(req, jsonResponse(r.body, r.status), settings);
      }
      if (path === "/documents/batch-status") {
        const r = await batchStatus(client, caller.userId, url.searchParams);
        return applyCorsHeaders(req, jsonResponse(r.body, r.status), settings);
      }
      const detail = path.match(/^\/documents\/([^/]+)$/);
      if (detail) {
        const r = await getDocument(client, caller.userId, decodeURIComponent(detail[1]));
        return applyCorsHeaders(req, jsonResponse(r.body, r.status), settings);
      }
      const st = path.match(/^\/documents\/([^/]+)\/status$/);
      if (st) {
        // `include_logs` 도 bool 이라 무효값은 422 다 — 오류를 버리면 안 된다.
        const errors: ValidationItem[] = [];
        const includeLogs = parseBoolParam(
          url.searchParams.get("include_logs"),
          "include_logs",
          errors,
        );
        if (errors.length > 0) {
          return applyCorsHeaders(req, jsonResponse({ detail: errors }, 422), settings);
        }
        const r = await getDocumentStatus(
          client,
          caller.userId,
          decodeURIComponent(st[1]),
          includeLogs,
        );
        return applyCorsHeaders(req, jsonResponse(r.body, r.status), settings);
      }
      return notFound();
    }

    if (path !== "/documents" && path !== "/") return notFound();
    if (req.method !== "POST") return methodNotAllowed();

    // ① 인증 — 쓰기는 로그인 필수(수익화 W1).
    const user = requireAuthenticatedUser(caller);

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
