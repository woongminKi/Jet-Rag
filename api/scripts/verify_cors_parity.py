"""`cors.ts` ↔ `app/main.py` 의 `CORSMiddleware` 동등성 교차 검증.

**실제 Starlette 미들웨어를 앱에 붙여** TestClient 로 때린 결과와 대조한다. CORS 는 원래
프레임워크가 하던 일이라 직접 구현하면 헤더 하나가 조용히 빠지기 쉽고, 그러면 브라우저가
응답을 통째로 버려 **서버 로그에는 아무것도 안 남는** 실패가 된다.

대조 항목: 상태코드, 본문(preflight), 그리고 CORS 관련 응답 헤더 전부.

사용:
    api/.venv/bin/python api/scripts/verify_cors_parity.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
MODULE_TS = os.path.join(ROOT, "supabase", "functions", "_shared", "cors.ts")
DENO_CONFIG = os.path.join(ROOT, "supabase", "functions", "deno.json")

sys.path.insert(0, os.path.join(ROOT, "api"))

ORIGINS = ["http://localhost:3001", "http://localhost:3000", "https://jetrag.woong-s.com"]

# 비교할 헤더 — 하나라도 빠지면 브라우저 동작이 달라진다.
CORS_HEADERS = [
    "access-control-allow-origin",
    "access-control-allow-credentials",
    "access-control-allow-methods",
    "access-control-allow-headers",
    "access-control-max-age",
    "access-control-expose-headers",
    "vary",
]

# (이름, 메서드, 요청 헤더, 라우트)
# 라우트 "vary" 는 응답에 이미 `Vary` 가 있는 경우다. 이게 없으면 "덮어쓰기 vs 덧붙이기"
# 버그를 검사기가 못 잡는다 — 실제로 음성 대조에서 안 잡혀 뒤늦게 추가했다.
CASES: list[tuple[str, str, dict[str, str], str]] = [
    ("Origin 없음 GET", "GET", {}, "probe"),
    ("허용 origin GET", "GET", {"Origin": ORIGINS[0]}, "probe"),
    ("허용 origin POST", "POST", {"Origin": ORIGINS[2]}, "probe"),
    ("Vercel preview GET", "GET", {"Origin": "https://jetrag-abc.vercel.app"}, "probe"),
    ("허용 밖 origin GET", "GET", {"Origin": "https://evil.com"}, "probe"),
    ("대소문자 다른 origin", "GET", {"Origin": "HTTP://LOCALHOST:3001"}, "probe"),
    ("OPTIONS 인데 preflight 아님", "OPTIONS", {"Origin": ORIGINS[0]}, "probe"),
    ("preflight GET", "OPTIONS", {"Origin": ORIGINS[0], "Access-Control-Request-Method": "GET"}, "probe"),
    (
        "preflight POST + 헤더",
        "OPTIONS",
        {
            "Origin": ORIGINS[1],
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization, content-type",
        },
        "probe",
    ),
    (
        "preflight 대문자 헤더 목록",
        "OPTIONS",
        {
            "Origin": ORIGINS[1],
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Authorization, X-Custom",
        },
        "probe",
    ),
    (
        "preflight DELETE (허용 밖 메서드)",
        "OPTIONS",
        {"Origin": ORIGINS[0], "Access-Control-Request-Method": "DELETE"},
        "probe",
    ),
    (
        "preflight OPTIONS (목록에 없음)",
        "OPTIONS",
        {"Origin": ORIGINS[0], "Access-Control-Request-Method": "OPTIONS"},
        "probe",
    ),
    (
        "preflight 허용 밖 origin",
        "OPTIONS",
        {"Origin": "https://evil.com", "Access-Control-Request-Method": "GET"},
        "probe",
    ),
    (
        "preflight origin·method 둘 다 실패",
        "OPTIONS",
        {"Origin": "https://evil.com", "Access-Control-Request-Method": "DELETE"},
        "probe",
    ),
    (
        "preflight Vercel preview",
        "OPTIONS",
        {"Origin": "https://x-y-z.vercel.app", "Access-Control-Request-Method": "POST"},
        "probe",
    ),
    ("preflight Origin 없음", "OPTIONS", {"Access-Control-Request-Method": "GET"}, "probe"),
    # 응답에 이미 Vary 가 있는 경우 — 덧붙여야 하고 덮어쓰면 안 된다.
    ("기존 Vary · 허용 origin", "GET", {"Origin": ORIGINS[0]}, "vary"),
    ("기존 Vary · 허용 밖 origin", "GET", {"Origin": "https://evil.com"}, "vary"),
    ("기존 Vary · Origin 없음", "GET", {}, "vary"),
]

RUNNER_TS = f"""
import {{ applyCorsHeaders, preflightResponse }} from "file://{MODULE_TS}";

const input = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const settings = {{ corsOrigins: input.origins }};
const out = [];

for (const c of input.cases) {{
  const req = new Request("https://api.example.com/probe", {{ method: c.method, headers: c.headers }});
  // 라우트 계층을 Python 쪽과 같게 흉내낸다. FastAPI 는 GET/POST 만 등록돼 있어
  // 그 밖의 메서드에는 405 를 낸다 — CORS 가 아니라 **앱**의 동작이다.
  // (Task 1.6 에서 Edge 함수도 같은 405 를 내야 한다는 요구사항이기도 하다.)
  const varyInit = c.route === "vary" ? {{ "vary": "Accept-Encoding" }} : {{}};
  const base = () =>
    c.method === "GET" || c.method === "POST"
      ? new Response(JSON.stringify({{ ok: true }}), {{
        status: 200,
        headers: {{ "content-type": "application/json", ...varyInit }},
      }})
      : new Response(JSON.stringify({{ detail: "Method Not Allowed" }}), {{
        status: 405,
        headers: {{ "content-type": "application/json" }},
      }});

  const pre = preflightResponse(req, settings);
  const res = pre ?? applyCorsHeaders(req, base(), settings);
  const headers: Record<string, string | null> = {{}};
  for (const h of input.headerNames) headers[h] = res.headers.get(h);
  out.push({{ status: res.status, body: await res.text(), headers }});
}}
console.log(JSON.stringify(out));
"""


def run_deno(payload: dict) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        cases_path = os.path.join(tmp, "cases.json")
        runner_path = os.path.join(tmp, "runner.ts")
        with open(cases_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        with open(runner_path, "w", encoding="utf-8") as f:
            f.write(RUNNER_TS)
        proc = subprocess.run(
            ["deno", "run", "--config", DENO_CONFIG, "--allow-read", "--allow-net", runner_path, cases_path],
            capture_output=True,
            text=True,
            timeout=300,
        )
    if proc.returncode != 0:
        raise SystemExit(f"deno 실행 실패:\n{proc.stderr[:1500]}")
    return json.loads(proc.stdout)


def build_client():
    """`app/main.py` 와 **같은 인자**로 CORSMiddleware 를 붙인 최소 앱."""
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.get("/probe")
    @app.post("/probe")
    def probe():  # noqa: ANN202
        return {"ok": True}

    @app.get("/vary")
    @app.post("/vary")
    def vary_route():  # noqa: ANN202
        # 응답이 이미 Vary 를 들고 있는 경우. CORS 는 덮어쓰지 않고 덧붙여야 한다.
        return JSONResponse({"ok": True}, headers={"Vary": "Accept-Encoding"})

    app.add_middleware(
        CORSMiddleware,
        allow_origins=ORIGINS,
        allow_origin_regex=r"https://.*\.vercel\.app",
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    return TestClient(app)


def main() -> None:
    client = build_client()
    payload = {
        "origins": ORIGINS,
        "headerNames": CORS_HEADERS,
        "cases": [{"method": m, "headers": h, "route": r} for _, m, h, r in CASES],
    }
    ts_results = run_deno(payload)

    fails = 0
    print(f"{'케이스':<34}{'판정':>10}  요약")
    print("-" * 96)
    for (name, method, headers, route), tv in zip(CASES, ts_results):
        resp = client.request(method, f"/{route}", headers=headers)
        pv = {
            "status": resp.status_code,
            "body": resp.text,
            "headers": {h: resp.headers.get(h) for h in CORS_HEADERS},
        }
        # 일반 응답의 본문은 프레임워크마다 직렬화가 다르다(공백 등). preflight 만 본문을 본다.
        is_preflight = method == "OPTIONS" and "Access-Control-Request-Method" in headers
        if not is_preflight:
            pv.pop("body")
            tv = {k: v for k, v in tv.items() if k != "body"}

        ok = pv == tv
        if not ok:
            fails += 1
            print(f"  {name:<32} {'MISMATCH':>8}")
            for h in CORS_HEADERS:
                a, b = pv["headers"].get(h), tv["headers"].get(h)
                if a != b:
                    print(f"      {h}: py={a!r} ts={b!r}")
            if pv.get("status") != tv.get("status"):
                print(f"      status: py={pv.get('status')} ts={tv.get('status')}")
            if pv.get("body") != tv.get("body"):
                print(f"      body: py={pv.get('body')!r} ts={tv.get('body')!r}")
            continue

        acao = pv["headers"]["access-control-allow-origin"]
        print(f"  {name:<32} {'OK':>8}  {pv['status']} ACAO={acao!r}")

    print()
    print(f"케이스 {len(CASES)}개 대조 (상태코드 + preflight 본문 + CORS 헤더 {len(CORS_HEADERS)}종)")
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
