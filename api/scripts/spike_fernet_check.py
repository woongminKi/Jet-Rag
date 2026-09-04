"""S3 채점기 — Deno/Edge 의 Fernet 구현이 Python `cryptography.fernet` 과 호환되는지.

**한 방향만 확인하면 안 된다.** 이관 도중에는 Python 과 Deno 가 `subscriptions.billing_key`
같은 컬럼을 함께 읽고 쓴다. 그래서 네 가지를 전부 본다:

  1. Python 이 만든 토큰을 Deno 가 복호 (기존 구독자 데이터 — 실패하면 이관 불가)
  2. Deno 가 만든 토큰을 Python 이 복호 (이관 도중 Deno 가 쓴 값)
  3. Deno 가 **변조 토큰을 거부**하는가 (HMAC 검증 누락 시 위조 통과 → 조용한 보안 구멍)
  4. 잘못된 키를 거부하는가

키는 **매 실행 일회용으로 생성**한다. 운영 키(`JETRAG_BILLING_KEY_ENCRYPTION_KEY`)와
운영 SID 는 절대 쓰지 않는다.

사용:
    api/.venv/bin/python api/scripts/spike_fernet_check.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

from cryptography.fernet import Fernet, InvalidToken

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FUNCTION_URL = "https://mpmtydudhojpukuuadrd.supabase.co/functions/v1/spike"

# 운영 SID 를 닮되 실제 값이 아닌 더미. 한국어를 섞어 UTF-8 왕복도 같이 본다.
SAMPLES = [
    "TEST_SID_1234567890",
    "S1234567890abcdefghij",
    "한글 SID 테스트 · 2026",
    "",  # 빈 문자열 — PKCS7 패딩이 한 블록 통째로 붙는 경계
    "x" * 4096,  # 여러 블록
]


def anon_key() -> str:
    key = os.environ.get("SUPABASE_ANON_KEY")
    if key:
        return key
    with open(os.path.join(ROOT, ".env"), encoding="utf-8") as f:
        for line in f:
            if line.startswith("NEXT_PUBLIC_SUPABASE_ANON_KEY="):
                return line.split("=", 1)[1].strip().strip("\"'")
    raise SystemExit("SUPABASE_ANON_KEY 를 찾지 못했다")


def call_edge(payload: dict, key: str) -> dict:
    req = urllib.request.Request(
        f"{FUNCTION_URL}?kind=fernet",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return json.loads(raw)
        except Exception:
            return {"error": f"HTTP {e.code}: {raw[:300]!r}", "result": None}


def main() -> None:
    anon = anon_key()
    fkey = Fernet.generate_key()  # 일회용 — 운영 키 사용 금지
    f = Fernet(fkey)

    hdr = f"{'평문':<26}{'①Py→Deno':>11}{'②Deno→Py':>11}{'③변조거부':>11}{'복호ms':>9}{'암호ms':>9}  판정"
    print(hdr)
    print("-" * len(hdr))
    fails = 0

    for plaintext in SAMPLES:
        token = f.encrypt(plaintext.encode("utf-8")).decode("utf-8")
        edge = call_edge(
            {"key": fkey.decode("utf-8"), "token": token, "plaintext": plaintext}, anon
        )
        label = (plaintext[:22] + "…") if len(plaintext) > 23 else (plaintext or "(빈 문자열)")

        if edge.get("error"):
            print(f"{label:<26} Edge 오류: {str(edge['error'])[:60]}")
            fails += 1
            continue

        r = edge["result"]
        # ① Deno 가 Python 토큰을 제대로 읽었는가
        py_to_deno = r["plaintext"] == plaintext
        # ② Python 이 Deno 토큰을 읽을 수 있는가
        try:
            back = f.decrypt(r["reEncoded"].encode("utf-8")).decode("utf-8")
            deno_to_py = back == plaintext
        except InvalidToken:
            deno_to_py = False
        # ③ 변조 거부 — Deno 가 예외 메시지를 돌려줬으면 거부한 것
        tamper_ok = bool(r["tamperRejected"])

        ok = py_to_deno and deno_to_py and tamper_ok
        if not ok:
            fails += 1
        print(
            f"{label:<26}{('O' if py_to_deno else 'X'):>11}{('O' if deno_to_py else 'X'):>11}"
            f"{('O' if tamper_ok else 'X'):>11}{r['decryptMs']:>9.2f}{r['encryptMs']:>9.2f}"
            f"  {'PASS' if ok else 'FAIL'}"
        )

    # ④ 다른 키로는 복호되면 안 된다
    other = Fernet.generate_key()
    token = f.encrypt(b"WRONG_KEY_TEST").decode("utf-8")
    edge = call_edge({"key": other.decode("utf-8"), "token": token}, anon)
    wrong_key_rejected = bool(edge.get("error"))
    if not wrong_key_rejected:
        fails += 1
    print()
    print(f"④ 다른 키 거부: {'O' if wrong_key_rejected else 'X — 잘못된 키로 복호됨'}")
    if wrong_key_rejected:
        print(f"   거부 사유: {str(edge['error']).splitlines()[0][:100]}")

    print()
    print("기준: Python↔Deno 양방향 복호 성공 · 변조 토큰 거부 · 다른 키 거부")
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
