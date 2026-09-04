"""`current_user.ts` ↔ `app/auth/dependencies.py` 동등성 교차 검증.

**실제 Starlette `Request` 를 만들어** 돌린다. 쿠키 파싱이 Python 쪽에서는 프레임워크 책임이라
직접 호출로는 그 규칙이 검증되지 않기 때문이다 — Edge 에는 그 계층이 없어 우리가 직접
구현했고, 규칙이 어긋나면 **일부 사용자만 조용히 익명으로 떨어진다.**

대조 대상: 3-way 분기 결과(user_id / email / is_authenticated) 와 실패 시 상태코드·detail.

사용:
    api/.venv/bin/python api/scripts/verify_current_user_parity.py
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
from types import SimpleNamespace

import jwt as pyjwt

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
MODULE_TS = os.path.join(ROOT, "supabase", "functions", "_shared", "current_user.ts")
DENO_CONFIG = os.path.join(ROOT, "supabase", "functions", "deno.json")

sys.path.insert(0, os.path.join(ROOT, "api"))

SECRET = "test-secret-at-least-32-bytes-long!!"
SUPABASE_URL = "https://abcd1234.supabase.co"
COOKIE = "sb-abcd1234-auth-token"
DEFAULT_USER = "00000000-0000-0000-0000-000000000001"
OWNER = "99999999-9999-9999-9999-999999999999"
USER = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"


def token(**over) -> str:
    claims = {"sub": USER, "aud": "authenticated", "exp": int(time.time()) + 3600}
    claims.update(over)
    return pyjwt.encode(claims, SECRET, algorithm="HS256")


def session_cookie(jwt_str: str, *, base64_form: bool = True, url_encode: bool = False) -> str:
    payload = json.dumps({"access_token": jwt_str})
    if base64_form:
        value = "base64-" + base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    else:
        value = payload
    if url_encode:
        value = urllib.parse.quote(value)
    return f"{COOKIE}={value}"


BASE_SETTINGS = {
    "supabaseUrl": SUPABASE_URL,
    "authEnabled": True,
    "defaultUserId": DEFAULT_USER,
    "ownerUserId": OWNER,
    "supabaseJwtAlgorithm": "HS256",
    "supabaseJwtSecret": SECRET,
    "supabaseJwksUrl": None,
}


def build_cases() -> list[dict]:
    t = token()
    expired = token(exp=int(time.time()) - 10)
    wrong_aud = token(aud="anon")
    other = token(sub=OTHER)

    def case(name, headers, over=None):
        return {"name": name, "headers": headers, "settings": {**BASE_SETTINGS, **(over or {})}}

    return [
        # --- authEnabled=false ---
        case("auth 꺼짐 · 헤더 없음", {}, {"authEnabled": False}),
        case("auth 꺼짐 · 깨진 토큰", {"Authorization": "Bearer garbage"}, {"authEnabled": False}),
        case("auth 꺼짐 · 유효 토큰", {"Authorization": f"Bearer {t}"}, {"authEnabled": False}),
        # --- 익명 fallback ---
        case("토큰 없음 · owner 설정됨", {}),
        case("토큰 없음 · owner 미설정", {}, {"ownerUserId": None}),
        case("Bearer 접두어만", {"Authorization": "Bearer "}),
        case("Bearer 아님", {"Authorization": "Basic abc"}),
        case("소문자 bearer", {"Authorization": f"bearer {t}"}),
        # --- 유효 토큰 ---
        case("Bearer 유효", {"Authorization": f"Bearer {t}"}),
        case("Bearer 유효 + email", {"Authorization": f"Bearer {token(email='a@b.com')}"}),
        case("Bearer 앞뒤 공백", {"Authorization": f"Bearer   {t}  "}),
        # --- 무효 토큰 → 401 ---
        case("Bearer 형식 오류", {"Authorization": "Bearer not-a-jwt"}),
        case("Bearer 만료", {"Authorization": f"Bearer {expired}"}),
        case("Bearer aud 불일치", {"Authorization": f"Bearer {wrong_aud}"}),
        case("Bearer 서명 불일치", {"Authorization": f"Bearer {t}"}, {"supabaseJwtSecret": "x" * 40}),
        # --- 쿠키 경로 ---
        case("쿠키 base64 형태", {"Cookie": session_cookie(t)}),
        case("쿠키 raw JSON 형태", {"Cookie": session_cookie(t, base64_form=False)}),
        case("쿠키 percent-encoded", {"Cookie": session_cookie(t, url_encode=True)}),
        case("쿠키 깨짐", {"Cookie": f"{COOKIE}=not-json"}),
        case("쿠키 이름 다름", {"Cookie": "other=1"}),
        case("쿠키 만료 토큰", {"Cookie": session_cookie(expired)}),
        case("쿠키 + 다른 쿠키 혼재", {"Cookie": f"a=1; {session_cookie(t)}; b=2"}),
        case("쿠키 중복 — 뒤가 이김", {"Cookie": f"{session_cookie(other)}; {session_cookie(t)}"}),
        case("쿠키 값에 공백", {"Cookie": f"  {COOKIE}  =  base64-  "}),
        case("쿠키 따옴표 감쌈", {"Cookie": f'{COOKIE}="{session_cookie(t).split("=", 1)[1]}"'}),
        case("SUPABASE_URL 이상", {"Cookie": session_cookie(t)}, {"supabaseUrl": "not a url"}),
        # --- Bearer vs 쿠키 우선순위 ---
        case("Bearer 우선", {"Authorization": f"Bearer {other}", "Cookie": session_cookie(t)}),
        case("Bearer 빈값이면 쿠키", {"Authorization": "Bearer ", "Cookie": session_cookie(t)}),
        case(
            "Bearer 무효 + 쿠키 유효 → 401",
            {"Authorization": "Bearer not-a-jwt", "Cookie": session_cookie(t)},
        ),
        # --- 청크 쿠키 ---
        case(
            "쿠키 청크 2개",
            {
                "Cookie": "; ".join(
                    [
                        f"{COOKIE}.0={session_cookie(t).split('=', 1)[1][:20]}",
                        f"{COOKIE}.1={session_cookie(t).split('=', 1)[1][20:]}",
                    ]
                )
            },
        ),
    ]


RUNNER_TS = f"""
import {{
  AuthError,
  getCurrentUser,
  requireAdmin,
  requireAuthenticatedUser,
}} from "file://{MODULE_TS}";

const input = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const out = [];
for (const c of input) {{
  const req = new Request("https://api.example.com/auth/me", {{ headers: c.headers }});
  try {{
    const u = await getCurrentUser(req, c.settings);
    const row: Record<string, unknown> = {{
      ok: true,
      userId: u.userId,
      email: u.email,
      isAuthenticated: u.isAuthenticated,
    }};
    // 게이트 결과도 같이 본다 — 분기가 같아도 게이트가 갈리면 권한이 달라진다.
    try {{
      requireAuthenticatedUser(u);
      row.writeGate = "pass";
    }} catch (e) {{
      row.writeGate = e instanceof AuthError ? `${{e.status}}:${{e.detail}}` : "?";
    }}
    try {{
      requireAdmin(u, c.settings);
      row.adminGate = "pass";
    }} catch (e) {{
      row.adminGate = e instanceof AuthError ? `${{e.status}}:${{e.detail}}` : "?";
    }}
    out.push(row);
  }} catch (e) {{
    out.push(
      e instanceof AuthError
        ? {{ ok: false, status: e.status, detail: e.detail }}
        : {{ ok: false, status: 0, detail: `예상 밖 예외: ${{e}}` }},
    );
  }}
}}
console.log(JSON.stringify(out));
"""


def run_deno(cases: list[dict]) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        cases_path = os.path.join(tmp, "cases.json")
        runner_path = os.path.join(tmp, "runner.ts")
        with open(cases_path, "w", encoding="utf-8") as f:
            json.dump(cases, f)
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


def make_request(headers: dict[str, str]):
    """실제 Starlette Request — 쿠키 파싱을 프레임워크에 그대로 맡긴다."""
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/auth/me",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "query_string": b"",
    }
    return Request(scope)


def to_settings(d: dict) -> SimpleNamespace:
    return SimpleNamespace(
        supabase_url=d["supabaseUrl"],
        auth_enabled=d["authEnabled"],
        default_user_id=d["defaultUserId"],
        owner_user_id=d["ownerUserId"],
        supabase_jwt_algorithm=d["supabaseJwtAlgorithm"],
        supabase_jwt_secret=d["supabaseJwtSecret"],
        supabase_jwks_url=d["supabaseJwksUrl"],
    )


def run_python(case: dict) -> dict:
    from fastapi import HTTPException

    from app.auth.dependencies import get_current_user, require_admin, require_authenticated_user

    settings = to_settings(case["settings"])
    req = make_request(case["headers"])
    try:
        u = get_current_user(req, settings)  # type: ignore[arg-type]
    except HTTPException as e:
        return {"ok": False, "status": e.status_code, "detail": e.detail}

    row: dict = {
        "ok": True,
        "userId": u.user_id,
        "email": u.email,
        "isAuthenticated": u.is_authenticated,
    }
    try:
        require_authenticated_user(u)
        row["writeGate"] = "pass"
    except HTTPException as e:
        row["writeGate"] = f"{e.status_code}:{e.detail}"
    try:
        require_admin(u, settings)  # type: ignore[arg-type]
        row["adminGate"] = "pass"
    except HTTPException as e:
        row["adminGate"] = f"{e.status_code}:{e.detail}"
    return row


def main() -> None:
    cases = build_cases()
    ts_results = run_deno(cases)

    fails = 0
    print(f"{'케이스':<30}{'판정':>10}  요약")
    print("-" * 100)
    for case, tv in zip(cases, ts_results):
        pv = run_python(case)
        ok = pv == tv
        if not ok:
            fails += 1
            print(f"  {case['name']:<28} {'MISMATCH':>8}")
            print(f"      py={pv}")
            print(f"      ts={tv}")
            continue
        if pv["ok"]:
            summary = (
                f"user={pv['userId'][:8]}… auth={pv['isAuthenticated']} "
                f"write={pv['writeGate']} admin={pv['adminGate']}"
            )
        else:
            summary = f"{pv['status']} '{pv['detail']}'"
        print(f"  {case['name']:<28} {'OK':>8}  {summary}")

    print()
    print(f"케이스 {len(cases)}개 대조 (3-way 분기 + 쓰기/admin 게이트 + 상태코드·detail)")
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
