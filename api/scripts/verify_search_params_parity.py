"""Task 2.1 채점기 — `search/params.ts` · `search/pgroonga.ts` 동등성 대조.

두 대상을 **다른 방식으로** 잰다.

1. **PGroonga 질의 빌드 / 조사 strip** — Python 함수를 직접 부른다.
   `_build_pgroonga_query` 는 sparse 검색에 그대로 들어가는 문자열이라 **한 글자만 달라도
   검색 결과가 통째로 바뀐다.** 완전 일치만 통과다.

2. **파라미터 검증** — 원본이 FastAPI Query 검증(422)과 핸들러 내부 검사(400)로 나뉘어 있어
   Python 함수 하나로는 재현할 수 없다. 그래서 **운영 엔드포인트를 진실로 삼아** 대조한다.
   422 는 pydantic 이 만드는 구조화된 배열이고 400 은 앱이 만드는 문자열이라 모양부터 다르다.

`--offline` 은 2)를 건너뛴다. CI 는 이 모드로 돈다 — 매 push 마다 운영을 때리면 CI 가
운영 가용성에 묶이고, 운영이 잠깐 흔들릴 때 관계없는 PR 이 빨갛게 된다.
파라미터 검증 계약을 바꿀 때는 **오프라인 없이** 한 번 돌려 운영과 맞는지 확인한다.

사용:
    api/.venv/bin/python api/scripts/verify_search_params_parity.py [--offline]
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SEARCH_DIR = os.path.join(ROOT, "supabase", "functions", "_shared", "search")
DENO_CONFIG = os.path.join(ROOT, "supabase", "functions", "deno.json")
LEGACY = "https://jetrag-api.woong-s.com/search"

sys.path.insert(0, os.path.join(ROOT, "api"))

# ---------------------------------------------------------------- 1) PGroonga

PARTICLE_CASES = [
    "전폭은", "전고는", "디스플레이는", "길이가", "전폭은?", "회사이",
    "는", "은가", "가나", "세무", "매출을", "보고서를", "회사도", "여기만",
    "학교에", "회사의", "얼마나", "종류야", "abc", "ab", "a",
    "데이터센터는!!", "지원???", "사업,.;:", "", "   ",
    # 조사로 끝나지만 길이 미달 → 보존
    "가는", "은는",
    # 비한글 + 조사 글자
    "test는", "AI가", "1234를",
]

PGROONGA_CASES = [
    "세무",
    "데이터센터 지원 사업의 신청 자격",
    "전폭은 얼마인가요?",
    "삼성 사업보고서 매출",
    "  앞뒤   공백   많은   질의  ",
    "단일토큰",
    "",
    "   ",
    "\t탭\t구분\t",
    "줄바꿈\n포함\n질의",
    "전각　공백",  # U+3000
    "a b",
    "정기결제 해지와 환불 절차가 어떻게 되는지",
    "vision need score threshold",
    "전폭은? 전고는! 길이가.",
]

# ---------------------------------------------------------------- 2) 파라미터 검증

# (이름, 쿼리스트링 dict) — 유효 케이스는 실제 검색을 돌리므로 최소로 둔다.
PARAM_CASES: list[tuple[str, dict]] = [
    ("q 없음", {}),
    ("q 빈 문자열", {"q": ""}),
    ("q 공백 1", {"q": " "}),
    ("q 공백 여러 개", {"q": "   "}),
    ("q 전각 공백", {"q": "　"}),
    ("q 200자", {"q": "가" * 200}),
    ("q 201자", {"q": "가" * 201}),
    ("limit 0", {"q": "a", "limit": "0"}),
    ("limit 51", {"q": "a", "limit": "51"}),
    ("limit 비정수", {"q": "a", "limit": "abc"}),
    ("limit 1 (경계)", {"q": "a", "limit": "1"}),
    ("limit 50 (경계)", {"q": "a", "limit": "50"}),
    ("offset -1", {"q": "a", "offset": "-1"}),
    ("offset 비정수", {"q": "a", "offset": "x"}),
    ("mode 잘못", {"q": "a", "mode": "bogus"}),
    ("mode dense", {"q": "a", "mode": "dense"}),
    ("doc_id 빈 문자열", {"q": "a", "doc_id": ""}),
    ("doc_id 공백만", {"q": "a", "doc_id": "   "}),
    ("doc_id 65자", {"q": "a", "doc_id": "x" * 65}),
    ("doc_id 64자 (경계)", {"q": "a", "doc_id": "x" * 64}),
    ("from_date 형식오류", {"q": "a", "from_date": "nope"}),
    ("to_date 형식오류", {"q": "a", "to_date": "2026-13-99"}),
    ("from_date 날짜만", {"q": "a", "from_date": "2026-04-01"}),
    ("from_date Z", {"q": "a", "from_date": "2026-04-01T00:00:00Z"}),
    ("from_date 오프셋", {"q": "a", "from_date": "2026-04-01T00:00:00+09:00"}),
]

RUNNER_TS = f"""
import {{ buildPgroongaQuery, stripKoreanParticle }} from "file://{SEARCH_DIR}/pgroonga.ts";
import {{ validateSearchParams }} from "file://{SEARCH_DIR}/params.ts";

const input = JSON.parse(await Deno.readTextFile(Deno.args[0]));
console.log(JSON.stringify({{
  particles: input.particles.map((t: string) => stripKoreanParticle(t)),
  pgroonga: input.pgroonga.map((q: string) => buildPgroongaQuery(q)),
  params: input.params.map((qs: Record<string, string>) => validateSearchParams(new URLSearchParams(qs))),
}}));
"""


def run_deno(payload: dict) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        cases = os.path.join(tmp, "cases.json")
        runner = os.path.join(tmp, "runner.ts")
        with open(cases, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        with open(runner, "w", encoding="utf-8") as f:
            f.write(RUNNER_TS)
        proc = subprocess.run(
            ["deno", "run", "--config", DENO_CONFIG, "--allow-read", "--allow-net", runner, cases],
            capture_output=True,
            text=True,
            timeout=300,
        )
    if proc.returncode != 0:
        raise SystemExit(f"deno 실행 실패:\n{proc.stderr[:1500]}")
    return json.loads(proc.stdout)


def call_legacy(qs: dict) -> dict:
    """운영 엔드포인트를 진실로 삼는다. 검증 실패 케이스가 대부분이라 검색 비용은 거의 없다."""
    url = f"{LEGACY}?{urllib.parse.urlencode(qs)}" if qs else LEGACY
    # Cloudflare 가 `Python-urllib/*` UA 를 403 으로 막는다(Task 1.7 전환 이후 실측).
    req = urllib.request.Request(url, headers={"User-Agent": "Jet-Rag-Ops/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read())
            # 정상 응답은 detail 이 없다 — 통과 여부만 본다.
            return {"status": resp.status, "detail": None}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return {"status": e.code, "detail": json.loads(raw).get("detail")}
        except Exception:
            return {"status": e.code, "detail": raw.decode("utf-8", "replace")[:200]}


def main() -> None:
    offline = "--offline" in sys.argv
    from app.routers.search import _build_pgroonga_query, _strip_korean_particle

    payload = {"particles": PARTICLE_CASES, "pgroonga": PGROONGA_CASES,
               "params": [qs for _, qs in PARAM_CASES]}
    ts = run_deno(payload)

    fails = 0

    print("=== 조사 strip ===")
    for t, tv in zip(PARTICLE_CASES, ts["particles"]):
        pv = _strip_korean_particle(t)
        if pv != tv:
            fails += 1
            print(f"  MISMATCH {t!r}: py={pv!r} ts={tv!r}")
    print(f"  {len(PARTICLE_CASES)}건 대조")

    print()
    print("=== PGroonga 질의 빌드 ===")
    for q, tv in zip(PGROONGA_CASES, ts["pgroonga"]):
        pv = _build_pgroonga_query(q)
        if pv != tv:
            fails += 1
            print(f"  MISMATCH {q!r}: py={pv!r} ts={tv!r}")
        else:
            print(f"  OK {q[:28]!r:<32} → {pv[:44]!r}")

    print()
    if offline:
        print("=== 파라미터 검증 — 건너뜀 (--offline) ===")
        print(f"  {len(PARAM_CASES)}건은 운영 엔드포인트 대조라 오프라인에서 못 잰다.")
        print()
        print(f"케이스 {len(PARTICLE_CASES) + len(PGROONGA_CASES)}건 대조 (순수 함수만)")
        print("FAIL 0" if fails == 0 else f"FAIL {fails}")
        sys.exit(1 if fails else 0)

    print("=== 파라미터 검증 (운영 엔드포인트 대조) ===")
    for (name, qs), tv in zip(PARAM_CASES, ts["params"]):
        legacy = call_legacy(qs)
        got = {"status": tv.get("status", 200), "detail": tv.get("detail")}
        ok = got == legacy
        if not ok:
            fails += 1
            print(f"  {name:<22} MISMATCH")
            print(f"      운영 = {json.dumps(legacy, ensure_ascii=False)[:200]}")
            print(f"      ts   = {json.dumps(got, ensure_ascii=False)[:200]}")
        else:
            d = legacy["detail"]
            short = "통과" if d is None else (d if isinstance(d, str) else d[0].get("type"))
            print(f"  {name:<22} OK   [{legacy['status']}] {short}")

    print()
    total = len(PARTICLE_CASES) + len(PGROONGA_CASES) + len(PARAM_CASES)
    print(f"케이스 {total}건 대조")
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
