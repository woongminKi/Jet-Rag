"""`classify_query_type` 을 Python 원본과 대조.

## 실제 입력을 먼저 읽는다
`/admin/queries/stats` 는 `search_metrics_log.query_text` 를 분류한다. 상상한 질의로만
대조하면 실제 입력에서 갈리는 걸 못 잡는다 — 그래서 **운영 테이블에서 전부 긁어와**
합성 경계 케이스와 함께 돌린다. `--dump` 로 그때 쓴 입력을 파일에 남긴다(재측정 대비).

## 합성 케이스가 노리는 것
Python `re` 와 JS 정규식이 갈리는 지점:
- `\\d` — Python 은 유니코드 Nd 전부. `"３개월"`, `"٣년"` 이 숫자로 잡힌다.
- `\\s` — Python 은 `\\x1c-\\x1f`·`\\x85` 를 공백으로 보고 `﻿`(U+FEFF) 는 안 본다. JS 는 반대.
- `strip()` — 위 공백 집합. `trim()` 과 다르다.
그리고 **우선순위** — 여러 라벨의 키워드가 동시에 들어 있을 때 어느 것이 이기는지.

사용:
    api/.venv/bin/python api/scripts/verify_query_classifier_parity.py [--dump out.json]
"""

from __future__ import annotations

import argparse
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

# --- 합성 경계 케이스 ---------------------------------------------------------
# 각 줄 뒤 주석은 "무엇을 태우려는가". 라벨을 여기 적지 않는다 — 적으면 원본이 아니라
# 내 기대를 대조하게 된다. 기대값은 Python 원본에서 뽑는다.
SYNTHETIC: list[str] = [
    # 빈 값·공백
    "", " ", "\t\n", " ", "", "", "﻿", "﻿요약﻿",
    # 9 라벨 각각
    "구조도 보여줘", "표 정리해줘", "두 문서 비교", "3개월 지원금",
    "요약해줘", "그때 뭐였지", "휴가 규정",
    # 우선순위 충돌 — 앞 규칙이 이겨야 한다
    "다이어그램 표 비교 요약 얼마",  # vision 이 최우선
    "표 비교 요약",                 # table > cross_doc
    "비교 3개 요약",                # cross_doc > numeric
    "3개월 요약",                   # numeric > summary
    "요약 그때",                    # summary > fuzzy
    # 숫자 패턴 — Nd·소수·공백·단위 순서
    "３개월",       # 전각 숫자 (Python \d 는 잡고 JS \d 는 못 잡는다)
    "٣년",          # 아라비아-인도 숫자
    "１２.５%",      # 전각 + 소수
    "3.5 kg", "3 km", "3m", "3﻿m",  # 공백 집합 차이
    "10개월", "10개", "10월", "10m",  # 긴 단위 우선 매칭
    "3", "kg", "3 살",              # 단위 없음 / 숫자 없음 / 목록 밖 단위
    "몇 개", "몇개", "몇가", "몇 a",  # `몇\s*[가-힣]`
    "얼마",
    # 부분 문자열 함정 — 키워드가 다른 단어 안에 들어 있는 경우
    "발표자료",       # "표" 가 안에 있다
    "정리해고 관련",  # "정리" 가 안에 있다
    "대비책",         # "대비"
    # 긴 질의
    "회사 내규 중 연차 휴가 이월 관련 조항이 어디 있었는지 기억나?",
]

# `source_chunk_text` 를 주는 케이스 — synonym_mismatch 분기용.
# admin 경로에서는 도달 불가지만 함수 계약이므로 대조한다.
SYNONYM_CASES: list[tuple[str, str]] = [
    ("개인정보 처리 방침", "비식별화 절차를 따른다"),
    ("비식별화 기준", "개인정보 보호"),
    ("개인정보 기준", "개인정보 와 비식별화 둘 다"),  # 양쪽에 다 있으면 매칭 안 됨
    ("색상 코드", "컬러 팔레트"),
    ("휴가", "연차"),  # 쌍에 없음
]

RUNNER_TS = f"""
import {{ classifyQueryType, QUERY_TYPE_LABELS }} from "file://{SHARED}/query_classifier.ts";

const input = JSON.parse(await Deno.readTextFile(Deno.args[0]));
console.log(JSON.stringify({{
  plain: input.queries.map((q: string) => classifyQueryType(q)),
  synonym: input.synonym.map(
    ([q, src]: [string, string]) => classifyQueryType(q, {{ sourceChunkText: src }}),
  ),
  // 나머지 인자 분기 — admin 은 안 쓰지만 계약이다.
  titles2: classifyQueryType("아무 말", {{ expectedDocTitles: ["a", "b"] }}),
  titles1: classifyQueryType("아무 말", {{ expectedDocTitles: ["a"] }}),
  negative: classifyQueryType("다이어그램", {{ isNegative: true }}),
  labels: QUERY_TYPE_LABELS,  // **입력을 되돌려받으면 아무것도 대조 못 한다.**
}}));
"""


def fetch_real_queries() -> list[str]:
    """운영 `search_metrics_log.query_text` 전부. PostgREST 1,000 행 상한을 페이지로 넘는다."""
    import os as _os

    from dotenv import load_dotenv

    load_dotenv(os.path.join(ROOT, ".env"))
    from supabase import create_client

    client = create_client(
        _os.environ["SUPABASE_URL"], _os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    )
    out: list[str] = []
    page = 0
    while True:
        rows = (
            client.table("search_metrics_log")
            .select("query_text")
            .range(page * 1000, page * 1000 + 999)
            .execute()
            .data
            or []
        )
        out.extend(r["query_text"] for r in rows if r.get("query_text") is not None)
        if len(rows) < 1000:
            break
        page += 1
    return out


def run_deno(payload: dict, timeout: int = 300) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        cf, rf = os.path.join(tmp, "cases.json"), os.path.join(tmp, "runner.ts")
        with open(cf, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        with open(rf, "w", encoding="utf-8") as f:
            f.write(RUNNER_TS)
        proc = subprocess.run(
            ["deno", "run", "--config", DENO_CONFIG, "--allow-all", rf, cf],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    if proc.returncode != 0:
        raise SystemExit(f"deno 실행 실패:\n{proc.stderr[:2500]}")
    return json.loads(proc.stdout)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", help="이번에 쓴 입력을 JSON 으로 저장")
    args = ap.parse_args()

    from app.services.query_classifier import QUERY_TYPE_LABELS, classify_query_type

    real = fetch_real_queries()
    queries = SYNTHETIC + real
    if args.dump:
        with open(args.dump, "w", encoding="utf-8") as f:
            json.dump({"synthetic": SYNTHETIC, "real": real}, f, ensure_ascii=False)

    ts = run_deno(
        {
            "queries": queries,
            "synonym": [list(c) for c in SYNONYM_CASES],
        }
    )

    fails = 0
    print(f"운영 query_text {len(real)}건 + 합성 {len(SYNTHETIC)}건")
    print()
    print("=== 라벨 대조 ===")
    seen: dict[str, int] = {}
    for q, tv in zip(queries, ts["plain"]):
        pv = classify_query_type(q)
        seen[pv] = seen.get(pv, 0) + 1
        if pv != tv:
            fails += 1
            print(f"  MISMATCH {q!r}: py={pv} ts={tv}")
    print(f"  {len(queries)}건 대조 — 분포 {json.dumps(seen, ensure_ascii=False)}")

    # **케이스가 분기를 실제로 태웠는지 검사기가 스스로 본다.**
    # admin 경로에서 도달 가능한 라벨은 8종(synonym_mismatch 제외)이다.
    reachable = [lb for lb in QUERY_TYPE_LABELS if lb not in ("synonym_mismatch", "out_of_scope")]
    missing = [lb for lb in reachable if lb not in seen]
    if missing:
        fails += 1
        print(f"  케이스 무효 — 한 번도 안 나온 라벨: {missing}")

    print()
    print("=== source_chunk_text 분기 (synonym_mismatch) ===")
    syn_hit = 0
    for (q, src), tv in zip(SYNONYM_CASES, ts["synonym"]):
        pv = classify_query_type(q, source_chunk_text=src)
        if pv == "synonym_mismatch":
            syn_hit += 1
        if pv != tv:
            fails += 1
            print(f"  MISMATCH {q!r} / {src!r}: py={pv} ts={tv}")
    print(f"  {len(SYNONYM_CASES)}건 대조 — synonym_mismatch {syn_hit}건")
    if syn_hit == 0 or syn_hit == len(SYNONYM_CASES):
        fails += 1
        print("  케이스 무효 — synonym 분기가 한쪽만 태워졌다")

    print()
    print("=== 나머지 인자 분기 ===")
    for name, pv, tv in (
        ("제목 2개 → cross_doc", classify_query_type("아무 말", expected_doc_titles=["a", "b"]), ts["titles2"]),
        ("제목 1개 → 무시", classify_query_type("아무 말", expected_doc_titles=["a"]), ts["titles1"]),
        ("is_negative → out_of_scope", classify_query_type("다이어그램", is_negative=True), ts["negative"]),
    ):
        if pv != tv:
            fails += 1
            print(f"  MISMATCH {name}: py={pv} ts={tv}")
        else:
            print(f"  {name:<28} OK   {pv}")

    print()
    print("=== 라벨 목록·순서 ===")
    if list(QUERY_TYPE_LABELS) != ts["labels"]:
        fails += 1
        print(f"  MISMATCH py={list(QUERY_TYPE_LABELS)} ts={ts['labels']}")
    else:
        print(f"  OK   {len(ts['labels'])}종")

    print()
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
