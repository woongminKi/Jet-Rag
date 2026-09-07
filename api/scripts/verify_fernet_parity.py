"""Fernet 구현(`_shared/billing/fernet.ts`)이 Python `cryptography` 와 **교차 호환**인지.

## 왜 교차 복호화인가
IV 가 난수고 timestamp 가 현재 시각이라 같은 입력도 매번 다른 토큰이 된다 —
**바이트 일치는 애초에 불가능하다.** 대신 양방향을 다 돌린다:

1. Python 이 만든 토큰 → TS 가 푼다   (DB 에 이미 있는 값을 Edge 가 읽을 수 있는가)
2. TS 가 만든 토큰 → Python 이 푼다   (이관 중 Railway 가 아직 읽을 수 있는가)

`subscriptions.billing_key` 에는 SID(빌링키)가 들어간다. 못 풀면 **자동결제가 통째로
멈춘다** — 조용히 틀리면 안 되는 자리다.

## 거절도 같아야 한다
위조·잘림·키 불일치·버전 오류를 한쪽만 받아 주면 그게 곧 구멍이다. 8 가지 손상 패턴을
양쪽에 똑같이 먹여 **둘 다 거절하는지** 본다.

**운영 키를 쓰지 않는다** — 이 스크립트가 매번 새 키를 만들어 쓴다.

사용:
    api/.venv/bin/python api/scripts/verify_fernet_parity.py
    api/.venv/bin/python api/scripts/verify_fernet_parity.py --negative
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile

from cryptography.fernet import Fernet, InvalidToken

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SHARED = os.path.join(ROOT, "supabase", "functions", "_shared")
DENO_CONFIG = os.path.join(ROOT, "supabase", "functions", "deno.json")

# 실제 KakaoPay SID 를 닮은 것 + 경계값들. 운영 값은 하나도 없다.
PLAINTEXTS = [
    "S1234567890abcdef1234567890abcdef",   # SID 형태
    "",                                     # 빈 문자열
    "a",                                    # 1 바이트 — PKCS7 이 15 바이트 채운다
    "0123456789abcde",                      # 15 바이트
    "0123456789abcdef",                     # 정확히 한 블록 → 패딩 블록이 하나 더 붙는다
    "0123456789abcdef0",                    # 17 바이트
    "한글 SID 는 없지만 UTF-8 다바이트를 확인한다",
    "x" * 1000,                             # 긴 값
    "줄바꿈\n포함\t탭",
]

RUNNER_TS = """
import { fernetDecrypt, fernetEncrypt } from "file://%(shared)s/billing/fernet.ts";

const cfg = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const out: Record<string, unknown> = {};

// 1) Python 이 만든 토큰을 TS 가 푼다
out.decrypted = [];
for (const t of cfg.pyTokens) {
  try {
    (out.decrypted as unknown[]).push({ ok: true, text: await fernetDecrypt(cfg.key, t) });
  } catch (e) {
    (out.decrypted as unknown[]).push({ ok: false, error: String(e) });
  }
}

// 2) TS 가 만든 토큰 — Python 이 푼다
out.tsTokens = [];
for (const p of cfg.plaintexts) {
  (out.tsTokens as unknown[]).push(await fernetEncrypt(cfg.key, p));
}

// 3) 손상된 토큰을 거절하는가
out.rejected = [];
for (const t of cfg.corrupt) {
  try {
    await fernetDecrypt(cfg.key, t);
    (out.rejected as unknown[]).push(false); // 풀렸다 = 구멍
  } catch {
    (out.rejected as unknown[]).push(true);
  }
}

// 4) 다른 키로는 못 푼다
out.wrongKey = [];
for (const t of cfg.pyTokens) {
  try {
    await fernetDecrypt(cfg.otherKey, t);
    (out.wrongKey as unknown[]).push(false);
  } catch {
    (out.wrongKey as unknown[]).push(true);
  }
}

// 5) 잘못된 키 자체를 거절하는가
out.badKeys = [];
for (const k of cfg.badKeys) {
  try {
    await fernetEncrypt(k, "x");
    (out.badKeys as unknown[]).push(false);
  } catch {
    (out.badKeys as unknown[]).push(true);
  }
}

if (cfg.negative) (out.tsTokens as string[])[0] = "변조된토큰";
await Deno.writeTextFile(Deno.args[1], JSON.stringify(out));
""" % {"shared": SHARED}


def corrupt_tokens(key: str, token: str) -> list[tuple[str, str]]:
    """8 가지 손상 패턴. 양쪽이 **똑같이 거절**해야 한다."""
    raw = base64.urlsafe_b64decode(token)

    def enc(b: bytes) -> str:
        return base64.urlsafe_b64encode(b).decode()

    flip_mac = bytearray(raw)
    flip_mac[-1] ^= 0xFF
    flip_ct = bytearray(raw)
    flip_ct[30] ^= 0xFF
    flip_iv = bytearray(raw)
    flip_iv[10] ^= 0xFF
    bad_ver = bytearray(raw)
    bad_ver[0] = 0x79
    flip_ts = bytearray(raw)
    flip_ts[3] ^= 0xFF
    return [
        ("MAC 1비트 뒤집기", enc(bytes(flip_mac))),
        ("암호문 1비트 뒤집기", enc(bytes(flip_ct))),
        ("IV 1비트 뒤집기", enc(bytes(flip_iv))),
        ("버전 바이트 변조", enc(bytes(bad_ver))),
        ("timestamp 변조", enc(bytes(flip_ts))),
        ("꼬리 32바이트 절단", enc(raw[:-32])),
        ("base64 아님", "이건!!base64가@@아니다"),
        ("빈 문자열", ""),
    ]


BAD_KEYS = [
    "",                                        # 빈 키
    "short",                                   # base64 도 아님
    base64.urlsafe_b64encode(b"x" * 16).decode(),  # 16 바이트 — 32 여야 한다
    base64.urlsafe_b64encode(b"x" * 64).decode(),  # 64 바이트
]


def main() -> None:
    negative = "--negative" in sys.argv
    # **매번 새 키.** 운영 키를 쓰지 않는다.
    key = Fernet.generate_key().decode()
    other_key = Fernet.generate_key().decode()
    f = Fernet(key.encode())

    py_tokens = [f.encrypt(p.encode()).decode() for p in PLAINTEXTS]
    corrupt = corrupt_tokens(key, py_tokens[0])

    with tempfile.TemporaryDirectory() as tmp:
        cf, rf = os.path.join(tmp, "c.json"), os.path.join(tmp, "r.ts")
        of = os.path.join(tmp, "out.json")
        with open(cf, "w", encoding="utf-8") as fp:
            json.dump({
                "key": key, "otherKey": other_key,
                "plaintexts": PLAINTEXTS, "pyTokens": py_tokens,
                "corrupt": [c[1] for c in corrupt], "badKeys": BAD_KEYS,
                "negative": negative,
            }, fp)
        with open(rf, "w", encoding="utf-8") as fp:
            fp.write(RUNNER_TS)
        proc = subprocess.run(
            ["deno", "run", "--config", DENO_CONFIG, "--allow-all", rf, cf, of],
            capture_output=True, text=True, timeout=600,
        )
        if proc.returncode != 0:
            raise SystemExit(f"deno 실행 실패:\n{proc.stderr[:4000]}")
        with open(of, encoding="utf-8") as fp:
            ts = json.load(fp)

    fails: list[str] = []
    checks = 0

    def cmp(label, a, b):
        nonlocal checks
        checks += 1
        if a != b:
            fails.append(f"{label}\n      py={a!r}\n      ts={b!r}")

    print("  --- 1. Python 이 만든 토큰을 TS 가 푼다 (DB 의 기존 값) ---")
    for p, got in zip(PLAINTEXTS, ts["decrypted"]):
        label = (p[:24] + "…") if len(p) > 24 else (p or "(빈 문자열)")
        cmp(f"복호화 {label!r} ok", True, got["ok"])
        cmp(f"복호화 {label!r} 값", p, got.get("text"))
        mark = "일치" if got["ok"] and got.get("text") == p else "**불일치**"
        print(f"    {label!r:<32} {mark}")

    print("  --- 2. TS 가 만든 토큰을 Python 이 푼다 (이관 중 역방향) ---")
    for p, tok in zip(PLAINTEXTS, ts["tsTokens"]):
        label = (p[:24] + "…") if len(p) > 24 else (p or "(빈 문자열)")
        checks += 1
        try:
            got = f.decrypt(tok.encode()).decode()
            ok = got == p
            if not ok:
                fails.append(f"TS 토큰 복호화 값이 다르다 {label!r}: {got!r}")
        except (InvalidToken, Exception) as exc:  # noqa: BLE001
            ok = False
            fails.append(f"Python 이 TS 토큰을 못 푼다 {label!r}: {exc}")
        print(f"    {label!r:<32} {'일치' if ok else '**불일치**'}")

    print("  --- 3. 손상된 토큰은 양쪽 다 거절한다 ---")
    for (name, tok), ts_rejected in zip(corrupt, ts["rejected"]):
        try:
            f.decrypt(tok.encode())
            py_rejected = False
        except Exception:  # noqa: BLE001
            py_rejected = True
        cmp(f"거절 [{name}]", py_rejected, ts_rejected)
        both = py_rejected and ts_rejected
        print(f"    {name:<22} py={'거절' if py_rejected else '**통과**'} "
              f"ts={'거절' if ts_rejected else '**통과**'} {'' if both else '  ← 구멍'}")

    print("  --- 4. 다른 키로는 못 푼다 ---")
    all_rejected = all(ts["wrongKey"])
    cmp("다른 키 전부 거절", True, all_rejected)
    print(f"    {len(ts['wrongKey'])}건 중 거절 {sum(ts['wrongKey'])}건")

    print("  --- 5. 잘못된 키 자체를 거절한다 ---")
    for k, rejected in zip(BAD_KEYS, ts["badKeys"]):
        try:
            Fernet(k.encode())
            py_bad = False
        except Exception:  # noqa: BLE001
            py_bad = True
        cmp(f"키 거절 {k[:16]!r}", py_bad, rejected)
        print(f"    {(k[:20] or '(빈 키)'):<24} py={'거절' if py_bad else '허용'} "
              f"ts={'거절' if rejected else '허용'}")

    for x in fails[:10]:
        print(f"  **{x}**")
    print(f"  비교 {checks}건, 불일치 {len(fails)}건")
    if negative:
        print("음성 대조: " + ("검사기 정상 (변조 검출)" if fails else "**검사기가 못 잡는다**"))
        sys.exit(0 if fails else 1)
    print("FAIL 0" if not fails else f"FAIL {len(fails)}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
