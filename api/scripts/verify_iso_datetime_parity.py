"""Task 2.2 채점기 — `search/iso_datetime.ts` 를 Python `_parse_iso_date` 와 대조.

## 왜 fuzz 인가
이 파서의 결과는 `created_at` 필터 문자열이 되고, 틀리면 **400 대신 조용히 다른 날짜로
필터**된다(`new Date()` 는 `2026-02-30` 을 3 월 2 일로 롤오버시킨다 — 이걸 발견해서 파서를
직접 쓰게 됐다). 손으로 고른 케이스는 내가 상상한 문법만 덮는다. Python `fromisoformat`
문법은 넓고(주차 날짜·기본형식·아무 구분자·소수점 6 자리 절삭) 내 상상 밖이 곧 구멍이다.
그래서 문법 조각을 조합해 전수에 가깝게 만들고, 거기에 변이를 섞는다.

## 비대칭 계약
CPython 의 `fromisoformat` 은 C 구현이고 순수 파이썬 미러와도 어긋난다
(`2026-04-01T090Z`·`09:00.5` 를 C 는 받고 미러는 거부 — 실측). C 는 두 자리를 `int()` 로
읽어서 `" 9"` 나 아랍-인도 숫자까지 통과시킨다. 그 버그를 흉내내는 대신 TS 는 문법을
엄격하게 잡았고, 이 스크립트는 방향을 나눠 채점한다:

| 분류 | 판정 |
|---|---|
| 둘 다 통과 · 문자열 불일치 | **FAIL** — 조용한 오파싱 |
| Python 거부 · TS 통과 | **FAIL** — 400 대신 엉뚱한 날짜로 필터된다 |
| Python 통과 · TS 거부 | 허용. 대신 **개수와 표본을 출력**해 조용히 늘지 못하게 한다 |

현실에서 오는 형식은 `REQUIRED` 로 따로 고정한다 — 여기 있는 건 완전 일치여야 한다.

## 교차검증 — CPython 두 구현 사이
"엄격한 쪽"이 자의적이지 않다는 걸 보이려고 순수 파이썬 구현(`_pydatetime`)과도 잰다.
2026-09-04 실측(46,616 케이스, seed 2 종):

| 비교 대상 | 불일치 | 정체 |
|---|---|---|
| C 구현 (운영) | 1,458 | C 만 받아주는 것 — `09:00.5`(분에 붙은 소수) 1,009 · `090`(3 자리 시각) 449 |
| 순수 파이썬 구현 | 24 | 순수 구현만 받아주는 것 — 전각 숫자 `２０２６-０４-０１`, 꼬리 숫자 `2026-04-019` 등 |

**두 구현이 일치하는 지점에서는 TS 가 전부 일치한다.** 어긋나는 지점에서만, 양쪽 모두에서
엄격한 쪽을 택했다.

사용:
    api/.venv/bin/python api/scripts/verify_iso_datetime_parity.py [--seed N]
"""

from __future__ import annotations

import itertools
import json
import os
import random
import subprocess
import sys
import tempfile

import _pydatetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SEARCH_DIR = os.path.join(ROOT, "supabase", "functions", "_shared", "search")
DENO_CONFIG = os.path.join(ROOT, "supabase", "functions", "deno.json")

sys.path.insert(0, os.path.join(ROOT, "api"))

# 문법 조각 — 조합해서 전수에 가깝게 만든다.
DATES = [
    "2026-04-01", "20260401", "2026-W14-1", "2026W141", "2026-W14", "2026W14",
    "2028-02-29", "2026-02-29", "2026-02-30", "2026-13-01", "2026-00-01",
    "2026-04-00", "2026-04-31", "0001-01-01", "0000-01-01", "9999-12-31",
    "2026-4-1", "2026-0401", "202604-01", "2026-W53-1", "2020-W53-1",
    "2026-W00-1", "2026-W14-8", "2026-W14-0", "2026-099", "2026099",
]
SEPS = ["T", " ", "x", "", "_"]
TIMES = [
    "", "09", "09:00", "09:00:00", "0900", "090000", "09:00:00.5",
    "09:00:00.123456", "09:00:00.1234567", "09:00:00,5", "090000.5",
    "09:00.5", "24:00:00", "23:59:59", "09:60:00", "09:00:60", "0:0:0",
    "09:00:00.", "9", "09:0", "090", "09:00:00.0000001",
]
OFFSETS = [
    "", "Z", "z", "+00:00", "-00:00", "+09:00", "-09:30", "+0900", "+09",
    "+24:00", "+23:59", "+09:00:30", "+09:00:00.000001", "+", "+9:00", "+09:0",
]

# 프론트·API 문서가 실제로 보내는 형식. 여기는 완전 일치가 아니면 FAIL 이다.
REQUIRED = [
    "2026-04-01", "2026-12-31", "2028-02-29", "0001-01-01", "9999-12-31",
    "20260401", "2026-W14-1", "2026W141", "2026-W14", "2026W14",
    "2026-04-01T09:00:00", "2026-04-01T09:00:00Z", "2026-04-01T00:00:00Z",
    "2026-04-01T09:00:00+09:00", "2026-04-01T09:00:00-09:30",
    "2026-04-01T09:00:00+0900", "2026-04-01T09:00:00+09",
    "2026-04-01T09:00:00.123456", "2026-04-01T09:00:00.123456Z",
    "2026-04-01T09:00:00.1234567", "2026-04-01T09:00:00.5",
    "2026-04-01T09:00:00,5", "2026-04-01T09:00", "2026-04-01T09",
    "2026-04-01T0900", "2026-04-01T090000", "2026-04-01T090000.5",
    "2026-04-01 09:00:00", "2026-04-01x09:00:00",
    "2026-04-01T09:00:00+09:00:30", "2026-04-01T09:00:00+23:59",
    "2026-04-01T23:59:59",
    # 거부해야 하는 것들 — 여기가 뚫리면 엉뚱한 날짜로 필터된다.
    "2026-02-30", "2026-02-29", "2026-13-01", "2026-04-31", "2026-00-01",
    "2026-04-00", "0000-01-01", "2026-04-01T24:00:00", "2026-04-01T09:60:00",
    "2026-04-01T09:00:60", "2026-04-01T09:00:00+24:00", "+002026-04-01",
    "2026-04-01t00:00:00z", "2026-04-01T00:00:00z", "2026-4-1", "2026-0401",
    "", "2026", "2026-04",
]

# 조합 밖 — 구조 자체가 이상한 것들.
ODDBALLS = [
    "", " ", "2026", "2026-04", "2026-04-01T", "2026-04-01TZ",
    "+002026-04-01", "2026-04-01T00:00:00Z+09:00", "2026-04-01T00:00:00ZZ",
    "Z2026-04-01T00:00:00Z", "２０２６-０４-０１", "2026-04-01\n",
    "2026-04-01T09:00:00 ", " 2026-04-01", "2026-04-01T٠٩:00:00",
    "2026-04-01T09:00:00+00:00:00", "2026-04-01--09:00:00",
    "2026-04-01T-09:00", "2026-04-01T09:00:00-", "10000-01-01",
]


def build_cases(seed: int) -> list[str]:
    cases = list(REQUIRED) + list(ODDBALLS)
    cases += DATES
    for d, sep, t, off in itertools.product(DATES, SEPS, TIMES, OFFSETS):
        if not t and (sep or off):
            continue  # 시각이 없으면 구분자·오프셋도 없다
        cases.append(f"{d}{sep}{t}{off}")

    # 변이 — 조합 문법 밖으로 한 걸음씩 밀어낸다.
    rng = random.Random(seed)
    base = [c for c in cases if c]
    for _ in range(3000):
        s = rng.choice(base)
        op = rng.randrange(4)
        i = rng.randrange(len(s))
        if op == 0:
            s = s[:i] + s[i + 1:]
        elif op == 1:
            s = s[:i] + rng.choice("0123456789-:+TZ.,W ") + s[i:]
        elif op == 2:
            s = s[:i] + rng.choice("0123456789-:+TZ.,W ") + s[i + 1:]
        else:
            s = s + rng.choice("0123456789-:+TZ.,W ")
        cases.append(s)

    seen: set[str] = set()
    return [c for c in cases if not (c in seen or seen.add(c))]


RUNNER_TS = f"""
import {{ parseSearchDate }} from "file://{SEARCH_DIR}/iso_datetime.ts";
const cases: string[] = JSON.parse(await Deno.readTextFile(Deno.args[0]));
console.log(JSON.stringify(cases.map((c) => {{
  const r = parseSearchDate(c);
  return r === undefined ? "ERR" : (r === null ? "NONE" : r);
}})));
"""


def run_deno(cases: list[str]) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        cf = os.path.join(tmp, "cases.json")
        rf = os.path.join(tmp, "runner.ts")
        with open(cf, "w", encoding="utf-8") as f:
            json.dump(cases, f)
        with open(rf, "w", encoding="utf-8") as f:
            f.write(RUNNER_TS)
        proc = subprocess.run(
            ["deno", "run", "--config", DENO_CONFIG, "--allow-read", rf, cf],
            capture_output=True, text=True, timeout=300,
        )
    if proc.returncode != 0:
        raise SystemExit(f"deno 실행 실패:\n{proc.stderr[:2000]}")
    return json.loads(proc.stdout)


def pyref(value: str) -> str:
    """CPython 순수 파이썬 구현 위에 `_parse_iso_date` 와 같은 로직을 얹는다."""
    if not value:
        return "NONE"
    utc = _pydatetime.timezone.utc
    try:
        if len(value) == 10:
            return _pydatetime.datetime.fromisoformat(value).replace(tzinfo=utc).isoformat()
        n = value.replace("Z", "+00:00") if value.endswith("Z") else value
        dt = _pydatetime.datetime.fromisoformat(n)
    except (ValueError, AssertionError, IndexError, TypeError):
        # 순수 구현은 형식 오류에 AssertionError 도 낸다.
        return "ERR"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=utc)
    return dt.isoformat()


def main() -> None:
    seed = 20260904
    if "--seed" in sys.argv:
        seed = int(sys.argv[sys.argv.index("--seed") + 1])

    from fastapi import HTTPException

    from app.routers.search import _parse_iso_date

    cases = build_cases(seed)
    ts = run_deno(cases)

    required = set(REQUIRED)
    misparse: list[tuple[str, str, str]] = []   # 둘 다 통과인데 다름
    unsafe: list[tuple[str, str]] = []          # Python 거부 · TS 통과
    stricter: list[str] = []                    # Python 통과 · TS 거부 (허용)
    req_fail: list[tuple[str, str, str]] = []
    accepted = 0

    for c, tv in zip(cases, ts):
        try:
            dt = _parse_iso_date(c, "from_date")
            # 빈 문자열은 원본이 falsy 조기 반환으로 `None` 을 돌려준다.
            pv = dt.isoformat() if dt is not None else "NONE"
        except HTTPException:
            pv = "ERR"
        if pv != "ERR":
            accepted += 1

        if c in required and pv != tv:
            req_fail.append((c, pv, tv))
        elif pv == "ERR" and tv != "ERR":
            unsafe.append((c, tv))
        elif pv != "ERR" and tv == "ERR":
            stricter.append(c)
        elif pv != tv:
            misparse.append((c, pv, tv))

    print(f"케이스 {len(cases)}건 대조 (seed={seed}) — Python 이 받아준 것 {accepted}건")
    print()
    print(f"필수 통과 목록 {len(REQUIRED)}건 — 불일치 {len(req_fail)}")
    for c, pv, tv in req_fail[:20]:
        print(f"  MISMATCH {c!r:<34} py={pv!r:<34} ts={tv!r}")
    print(f"조용한 오파싱 (둘 다 통과·문자열 다름) — {len(misparse)}")
    for c, pv, tv in misparse[:20]:
        print(f"  MISPARSE {c!r:<34} py={pv!r:<34} ts={tv!r}")
    print(f"위험 (Python 거부·TS 통과) — {len(unsafe)}")
    for c, tv in unsafe[:20]:
        print(f"  UNSAFE   {c!r:<34} ts={tv!r}")
    print(f"엄격 (Python 통과·TS 거부, 허용) — {len(stricter)}")
    for c in stricter[:8]:
        print(f"    {c!r}")
    if len(stricter) > 8:
        print(f"    ... 외 {len(stricter) - 8}건")

    # 교차검증 — 엄격한 쪽이 자의적이지 않다는 근거.
    ref_diff = [c for c, tv in zip(cases, ts) if pyref(c) != tv]
    print(f"순수 파이썬 구현과의 불일치 — {len(ref_diff)} (순수 구현만 관대한 입력)")
    for c in ref_diff[:6]:
        print(f"    {c!r}")

    fails = len(req_fail) + len(misparse) + len(unsafe)
    print()
    print("FAIL 0" if not fails else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
