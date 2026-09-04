"""`jwt.ts` ↔ `app/auth/jwt_verify.py` 동등성 교차 검증.

**판정뿐 아니라 실패 메시지까지** 대조한다. 이 문자열이 그대로 401 detail 로 나가므로
바뀌면 프론트 오류 분기나 사용자 안내가 조용히 달라진다.

운영 경로는 ES256/JWKS 다(프로젝트 JWKS 실측: EC / P-256 / ES256). 그래서 로컬 JWKS 서버를
띄워 **양쪽 구현이 같은 엔드포인트에서 같은 키를 받아** 검증하게 만든다. HS256 은 레거시
호환 경로로 같이 덮는다.

사용:
    api/.venv/bin/python api/scripts/verify_jwt_parity.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import jwt as pyjwt
from cryptography.hazmat.primitives.asymmetric import ec

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
MODULE_TS = os.path.join(ROOT, "supabase", "functions", "_shared", "jwt.ts")
DENO_CONFIG = os.path.join(ROOT, "supabase", "functions", "deno.json")

sys.path.insert(0, os.path.join(ROOT, "api"))

AUD = "authenticated"
SUB = "11111111-1111-1111-1111-111111111111"
SECRET = "test-secret-at-least-32-bytes-long!!"
OTHER_SECRET = "another-secret-at-least-32-bytes!!!!"
KID = "test-kid"


def now() -> int:
    return int(time.time())


# ------------------------------------------------------------------ ES256 키 + JWKS 서버


def make_es256_material() -> tuple[ec.EllipticCurvePrivateKey, dict]:
    key = ec.generate_private_key(ec.SECP256R1())
    numbers = key.public_key().public_numbers()

    def b64u(n: int) -> str:
        import base64

        return base64.urlsafe_b64encode(n.to_bytes(32, "big")).decode().rstrip("=")

    jwks = {
        "keys": [
            {
                "kty": "EC",
                "crv": "P-256",
                "x": b64u(numbers.x),
                "y": b64u(numbers.y),
                "alg": "ES256",
                "use": "sig",
                "kid": KID,
            }
        ]
    }
    return key, jwks


def start_jwks_server(jwks: dict) -> tuple[HTTPServer, str]:
    body = json.dumps(jwks).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):  # 조용히
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}/jwks.json"


# ------------------------------------------------------------------ 케이스


def build_cases(es_key: ec.EllipticCurvePrivateKey, jwks_url: str) -> list[dict]:
    hs = {"alg": "HS256", "secret": SECRET, "jwks": None}
    es = {"alg": "ES256", "secret": None, "jwks": jwks_url}

    def hs_token(claims: dict, alg: str = "HS256", secret: str = SECRET) -> str:
        return pyjwt.encode(claims, secret, algorithm=alg)

    def es_token(claims: dict, kid: str = KID) -> str:
        return pyjwt.encode(claims, es_key, algorithm="ES256", headers={"kid": kid})

    base = {"sub": SUB, "aud": AUD, "exp": now() + 3600}

    cases: list[dict] = [
        # --- 설정 오류 ---
        {"name": "빈 토큰", "token": "", "settings": hs},
        {"name": "미지원 알고리즘 none", "token": "x.y.z", "settings": {**hs, "alg": "none"}},
        {"name": "미지원 알고리즘 PS256", "token": "x.y.z", "settings": {**hs, "alg": "PS256"}},
        {"name": "HS 인데 secret 없음", "token": "x.y.z", "settings": {**hs, "secret": None}},
        {"name": "ES 인데 JWKS URL 없음", "token": "x.y.z", "settings": {**es, "jwks": None}},
        # --- HS256 ---
        {"name": "HS 정상", "token": hs_token(base), "settings": hs},
        {"name": "HS email 포함", "token": hs_token({**base, "email": "a@b.com"}), "settings": hs},
        {"name": "HS email 숫자", "token": hs_token({**base, "email": 123}), "settings": hs},
        {"name": "HS email null", "token": hs_token({**base, "email": None}), "settings": hs},
        {"name": "HS 만료", "token": hs_token({**base, "exp": now() - 10}), "settings": hs},
        {"name": "HS aud 불일치", "token": hs_token({**base, "aud": "anon"}), "settings": hs},
        {"name": "HS aud 배열 포함", "token": hs_token({**base, "aud": [AUD, "other"]}), "settings": hs},
        {"name": "HS aud 배열 미포함", "token": hs_token({**base, "aud": ["x", "y"]}), "settings": hs},
        {"name": "HS aud 누락", "token": hs_token({"sub": SUB, "exp": now() + 3600}), "settings": hs},
        {"name": "HS exp 누락", "token": hs_token({"sub": SUB, "aud": AUD}), "settings": hs},
        {"name": "HS sub 누락", "token": hs_token({"aud": AUD, "exp": now() + 3600}), "settings": hs},
        {"name": "HS sub 빈 문자열", "token": hs_token({**base, "sub": ""}), "settings": hs},
        {"name": "HS 서명 불일치", "token": hs_token(base, secret=OTHER_SECRET), "settings": hs},
        {"name": "HS 헤더 alg 가 HS512", "token": hs_token(base, alg="HS512"), "settings": hs},
        {"name": "HS nbf 미래", "token": hs_token({**base, "nbf": now() + 3600}), "settings": hs},
        {"name": "형식 오류 not-a-jwt", "token": "not-a-jwt", "settings": hs},
        {"name": "형식 오류 a.b", "token": "a.b", "settings": hs},
        {"name": "형식 오류 a.b.c.d", "token": "a.b.c.d", "settings": hs},
        # --- ES256 / JWKS (운영 경로) ---
        {"name": "ES 정상", "token": es_token(base), "settings": es},
        {"name": "ES email 포함", "token": es_token({**base, "email": "e@f.com"}), "settings": es},
        {"name": "ES 만료", "token": es_token({**base, "exp": now() - 10}), "settings": es},
        {"name": "ES aud 불일치", "token": es_token({**base, "aud": "anon"}), "settings": es},
        {"name": "ES kid 미매칭", "token": es_token(base, kid="unknown-kid"), "settings": es},
        {"name": "ES 형식 오류", "token": "x.y.z", "settings": es},
        {
            "name": "ES JWKS 연결 실패",
            "token": es_token(base),
            "settings": {**es, "jwks": "http://127.0.0.1:1/jwks.json"},
        },
    ]
    return cases


# ------------------------------------------------------------------ 실행


RUNNER_TS = f"""
import {{ JWTValidationError, verifyJwt }} from "file://{MODULE_TS}";

const cases = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const out = [];
for (const c of cases) {{
  try {{
    const v = await verifyJwt(c.token, {{
      supabaseJwtAlgorithm: c.settings.alg,
      supabaseJwtSecret: c.settings.secret,
      supabaseJwksUrl: c.settings.jwks,
    }});
    out.push({{ ok: true, userId: v.userId, email: v.email }});
  }} catch (e) {{
    out.push({{
      ok: false,
      error: e instanceof JWTValidationError ? e.message : `예상 밖 예외: ${{e}}`,
    }});
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


def run_python(case: dict) -> dict:
    from app.auth.jwt_verify import JWTValidationError, verify_jwt

    settings = SimpleNamespace(
        supabase_jwt_algorithm=case["settings"]["alg"],
        supabase_jwt_secret=case["settings"]["secret"],
        supabase_jwks_url=case["settings"]["jwks"],
    )
    try:
        v = verify_jwt(case["token"], settings)  # type: ignore[arg-type]
        return {"ok": True, "userId": v.user_id, "email": v.email}
    except JWTValidationError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # 원본이 못 흡수한 예외도 차이로 드러나야 한다
        return {"ok": False, "error": f"예상 밖 예외: {type(e).__name__}: {e}"}


def main() -> None:
    es_key, jwks = make_es256_material()
    server, jwks_url = start_jwks_server(jwks)
    try:
        cases = build_cases(es_key, jwks_url)
        ts_results = run_deno(cases)

        fails = 0
        print(f"{'케이스':<26}{'판정':>8}  상세")
        print("-" * 92)
        for case, tv in zip(cases, ts_results):
            pv = run_python(case)
            ok = pv == tv
            if not ok:
                fails += 1
            if ok:
                summary = f"ok={pv['ok']}" + (
                    f" sub={pv.get('userId')} email={pv.get('email')}" if pv["ok"] else f" '{pv.get('error')}'"
                )
                print(f"  {case['name']:<24} {'OK':>6}  {summary}")
            else:
                print(f"  {case['name']:<24} {'MISMATCH':>6}")
                print(f"      py={pv}")
                print(f"      ts={tv}")

        print()
        print(f"케이스 {len(cases)}개 대조 (판정 + 실패 메시지 전문)")
        print("FAIL 0" if fails == 0 else f"FAIL {fails}")
        sys.exit(1 if fails else 0)
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
