"""`pydifflib.ts` 를 CPython `difflib.SequenceMatcher(None, a, b).ratio()` 와 대조.

`dedup` 의 Tier 3 판정이 파일명 유사도 0.6 을 임계로 쓴다. 이 값이 조금만 달라도
"이전 버전 관계" 판정이 뒤집히므로 알고리즘을 그대로 옮겼는지 확인해야 한다.

## 무엇을 노렸나
- **autojunk 경계** — `len(b) >= 200` 에서 켜지고, `len(b)/100 + 1` 회 초과 원소를
  색인에서 뺀다. 이걸 빼먹으면 긴 파일명에서 값이 달라진다.
- **코드포인트 vs UTF-16** — 이모지가 든 이름에서 길이부터 갈린다.
- **인자 순서** — `b` 만 색인하므로 `ratio(a,b) != ratio(b,a)` 인 경우가 있다.
- 무작위 쌍 대량 대조로 위 셋 밖의 차이도 잡는다.

사용:
    api/.venv/bin/python api/scripts/verify_pydifflib_parity.py
    api/.venv/bin/python api/scripts/verify_pydifflib_parity.py --negative
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import tempfile
from difflib import SequenceMatcher

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SHARED = os.path.join(ROOT, "supabase", "functions", "_shared")
DENO_CONFIG = os.path.join(ROOT, "supabase", "functions", "deno.json")

REAL_NAMES = [
    "user/253e3fa9-d706-5912-8aa2-1c6fd48a9a4d/6da9ce19ab324293d9540b2f9125cd13.pdf",
    "user/253e3fa9-d706-5912-8aa2-1c6fd48a9a4d/6da9ce19ab324293d9540b2f9125cd14.pdf",
    "user/aaaaaaaa-0000-0000-0000-000000000000/ffffffffffffffffffffffffffffffff.pdf",
    "보건의료_빅데이터_플랫폼_시범사업_추진계획(안).pdf",
    "보건의료_빅데이터_플랫폼_시범사업_추진계획(최종).pdf",
    "[삼성전자]사업보고서(2026.03.10).pdf",
    "[SK]사업보고서(2026.03.18).pdf",
    "report_v1.docx", "report_v2.docx", "report-final.docx",
    "",
    "a",
    "aa",
    "😀 회의록 2026-01.pdf",
    "😀😀 회의록 2026-02.pdf",
    "𠮷野家メニュー.pdf",
]

def build_pairs() -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    # 실제 이름 전조합 (양방향 — 인자 순서가 값을 바꾼다)
    for i, a in enumerate(REAL_NAMES):
        for b in REAL_NAMES[i:]:
            pairs.append((a, b))
            if a != b:
                pairs.append((b, a))
    # autojunk 경계 — 199 / 200 / 201 자
    for n in (150, 199, 200, 201, 400):
        pairs.append(("x" * n, "x" * n))
        pairs.append(("x" * n, "x" * (n - 1) + "y"))
        pairs.append(("ab" * (n // 2), "ba" * (n // 2)))
        pairs.append(("가" * n, "가" * (n - 3) + "나다라"))
        # 흔한 원소가 섞인 긴 문자열
        pairs.append(("a" * n + "bcdef", "a" * n + "bcdeg"))
    # 무작위 — 알파벳을 좁혀 반복이 많이 나오게 한다(autojunk 를 자극)
    rnd = random.Random(20260907)
    for _ in range(400):
        alpha = rnd.choice(["ab", "abc", "abcdef", "abcdefghij가나다😀"])
        la = rnd.randint(0, 260)
        lb = rnd.randint(0, 260)
        pairs.append((
            "".join(rnd.choice(alpha) for _ in range(la)),
            "".join(rnd.choice(alpha) for _ in range(lb)),
        ))
    return pairs


RUNNER_TS = """
import { sequenceMatcherRatio } from "file://%(shared)s/pydifflib.ts";
const cfg = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const out = cfg.pairs.map(([a, b]: [string, string]) => sequenceMatcherRatio(a, b));
if (cfg.negative === true) out[0] = (out[0] as number) + 1e-9;
await Deno.writeTextFile(Deno.args[1], JSON.stringify(out));
""" % {"shared": SHARED}


def main() -> None:
    negative = "--negative" in sys.argv
    pairs = build_pairs()
    py = [SequenceMatcher(None, a, b).ratio() for a, b in pairs]

    with tempfile.TemporaryDirectory() as tmp:
        cf, rf = os.path.join(tmp, "c.json"), os.path.join(tmp, "r.ts")
        of = os.path.join(tmp, "out.json")
        with open(cf, "w", encoding="utf-8") as f:
            json.dump({"pairs": pairs, "negative": negative}, f)
        with open(rf, "w", encoding="utf-8") as f:
            f.write(RUNNER_TS)
        proc = subprocess.run(
            ["deno", "run", "--config", DENO_CONFIG, "--allow-all", rf, cf, of],
            capture_output=True, text=True, timeout=1200,
        )
        if proc.returncode != 0:
            raise SystemExit(f"deno 실행 실패:\n{proc.stderr[:4000]}")
        with open(of, encoding="utf-8") as f:
            ts = json.load(f)

    fails = []
    worst = 0.0
    for (a, b), pa, tb in zip(pairs, py, ts):
        d = abs(pa - tb)
        worst = max(worst, d)
        # 부동소수 연산 순서가 같아야 하므로 **완전 일치**를 요구한다.
        if pa != tb:
            fails.append(f"ratio({a[:24]!r}…, {b[:24]!r}…) py={pa!r} ts={tb!r} 차={d}")

    for f in fails[:10]:
        print(f"  **{f}**")
    print(f"  쌍 {len(pairs)}건 대조, 불일치 {len(fails)}건, 최대 오차 {worst}")
    if negative:
        print("음성 대조: " + ("검사기 정상 (변조 검출)" if fails else "**검사기가 못 잡는다**"))
        sys.exit(0 if fails else 1)
    print("FAIL 0" if not fails else f"FAIL {len(fails)}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
