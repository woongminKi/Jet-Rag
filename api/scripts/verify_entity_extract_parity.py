"""`entity_extract.py` 의 룰 기반 엔티티 4종을 Python 원본과 대조.

## 이 조각이 왜 위험한가
정규식 8개가 전부 `\\b` 와 `\\d` 를 쓴다. **둘 다 Python↔JS 가 다르다.**

- Python `\\b` = 유니코드 `\\w` 경계 / JS `\\b` = **ASCII** `\\w` 경계
  → `50,000원` 이 JS 에선 아예 안 잡히고, `약25%` 는 JS 에서만 잡힌다 (§21 실측 6건)
- Python `\\d` = 유니코드 Nd 전부 → 전각 `２５%` 가 잡힌다

그래서 `PY_WORD_CLASS`(= `isalnum` + `_`, `v` 플래그 집합 뺄셈) lookaround 로 풀었다.

## 케이스가 노리는 것
- §21 fixture 17건 (JS 기본 `\\b` 로 옮겼다면 6건이 갈렸을 입력) — **회귀 고정**
- `\\b` 축약형(`(?<!W)`/`(?!W)`)이 틀리는 지점 — ISBN 이 `-` 로 끝나는 경우
- 날짜 3패턴의 lookbehind/lookahead 경계
- `m.lastindex` 분기 (ISSN/ISBN 은 group(1), `제N호` 는 group(0))
- 순서 보존 dedup, `pyStrip` 경계 문자

사용:
    api/.venv/bin/python api/scripts/verify_entity_extract_parity.py
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
FIXTURE = os.path.join(HERE, "fixtures", "entity_regex_baseline.json")

sys.path.insert(0, os.path.join(ROOT, "api"))

# §21 에서 Python 기준 정답을 떠 둔 입력들. JS 기본 `\b` 면 6건이 갈린다.
with open(FIXTURE, encoding="utf-8") as _f:
    BASELINE_CASES: list[str] = json.load(_f)["cases"]

EXTRA_CASES = [
    "",
    "엔티티 없음",
    # --- `\b` 축약형이 틀리는 지점 ---
    # greedy 가 `-` 로 끝나고 **다음 문자가 word** → Python 은 경계 성립.
    # `(?!W)` 로 줄여 썼다면 백트랙해서 `-` 를 뱉는다.
    "ISBN 1234567890-a",
    "ISBN 1234567890-",
    "ISBN 979-11-1234-5X 뒤",
    "ISBN 979-11-1234-5X뒤",     # 뒤가 한글 = Python 기준 word
    "ISSN 2288-708X",
    "ISSN 2288-708X가",           # 뒤가 한글
    "ISSN2288-7083",              # `\s+` 없음 → 매칭 안 됨
    # --- 날짜 ---
    "2024년 4월 30일",
    "2024년4월",
    "2024. 12. 31.",
    "2024.12.31",
    "12024.12.31",                # 앞이 숫자 → `(?<!\d)` 로 차단
    "2024.12.311",                # 뒤가 숫자 → `(?!\d)` 로 차단
    "2024-04",
    "2024/12/31",
    "２０２４년 ４월",              # **전각** — Python `\d` 는 잡는다
    "٢٠٢٤-٠٤",                    # 아라비아-인도 숫자
    # --- 금액·백분율 경계 ---
    "₩1,000",
    "₩1,000원",
    "1.5%",
    "1000%",                      # `\d{1,3}` → `100` 만
    "50,000 원",                  # 사이 공백
    "50,000 원",             # NBSP — Python `\s`
    "50,000원",             # **U+001C** — Python `\s` 만
    "50,000﻿원",             # **U+FEFF** — JS `\s` 만
    "제12호 제12호 제13호",        # 순서 보존 dedup
    "3만원 5억원 7조원 9천원",
    "_50,000원",                  # 앞이 `_` = word → `\b` 불성립
    "Ᲊ50,000원",                  # **U+1C8A** — JS 만 word (ALNUM_EXCESS)
    "🙂25%",                       # astral, non-word
]

CASES = BASELINE_CASES + EXTRA_CASES

RUNNER_TS = f"""
import {{ entitiesEmpty, extractEntities }} from "file://{SHARED}/ingest/entity_extract.ts";

const cases = JSON.parse(await Deno.readTextFile(Deno.args[0]));
console.log(JSON.stringify(cases.map((t: string) => {{
  const e = extractEntities(t);
  return {{ ...e, empty: entitiesEmpty(e) }};
}})));
"""


def run_deno(cases: list[str], timeout: int = 300) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        cf, rf = os.path.join(tmp, "cases.json"), os.path.join(tmp, "runner.ts")
        with open(cf, "w", encoding="utf-8") as f:
            json.dump(cases, f)
        with open(rf, "w", encoding="utf-8") as f:
            f.write(RUNNER_TS)
        proc = subprocess.run(
            ["deno", "run", "--config", DENO_CONFIG, "--allow-all", rf, cf],
            capture_output=True, text=True, timeout=timeout,
        )
    if proc.returncode != 0:
        raise SystemExit(f"deno 실행 실패:\n{proc.stderr[:3000]}")
    return json.loads(proc.stdout)


def main() -> None:
    from app.services.entity_extract import extract_entities

    ts = run_deno(CASES)
    if len(ts) != len(CASES):
        raise SystemExit(f"결과 개수 불일치 {len(ts)} != {len(CASES)}")

    fails = 0
    hits = 0          # 뭐라도 추출된 케이스 수 — 케이스 유효성 확인용
    per_kind = {"dates": 0, "amounts": 0, "percentages": 0, "identifiers": 0}

    for i, text in enumerate(CASES):
        e = extract_entities(text)
        want = {**e.to_dict(), "empty": e.is_empty()}
        got = ts[i]
        if not want["empty"]:
            hits += 1
        for k in per_kind:
            if want[k]:
                per_kind[k] += 1
        if want != got:
            fails += 1
            print(f"  **[{i}] 불일치** 입력 {json.dumps(text, ensure_ascii=False)}")
            for k in ("dates", "amounts", "percentages", "identifiers", "empty"):
                if want[k] != got.get(k):
                    print(f"      {k:<13} py={json.dumps(want[k], ensure_ascii=False)}"
                          f"  ts={json.dumps(got.get(k), ensure_ascii=False)}")

    print(f"  extractEntities           {len(CASES)}건 대조 "
          f"(추출 발생 {hits} / 빈 결과 {len(CASES) - hits})")
    print(f"    종류별 추출 발생: " +
          "  ".join(f"{k} {v}" for k, v in per_kind.items()))

    # 케이스 무효 검사 — 네 종류가 전부 한 번은 잡혀야 대조가 의미 있다.
    for k, v in per_kind.items():
        if v == 0:
            fails += 1
            print(f"    **케이스 무효** — {k} 가 한 번도 안 잡혔다")
    if hits == 0 or hits == len(CASES):
        fails += 1
        print("    **케이스 무효** — 추출/미추출 한쪽만 태워졌다")

    print()
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
