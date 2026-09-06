/**
 * `POST /admin/subscriptions` 본문 검증 — pydantic `SubscriptionUpsertRequest` 재현.
 *
 * 아래 표는 **운영 Railway 에 실제로 무효 본문을 보내 받은 응답**이다(2026-09-06).
 * 검증에서 멈추는 본문만 보냈으므로 DB 에는 아무것도 쓰이지 않았다. 상상해서 쓰면
 * 틀린다 — 예컨대 배열 본문의 `model_attributes_type` 은 문서만 봐서는 안 나온다.
 *
 * | 본문 | type | loc |
 * |---|---|---|
 * | `{}` | `missing` × 2 | `["body","user_id"]`, `["body","plan_code"]` |
 * | `{"user_id":123,...}` | `string_type` | `["body","user_id"]` |
 * | `plan_code:"bad"` | `literal_error` | `["body","plan_code"]` |
 * | `[1,2]` · `"hello"` | `model_attributes_type` | `["body"]` |
 * | `null` · 빈 본문 | `missing` | `["body"]` |
 * | 깨진 JSON | `json_invalid` | `["body", <문자 오프셋>]` |
 *
 * ## 한 곳은 근사다
 * `json_invalid` 의 `ctx.error` 와 `loc[1]` 은 **Python `json` 모듈의 메시지·오프셋**이다.
 * JS `JSON.parse` 의 오류 문구는 다르므로 그대로 옮길 수 없다. 실측한 두 케이스가 모두
 * `"Expecting value"` 였고 오프셋은 실패 지점이었으므로 그 형태로 맞췄지만,
 * **다른 형태로 깨진 JSON 에서는 문구가 갈릴 수 있다**(예: 따옴표 없는 키).
 * 관리자만 호출하는 경로이고 본문이 깨지는 상황 자체가 예외적이라 여기까지만 맞춘다.
 */

import { literalExpectedText } from "../pydantic_errors.ts";
import type { SubscriptionUpsertPayload } from "./pipeline.ts";

const PLAN_CODES = ["free", "pro"] as const;
const STATUSES = ["active", "past_due", "canceled"] as const;

export type ParseResult =
  | { ok: true; payload: SubscriptionUpsertPayload }
  | { ok: false; detail: unknown[] };

function literalError(field: string, input: unknown, allowed: readonly string[]) {
  const expected = literalExpectedText(allowed);
  return {
    type: "literal_error",
    loc: ["body", field],
    msg: `Input should be ${expected}`,
    input,
    ctx: { expected },
  };
}

export async function parseSubscriptionUpsert(req: Request): Promise<ParseResult> {
  const raw = await req.text();

  // 빈 본문과 `null` 은 같은 오류다 — 둘 다 "필드(=본문) 자체가 없다".
  if (raw === "") {
    return {
      ok: false,
      detail: [{ type: "missing", loc: ["body"], msg: "Field required", input: null }],
    };
  }

  let body: unknown;
  try {
    body = JSON.parse(raw);
  } catch (e) {
    // 오프셋은 JS 오류 메시지에서 뽑는다. Python 과 다를 수 있다(§한 곳은 근사다).
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

  if (body === null) {
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

  // **선언 순서가 곧 오류 배열 순서다** — user_id → plan_code → status → period.
  let userId = "";
  if (!("user_id" in obj)) {
    errors.push({ type: "missing", loc: ["body", "user_id"], msg: "Field required", input: obj });
  } else if (typeof obj.user_id !== "string") {
    errors.push({
      type: "string_type",
      loc: ["body", "user_id"],
      msg: "Input should be a valid string",
      input: obj.user_id,
    });
  } else {
    userId = obj.user_id;
  }

  let planCode = "";
  if (!("plan_code" in obj)) {
    errors.push({ type: "missing", loc: ["body", "plan_code"], msg: "Field required", input: obj });
  } else if (!PLAN_CODES.includes(obj.plan_code as typeof PLAN_CODES[number])) {
    errors.push(literalError("plan_code", obj.plan_code, PLAN_CODES));
  } else {
    planCode = obj.plan_code as string;
  }

  // 기본값이 있는 필드 — 키가 없으면 오류가 아니다.
  let status = "active";
  if ("status" in obj) {
    if (!STATUSES.includes(obj.status as typeof STATUSES[number])) {
      errors.push(literalError("status", obj.status, STATUSES));
    } else {
      status = obj.status as string;
    }
  }

  let periodEnd: string | null = null;
  if ("current_period_end" in obj && obj.current_period_end !== null) {
    if (typeof obj.current_period_end !== "string") {
      errors.push({
        type: "string_type",
        loc: ["body", "current_period_end"],
        msg: "Input should be a valid string",
        input: obj.current_period_end,
      });
    } else {
      periodEnd = obj.current_period_end;
    }
  }

  if (errors.length) return { ok: false, detail: errors };
  return {
    ok: true,
    payload: {
      user_id: userId,
      plan_code: planCode,
      status,
      current_period_end: periodEnd,
    },
  };
}
