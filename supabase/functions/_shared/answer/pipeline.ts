/**
 * `GET /answer` 응답 조립 — `answer.py` 의 `answer()` 핸들러 포팅.
 *
 * ## 순서가 계약이다 (전부 실측)
 * ```
 * 라우팅(404·405) → rate_limit dependency(카운터 +1) → 파라미터 검증(422)
 *   → strip+NFC 후 빈 질의면 400 → intent → decomposition → 검색 → LLM
 * ```
 * `/me` 의 "라우팅이 인증보다 먼저"와 층이 다르다. 여기서는 **dependency 가 파라미터
 * 검증보다 먼저**라 무효 요청도 카운터를 올린다(2026-09-06 실측: 무효 10 건 → +10).
 *
 * ## `X-Reranker-Path: disabled` 는 항상 붙는다
 * `/search` 는 단계 2-c 에서 붙여 fast path 응답에는 없었는데, `/answer` 는 핸들러
 * 맨 앞에서 무조건 붙인다. 400·503 응답에도 붙는지는 호출부가 결정한다(원본은 `response`
 * 객체에 미리 써 두므로 **예외로 빠져나가도 남는다**).
 *
 * ## decomposition 은 이식하지 않았다
 * 운영에서 꺼져 있고(실측 `decomposed_subqueries: []`, `cost_usd: 0.0`), 켜면 LLM 을
 * 부르며 캐시까지 딸린다. `unsupported.ts` 의 기존 정책대로 **ENV 가 켜져 있으면 500 으로
 * 명확히 거절**한다 — 조용히 다른 결과를 내는 것보다 낫다.
 * 단, ENV 가 켜져 있어도 `needsDecomposition` 이 false 면 원본도 LLM 을 안 부르므로
 * 그 경우는 그대로 진행한다.
 *
 * ## `0.0` 과 `0` — 맞추지 않는다 (`assemble.ts` 와 같은 정책)
 * pydantic 은 `"decomposition_cost_usd":0.0` · `"router_confidence":1.0` 으로 쓰고
 * `JSON.stringify` 는 `0` · `1` 로 쓴다. 어떤 파서로 읽어도 같은 수라 프론트 동작은 같다.
 * 맞추려면 표준 직렬화를 버려야 하므로 그대로 두고, **대조는 바이트가 아니라 파싱한 값으로** 한다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

import { pyStrip } from "../search/pystr.ts";
import { route } from "../search/intent.ts";
import { buildPgroongaQuery } from "../search/pgroonga.ts";
import { findEnabledUnsupported, unsupportedDetail } from "../search/unsupported.ts";
import { type ChatMessage, complete } from "../llm/gemini.ts";
import { type EnrichedChunk, gatherChunks, type QueryParsedInfo } from "./chunks.ts";
import { buildMessages, LLM_MODEL_FALLBACK, NO_RESULT_ANSWER } from "./prompt.ts";

/** `_LOW_CONFIDENCE_THRESHOLD`. */
export const LOW_CONFIDENCE_THRESHOLD = 0.75;

export class AnswerHttpError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
    readonly headers: Record<string, string> = {},
  ) {
    super(detail);
    this.name = "AnswerHttpError";
  }
}

/** `is_quota_exhausted` 포팅 — 클래스명·429·메시지 3 단계 중 Edge 에서 가능한 것. */
export function isQuotaExhausted(e: unknown): boolean {
  if (e && typeof e === "object") {
    const anyE = e as Record<string, unknown>;
    for (const attr of ["status_code", "code"]) {
      if (anyE[attr] === 429) return true;
    }
  }
  const msg = e instanceof Error ? e.message : String(e ?? "");
  if (!msg) return false;
  const upper = msg.toUpperCase();
  return upper.includes("RESOURCE_EXHAUSTED") || msg.includes("429") || upper.includes("QUOTA");
}

export interface AnswerDeps {
  client: SupabaseClient;
  embedQuery: (q: string) => Promise<number[] | null>;
  llm: { apiKey: string; model: string };
  readEnv: (k: string) => string | undefined;
  now?: () => number;
  /** 테스트 주입 — 실제 LLM 을 부르지 않고 대조하려고 연다. */
  completeFn?: (messages: readonly ChatMessage[]) => Promise<string>;
}

export interface AnswerResult {
  query: string;
  answer: string;
  sources: Record<string, unknown>[];
  has_search_results: boolean;
  model: string;
  took_ms: number;
  query_parsed: QueryParsedInfo;
  meta: Record<string, unknown>;
}

/** `sources` 조립 — `snippet` 은 본문 앞 200 자(코드포인트). */
export function buildSources(chunks: readonly EnrichedChunk[]): Record<string, unknown>[] {
  return chunks.map((c) => ({
    chunk_id: c.chunk_id,
    doc_id: c.doc_id,
    doc_title: c.doc_title,
    chunk_idx: c.chunk_idx,
    page: c.page,
    section_title: c.section_title,
    score: c.score,
    snippet: [...(c.text ?? "")].slice(0, 200).join(""),
  }));
}

export async function buildAnswer(
  params: { q: string; topK: number; docId: string | null },
  userId: string,
  deps: AnswerDeps,
): Promise<AnswerResult> {
  const nowFn = deps.now ?? (() => Date.now());
  const startMs = nowFn();

  // 원본: `unicodedata.normalize("NFC", q.strip())` — strip 이 먼저다.
  const cleanQ = pyStrip(params.q).normalize("NFC");
  if (!cleanQ) {
    throw new AnswerHttpError(400, "질문이 비어있습니다.");
  }

  const decision = route(cleanQ);
  const meta: Record<string, unknown> = {
    low_confidence: (decision?.confidenceScore ?? 0) < LOW_CONFIDENCE_THRESHOLD,
    router_signals: decision ? [...decision.triggeredSignals] : [],
    router_confidence: decision?.confidenceScore ?? 0,
  };

  // decomposition — 원본은 `needs_decomposition` 이 false 면 ENV 를 보지도 않는다.
  if (decision?.needsDecomposition) {
    const on = findEnabledUnsupported(deps.readEnv).filter(
      (e) => e === "JETRAG_PAID_DECOMPOSITION_ENABLED",
    );
    if (on.length) throw new AnswerHttpError(500, unsupportedDetail(on));
  }
  meta.decomposed_subqueries = [];
  meta.decomposition_cost_usd = 0.0;
  meta.decomposition_cached = false;

  const { chunks, queryParsed } = await gatherChunks(
    { query: cleanQ, docId: params.docId, topK: params.topK, userId },
    { client: deps.client, embedQuery: deps.embedQuery, buildPgQuery: buildPgroongaQuery },
  );

  if (chunks.length === 0) {
    // 검색 0 건 → **LLM 을 부르지 않는다.** 모델명은 fallback 상수다.
    return {
      query: cleanQ,
      answer: NO_RESULT_ANSWER,
      sources: [],
      has_search_results: false,
      model: LLM_MODEL_FALLBACK,
      took_ms: Math.trunc(nowFn() - startMs),
      query_parsed: queryParsed,
      meta,
    };
  }

  const messages = buildMessages(cleanQ, chunks);
  let text: string;
  try {
    text = deps.completeFn
      ? await deps.completeFn(messages)
      : await complete(messages, { apiKey: deps.llm.apiKey, model: deps.llm.model }, {
        temperature: 0.2,
      });
  } catch (e) {
    if (isQuotaExhausted(e)) {
      console.warn("answer: Gemini quota 소진 — 503");
      throw new AnswerHttpError(
        503,
        "답변 생성 일시 오류 — 일일 quota 가 소진되었습니다. 잠시 후 다시 시도해주세요.",
        { "Retry-After": "3600" },
      );
    }
    console.error("answer: LLM 호출 실패", e);
    throw new AnswerHttpError(
      503,
      "답변 생성 일시 오류 — 잠시 후 다시 시도해주세요.",
      { "Retry-After": "60" },
    );
  }

  return {
    query: cleanQ,
    // 원본 `llm_text.strip()` — Python strip 이다.
    answer: pyStrip(text),
    sources: buildSources(chunks),
    has_search_results: true,
    model: deps.llm.model,
    took_ms: Math.trunc(nowFn() - startMs),
    query_parsed: queryParsed,
    meta,
  };
}
