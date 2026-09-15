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
 * | `POST /documents/{id}/reingest` | 전체 재인제스트 — chunks 삭제 후 재실행 |
 * | `POST /documents/{id}/reingest-missing` | 증분 — chunks 보존 + 누락 vision 페이지만 |
 * | `POST /ingest/email` | 이메일 첨부 인제스트 (공유 secret 인증) |
 * | `POST /documents/precheck` | sha256 목록 → existing/failed/new (에이전트 중복 회피) |
 *
 * ## 원본에 있었으나 사라진 것
 * `POST /documents/url` — 2026-09-07 폐기. 본문 추출이 `trafilatura`(Python 전용)에
 * 묶여 있었고, 그 채널로 만들어진 문서가 **0 건**이라 옮길 대상이 없었다.
 *
 * ## 읽기에는 인증 게이트를 걸지 않는다
 * 토큰이 없으면 `getCurrentUser` 가 owner 컨텍스트(`isAuthenticated: false`)를 준다 —
 * 수익화 W1 의 "데모 병행" 설계다. 쓰기(`POST`)에만 `requireAuthenticatedUser` 를 건다.
 *
 * ## 인증·상한은 원본 순서 그대로
 * 원본은 `require_authenticated_user` → `check_rate_limit("docs")` 를 **의존성**으로
 * 걸어 본문보다 먼저 돌린다. 무효한 파일이어도 상한 카운터는 올라간다. 그 순서를 지킨다.
 *
 * ## 2026-09-15 — 상한의 내용이 바뀌었다 (스펙 §4 S4)
 * 일일 30건·보유 문서 수 대신 **분당 60건(남용 방지)** + **플랜 저장 용량**이다. 용량은
 * 본문 파싱 뒤라야 크기를 알 수 있어 `persist.ts` 안(`checkStorage`)에서 402 를 낸다.
 */

import { loadSettings } from "../_shared/config.ts";
import { applyCorsHeaders, preflightResponse } from "../_shared/cors.ts";
import { createServiceClient } from "../_shared/db.ts";
import { getCurrentUser, requireAuthenticatedUser, touchDeviceUser } from "../_shared/current_user.ts";
import { deviceScopeAllows } from "../_shared/device_token.ts";
import { jsonResponse, methodNotAllowed, notFound, toResponse } from "../_shared/errors.ts";
import { enforceUploadBurst, RateLimitError } from "../_shared/rate_limit.ts";
import { handleUpload } from "../_shared/documents/upload.ts";
import { parsePrecheckBody, precheckHashes } from "../_shared/documents/precheck.ts";
import {
  getDocument,
  getDocumentStatus,
  listDocuments,
  parseBoolParam,
  type ValidationItem,
} from "../_shared/documents/read.ts";
import { batchStatus, listActiveDocuments } from "../_shared/documents/active.ts";
import { reingestDocument, reingestMissingVision } from "../_shared/documents/reingest.ts";
import { handleEmailWebhook } from "../_shared/ingest/email_route.ts";
import { makeStorageCheck } from "../_shared/me/quota.ts";

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
    const caller = await getCurrentUser(req, settings, { deviceClient: client });
    // 기기 토큰은 화이트리스트 라우트만. 나머지는 403 — 새어도 읽기·삭제가 안 된다.
    if (caller.authKind === "device") {
      if (!deviceScopeAllows(caller.scopes, req.method, path)) {
        return applyCorsHeaders(
          req,
          // `code` 는 상태코드를 못 읽는 클라이언트(iOS 단축어)가 401 과 구분하는 유일한 단서다.
          jsonResponse({ detail: "기기 토큰의 권한 범위를 벗어난 요청입니다.", code: "scope" }, 403),
          settings,
        );
      }
      // 게이트를 통과한 요청만 "사용"으로 친다. 분당 1회로 throttle 되어 있다.
      await touchDeviceUser(client, caller);
    }

    // ---- 이메일 인제스트 webhook ----
    // JWT 가 아니라 공유 secret 으로 인증한다 — 발신자가 Cloudflare Email Worker 다.
    // `/documents` 아래가 아니지만 같은 함수에 둔다(문서를 만드는 경로이고,
    // 함수를 하나 더 만들면 배포 단위만 늘어난다).
    if (path === "/ingest/email") {
      if (req.method !== "POST") return methodNotAllowed();
      const r = await handleEmailWebhook(
        { client, bucket: settings.supabaseStorageBucket, settings },
        req,
      );
      return applyCorsHeaders(req, jsonResponse(r.body, r.status), settings);
    }

    // ---- 재인제스트 2종 — **GET 블록보다 먼저** 본다 ----
    // 경로를 메서드보다 먼저 매칭해야 `GET /documents/{id}/reingest` 가 404 가 아니라
    // 405 로 나간다. FastAPI 도 그렇게 동작한다(§Edge 이관 — 라우팅이 인증보다 먼저).
    const re = path.match(/^\/documents\/([^/]+)\/(reingest|reingest-missing)$/);
    if (re) {
      if (req.method !== "POST") return methodNotAllowed();
      // 쓰기 = 로그인 필수. 남의 문서면 아래에서 404 다(존재 위장).
      const writer = requireAuthenticatedUser(caller);
      const rdeps = { client, bucket: settings.supabaseStorageBucket };
      const docId = decodeURIComponent(re[1]);
      // 원본은 reingest 에 일일 상한을 안 건다 — 업로드(`POST /documents`)에만 있다.
      const r = re[2] === "reingest"
        ? await reingestDocument(rdeps, writer.userId, docId, url.searchParams)
        : await reingestMissingVision(rdeps, writer.userId, docId, url.searchParams);
      return applyCorsHeaders(req, jsonResponse(r.body, r.status), settings);
    }

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

    // ---- precheck — 업로드와 같은 게이트(로그인 필수), 본문은 JSON ----
    // **GET 블록 뒤, POST 업로드 앞**이다. 뒤로 가면 `path !== "/documents"` 에 걸려 404 다.
    // 일일 상한은 걸지 않는다 — 파일을 만들지 않는 조회이고, 상한에 걸린 사용자도
    // "이미 있다"는 답은 받아야 에이전트 원장이 정리된다.
    if (path === "/documents/precheck") {
      if (req.method !== "POST") return methodNotAllowed();
      const user = requireAuthenticatedUser(caller);
      let body: unknown = null;
      try {
        body = await req.json();
      } catch {
        return applyCorsHeaders(
          req,
          jsonResponse({ detail: "JSON 본문이 필요합니다." }, 422),
          settings,
        );
      }
      const parsed = parsePrecheckBody(body);
      if (!parsed.ok) {
        return applyCorsHeaders(req, jsonResponse({ detail: parsed.detail }, 422), settings);
      }
      const results = await precheckHashes(client, user.userId, parsed.hashes);
      return applyCorsHeaders(req, jsonResponse({ results }), settings);
    }

    if (path !== "/documents" && path !== "/") return notFound();
    if (req.method !== "POST") return methodNotAllowed();

    // ① 인증 — 쓰기는 로그인 필수(수익화 W1).
    const user = requireAuthenticatedUser(caller);

    // ② 남용 방지 — 분당 60건. 플랜 quota 는 용량으로 persist 안에서 본다(S4).
    // **본문 검증보다 먼저**다 — 원본이 의존성으로 걸어 뒀다.
    try {
      await enforceUploadBurst(user, { client });
    } catch (e) {
      if (e instanceof RateLimitError) {
        return applyCorsHeaders(
          req,
          jsonResponse({ detail: e.detail, code: e.code }, e.status),
          settings,
        );
      }
      throw e;
    }

    let form: FormData;
    try {
      form = await req.formData();
    } catch {
      return applyCorsHeaders(
        req,
        jsonResponse({ detail: "multipart/form-data 본문이 필요합니다.", code: "form" }, 422),
        settings,
      );
    }

    const result = await handleUpload(form, {
      client,
      bucket: settings.supabaseStorageBucket,
      userId: user.userId,
      checkStorage: makeStorageCheck(client, user, settings),
    });
    return applyCorsHeaders(req, jsonResponse(result.body, result.status), settings);
  } catch (e) {
    return applyCorsHeaders(req, toResponse(e), settings);
  }
});
