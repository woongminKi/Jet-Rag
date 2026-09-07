"""DeepInfra 임베딩 어댑터의 **순수 부분**을 Python 원본과 대조.

## 무엇을 대조하고 무엇을 안 하는가
- `_parse_retry_after` · `_parse_batch_response` 는 순수 함수라 그대로 대조한다.
- HTTP 호출·재시도 루프는 대조 대상이 아니다. 실제 API 를 때리면 **쿼터와 비용**이
  나가고, mock 을 두면 양쪽이 다른 mock 을 보게 되어 대조의 의미가 없다. 그쪽은
  `embed_provider_test.ts` 가 계약으로 고정한다.

## 이 두 함수가 틀리면 조용히 나빠진다
- 배치 응답 정렬이 틀리면 **엉뚱한 청크에 벡터가 박힌다.** 검색이 이상해지는데
  원인을 찾기 어렵다.
- `Retry-After` 파싱이 틀리면 429 폭풍에서 쿼터를 태운다.

사용:
    api/.venv/bin/python api/scripts/verify_embed_provider_parity.py
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

# 기준 시각 — HTTP-date 케이스를 결정적으로 만들려고 양쪽에 같은 값을 준다.
NOW_MS = 1_893_456_000_000  # 2030-01-01T00:00:00Z

RETRY_AFTER_CASES = [
    None, "", "   ",
    "0", "1", "7", "60", "61", "99999",
    "-1", "-5", "+5",
    "1.5", "abc", "later", "5 seconds",  # HTTP 헤더는 ASCII 만 — 한글 케이스는 비현실적이라 뺐다
    "Tue, 01 Jan 2030 00:00:30 GMT",   # +30s
    "Tue, 01 Jan 2030 00:02:00 GMT",   # +120s → 60 으로 잘림
    "Tue, 01 Jan 2029 00:00:00 GMT",   # 과거 → None
    "Tue, 01 Jan 2030 00:00:00 GMT",   # 정확히 0 → None
]

DIM = 1024


def vec(seed: int) -> list[float]:
    return [float((seed + i) % 7) for i in range(DIM)]


BATCH_CASES = [
    # (설명, 응답 dict, expected)
    ("정상 1건", {"data": [{"embedding": vec(0), "index": 0}]}, 1),
    ("index 뒤섞임", {"data": [
        {"embedding": vec(2), "index": 2},
        {"embedding": vec(0), "index": 0},
        {"embedding": vec(1), "index": 1},
    ]}, 3),
    ("index 없음", {"data": [{"embedding": vec(5)}, {"embedding": vec(9)}]}, 2),
    ("길이 불일치", {"data": [{"embedding": vec(0)}]}, 2),
    ("차원 불일치", {"data": [{"embedding": [1.0, 2.0], "index": 0}]}, 1),
    ("data 없음", {"oops": 1}, 1),
    ("data 가 배열 아님", {"data": {"a": 1}}, 1),
    ("embedding 없음", {"data": [{"index": 0}]}, 1),
    ("빈 배열 + expected 0", {"data": []}, 0),
]

RUNNER_TS = f"""
import {{ parseBatchResponse, parseRetryAfter }} from "file://{SHARED}/ingest/embed_provider.ts";

const input = JSON.parse(await Deno.readTextFile(Deno.args[0]));
console.log(JSON.stringify({{
  retry: input.retry.map((raw: string | null) => parseRetryAfter(raw, input.nowMs)),
  batch: input.batch.map(([, body, expected]: [string, unknown, number]) => {{
    try {{
      return {{ ok: true, len: parseBatchResponse(body, expected).length,
               first: parseBatchResponse(body, expected).map((v: number[]) => v[0]) }};
    }} catch (e) {{
      return {{ ok: false, kind: (e as Error).name }};
    }}
  }}),
}}));
"""


def main() -> None:
    import app.adapters.impl.bgem3_deepinfra_embedding as D
    import httpx

    # --- Python 기준값 ---
    py_retry = []
    for raw in RETRY_AFTER_CASES:
        # 원본은 httpx.HTTPStatusError 에서 헤더를 꺼낸다. 같은 모양으로 만들어 준다.
        headers = {} if raw is None else {"Retry-After": raw}
        resp = httpx.Response(429, headers=headers)
        exc = httpx.HTTPStatusError("429", request=httpx.Request("POST", "http://x"), response=resp)
        # `time.time()` 대신 고정 시각을 쓰도록 잠시 갈아끼운다 — HTTP-date 케이스가
        # 실행 시각에 따라 흔들리면 대조가 안 된다.
        real_time = D.time.time
        D.time.time = lambda: NOW_MS / 1000  # type: ignore[assignment]
        try:
            py_retry.append(D._parse_retry_after(exc))
        finally:
            D.time.time = real_time  # type: ignore[assignment]

    py_batch = []
    for _label, body, expected in BATCH_CASES:
        # **request 를 반드시 붙인다.** 없으면 `raise_for_status()` 가 "request instance
        # has not been set" 으로 던져서 **정상 케이스까지 실패**한다. 처음에 그렇게 짜서
        # Python 이 전부 실패로 나왔다 — 대상이 아니라 자[尺]가 틀렸던 것이다.
        resp = httpx.Response(200, json=body, request=httpx.Request("POST", "http://x"))
        try:
            out = D._parse_batch_response(resp, expected=expected)
            py_batch.append({"ok": True, "len": len(out), "first": [v[0] for v in out]})
        except Exception:
            py_batch.append({"ok": False})

    with tempfile.TemporaryDirectory() as tmp:
        cf, rf = os.path.join(tmp, "c.json"), os.path.join(tmp, "r.ts")
        with open(cf, "w", encoding="utf-8") as f:
            json.dump({"retry": RETRY_AFTER_CASES, "batch": BATCH_CASES, "nowMs": NOW_MS}, f)
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

    # --- Retry-After ---
    bad = []
    for i, (raw, want) in enumerate(zip(RETRY_AFTER_CASES, py_retry)):
        got = ts["retry"][i]
        # 부동소수 오차 허용 (HTTP-date 경로가 초 단위 나눗셈을 한다)
        same = (want is None and got is None) or (
            want is not None and got is not None and abs(float(want) - float(got)) < 1e-6)
        if not same:
            bad.append((raw, want, got))
    if bad:
        fails += 1
        print(f"  **parseRetryAfter {len(bad)}건 불일치**")
        for raw, w, g in bad[:6]:
            print(f"    {raw!r:<34} py={w!r}  ts={g!r}")
    else:
        n_val = sum(1 for v in py_retry if v is not None)
        print(f"  parseRetryAfter           {len(RETRY_AFTER_CASES)}건 OK  "
              f"(값 있음 {n_val} / None {len(py_retry) - n_val})")
    if not (0 < sum(1 for v in py_retry if v is not None) < len(py_retry)):
        fails += 1
        print("    **케이스 무효** — 한쪽 결과만 나왔다")

    # --- 배치 파싱 ---
    bad2 = []
    for i, (label, _b, _e) in enumerate(BATCH_CASES):
        want, got = py_batch[i], ts["batch"][i]
        if want["ok"] != got["ok"]:
            bad2.append((label, want, got))
        elif want["ok"] and (want["len"] != got["len"] or want["first"] != got["first"]):
            bad2.append((label, want, got))
    if bad2:
        fails += 1
        print(f"  **parseBatchResponse {len(bad2)}건 불일치**")
        for label, w, g in bad2[:5]:
            print(f"    {label:<22} py={json.dumps(w)[:90]}")
            print(f"    {'':<22} ts={json.dumps(g)[:90]}")
    else:
        n_ok = sum(1 for v in py_batch if v["ok"])
        print(f"  parseBatchResponse        {len(BATCH_CASES)}건 OK  "
              f"(성공 {n_ok} / 실패 {len(py_batch) - n_ok})")
    if not (0 < sum(1 for v in py_batch if v["ok"]) < len(py_batch)):
        fails += 1
        print("    **케이스 무효** — 성공/실패 한쪽만 나왔다")

    print()
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
