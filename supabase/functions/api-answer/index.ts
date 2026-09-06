/**
 * `api-answer` — `/answer` Edge Function 의 HTTP 껍데기.
 *
 * `/search` 와 나눈 이유는 함수 하나의 코드·콜드스타트를 키우지 않기 위해서다.
 * 검색 자산(`_shared/search/*`)은 그대로 공유한다.
 *
 * ## 순서가 계약이다 — `/me` 와 다르다
 * ```
 * 경로·메서드(404·405) → rate_limit(카운터 +1) → 파라미터 검증(422) → 핸들러(400·200·503)
 * ```
 * FastAPI 는 dependency 를 파라미터 검증보다 **먼저** 푼다. 2026-09-06 실측: `/answer` 에
 * 무효 요청 10 건을 보내니 `usage_counters` 가 정확히 10 올랐다. 순서를 바꾸면 카운터가
 * 안 오르고, 그건 응답만 봐서는 안 보이는 차이다.
 *
 * ## `X-Reranker-Path: disabled` 는 **200 에만** 붙는다
 * 처음엔 "핸들러 맨 앞에서 무조건 붙는다"고 적었는데 **틀렸다.** 원본은
 * ① 빈 질의 400 을 먼저 던지고 ② 그다음에 `response.headers` 에 쓴다. 게다가
 * `raise HTTPException` 으로 나가면 FastAPI 가 **새 Response 를 만들어** 그 헤더가
 * 실리지 않는다. 그래서 400·503 에도 없다.
 *
 * 배포 후 HTTP 대조에서만 드러났다(원본 400 은 헤더 없음, 초기 구현은 `disabled`).
 * in-process 대조로는 잡을 수 없는 층이다.
 *
 * ## 익명도 통과시킨다
 * 원본 의존성이 `CurrentUserDep = LEGACY_DEFAULT_USER` 라 토큰이 없으면 기본 사용자로
 * 답한다. 무효 토큰만 401 이다.
 */

import { loadSettings, type Settings } from "../_shared/config.ts";
import { createServiceClient } from "../_shared/db.ts";
import { applyCorsHeaders, preflightResponse } from "../_shared/cors.ts";
import { getCurrentUser, requireAuthenticatedUser } from "../_shared/current_user.ts";
import { jsonResponse, methodNotAllowed, notFound, toResponse } from "../_shared/errors.ts";
import { embedQuery, isTransientEmbedError } from "../_shared/search/embed.ts";
import { enforceRateLimit, METRIC_ANSWERS, RateLimitError } from "../_shared/rate_limit.ts";
import { validateAnswerParams } from "../_shared/answer/params.ts";
import { AnswerHttpError, buildAnswer } from "../_shared/answer/pipeline.ts";
import { parseFeedbackJson, submitFeedback, validateFeedbackModel } from "../_shared/answer/feedback.ts";

const FUNCTION_PREFIX = "/api-answer";

/** 성공 응답에만 붙는 헤더. `/answer` 는 reranker 를 안 탄다. */
const RERANKER_HEADER = { "X-Reranker-Path": "disabled" };

function stripTrailingSlash(path: string): string {
  return path.length > 1 && path.endsWith("/") ? path.slice(0, -1) : path;
}

function resolvePath(req: Request): string {
  const forwarded = req.headers.get("X-Forwarded-Path");
  const path = forwarded ?? new URL(req.url).pathname;
  const stripped = !forwarded && path.startsWith(FUNCTION_PREFIX)
    ? path.slice(FUNCTION_PREFIX.length) || "/"
    : path;
  return stripTrailingSlash(stripped);
}

function resolveWaitUntil(): ((p: Promise<unknown>) => void) | undefined {
  const rt = (globalThis as { EdgeRuntime?: { waitUntil?: (p: Promise<unknown>) => void } })
    .EdgeRuntime;
  return typeof rt?.waitUntil === "function" ? rt.waitUntil.bind(rt) : undefined;
}

/**
 * `POST /answer/feedback`.
 *
 * 순서가 **`/answer` 본체와도 다르다** — JSON 파싱 실패만 인증보다 먼저 422 고,
 * 그 밖의 모든 본문 오류는 인증이 먼저다(실측, `feedback.ts` 헤더 참조).
 * rate limit 은 걸리지 않는다(원본에 dependency 가 없다).
 */
async function handleFeedback(req: Request, settings: Settings): Promise<Response> {
  if (req.method !== "POST") return methodNotAllowed();

  // ① JSON 파싱 — **인증 전**이다.
  const parsed = parseFeedbackJson(await req.text());
  if (!parsed.ok) return jsonResponse({ detail: parsed.detail }, 422);

  // ② 인증
  const user = requireAuthenticatedUser(await getCurrentUser(req, settings));

  // ③ 모델 검증
  const model = validateFeedbackModel(parsed.body);
  if (!model.ok) return jsonResponse({ detail: model.detail }, 422);

  const client = createServiceClient(settings);
  return jsonResponse(await submitFeedback(model.payload, user.userId, client));
}

Deno.serve(async (req: Request) => {
  const settings = loadSettings();

  const pre = preflightResponse(req, settings);
  if (pre) return pre;

  let response: Response;
  try {
    const path = resolvePath(req);
    if (path === "/answer/feedback") {
      response = await handleFeedback(req, settings);
    } else if (path !== "/answer") {
      response = notFound();
    } else if (req.method !== "GET") {
      response = methodNotAllowed();
    } else {
      const user = await getCurrentUser(req, settings);
      const client = createServiceClient(settings);

      // ① dependency — **파라미터 검증보다 먼저**다. 무효 요청도 카운터를 올린다.
      try {
        await enforceRateLimit(METRIC_ANSWERS, req, user, settings, { client });
      } catch (e) {
        if (e instanceof RateLimitError) {
          return applyCorsHeaders(req, jsonResponse({ detail: e.detail }, e.status), settings);
        }
        throw e;
      }

      // ② 파라미터 검증 — 여기까지는 `X-Reranker-Path` 가 없다.
      const url = new URL(req.url);
      const validated = validateAnswerParams(url.searchParams);
      if (!validated.ok) {
        response = jsonResponse({ detail: validated.detail }, 422);
      } else {
        // ③ 핸들러 — 성공(200)에만 X-Reranker-Path 가 붙는다. 400·503 에는 없다.
        try {
          const body = await buildAnswer(
            {
              q: validated.params.q,
              topK: validated.params.topK,
              docId: validated.params.docId,
            },
            user.userId,
            {
              client,
              embedQuery: async (q: string) => {
                try {
                  const r = await embedQuery(q, {
                    read: (k) => Deno.env.get(k),
                    client,
                    waitUntil: resolveWaitUntil(),
                  });
                  return r.vector;
                } catch (e) {
                  // 원본과 같은 분기 — transient 면 sparse-only 로 내려가고,
                  // 영구 실패는 503 이다(`검색 일시 오류`).
                  if (isTransientEmbedError(e)) {
                    console.warn("answer: 임베딩 transient → sparse-only fallback:", e);
                    return null;
                  }
                  throw new AnswerHttpError(
                    503,
                    "검색 일시 오류 — 임베딩 서비스에 연결할 수 없습니다.",
                    { "Retry-After": "60" },
                  );
                }
              },
              llm: { apiKey: Deno.env.get("GEMINI_API_KEY") ?? "", model: settings.llmModelAnswer },
              readEnv: (k) => Deno.env.get(k),
            },
          );
          response = jsonResponse(body, 200, RERANKER_HEADER);
        } catch (e) {
          if (e instanceof AnswerHttpError) {
            // **RERANKER_HEADER 를 붙이지 않는다** — 원본은 예외로 나가면 헤더가 사라진다.
            response = jsonResponse({ detail: e.detail }, e.status, e.headers);
          } else {
            throw e;
          }
        }
      }
    }
  } catch (e) {
    response = toResponse(e);
  }

  return applyCorsHeaders(req, response, settings);
});
