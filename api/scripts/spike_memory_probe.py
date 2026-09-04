"""S5 채점기 — Edge Function 의 메모리 상한을 **할당 사다리**로 실측한다.

## 왜 이런 방식인가
Edge 의 `Deno.memoryUsage()` 는 0 을 돌려준다(2026-09-04 실측). 즉 "지금 얼마 쓰는가"를
읽을 계기가 없다. 그래서 **"얼마나 더 쓸 수 있는가"** 를 잰다:

    파서 없이 죽는 지점(A) − 파싱 산출물을 든 채 죽는 지점(B) = 파서가 붙잡고 있는 양

없는 계기를 **두 번의 임계 측정의 차이**로 대체하는 것이다. 상한 자체도 이 과정에서 나온다.

## 실패 신호를 구분해야 한다
CPU 초과와 메모리 초과가 **둘 다** `WORKER_RESOURCE_LIMIT`(HTTP 546) 로 오면, 앞으로
어떤 실패를 봐도 원인을 못 가른다. 그래서 CPU 초과 케이스(`?kind=burn&ms=3000`)를 같이
찍어 두 신호를 대조한다. 같으면 "구분 불가"라고 기록해야지, 추측으로 CPU 라고 쓰면 안 된다.

사용:
    python3 api/scripts/spike_memory_probe.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
FUNCTION_URL = "https://mpmtydudhojpukuuadrd.supabase.co/functions/v1/spike"

# 사다리 눈금(MB). 8MB 청크로 할당하므로 8의 배수로 둔다.
# 기본은 거친 탐색, `--from/--to/--step` 으로 정밀 구간을 다시 훑는다.
COARSE = [32, 64, 96, 128, 160, 192, 224, 256, 320, 384, 448, 512]


def ladder_from_args() -> list[int]:
    def opt(name: str, default: int) -> int:
        for a in sys.argv[1:]:
            if a.startswith(f"--{name}="):
                return int(a.split("=", 1)[1])
        return default

    if not any(a.startswith("--from=") for a in sys.argv[1:]):
        return COARSE
    return list(range(opt("from", 160), opt("to", 256) + 1, opt("step", 8)))


LADDER = COARSE

# (라벨, parse 모드, 파일 경로) — 파일은 로컬 실자산. 없으면 그 행은 건너뛴다.
PARSE_CASES = [
    ("파서 없음", "none", None),
    ("PDF 1페이지", "pdf", "assets/public/law sample3.pdf"),
    ("HWPX 497섹션", "hwpx", "직제_규정(2024.4.30.개정).hwpx"),
    ("DOCX 322섹션", "docx", "승인글 템플릿1.docx"),
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


def call(url: str, key: str, body: bytes = b"") -> tuple[int, dict | str]:
    """(HTTP 상태, 파싱된 본문 또는 원문). 실패도 결과이므로 예외로 버리지 않는다."""
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/octet-stream"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw.decode("utf-8", "replace")[:300]
    except Exception as e:  # 연결 자체가 끊기는 경우도 신호다
        return 0, f"{type(e).__name__}: {e}"


def failure_signature(status: int, payload: dict | str) -> str:
    if isinstance(payload, dict):
        code = payload.get("code")
        if code:
            return f"HTTP {status} / {code}"
        if payload.get("error"):
            return f"HTTP {status} / {str(payload['error']).splitlines()[0][:60]}"
        return f"HTTP {status} / (본문에 error 없음)"
    return f"HTTP {status} / {payload[:60]}"


def find_ceiling(key: str, parse: str, body: bytes) -> tuple[int, str | None]:
    """사다리를 올라가며 마지막으로 성공한 MB 와 첫 실패 신호를 돌려준다."""
    last_ok = 0
    signature = None
    for mb in LADDER:
        url = f"{FUNCTION_URL}?kind=mem&mb={mb}&parse={parse}"
        status, payload = call(url, key, body)
        ok = status == 200 and isinstance(payload, dict) and not payload.get("error")
        if ok:
            last_ok = mb
            continue
        signature = failure_signature(status, payload)
        break
    return last_ok, signature


def main() -> None:
    global LADDER
    LADDER = ladder_from_args()
    key = anon_key()
    print(f"사다리: {LADDER[0]}~{LADDER[-1]}MB, 간격 {LADDER[1] - LADDER[0]}MB\n")

    print("=== 1) CPU 초과의 실패 신호 (대조군) ===")
    status, payload = call(f"{FUNCTION_URL}?kind=burn&ms=3000", key)
    cpu_sig = failure_signature(status, payload) if status != 200 else f"HTTP {status} (초과 안 남)"
    print(f"  burn 3000ms → {cpu_sig}")
    print()

    print("=== 2) 메모리 상한 사다리 ===")
    hdr = f"{'케이스':<18}{'마지막 성공(MB)':>16}{'첫 실패 신호':>34}"
    print(hdr)
    print("-" * 70)
    results = []
    for label, parse, rel in PARSE_CASES:
        body = b""
        if rel:
            path = os.path.join(ROOT, rel)
            if not os.path.exists(path):
                print(f"{label:<18}{'파일 없음 — 건너뜀':>16}")
                continue
            with open(path, "rb") as f:
                body = f.read()
        ceiling, sig = find_ceiling(key, parse, body)
        results.append((label, ceiling, sig))
        print(f"{label:<18}{ceiling:>16}{(sig or '사다리 끝까지 성공'):>34}")

    print()
    if results and results[0][0]:
        base_ceiling = results[0][1]
        print("=== 3) 파서 실사용량 역산 (파서 없음 상한 − 각 상한) ===")
        for label, ceiling, _ in results[1:]:
            print(f"  {label:<18} 약 {base_ceiling - ceiling:>4}MB")
        print()
        print(f"  ※ 눈금 간격 {LADDER[1] - LADDER[0]}MB — 이 값이 곧 오차 하한이다.")

    print()
    print("메모리 신호와 CPU 신호가 같으면 앞으로 실패 원인을 못 가른다 — work-log 에 그대로 기록할 것.")


if __name__ == "__main__":
    main()
