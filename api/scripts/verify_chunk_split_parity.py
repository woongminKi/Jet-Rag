"""`chunk.py` 의 문장 분할·날짜 마스킹·overlap 을 Python 원본과 대조.

## 왜 순수 함수 단위인가
`chunk` 단계는 543 줄이고 의존 모듈이 둘 더 있다. 한 번에 옮기면 어디서 갈렸는지
못 짚는다. 이 조각(분할·마스킹·overlap)은 **입력이 문자열뿐인 순수 함수**라 DB 없이
정확히 대조된다. 섹션 병합·레코드 변환은 다음 조각이다.

## 케이스가 노리는 것
Python↔JS 가 갈리는 지점을 **일부러** 태운다:
- `\\s` 문자 집합 (`\\x1c-\\x1f`·`\\x85` vs `U+FEFF`)
- `\\d` 유니코드 Nd (전각 숫자 날짜)
- `len()`·슬라이스가 코드포인트 (이모지·한자 확장)
- lookbehind 경계 (한글/닫는괄호 + 문장부호)
- overlap 예산 경계 (`MAX_SIZE` 딱 맞음/초과)

사용:
    api/.venv/bin/python api/scripts/verify_chunk_split_parity.py
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

_KO = "한국어 문장입니다. "          # **11자**. 처음 20자로 잘못 세어 케이스가 510자밖에
                                     # 안 됐고 분할이 아예 안 일어났다(임계 800). 실측으로 고침.
_EMOJI = "🙂"                        # UTF-16 2칸, 코드포인트 1칸

# --- 날짜 마스킹 ---
MASK_CASES = [
    "판결 2025. 7. 9. 선고",
    "2024. 12. 31. 및 2023. 1. 1. 두 건",
    "２０２５. ７. ９.",                 # **전각 숫자** — Python `\\d` 는 잡는다
    "٢٠٢٥. ٧. ٩.",                     # 아라비아-인도 숫자
    "2025.7.9.",                        # 공백 없음 → 매칭 안 됨
    "2025. 7. 9.",            # NBSP 구분 — Python `\\s` 는 공백
    "2025.7.9.",            # **U+001C** — Python `\\s` 만 공백
    "2025.﻿7.﻿9.",            # **U+FEFF** — JS `\\s` 만 공백
    "날짜 없음",
    "",
]

# --- 따옴표·괄호 균형 ---
BALANCE_CASES = [
    '따옴표 "하나', '따옴표 "둘"', "괄호 (열림", "괄호 (닫힘)",
    "「열림", "「닫힘」", "『열림", "['", "정상 문장",
    "“한글 여는", "“한글”", "‘작은", "‘작은’", "apostrophe don't", "",
]

# --- overlap ---
OVERLAP_CASES = [
    [],
    ["하나뿐"],
    ["가" * 150, "나" * 150],                       # 정상 — prefix 100
    ["가" * 50, "나" * 50],                         # prev 가 100 미만 → 전체가 prefix
    ["가" * 150, "나" * 999],                       # budget 0 → overlap 생략
    ["가" * 150, "나" * 950],                       # budget 49 → prefix 축소
    ["가" * 150, "나" * 900, "다" * 100],           # 3개 연쇄
    [_EMOJI * 150, _EMOJI * 150],                   # **코드포인트 vs UTF-16**
    ["", "뒤만 있음"],
]

# --- 문장 분할 (통합) ---
SPLIT_CASES = [
    "짧은 문장.",
    _KO * 100,                                       # 1,100자 — 분할 발생
    _KO * 100 + "2025. 7. 9. 선고 " + _KO * 40,      # 날짜 마스킹 + 분할
    "가" * 2500,                                     # 문장 경계 없음 → 강제 분할
    _EMOJI * 1200,                                   # 코드포인트 임계
    "문장 하나.\n\n문단 둘.",                        # `\\n\\s*\\n`
    "문장 하나.\n \n문단 둘.",                       # 사이에 공백
    "문장 하나.\n\n문단 둘.",                  # **U+001C** — Python 만 공백
    "문장 하나.\n﻿\n문단 둘.",                  # **U+FEFF** — JS 만 공백
    "Section 1. 다음",                               # 숫자 뒤 → split 안 함
    "et al. 인용",                                   # 영문 약어 → split 안 함
    "끝났다. 다음 문장",                             # 한글 종결 → split
    "괄호다). 다음",                                 # 닫는 괄호 → split
    # **불균형 흡수 분기** — current 가 800 을 넘는 시점에 따옴표가 홀수여야 한다.
    _KO * 70 + '"열린 인용 ' + _KO * 10 + " 끝.",
    _KO * 70 + "(열린 괄호 " + _KO * 10 + " 끝.",
    _KO * 70 + "「열린 낫표 " + _KO * 10 + " 끝.",
    # **`pyStrip` vs `trim()`** — 조합 결과의 **양끝**에 와야 갈린다.
    #   U+FEFF: JS `trim()` 은 지우고 Python `strip()` 은 안 지운다
    #   U+001C: Python 은 지우고 JS `trim()` 은 안 지운다
    "\ufeff끝났다. 다음 문장\ufeff",
    "\u001c끝났다. 다음 문장\u001c",
    "\ufeff" + _KO * 100 + "\ufeff",
    "",
]

RUNNER_TS = f"""
import {{
  applyOverlap, isUnbalancedQuoteOrParen, maskLegalDates, restoreLegalDates, splitBySentence,
}} from "file://{SHARED}/ingest/chunk_split.ts";

const input = JSON.parse(await Deno.readTextFile(Deno.args[0]));
console.log(JSON.stringify({{
  mask: input.mask.map((t: string) => {{
    const {{ masked, matches }} = maskLegalDates(t);
    // 복원까지 왕복해야 placeholder 인덱싱이 맞는지 확인된다.
    return {{ masked, matches, restored: restoreLegalDates(masked, matches) }};
  }}),
  balance: input.balance.map((t: string) => isUnbalancedQuoteOrParen(t)),
  overlap: input.overlap.map((ps: string[]) => applyOverlap(ps)),
  split: input.split.map((t: string) => splitBySentence(t)),
}}));
"""


def run_deno(payload: dict, timeout: int = 300) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        cf, rf = os.path.join(tmp, "cases.json"), os.path.join(tmp, "runner.ts")
        with open(cf, "w", encoding="utf-8") as f:
            json.dump(payload, f)
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
    import app.ingest.stages.chunk as C

    ts = run_deno({
        "mask": MASK_CASES,
        "balance": BALANCE_CASES,
        "overlap": OVERLAP_CASES,
        "split": SPLIT_CASES,
    })

    fails = 0

    def report(label: str, cases, want, got, brief=lambda v: json.dumps(v, ensure_ascii=False)[:150]):
        nonlocal fails
        bad = [i for i, (a, b) in enumerate(zip(want, got)) if a != b]
        if bad:
            fails += 1
            print(f"  **{label} — {len(bad)}건 불일치** 인덱스 {bad[:6]}")
            for i in bad[:3]:
                print(f"    [{i}] 입력 {json.dumps(cases[i], ensure_ascii=False)[:70]}")
                print(f"        py {brief(want[i])}")
                print(f"        ts {brief(got[i])}")
        else:
            print(f"  {label:<28} {len(cases)}건 OK")

    print("=== 날짜 마스킹 + 복원 ===")
    want = []
    for t in MASK_CASES:
        masked, matches = C._mask_legal_dates(t)
        want.append({"masked": masked, "matches": matches,
                     "restored": C._restore_legal_dates(masked, matches)})
    report("maskLegalDates", MASK_CASES, want, ts["mask"])
    # 마스킹이 실제로 일어난 케이스가 있어야 대조가 의미 있다.
    hit = sum(1 for w in want if w["matches"])
    print(f"    (마스킹 발생 {hit}건 / 미발생 {len(want) - hit}건)")
    if hit == 0 or hit == len(want):
        fails += 1
        print("    **케이스 무효** — 마스킹 분기가 한쪽만 태워졌다")

    print()
    print("=== 따옴표·괄호 균형 ===")
    want = [C._is_unbalanced_quote_or_paren(t) for t in BALANCE_CASES]
    report("isUnbalanced", BALANCE_CASES, want, ts["balance"])
    if not (0 < sum(want) < len(want)):
        fails += 1
        print("    **케이스 무효** — True/False 한쪽만 나왔다")

    print()
    print("=== overlap ===")
    want = [C._apply_overlap(list(ps)) for ps in OVERLAP_CASES]
    report("applyOverlap", OVERLAP_CASES, want, ts["overlap"],
           brief=lambda v: f"{len(v)}조각 " + json.dumps([len(x) for x in v]))

    print()
    print("=== 문장 분할 (통합) ===")
    want = [C._split_by_sentence(t) for t in SPLIT_CASES]
    report("splitBySentence", SPLIT_CASES, want, ts["split"],
           brief=lambda v: f"{len(v)}조각 " + json.dumps([len(x) for x in v]))
    multi = sum(1 for w in want if len(w) > 1)
    print(f"    (분할 발생 {multi}건 / 단일 {len(want) - multi}건)")
    if multi == 0:
        fails += 1
        print("    **케이스 무효** — 분할이 한 번도 안 일어났다")

    print()
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
