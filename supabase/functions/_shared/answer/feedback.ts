/**
 * `POST /answer/feedback` — 답변 피드백 저장. `answer.py` 의 `submit_answer_feedback` 포팅.
 *
 * ## 모듈 전역 상태 하나는 재현할 수 없다
 * 원본은 insert 가 **처음 실패하면** `_feedback_disabled = True` 로 두고, 그 프로세스가
 * 사는 동안 DB 를 아예 안 건드린다. Edge 아이솔레이트는 요청 간 상태가 보장되지 않아
 * 그 래치를 재현할 수 없다 — `/stats` 의 링 버퍼·`vision_metrics` 카운터와 같은 문제다.
 *
 * 관측 가능한 차이는 **실패했을 때의 `note` 문구뿐**이다:
 *
 * | 상황 | 원본 | Edge |
 * |---|---|---|
 * | 첫 실패 | `피드백 저장 일시 실패 — 마이그 011 미적용 가능` | 같음 |
 * | 두 번째부터 | `answer_feedback 테이블 미존재 — 마이그 011 적용 필요` | **첫 실패와 같은 문구** |
 *
 * 운영에서 `answer_feedback` 테이블은 존재하고(2026-09-06 실측: 조회 정상) insert 가
 * 성공하므로 **이 분기는 도달하지 않는다.** 테이블이 사라지는 상황이면 어차피 운영자가
 * 개입해야 한다. 재현하려고 DB 에 래치를 만드는 건 원본에 없는 상태를 새로 만드는 것이라
 * 하지 않았다.
 *
 * ## bool 강제 변환 규칙 (pydantic v2 lax, 실측)
 * 문자열은 **트림 없이** 정확 매칭한다 — `"  yes  "` 는 오류다.
 * `true/t/yes/y/on/1` → true, `false/f/no/n/off/0` → false (대소문자 무관).
 * 정수·실수는 0/1 만 받고, `null`·배열·객체는 **다른 오류 타입**(`bool_type`)이다.
 * `helpful: "yes"` 를 무효 입력이라 짐작했다가 200 이 나와 운영 DB 에 행이 쓰였다 —
 * 그 뒤로 이런 규칙은 HTTP 를 치기 전에 pydantic 으로 먼저 확인한다.
 *
 * ## JSON 파싱만 인증보다 먼저다 (실측)
 * 이 라우터는 `dependencies=[Depends(require_authenticated_user)]` 로 인증을 걸지만,
 * **본문이 JSON 으로 파싱조차 안 되면 인증 전에 422** 가 나간다. 파싱이 되면 그 뒤는
 * 인증이 먼저다.
 *
 * | 비인증 요청 본문 | 응답 |
 * |---|---|
 * | `nope` (깨진 JSON) | **422** `json_invalid` |
 * | 빈 본문 · `null` · `[1]` · `"x"` · `{}` · 필드 타입오류 | **401** |
 *
 * `/search/eval-precision` 도 같다. 그래서 파서를 `parseFeedbackJson`(인증 전) 과
 * `validateFeedbackModel`(인증 후) 둘로 나눠 뒀다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

export interface FeedbackPayload {
  query: string;
  answer_text: string;
  helpful: boolean;
  comment: string | null;
  doc_id: string | null;
  sources_count: number;
  model: string | null;
}

export type FeedbackParseResult =
  | { ok: true; payload: FeedbackPayload }
  | { ok: false; detail: unknown[] };

const BOOL_TRUE = new Set(["true", "t", "yes", "y", "on", "1"]);
const BOOL_FALSE = new Set(["false", "f", "no", "n", "off", "0"]);

/** pydantic v2 lax bool. `null`/배열은 `bool_type`, 값이 안 맞으면 `bool_parsing`. */
export function pydanticBool(
  v: unknown,
): { ok: true; value: boolean } | { ok: false; kind: "parsing" | "type" } {
  if (typeof v === "boolean") return { ok: true, value: v };
  if (typeof v === "string") {
    // **트림하지 않는다** — `"  yes  "` 는 원본에서 오류다.
    const s = v.toLowerCase();
    if (BOOL_TRUE.has(s)) return { ok: true, value: true };
    if (BOOL_FALSE.has(s)) return { ok: true, value: false };
    return { ok: false, kind: "parsing" };
  }
  if (typeof v === "number") {
    if (v === 0) return { ok: true, value: false };
    if (v === 1) return { ok: true, value: true };
    // **정수와 실수의 오류 타입이 다르다** — `2` 는 `bool_parsing`, `1.5` 는 `bool_type`.
    // 대조에서 잡혔다(처음엔 둘 다 parsing 으로 뒀다).
    return { ok: false, kind: Number.isInteger(v) ? "parsing" : "type" };
  }
  return { ok: false, kind: "type" };
}

/** pydantic v2 문자열 → int (쿼리 경로와 같은 규칙). */
function pydanticIntValue(
  v: unknown,
): { ok: true; value: number } | { ok: false; kind: "parsing" | "type" } {
  if (typeof v === "number") {
    return Number.isInteger(v) ? { ok: true, value: v } : { ok: false, kind: "parsing" };
  }
  if (typeof v === "string") {
    const s = v.trim();
    if (/^[+-]?\d+$/.test(s)) return { ok: true, value: Number(s) };
    if (/^[+-]?(\d+\.\d*|\.\d+)$/.test(s)) {
      const f = Number(s);
      if (Number.isInteger(f)) return { ok: true, value: f };
    }
    return { ok: false, kind: "parsing" };
  }
  return { ok: false, kind: "type" };
}

function missing(field: string, input: unknown) {
  return { type: "missing", loc: ["body", field], msg: "Field required", input };
}

function stringType(field: string, input: unknown) {
  return {
    type: "string_type",
    loc: ["body", field],
    msg: "Input should be a valid string",
    input,
  };
}

/**
 * **인증 전** 단계 — 본문을 JSON 으로 읽기만 한다.
 * 빈 본문은 오류가 아니다(`undefined` 로 통과시키고 인증 뒤에 `missing` 이 된다).
 */
export function parseFeedbackJson(
  raw: string,
): { ok: true; body: unknown } | { ok: false; detail: unknown[] } {
  if (raw === "") return { ok: true, body: undefined };
  try {
    return { ok: true, body: JSON.parse(raw) };
  } catch (e) {
    const m = /position (\d+)/.exec(String((e as Error).message));
    return {
      ok: false,
      detail: [{
        type: "json_invalid",
        loc: ["body", m ? Number(m[1]) : 0],
        msg: "JSON decode error",
        input: {},
        ctx: { error: "Expecting value" },
      }],
    };
  }
}

/**
 * **인증 후** 단계 — 모델 검증. **선언 순서가 곧 오류 배열 순서다** —
 * query → answer_text → helpful → comment → doc_id → sources_count → model.
 */
export function validateFeedbackModel(body: unknown): FeedbackParseResult {
  if (body === undefined || body === null) {
    return {
      ok: false,
      detail: [{ type: "missing", loc: ["body"], msg: "Field required", input: null }],
    };
  }
  if (typeof body !== "object" || Array.isArray(body)) {
    return {
      ok: false,
      detail: [{
        type: "model_attributes_type",
        loc: ["body"],
        msg: "Input should be a valid dictionary or object to extract fields from",
        input: body,
      }],
    };
  }

  const obj = body as Record<string, unknown>;
  const errors: unknown[] = [];

  const reqStr = (field: string): string => {
    if (!(field in obj)) {
      errors.push(missing(field, obj));
      return "";
    }
    if (typeof obj[field] !== "string") {
      errors.push(stringType(field, obj[field]));
      return "";
    }
    return obj[field] as string;
  };
  const optStr = (field: string): string | null => {
    if (!(field in obj) || obj[field] === null) return null;
    if (typeof obj[field] !== "string") {
      errors.push(stringType(field, obj[field]));
      return null;
    }
    return obj[field] as string;
  };

  const query = reqStr("query");
  const answerText = reqStr("answer_text");

  let helpful = false;
  if (!("helpful" in obj)) {
    errors.push(missing("helpful", obj));
  } else {
    const b = pydanticBool(obj.helpful);
    if (!b.ok) {
      errors.push(
        b.kind === "parsing"
          ? {
            type: "bool_parsing",
            loc: ["body", "helpful"],
            msg: "Input should be a valid boolean, unable to interpret input",
            input: obj.helpful,
          }
          : {
            type: "bool_type",
            loc: ["body", "helpful"],
            msg: "Input should be a valid boolean",
            input: obj.helpful,
          },
      );
    } else {
      helpful = b.value;
    }
  }

  const comment = optStr("comment");
  const docId = optStr("doc_id");

  let sourcesCount = 0;
  if ("sources_count" in obj) {
    const n = pydanticIntValue(obj.sources_count);
    if (!n.ok) {
      errors.push(
        n.kind === "parsing"
          ? {
            type: "int_parsing",
            loc: ["body", "sources_count"],
            msg: "Input should be a valid integer, unable to parse string as an integer",
            input: obj.sources_count,
          }
          : {
            type: "int_type",
            loc: ["body", "sources_count"],
            msg: "Input should be a valid integer",
            input: obj.sources_count,
          },
      );
    } else {
      sourcesCount = n.value;
    }
  }

  const model = optStr("model");

  if (errors.length) return { ok: false, detail: errors };
  return {
    ok: true,
    payload: {
      query,
      answer_text: answerText,
      helpful,
      comment,
      doc_id: docId,
      sources_count: sourcesCount,
      model,
    },
  };
}

/** insert 행. **실행과 분리해 둔다** — 대조는 이걸 보고 실제로 쓰지 않는다. */
export function buildFeedbackRow(
  payload: FeedbackPayload,
  userId: string,
): Record<string, unknown> {
  return {
    user_id: userId,
    doc_id: payload.doc_id,
    query: payload.query,
    answer_text: payload.answer_text,
    helpful: payload.helpful,
    comment: payload.comment,
    sources_count: payload.sources_count,
    model: payload.model,
  };
}

/** insert 질의. 조립을 호출부에 복붙하지 않으려고 함수로 뺐다. */
export function buildFeedbackInsertQuery(
  client: SupabaseClient,
  row: Record<string, unknown>,
): unknown {
  // **의도한 차이 1건** — supabase-py 는 `Prefer: return=representation` 만 보내고
  // 쿼리에 `select` 가 없는데, supabase-js 는 `.select()` 없이는 데이터를 안 돌려주고
  // 붙이면 `select=*` 가 따라온다. 반환 컬럼은 결국 전체로 같고(호출부는 `id` 만 읽는다)
  // `.select()` 를 빼면 `feedback_id` 가 늘 null 이 된다. 그래서 붙였다.
  return client.from("answer_feedback").insert(row).select();
}

export interface FeedbackResult {
  feedback_id: number | null;
  skipped: boolean;
  note: string | null;
}

export async function submitFeedback(
  payload: FeedbackPayload,
  userId: string,
  client: SupabaseClient,
): Promise<FeedbackResult> {
  const row = buildFeedbackRow(payload, userId);
  try {
    const { data, error } = await (buildFeedbackInsertQuery(client, row) as Promise<
      { data: unknown; error: unknown }
    >);
    if (error) throw new Error(String((error as { message?: string }).message ?? error));
    const rows = (data ?? []) as Record<string, unknown>[];
    // 원본 `(resp.data or [{}])[0].get("id")` — 비면 id 가 없어 null 이다.
    const id = rows.length ? rows[0].id : undefined;
    return { feedback_id: (id ?? null) as number | null, skipped: false, note: null };
  } catch (e) {
    console.warn("answer_feedback INSERT 실패:", e);
    // §모듈 전역 상태 참조 — 원본은 두 번째부터 다른 문구를 내지만 재현할 수 없다.
    return {
      feedback_id: null,
      skipped: true,
      note: "피드백 저장 일시 실패 — 마이그 011 미적용 가능",
    };
  }
}
