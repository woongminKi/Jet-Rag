"""`chunk.py` 의 짧은 섹션 병합·표 셀 판정을 Python 원본과 대조.

## 이 조각이 왜 위험한가
표 셀 판정 하나가 뒤집히면 **병합 여부가 달라지고 청크 경계가 통째로 바뀐다.**
그 판정이 Python 문자 메서드 3종(`isspace`/`isdigit`/`isalnum`)에 의존하는데 JS 기본
문자 클래스로는 못 맞춘다 — `pychar.ts` 를 전수 대조로 먼저 만든 이유다.

## 케이스가 노리는 것
- 30자 경계, 비율 0.5 경계 (판정이 뒤집히는 지점)
- `U+001C`(Python 만 공백) · `²`(Python 만 digit) · 새 유니코드(Python 만 non-alnum)
- 코드포인트 길이 (이모지)
- 병합 3조건(길이·page·합계) 각각의 경계
- `section_title` 우선순위와 falsy, `metadata` 충돌

사용:
    api/.venv/bin/python api/scripts/verify_chunk_merge_parity.py
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

# --- 표 셀 판정 ---
CELL_CASES = [
    "a | b",                    # ` | ` → 무조건 True
    "가" * 100 + " | " + "나",   # 길어도 ` | ` 면 True
    "",                          # non_ws 0 → False
    "   ",                       # 공백만 → False
    "가" * 29,                   # 29자, 문자뿐 → 비율 0
    "가" * 30,                   # **30자 경계** → 즉시 False
    "1234567890",                # 전부 숫자 → 비율 1.0
    "가나12",                     # 2/4 = 0.5 → **경계 딱**
    "가나다12",                   # 2/5 = 0.4 → False
    "가12",                      # 2/3 = 0.67 → True
    "...",                       # 구두점만 → 1.0
    "²²가가",                     # **`²` 는 Python isdigit True** (JS \p{Nd} 아님)
    "가가",           # **U+001C 는 Python 만 공백** → non_ws 가 달라진다
    "﻿﻿가가",           # **U+FEFF 는 JS 만 공백**
    "Ᲊ Ᲊ 12",                    # 새 유니코드 — Python isalnum False
    "🙂" * 29,                   # 코드포인트 29 (UTF-16 58)
    "🙂" * 30,                   # 코드포인트 30 → 경계
    "  가12  ",                  # strip 후 판정
]

_S = lambda t, page=None, title=None, meta=None: {
    "text": t, "page": page, "section_title": title, "bbox": None, "metadata": meta or {},
}

# --- 섹션 병합 ---
MERGE_CASES = [
    [],
    [_S("하나뿐")],
    [_S("짧다"), _S("이어짐")],                                   # 병합
    [_S("가" * 199), _S("나" * 100)],                             # 199 < 200 → 병합
    [_S("가" * 200), _S("나" * 100)],                             # **200 경계** → 병합 안 함
    [_S("가" * 199), _S("나" * 799)],                             # 199+799+2=1000 → 병합
    [_S("가" * 199), _S("나" * 800)],                             # 1001 → 병합 안 함
    [_S("짧다", page=1), _S("이어짐", page=2)],                    # page 다름 → 안 함
    [_S("짧다", page=1), _S("이어짐", page=1)],                    # 같은 page → 병합
    [_S("짧다", page=None), _S("이어짐", page=None)],              # None == None → 병합
    [_S("a | b"), _S("이어짐")],                                   # 앞이 표 셀 → 안 함
    [_S("짧다"), _S("a | b")],                                     # 뒤가 표 셀 → 안 함
    [_S("짧다"), _S("이어짐"), _S("셋째")],                        # 연쇄 병합
    [_S("짧다", title="A"), _S("이어짐", title="B")],              # 뒤쪽 title 우선
    [_S("짧다", title="A"), _S("이어짐", title=None)],             # 뒤가 None → 앞 유지
    [_S("짧다", title="A"), _S("이어짐", title="")],               # **빈 문자열은 falsy**
    [_S("짧다", meta={"x": 1, "y": 2}), _S("이어짐", meta={"y": 9})],  # 충돌 시 뒤쪽
    [_S("🙂" * 199), _S("나" * 100)],                             # 코드포인트 길이
]

RUNNER_TS = f"""
import {{ looksLikeTableCell, mergeShortSections }} from "file://{SHARED}/ingest/chunk_merge.ts";

const input = JSON.parse(await Deno.readTextFile(Deno.args[0]));
console.log(JSON.stringify({{
  cell: input.cell.map((t: string) => looksLikeTableCell(t)),
  merge: input.merge.map((ss: unknown[]) => mergeShortSections(ss as never)),
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
    from app.adapters.parser import ExtractedSection

    ts = run_deno({"cell": CELL_CASES, "merge": MERGE_CASES})
    fails = 0

    print("=== 표 셀 판정 ===")
    want = [C._looks_like_table_cell(t) for t in CELL_CASES]
    bad = [i for i, (a, b) in enumerate(zip(want, ts["cell"])) if a != b]
    if bad:
        fails += 1
        print(f"  **{len(bad)}건 불일치** 인덱스 {bad}")
        for i in bad[:5]:
            print(f"    [{i}] {CELL_CASES[i]!r:<40} py={want[i]} ts={ts['cell'][i]}")
    else:
        print(f"  looksLikeTableCell        {len(CELL_CASES)}건 OK  (True {sum(want)} / False {len(want)-sum(want)})")
    if not (0 < sum(want) < len(want)):
        fails += 1
        print("    **케이스 무효** — True/False 한쪽만 나왔다")

    print()
    print("=== 섹션 병합 ===")
    def to_py(d):
        return ExtractedSection(text=d["text"], page=d["page"],
                                section_title=d["section_title"], bbox=None,
                                metadata=dict(d["metadata"]))
    def to_dict(s):
        return {"text": s.text, "page": s.page, "section_title": s.section_title,
                "bbox": list(s.bbox) if s.bbox else None, "metadata": s.metadata}

    merged_any = 0
    for i, case in enumerate(MERGE_CASES):
        want_v = [to_dict(x) for x in C._merge_short_sections([to_py(d) for d in case])]
        got_v = ts["merge"][i]
        if len(want_v) < len(case):
            merged_any += 1
        if want_v != got_v:
            fails += 1
            print(f"  **[{i}] 불일치** — 입력 {len(case)}개 → py {len(want_v)}개 / ts {len(got_v)}개")
            for a, b in zip(want_v, got_v):
                if a != b:
                    print(f"      py {json.dumps(a, ensure_ascii=False)[:130]}")
                    print(f"      ts {json.dumps(b, ensure_ascii=False)[:130]}")
                    break
    if fails == 0 or all(True for _ in []):
        pass
    print(f"  mergeShortSections        {len(MERGE_CASES)}건 대조 "
          f"(병합 발생 {merged_any}건)")
    if merged_any == 0:
        fails += 1
        print("    **케이스 무효** — 병합이 한 번도 안 일어났다")

    print()
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
