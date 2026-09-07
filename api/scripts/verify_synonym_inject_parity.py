"""동의어 주입(`synonym_inject.ts` · `synonym_dict.ts`)을 원본과 대조.

## 왜 지금 옮겼나
실측(2026-09-07): 전체 청크 **37,080 개 중 199 개**에 `[검색어: ...]` 마커가 있고
`metadata.synonym_source = "dict"` 다. 켜진 적이 있고 결과가 남아 있다 — 안 옮긴 채
재인제스트하면 그 199 개의 sparse 매칭이 사라진다.

## 순서와 상한이 전부다
후보는 사전 **삽입 순서**로 쌓이다가 **5 개에서 잘린다.** 즉 사전 순서가 "어떤 후보가
살아남는가" 를 정한다. 그래서 실제 코퍼스 텍스트로 돌린다 — 사전 키가 여러 개 걸리는
문장이라야 상한이 실제로 발동한다.

## 노린 함정
- **양방향 조회** — 키로도, 값으로도 찾는다. 자기 자신은 제외.
- **cap 5 에서 자르기** — 사전 순서가 결과를 바꾼다.
- **이미 본문에 있는 후보 제외** — 부분 문자열 기준이다.
- **대괄호 제거** — 후보에 `[`·`]` 가 있으면 마커 구조가 깨진다.
- **`.strip()`** — Python 공백 집합은 JS `trim()` 과 다르다.
- **코드펜스 벗기기** — Python `split("```", 2)` 는 최대 2 회 분할이다.

사용:
    api/.venv/bin/python api/scripts/verify_synonym_inject_parity.py
    api/.venv/bin/python api/scripts/verify_synonym_inject_parity.py --negative
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

RUNNER_TS = """
import {
  collectSynonymCandidates, injectMarker, injectSynonyms, parseLlmPairs,
} from "file://%(shared)s/ingest/synonym_inject.ts";

const cfg = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const out: Record<string, unknown> = {};
out.candidates = cfg.texts.map((t: string) =>
  collectSynonymCandidates(t, cfg.pairs ?? null)
);
out.injected = cfg.texts.map((t: string) => {
  const r = injectSynonyms(t, cfg.pairs ?? null);
  return r === null ? null : { text: r.text, candidates: r.candidates };
});
out.markers = cfg.markerCases.map(([t, c]: [string, string[]]) => injectMarker(t, c));
out.llmPairs = cfg.llmResponses.map((s: string) => parseLlmPairs(s));
if (cfg.negative) (out.candidates as string[][])[0] = ["변조"];
await Deno.writeTextFile(Deno.args[1], JSON.stringify(out));
""" % {"shared": SHARED}

# 사전 키가 여러 개 걸리는 문장이라야 cap 5 가 실제로 발동한다.
TEXTS = [
    "데이터센터와 인공지능 인프라, 전자의무기록 연계 및 상면 임대·무중단 전원 구성",
    "쏘나타 전장 전폭 전고 윤거 트림 공차중량 제원표",
    "변제충당 순서와 소멸시효, 지연손해금 및 하도급대금 직접지급",
    "비식별화·가명정보·재식별 및 환자 정보 보호 동의서 민감정보",
    "재산물품관리 회원카드 회비 직제 사무국 정기총회",
    "태양계 왜소행성 삼국시대 고대 한반도",
    # 이미 동의어가 본문에 있는 경우 — 그 후보는 빠져야 한다
    "데이터센터 DC 전산센터 data center 를 모두 언급한 문장",
    # 값 쪽으로 등장 — 양방향 조회
    "전산센터 운영 지침",
    "AI 도입 계획",
    # 사전에 없는 문장
    "오늘 점심은 김치찌개였다",
    "",
    "   ",
    # 사전 키가 부분 문자열로만 걸리는 경우
    "직제개편안내",
]

MARKER_CASES = [
    ["본문", ["가", "나"]],
    ["본문", []],
    ["", ["가"]],
    ["끝에 개행이 있는 본문\n", ["DC"]],
    ["조합 문자 포함 한글", ["전산센터", "data center"]],
]

LLM_RESPONSES = [
    '{"pairs":[{"term":"데이터센터","synonyms":["DC","전산센터"]}]}',
    '```json\n{"pairs":[{"term":"AI","synonyms":["인공지능"]}]}\n```',
    '```\n{"pairs":[{"term":"AI","synonyms":["인공지능"]}]}\n```',
    "설명이 앞에 붙은 경우 {\"pairs\":[]}",
    '{"pairs":[]}',
    '{"pairs":"not a list"}',
    '{"nope":1}',
    "[]",
    "not json at all",
    "",
    # term/synonyms 형식 오류
    '{"pairs":[{"term":"","synonyms":["x"]},{"term":"ok","synonyms":[]},'
    '{"term":"ok2","synonyms":["  ","y"]}]}',
    # cap 8 초과
    '{"pairs":[' + ",".join(
        '{"term":"t%d","synonyms":["s%d"]}' % (i, i) for i in range(12)
    ) + "]}",
]

LLM_PAIRS = [["데이터센터", ["DC", "전산센터"]], ["직제", ["조직도"]]]


def main() -> None:
    negative = "--negative" in sys.argv
    sys.path.insert(0, os.path.join(ROOT, "api"))
    # 원본은 함수 안에서 ENV 를 본다 — 켜 두지 않으면 전부 빈 배열이 나온다.
    os.environ["JETRAG_SYNONYM_INJECTION_ENABLED"] = "true"

    from app.services.synonym_inject import (
        _parse_llm_pairs,
        collect_synonym_candidates,
        inject_marker,
    )

    py_cands = [collect_synonym_candidates(t, doc_llm_pairs=LLM_PAIRS) for t in TEXTS]
    py_injected = []
    for t, c in zip(TEXTS, py_cands):
        py_injected.append(None if not c else {"text": inject_marker(t, c), "candidates": c})
    py_markers = [inject_marker(t, c) for t, c in MARKER_CASES]
    py_llm = [[list(p) for p in _parse_llm_pairs(s)] for s in LLM_RESPONSES]

    with tempfile.TemporaryDirectory() as tmp:
        cf, rf = os.path.join(tmp, "c.json"), os.path.join(tmp, "r.ts")
        of = os.path.join(tmp, "o.json")
        with open(cf, "w", encoding="utf-8") as f:
            json.dump({
                "texts": TEXTS, "pairs": LLM_PAIRS, "markerCases": MARKER_CASES,
                "llmResponses": LLM_RESPONSES, "negative": negative,
            }, f)
        with open(rf, "w", encoding="utf-8") as f:
            f.write(RUNNER_TS)
        proc = subprocess.run(
            ["deno", "run", "--config", DENO_CONFIG, "--allow-all", rf, cf, of],
            capture_output=True, text=True, timeout=900,
        )
        if proc.returncode != 0:
            raise SystemExit(f"deno 실행 실패:\n{proc.stderr[:4000]}")
        with open(of, encoding="utf-8") as f:
            ts = json.load(f)

    fails: list[str] = []
    checks = 0

    def cmp(label, a, b):
        nonlocal checks
        checks += 1
        if a != b:
            fails.append(f"{label}\n      py={a!r}\n      ts={b!r}")

    print("  --- 후보 수집 (사전 순서·cap 5·본문 중복 제외) ---")
    capped = 0
    for t, a, b in zip(TEXTS, py_cands, ts["candidates"]):
        cmp(f"collect({t[:22]!r})", a, b)
        if len(a) >= 5:
            capped += 1
        label = (t[:26] + "…") if len(t) > 26 else (t or "(빈 문자열)")
        print(f"    {label:<30} {len(a)}개 {'(cap)' if len(a) >= 5 else '     '} "
              f"{'일치' if a == b else '**불일치**'}")
    print(f"    → cap 5 가 실제로 발동한 케이스 {capped}건")

    print("  --- 마커 주입 ---")
    for (t, c), a, b in zip(MARKER_CASES, py_markers, ts["markers"]):
        cmp(f"inject_marker({t[:14]!r}, {c})", a, b)
    print(f"    {len(MARKER_CASES)}건 대조")

    print("  --- injectSynonyms (수집 + 주입) ---")
    for t, a, b in zip(TEXTS, py_injected, ts["injected"]):
        cmp(f"injectSynonyms({t[:20]!r})", a, b)
    print(f"    {len(TEXTS)}건 대조")

    print("  --- LLM 응답 파싱 ---")
    for s, a, b in zip(LLM_RESPONSES, py_llm, ts["llmPairs"]):
        cmp(f"parse_llm_pairs({s[:26]!r})", a, [list(x) for x in b])
    print(f"    {len(LLM_RESPONSES)}건 대조")

    for f in fails[:8]:
        print(f"  **{f}**")
    print(f"  비교 {checks}건, 불일치 {len(fails)}건")
    if negative:
        print("음성 대조: " + ("검사기 정상 (변조 검출)" if fails else "**검사기가 못 잡는다**"))
        sys.exit(0 if fails else 1)
    print("FAIL 0" if not fails else f"FAIL {len(fails)}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
