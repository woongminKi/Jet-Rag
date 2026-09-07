"""Gemini Vision 응답 파싱·단가를 Python 원본과 대조.

## 무엇을 대조하고 무엇을 안 하는가
- `_parse`(응답 JSON → VisionCaption)와 `_estimate_cost` 는 순수 함수라 그대로 대조한다.
- **실제 Gemini 호출은 안 한다.** 비용이 나가고, mock 을 두면 양쪽이 다른 mock 을 보게
  되어 대조의 의미가 없다. 요청 모양(파트 순서·inline_data·generationConfig)은
  단위 테스트로 고정한다.

## 이 파싱이 틀리면 조용히 나빠진다
- `type` 화이트리스트 밖을 "기타" 로 안 바꾸면 chunk metadata 에 엉뚱한 값이 박힌다
- `table_caption` 의 빈 문자열을 `null` 로 안 만들면 검색 보조 인덱스에 빈 행이 쌓인다
- 단가가 틀리면 `vision_usage_log.estimated_cost` 집계가 통째로 어긋난다

사용:
    api/.venv/bin/python api/scripts/verify_vision_caption_parity.py
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

# (설명, 응답 JSON 문자열)
PARSE_CASES: list[tuple[str, str]] = [
    ("정상 전체", json.dumps({
        "type": "표", "ocr_text": "매출 100", "caption": "월별 매출 표",
        "table_caption": "월별 매출", "figure_caption": None,
        "structured": {"headers": ["월", "매출"], "rows": [["1월", "100"]]},
    }, ensure_ascii=False)),
    ("type 화이트리스트 밖", json.dumps({"type": "INVOICE", "ocr_text": "a", "caption": "b"})),
    ("type 없음", json.dumps({"ocr_text": "a", "caption": "b"})),
    ("type null", json.dumps({"type": None, "ocr_text": "a"})),
    ("type 8종 전부", json.dumps({"type": "화이트보드", "ocr_text": "", "caption": ""},
                              ensure_ascii=False)),
    ("type 명함", json.dumps({"type": "명함", "structured": {"name": "홍길동"}},
                           ensure_ascii=False)),
    # --- ocr_text / caption 의 falsy 처리 ---
    ("ocr_text null", json.dumps({"type": "문서", "ocr_text": None}, ensure_ascii=False)),
    ("ocr_text 숫자", json.dumps({"type": "문서", "ocr_text": 123}, ensure_ascii=False)),
    ("ocr_text 0", json.dumps({"type": "문서", "ocr_text": 0}, ensure_ascii=False)),
    ("ocr_text false", json.dumps({"type": "문서", "ocr_text": False}, ensure_ascii=False)),
    ("ocr_text 빈 문자열", json.dumps({"type": "문서", "ocr_text": ""}, ensure_ascii=False)),
    ("ocr_text 배열", json.dumps({"type": "문서", "ocr_text": ["a", "b"]}, ensure_ascii=False)),
    ("caption 숫자", json.dumps({"type": "문서", "caption": 42}, ensure_ascii=False)),
    # --- Python str() vs JS String() 이 갈리는 컨테이너 값 ---
    ("ocr_text 객체", json.dumps({"type": "문서", "ocr_text": {"k": "v"}}, ensure_ascii=False)),
    ("ocr_text 빈 배열", json.dumps({"type": "문서", "ocr_text": []}, ensure_ascii=False)),
    ("ocr_text 중첩", json.dumps({"type": "문서", "ocr_text": [[1], {"a": [2.5, True, None]}]},
                              ensure_ascii=False)),
    ("ocr_text 배열 속 따옴표", json.dumps({"type": "문서", "ocr_text": ["it's", 'say "hi"']},
                                    ensure_ascii=False)),
    ("ocr_text 배열 속 양쪽 따옴표",
     json.dumps({"type": "문서", "ocr_text": ["a'b\"c"]}, ensure_ascii=False)),
    ("ocr_text 배열 속 개행", json.dumps({"type": "문서", "ocr_text": ["a\nb\tc"]},
                                   ensure_ascii=False)),
    ("ocr_text 배열 속 한글", json.dumps({"type": "문서", "ocr_text": ["가나", "다라"]},
                                   ensure_ascii=False)),
    ("ocr_text 실수", json.dumps({"type": "문서", "ocr_text": 2.5}, ensure_ascii=False)),
    ("ocr_text true", json.dumps({"type": "문서", "ocr_text": True}, ensure_ascii=False)),
    ("caption 배열", json.dumps({"type": "문서", "caption": ["x", "y"]}, ensure_ascii=False)),
    # --- table/figure caption 정규화 ---
    ("table_caption 빈 문자열", json.dumps({"type": "표", "table_caption": ""},
                                      ensure_ascii=False)),
    ("table_caption 공백만", json.dumps({"type": "표", "table_caption": "   "},
                                    ensure_ascii=False)),
    ("table_caption null", json.dumps({"type": "표", "table_caption": None},
                                    ensure_ascii=False)),
    ("table_caption 숫자", json.dumps({"type": "표", "table_caption": 5}, ensure_ascii=False)),
    ("table_caption 정상", json.dumps({"type": "표", "table_caption": "월별 매출"},
                                   ensure_ascii=False)),
    ("figure_caption 정상", json.dumps({"type": "차트", "figure_caption": "구조도"},
                                    ensure_ascii=False)),
    ("둘 다 정상", json.dumps({"type": "표", "table_caption": "T", "figure_caption": "F"},
                          ensure_ascii=False)),
    # --- structured 정규화 ---
    ("structured 빈 dict", json.dumps({"type": "표", "structured": {}}, ensure_ascii=False)),
    ("structured null", json.dumps({"type": "표", "structured": None}, ensure_ascii=False)),
    ("structured 배열", json.dumps({"type": "표", "structured": [1, 2]}, ensure_ascii=False)),
    ("structured 문자열", json.dumps({"type": "표", "structured": "없음"}, ensure_ascii=False)),
    ("structured 중첩", json.dumps({
        "type": "화이트보드", "structured": {"action_items": ["A 담당 홍길동", "B 기한 3/1"]},
    }, ensure_ascii=False)),
    # --- 빈 응답 ---
    ("빈 객체", "{}"),
]

# 파싱이 **실패해야** 하는 것들
PARSE_FAIL_CASES: list[tuple[str, str]] = [
    ("깨진 JSON", "{not json"),
    ("빈 문자열", ""),
    ("배열", "[1,2,3]"),
    ("문자열 리터럴", '"just a string"'),
    ("숫자", "42"),
    ("null", "null"),
]

# (model, prompt, output, thinking)
COST_CASES = [
    ("gemini-2.5-flash", 1000, 500, 0),
    ("gemini-2.5-flash", 1000, 500, 200),
    ("gemini-2.5-flash-lite", 1000, 500, 200),
    ("gemini-2.0-flash", 1000, 500, 200),
    ("gemini-2.0-flash-lite", 1000, 500, 200),
    ("gemini-2.0-flash-thinking-exp", 1000, 500, 200),
    ("존재하지-않는-모델", 1000, 500, 200),   # fallback 단가
    ("gemini-2.5-flash", 0, 0, 0),
    ("gemini-2.5-flash", 1_000_000, 1_000_000, 1_000_000),
]

RUNNER_TS = f"""
import {{
  estimateCost, parseVisionResponse,
}} from "file://{SHARED}/ingest/vision_caption.ts";

const input = JSON.parse(await Deno.readTextFile(Deno.args[0]));
console.log(JSON.stringify({{
  parse: input.parse.map(([, text]: [string, string]) => {{
    try {{
      const c = parseVisionResponse(text);
      return {{ ok: true, value: c }};
    }} catch {{
      return {{ ok: false }};
    }}
  }}),
  fail: input.fail.map(([, text]: [string, string]) => {{
    try {{
      parseVisionResponse(text);
      return {{ ok: true }};
    }} catch {{
      return {{ ok: false }};
    }}
  }}),
  cost: input.cost.map(([model, p, o, t]: [string, number, number, number]) =>
    estimateCost({{ model, promptTokens: p, outputTokens: o, thinkingTokens: t }})),
}}));
"""


def to_dict(c) -> dict:
    return {
        "type": c.type, "ocr_text": c.ocr_text, "caption": c.caption,
        "structured": c.structured, "usage": c.usage,
        "table_caption": c.table_caption, "figure_caption": c.figure_caption,
    }


def main() -> None:
    from app.adapters.impl.gemini_vision import GeminiVisionCaptioner, _estimate_cost

    py_parse = []
    for _label, text in PARSE_CASES:
        try:
            py_parse.append({"ok": True, "value": to_dict(GeminiVisionCaptioner._parse(text))})
        except Exception:
            py_parse.append({"ok": False})

    py_fail = []
    for _label, text in PARSE_FAIL_CASES:
        try:
            GeminiVisionCaptioner._parse(text)
            py_fail.append({"ok": True})
        except Exception:
            py_fail.append({"ok": False})

    py_cost = [
        _estimate_cost(model=m, prompt_tokens=p, output_tokens=o, thinking_tokens=t)
        for m, p, o, t in COST_CASES
    ]

    with tempfile.TemporaryDirectory() as tmp:
        cf, rf = os.path.join(tmp, "c.json"), os.path.join(tmp, "r.ts")
        with open(cf, "w", encoding="utf-8") as f:
            json.dump({"parse": PARSE_CASES, "fail": PARSE_FAIL_CASES, "cost": COST_CASES}, f)
        with open(rf, "w", encoding="utf-8") as f:
            f.write(RUNNER_TS)
        proc = subprocess.run(
            ["deno", "run", "--config", DENO_CONFIG, "--allow-all", rf, cf],
            capture_output=True, text=True, timeout=600,
        )
    if proc.returncode != 0:
        raise SystemExit(f"deno 실행 실패:\n{proc.stderr[:3000]}")
    ts = json.loads(proc.stdout)

    fails = 0

    # --- 파싱 ---
    bad = []
    for i, (label, _t) in enumerate(PARSE_CASES):
        w, g = py_parse[i], ts["parse"][i]
        if w["ok"] != g["ok"] or (w["ok"] and w["value"] != g["value"]):
            bad.append((label, w, g))
    if bad:
        fails += 1
        print(f"  **파싱 {len(bad)}건 불일치**")
        for label, w, g in bad[:6]:
            print(f"    {label}")
            if w.get("ok") and g.get("ok"):
                for k in w["value"]:
                    if w["value"][k] != g["value"].get(k):
                        print(f"      {k:<16} py={w['value'][k]!r}  ts={g['value'].get(k)!r}")
            else:
                print(f"      py ok={w['ok']}  ts ok={g['ok']}")
    else:
        print(f"  parseVisionResponse       {len(PARSE_CASES)}건 OK")

    # --- 실패해야 하는 것 ---
    bad2 = [PARSE_FAIL_CASES[i][0] for i in range(len(PARSE_FAIL_CASES))
            if py_fail[i]["ok"] != ts["fail"][i]["ok"]]
    if bad2:
        fails += 1
        print(f"  **거절 케이스 불일치**: {bad2}")
    else:
        n_rej = sum(1 for v in py_fail if not v["ok"])
        print(f"  파싱 거절                 {len(PARSE_FAIL_CASES)}건 OK (거절 {n_rej})")
    if all(v["ok"] for v in py_fail):
        fails += 1
        print("    **케이스 무효** — 거절되는 입력이 하나도 없다")

    # --- 단가 ---
    bad3 = []
    for i, (m, p, o, t) in enumerate(COST_CASES):
        if abs(py_cost[i] - ts["cost"][i]) > 1e-15:
            bad3.append((m, p, o, t, py_cost[i], ts["cost"][i]))
    if bad3:
        fails += 1
        print(f"  **단가 {len(bad3)}건 불일치**")
        for m, p, o, t, a, b in bad3[:6]:
            print(f"    {m:<32} py={a!r}  ts={b!r}")
    else:
        print(f"  estimateCost              {len(COST_CASES)}건 OK")
    # 케이스 무효 — 모델별로 값이 갈려야 의미가 있다.
    if len(set(py_cost)) < 3:
        fails += 1
        print("    **케이스 무효** — 단가 결과가 거의 같다. 모델 분기를 못 본다")

    print()
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
