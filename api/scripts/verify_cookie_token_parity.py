"""`cookie_token.ts` ↔ `app/auth/cookie_token.py` 동등성 교차 검증.

단위 테스트는 내가 이해한 계약을 고정할 뿐 **원본과 같다는 증명이 아니다.** 같은 입력을
양쪽에 넣고 결과를 직접 대조한다.

이 파일이 특히 중요한 이유: 여기가 어긋나면 증상이 "로그인 실패"가 아니라
**"일부 사용자만 조용히 익명으로 떨어짐"** 이다. 쿠키가 3,180자를 넘어 청크로 쪼개진
세션이나 `base64-` prefix 세션처럼 **특정 경로만** 깨지기 때문이다.

특히 확인하려는 것: Python `base64.urlsafe_b64decode` 는 기본 `validate=False` 라
알파벳 밖 문자를 조용히 버리는데 JS `atob` 은 던진다. 손상 입력에서 두 구현이
**최종적으로 같은 판정(None/null)** 에 도달하는지를 실제로 본다.

사용:
    api/.venv/bin/python api/scripts/verify_cookie_token_parity.py
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
MODULE_TS = os.path.join(ROOT, "supabase", "functions", "_shared", "cookie_token.ts")

sys.path.insert(0, os.path.join(ROOT, "api"))

REF = "abcd1234"
NAME = f"sb-{REF}-auth-token"


def b64url_nopad(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode("utf-8")).decode("ascii").rstrip("=")


def session(token: str) -> str:
    return json.dumps({"access_token": token, "refresh_token": "r", "expires_in": 3600})


def chunked(value: str, n: int) -> dict[str, str]:
    size = -(-len(value) // n)
    return {f"{NAME}.{i}": value[i * size : (i + 1) * size] for i in range(n)}


_LONG = session("t" + "x" * 4000)

# (이름, cookies, project_ref)
EXTRACT_CASES: list[tuple[str, dict, object]] = [
    ("단일 정상", {NAME: session("jwt-1")}, REF),
    ("쿠키 없음", {}, REF),
    ("다른 쿠키만", {"other": "x"}, REF),
    ("ref 빈 문자열", {NAME: session("jwt-1")}, ""),
    ("ref None", {NAME: session("jwt-1")}, None),
    ("단일 빈 문자열", {NAME: ""}, REF),
    ("JSON 아님", {NAME: "not json"}, REF),
    ("깨진 JSON", {NAME: "{broken"}, REF),
    ("청크 2개", chunked(session("jwt-chunk2"), 2), REF),
    ("청크 5개", chunked(session("jwt-chunk5"), 5), REF),
    ("청크 16개(경계)", chunked(_LONG, 16), REF),
    ("청크 17개(상한 초과)", chunked(_LONG, 17), REF),
    ("단일이 청크보다 우선", {NAME: session("single"), f"{NAME}.0": session("chunk")}, REF),
    ("청크 구멍", {f"{NAME}.0": session("x")[:10], f"{NAME}.2": session("x")[10:]}, REF),
    (".1 만 존재", {f"{NAME}.1": session("x")}, REF),
    ("base64 패딩 없음", {NAME: "base64-" + b64url_nopad(session("jwt-b64"))}, REF),
    (
        "base64 패딩 있음",
        {NAME: "base64-" + base64.urlsafe_b64encode(session("jwt-pad").encode()).decode()},
        REF,
    ),
    ("base64 + 청크", chunked("base64-" + b64url_nopad(_LONG), 3), REF),
    ("base64 한글", {NAME: "base64-" + b64url_nopad(json.dumps({"access_token": "토큰-한글"}, ensure_ascii=False))}, REF),
    # --- 손상 입력: Python 의 관용 디코드 vs JS 의 엄격 디코드가 갈릴 수 있는 지점 ---
    ("base64 빈 값", {NAME: "base64-"}, REF),
    ("base64 잘못된 문자", {NAME: "base64-!!!not-base64!!!"}, REF),
    ("base64 공백 포함", {NAME: "base64-" + b64url_nopad(session("jwt-sp"))[:8] + "  " + b64url_nopad(session("jwt-sp"))[8:]}, REF),
    ("base64 개행 포함", {NAME: "base64-" + b64url_nopad(session("jwt-nl"))[:8] + "\n" + b64url_nopad(session("jwt-nl"))[8:]}, REF),
    ("base64 길이 오류(1글자)", {NAME: "base64-A"}, REF),
    ("base64 유효하나 JSON 아님", {NAME: "base64-" + b64url_nopad("hello world")}, REF),
    ("base64 유효하나 깨진 UTF-8", {NAME: "base64-" + base64.urlsafe_b64encode(b"\xff\xfe\xfd").decode().rstrip("=")}, REF),
    # --- 값 형태 분기 ---
    ("배열 형식", {NAME: json.dumps(["jwt-arr", "refresh"])}, REF),
    ("빈 배열", {NAME: json.dumps([])}, REF),
    ("배열 첫 원소 빈 문자열", {NAME: json.dumps([""])}, REF),
    ("배열 첫 원소 숫자", {NAME: json.dumps([123])}, REF),
    ("access_token 빈 문자열", {NAME: json.dumps({"access_token": ""})}, REF),
    ("access_token 숫자", {NAME: json.dumps({"access_token": 123})}, REF),
    ("access_token null", {NAME: json.dumps({"access_token": None})}, REF),
    ("access_token 없음", {NAME: json.dumps({"x": 1})}, REF),
    ("JSON 문자열", {NAME: json.dumps("bare")}, REF),
    ("JSON 숫자", {NAME: json.dumps(42)}, REF),
    ("JSON null", {NAME: json.dumps(None)}, REF),
    ("중첩 객체", {NAME: json.dumps({"access_token": {"a": 1}})}, REF),
]

# 의도적 차이 — 값까지 고정해 둔다. 그래야 "허용"이 아니라 "감시"가 된다.
# 동작이 여기서 조금이라도 바뀌면 다시 MISMATCH 로 잡힌다.
#
# 근거: Python 은 공백을 포함한 길이로 padding 을 계산한 뒤 binascii 가 그 공백을 버려서
# 성공 여부가 우연히 갈린다(공백 2개는 통과, 개행 1개는 실패). 설계된 계약이 아니다.
# TS 는 공백을 먼저 제거하고 padding 을 계산해 결정론적으로 만든다 — 손상값에 더 관대해지는
# 방향이며, 병행 운용 중 "Railway 는 로그인인데 Edge 는 익명" 이 되는 것보다 안전하다.
# 꺼낸 JWT 는 직후 서명 검증을 거치므로 통과시킨다고 권한이 생기지 않는다.
ALLOWED_DIFFS: dict[str, tuple[object, object]] = {
    "base64 공백 포함": ("jwt-sp", "jwt-sp"),
    "base64 개행 포함": (None, "jwt-nl"),
}

DERIVE_CASES = [
    "https://abcd1234.supabase.co",
    "https://abcd1234.supabase.co/rest/v1",
    "https://ABCD1234.supabase.co",
    "http://localhost:54321",
    "https://abcd1234.supabase.co:443/x?y=1#z",
    "",
    "abcd1234.supabase.co",
    "not a url",
    "https://",
    "ftp://a.b.c",
    "//abcd1234.supabase.co",
    "https://.leading-dot.com",
]

RUNNER_TS = f"""
import {{ deriveProjectRef, extractAccessToken }} from "file://{MODULE_TS}";

const input = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const out = {{
  extract: input.extract.map((c: {{ cookies: Record<string, string>; ref: string | null }}) =>
    extractAccessToken(c.cookies, c.ref)
  ),
  derive: input.derive.map((u: string) => deriveProjectRef(u)),
}};
console.log(JSON.stringify(out));
"""


def run_deno(payload: dict) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        cases_path = os.path.join(tmp, "cases.json")
        runner_path = os.path.join(tmp, "runner.ts")
        with open(cases_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        with open(runner_path, "w", encoding="utf-8") as f:
            f.write(RUNNER_TS)
        proc = subprocess.run(
            ["deno", "run", "--allow-read", runner_path, cases_path],
            capture_output=True,
            text=True,
            timeout=180,
        )
    if proc.returncode != 0:
        raise SystemExit(f"deno 실행 실패:\n{proc.stderr[:1000]}")
    return json.loads(proc.stdout)


def main() -> None:
    from app.auth.cookie_token import derive_project_ref, extract_access_token

    payload = {
        "extract": [{"cookies": c, "ref": r} for _, c, r in EXTRACT_CASES],
        "derive": DERIVE_CASES,
    }
    ts = run_deno(payload)

    fails = 0
    allowed = 0

    print("=== extract_access_token ===")
    for (name, cookies, ref), tv in zip(EXTRACT_CASES, ts["extract"]):
        pv = extract_access_token(cookies, ref or "")
        if name in ALLOWED_DIFFS:
            # 값이 고정값과 정확히 같을 때만 "허용된 차이". 하나라도 달라지면 실패다.
            if (pv, tv) == ALLOWED_DIFFS[name]:
                allowed += 1
                print(f"  {name:<28} 허용된 차이  py={pv!r} ts={tv!r}")
            else:
                fails += 1
                print(f"  {name:<28} MISMATCH(고정값과 다름)  py={pv!r} ts={tv!r} 기대={ALLOWED_DIFFS[name]}")
            continue
        ok = pv == tv
        if not ok:
            fails += 1
        mark = "OK" if ok else "MISMATCH"
        detail = "" if ok else f"  py={pv!r} ts={tv!r}"
        print(f"  {name:<28} {mark}{detail}")

    print()
    print("=== derive_project_ref ===")
    for url, tv in zip(DERIVE_CASES, ts["derive"]):
        pv = derive_project_ref(url)
        ok = pv == tv
        if not ok:
            fails += 1
        mark = "OK" if ok else "MISMATCH"
        detail = "" if ok else f"  py={pv!r} ts={tv!r}"
        print(f"  {url[:40]:<42} {mark}{detail}")

    total = len(EXTRACT_CASES) + len(DERIVE_CASES)
    print()
    print(f"케이스 {total}개 대조 (허용된 차이 {allowed}건 — 위 ALLOWED_DIFFS 주석 참조)")
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
