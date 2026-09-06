"""`POST /answer/feedback` 을 Python 원본과 대조.

## insert 는 실행하지 않는다
이 엔드포인트는 `answer_feedback` 에 **행을 쓴다.** 대조하겠다고 유효 본문을 보내면
운영 DB 가 오염된다 — 실제로 `helpful: "yes"` 를 무효 입력이라 짐작했다가 200 이 나와
행 하나가 쓰였고(2026-09-06, 이후 삭제) 그래서 이 스크립트는 **행 + insert 질의 모양만**
본다. `/me` 의 rotate·`/admin` 의 upsert 와 같은 방식이다.

## 순서 계약이 두 단계다
JSON 파싱 실패만 인증보다 먼저 422 고, 그 밖의 본문 오류는 인증이 먼저 401 이다(실측).
그래서 파서도 `parseFeedbackJson`(인증 전) / `validateFeedbackModel`(인증 후) 로 나뉜다.
이 스크립트는 두 단계를 각각 대조하고, HTTP 계층의 순서는 배포 후에 따로 확인한다.

사용:
    api/.venv/bin/python api/scripts/verify_feedback_parity.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SHARED = os.path.join(ROOT, "supabase", "functions", "_shared")
DENO_CONFIG = os.path.join(ROOT, "supabase", "functions", "deno.json")

sys.path.insert(0, os.path.join(ROOT, "api"))

FIXED_USER = "2af8fca5-03ab-421b-94b8-53d4fe9d8046"

# 모델 검증 케이스 — 이름은 fixture 키와 맞춘다(있는 것만 실측과 대조).
MODEL_CASES = [
    ("빈 객체", {}),
    ("helpful 누락", {"query": "q", "answer_text": "a"}),
    ("query 타입오류", {"query": 1, "answer_text": "a", "helpful": True}),
    ("sources_count 타입오류",
     {"query": "q", "answer_text": "a", "helpful": True, "sources_count": "x"}),
    ("POST 인증 + helpful=null", {"query": "q", "answer_text": "a", "helpful": None}),
    ("POST 인증 + helpful=2", {"query": "q", "answer_text": "a", "helpful": 2}),
    ("POST 인증 + comment 타입오류",
     {"query": "q", "answer_text": "a", "helpful": True, "comment": 5}),
    ("배열 본문", [1]),
    # 유효 — **보내지 않고** payload 만 대조한다.
    ("유효 최소", {"query": "q", "answer_text": "a", "helpful": True}),
    ("유효 전체", {"query": "q", "answer_text": "a", "helpful": "yes", "comment": "c",
                "doc_id": "d", "sources_count": "3", "model": "m"}),
    ("유효 helpful=off", {"query": "q", "answer_text": "a", "helpful": "OFF"}),
    ("doc_id null", {"query": "q", "answer_text": "a", "helpful": False, "doc_id": None}),
]

# JSON 파싱 단계 — 인증 전에 판정되는 것만.
JSON_CASES = ["nope", "", "null", "{}", "[1]", '"x"', '{"a":}', "{'a':1}"]

# bool 강제 변환 — HTTP 를 치지 않고 pydantic 과 직접 대조한다.
BOOL_CASES = [True, False, "true", "TRUE", "t", "yes", "y", "on", "1",
              "false", "f", "no", "n", "off", "0", "  yes  ", "aaa", "",
              0, 1, 2, 0.0, 1.0, 1.5, None, [], {}]

RUNNER_TS = f"""
import {{ createClient }} from "@supabase/supabase-js";
import {{
  buildFeedbackInsertQuery, buildFeedbackRow, parseFeedbackJson, pydanticBool,
  validateFeedbackModel,
}} from "file://{SHARED}/answer/feedback.ts";

const input = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const client = createClient(input.env.SUPABASE_URL, input.env.SUPABASE_SERVICE_ROLE_KEY, {{
  auth: {{ persistSession: false }},
}});

const models: Record<string, unknown> = {{}};
for (const [name, body] of input.model_cases) {{
  const r = validateFeedbackModel(body);
  models[name] = r.ok ? {{ ok: true, payload: r.payload }} : {{ ok: false, detail: r.detail }};
}}

const jsons = input.json_cases.map((raw: string) => {{
  const r = parseFeedbackJson(raw);
  return r.ok ? {{ ok: true, body: r.body === undefined ? "<undefined>" : r.body }}
              : {{ ok: false, detail: r.detail }};
}});

const bools = input.bool_cases.map((v: unknown) => {{
  const r = pydanticBool(v);
  return r.ok ? {{ ok: true, value: r.value }} : {{ ok: false, kind: r.kind }};
}});

// insert 는 **실행하지 않는다** — 행과 질의 모양만.
const row = buildFeedbackRow(input.row_payload, input.user_id);
const q = buildFeedbackInsertQuery(client, row);
const query = (q as any).url.searchParams.toString();
const method = (q as any).method ?? "";
const prefer = ((q as any).headers ?? {{}})["Prefer"] ?? "";

console.log(JSON.stringify({{ models, jsons, bools, row, query, method, prefer }}));
"""


def run_deno(payload: dict, timeout: int = 300) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        cf, rf = os.path.join(tmp, "cases.json"), os.path.join(tmp, "runner.ts")
        with open(cf, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        with open(rf, "w", encoding="utf-8") as f:
            f.write(RUNNER_TS)
        proc = subprocess.run(
            ["deno", "run", "--config", DENO_CONFIG, "--allow-all", rf, cf],
            capture_output=True, text=True, timeout=timeout,
        )
    if proc.returncode != 0:
        raise SystemExit(f"deno 실행 실패:\n{proc.stderr[:3000]}")
    return json.loads(proc.stdout)


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(ROOT, ".env"))

    from pydantic import TypeAdapter, ValidationError

    from app.routers.answer import AnswerFeedbackRequest

    fails = 0

    def check(name, py, ts):
        nonlocal fails
        if py != ts:
            fails += 1
            print(f"  MISMATCH {name}")
            print(f"      py={json.dumps(py, ensure_ascii=False, default=str)[:420]}")
            print(f"      ts={json.dumps(ts, ensure_ascii=False, default=str)[:420]}")
        else:
            print(f"  {name:<32} OK")

    row_payload = {
        "query": "질문", "answer_text": "답변", "helpful": True, "comment": None,
        "doc_id": None, "sources_count": 3, "model": "gemini-2.5-flash",
    }

    ts = run_deno({
        "env": {k: os.environ[k] for k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")},
        "user_id": FIXED_USER,
        "model_cases": [[n, b] for n, b in MODEL_CASES],
        "json_cases": JSON_CASES,
        "bool_cases": BOOL_CASES,
        "row_payload": row_payload,
    })

    print("=== 모델 검증 (pydantic 직접) ===")
    with open(os.path.join(HERE, "fixtures", "feedback_422_measured.json"), encoding="utf-8") as f:
        measured = json.load(f)
    matched_fixture = 0
    for name, body in MODEL_CASES:
        try:
            payload = AnswerFeedbackRequest(**body) if isinstance(body, dict) else None
            if payload is None:
                raise TypeError("dict 아님")
            want = {"ok": True, "payload": payload.model_dump()}
        except ValidationError as e:
            want = {"ok": False, "detail": [
                {k: v for k, v in err.items() if k in ("type", "loc", "msg", "input", "ctx")}
                for err in json.loads(e.json())
            ]}
            # pydantic 의 loc 은 필드명만 — FastAPI 가 앞에 "body" 를 붙인다.
            for err in want["detail"]:
                err["loc"] = ["body", *err["loc"]]
        except TypeError:
            want = {"ok": False, "detail": [{
                "type": "model_attributes_type", "loc": ["body"],
                "msg": "Input should be a valid dictionary or object to extract fields from",
                "input": body,
            }]}
        check(name, want, ts["models"][name])
        # 실측 fixture 에 같은 케이스가 있으면 그것과도 대조한다(이중 확인).
        if name in measured and measured[name][0] == 422:
            got = ts["models"][name]
            if got.get("ok") or got["detail"] != measured[name][1]["detail"]:
                fails += 1
                print(f"      **실측 fixture 와 불일치** {name}")
            else:
                matched_fixture += 1
    print(f"  실측 fixture 와 교차 확인 {matched_fixture}건")
    if matched_fixture == 0:
        fails += 1
        print("  케이스 무효 — 실측과 겹치는 케이스가 하나도 없다")

    print()
    print("=== JSON 파싱 단계 (인증 전) ===")
    py_jsons = []
    for raw in JSON_CASES:
        if raw == "":
            py_jsons.append({"ok": True, "body": "<undefined>"})
            continue
        try:
            py_jsons.append({"ok": True, "body": json.loads(raw)})
        except json.JSONDecodeError as e:
            py_jsons.append({"ok": False, "detail": [{
                "type": "json_invalid", "loc": ["body", e.pos], "msg": "JSON decode error",
                "input": {}, "ctx": {"error": e.msg},
            }]})
    # 파싱 성공/실패 여부만 대조한다 — 오프셋·메시지는 Python json 과 JSON.parse 가 다르다.
    check("성공/실패 판정 8건",
          [p["ok"] for p in py_jsons], [t["ok"] for t in ts["jsons"]])
    check("성공 시 파싱 결과",
          [p.get("body") for p in py_jsons if p["ok"]],
          [t.get("body") for t in ts["jsons"] if t["ok"]])
    ok_n = sum(1 for p in py_jsons if p["ok"])
    if ok_n == 0 or ok_n == len(py_jsons):
        fails += 1
        print("  케이스 무효 — 파싱 성공/실패 한쪽만 태워졌다")

    print()
    print("=== bool 강제 변환 ===")
    ta = TypeAdapter(bool)
    py_bools = []
    for v in BOOL_CASES:
        try:
            py_bools.append({"ok": True, "value": ta.validate_python(v)})
        except ValidationError as e:
            t = e.errors()[0]["type"]
            py_bools.append({"ok": False, "kind": "type" if t == "bool_type" else "parsing"})
    check(f"{len(BOOL_CASES)}건", py_bools, ts["bools"])

    print()
    print("=== insert — 실행하지 않고 모양만 ===")
    py_row = {
        "user_id": FIXED_USER,
        "doc_id": row_payload["doc_id"],
        "query": row_payload["query"],
        "answer_text": row_payload["answer_text"],
        "helpful": row_payload["helpful"],
        "comment": row_payload["comment"],
        "sources_count": row_payload["sources_count"],
        "model": row_payload["model"],
    }
    check("insert 행", py_row, ts["row"])
    from postgrest import SyncPostgrestClient
    pg = SyncPostgrestClient("https://example.supabase.co/rest/v1", headers={})
    b = pg.table("answer_feedback").insert(py_row)
    # **의도한 차이** — supabase-py 는 `Prefer: return=representation` 만 보내고 쿼리에
    # `select` 가 없다. supabase-js 는 `.select()` 없이 데이터를 안 주고, 붙이면 `select=*`
    # 가 따라온다. 반환 컬럼은 결국 전체로 같아 호출부(`id` 읽기)에는 영향이 없다.
    py_q, ts_q = str(b.request.params), ts["query"]
    if py_q == ts_q:
        fails += 1
        print("  케이스 무효 — 의도한 차이가 사라졌다. 주석과 코드를 다시 맞출 것")
    elif ts_q != "select=*":
        fails += 1
        print(f"  MISMATCH insert 질의 — 기대 'select=*', 실제 {ts_q!r}")
    else:
        print(f"  insert 질의 (의도한 차이)      OK   py={py_q!r} ts={ts_q!r}")

    print()
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
