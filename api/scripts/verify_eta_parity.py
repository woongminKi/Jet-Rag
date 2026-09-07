"""잔여 시간 추정(`compute_remaining_ms`)을 Python 원본과 대조.

## 왜 따로 대조하는가
`/documents/active` 응답 대조는 통과했지만 **진행 중인 잡이 없어 `items` 가 비었다.**
즉 ETA 코드 325 줄이 **한 번도 안 돌았다.** 그 상태로 "이식 완료" 라고 하면 안 된다.

여기서는 같은 DB(같은 baseline)를 보고 **같은 입력**을 양쪽에 넣어 결과를 비교한다.

## 무엇이 갈릴 수 있나
- `statistics.median` 짝수 평균 / `sort()` 문자열 정렬 함정
- Python `int()` 는 truncate, `round()` 는 **은행가 반올림**(percentile 랭크 계산)
- `isinstance(duration, int)` — 실수 duration 은 제외된다
- stage 별 sample <3 이면 키 누락 → fallback

사용:
    api/.venv/bin/python api/scripts/verify_eta_parity.py
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

# (job_status, current_stage, stage_progress)
CASES: list[tuple[str, str | None, dict | None]] = [
    # 비활성 — 전부 None 이어야 한다
    ("completed", "done", None),
    ("failed", "extract", None),
    ("cancelled", None, None),
    # queued — 전체 합산 (stage_progress 무시)
    ("queued", None, None),
    ("queued", "extract", None),
    ("queued", "extract", {"current": 5, "total": 10, "unit": "pages"}),
    # running — current_stage 없으면 전체 합산
    ("running", None, None),
    # running + 각 stage (뒤로 갈수록 짧아져야 한다)
    ("running", "extract", None),
    ("running", "chunk", None),
    ("running", "load", None),
    ("running", "embed", None),
    ("running", "dedup", None),
    # 모르는 stage — 전체 합산 fallback
    ("running", "존재하지않는단계", None),
    # extract + unit=pages → vision 분해
    ("running", "extract", {"current": 0, "total": 10, "unit": "pages"}),
    ("running", "extract", {"current": 5, "total": 10, "unit": "pages"}),
    ("running", "extract", {"current": 10, "total": 10, "unit": "pages"}),
    ("running", "extract", {"current": 15, "total": 10, "unit": "pages"}),   # 초과 → 0
    ("running", "extract", {"current": 5, "total": 0, "unit": "pages"}),     # total 0 → 분해 끔
    ("running", "extract", {"current": 5, "total": -1, "unit": "pages"}),
    ("running", "extract", {"current": 5, "total": 10}),                     # unit 없음
    ("running", "extract", {"current": 5, "total": 10, "unit": "chunks"}),   # unit 다름
    ("running", "extract", {"unit": "pages"}),                               # current/total 없음
    ("running", "extract", {"current": "5", "total": "10", "unit": "pages"}),  # 문자열 → 무시
    # 일반 sub-progress (extract 아님) → 비율 분해
    ("running", "embed", {"current": 0, "total": 10}),
    ("running", "embed", {"current": 5, "total": 10}),
    ("running", "embed", {"current": 10, "total": 10}),
    ("running", "embed", {"current": 20, "total": 10}),   # ratio clamp 1.0 → 0
    ("running", "embed", {"current": -5, "total": 10}),   # clamp 0.0
    ("running", "embed", {"current": 5, "total": 0}),     # total 0 → 전체
    ("running", "embed", {}),                              # 빈 dict → 전체
]

RUNNER_TS = f"""
import {{ createClient }} from "@supabase/supabase-js";
import {{ computeRemainingMs, median, percentile }} from "file://{SHARED}/documents/eta.ts";

const input = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const client = createClient(input.url, input.key, {{ auth: {{ persistSession: false }} }});

const eta = [];
for (const [jobStatus, currentStage, stageProgress] of input.cases) {{
  eta.push(await computeRemainingMs(client, {{ jobStatus, currentStage, stageProgress }}));
}}
console.log(JSON.stringify({{
  eta,
  median: input.medianCases.map((xs: number[]) => median(xs)),
  percentile: input.pctCases.map(([xs, p]: [number[], number]) => percentile(xs, p)),
}}));
"""

MEDIAN_CASES = [[], [1], [1, 2], [1, 2, 3], [1, 2, 3, 4], [9, 10], [10, 9, 100], [3, 3, 3]]
PCT_CASES = [
    [[1], 0.95], [[1, 2], 0.95], [[1, 2, 3], 0.95],
    [list(range(1, 11)), 0.95], [list(range(1, 21)), 0.95],
    [list(range(1, 22)), 0.95],   # 0.95*20 = 19.0
    [list(range(1, 12)), 0.95],   # 0.95*10 = 9.5 → **은행가 반올림 경계**
    [[5, 3, 1, 4, 2], 0.5],
]


def main() -> None:
    from app.config import get_settings
    from app.db import get_supabase_client
    from app.ingest import eta as E

    settings = get_settings()
    sb = get_supabase_client()

    E.reset_cache()
    py_eta = [
        E.compute_remaining_ms(sb, job_status=s, current_stage=c, stage_progress=p)
        for s, c, p in CASES
    ]
    py_median = [float(__import__("statistics").median(xs)) if xs else 0.0 for xs in MEDIAN_CASES]
    py_pct = [E._percentile([float(x) for x in xs], p) for xs, p in PCT_CASES]

    with tempfile.TemporaryDirectory() as tmp:
        cf, rf = os.path.join(tmp, "c.json"), os.path.join(tmp, "r.ts")
        with open(cf, "w", encoding="utf-8") as f:
            json.dump({
                "url": settings.supabase_url,
                "key": settings.supabase_service_role_key,
                "cases": CASES,
                "medianCases": MEDIAN_CASES,
                "pctCases": PCT_CASES,
            }, f)
        with open(rf, "w", encoding="utf-8") as f:
            f.write(RUNNER_TS)
        proc = subprocess.run(
            ["deno", "run", "--config", DENO_CONFIG, "--allow-all", rf, cf],
            capture_output=True, text=True, timeout=600,
        )
    if proc.returncode != 0:
        raise SystemExit(f"deno 실행 실패:\n{proc.stderr[:3000]}")
    # `console.debug`/`warn` 이 stdout 을 오염시킬 수 있다 — **JSON 은 마지막 줄**이다.
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip().startswith("{")]
    if not lines:
        raise SystemExit(
            f"deno 출력에 JSON 이 없다:\nstdout={proc.stdout[:600]}\nstderr={proc.stderr[:900]}"
        )
    ts = json.loads(lines[-1])

    fails = 0

    bad = [i for i, (a, b) in enumerate(zip(py_median, ts["median"])) if a != b]
    if bad:
        fails += 1
        print(f"  **median {len(bad)}건 불일치** {[(MEDIAN_CASES[i], py_median[i], ts['median'][i]) for i in bad[:3]]}")
    else:
        print(f"  median                    {len(MEDIAN_CASES)}건 OK")

    bad = [i for i, (a, b) in enumerate(zip(py_pct, ts["percentile"])) if a != b]
    if bad:
        fails += 1
        print(f"  **percentile {len(bad)}건 불일치**")
        for i in bad[:4]:
            print(f"    n={len(PCT_CASES[i][0])} p={PCT_CASES[i][1]}  py={py_pct[i]} ts={ts['percentile'][i]}")
    else:
        print(f"  percentile                {len(PCT_CASES)}건 OK")

    bad = [i for i, (a, b) in enumerate(zip(py_eta, ts["eta"])) if a != b]
    if bad:
        fails += 1
        print(f"  **computeRemainingMs {len(bad)}건 불일치**")
        for i in bad[:8]:
            s, c, p = CASES[i]
            print(f"    {s:<10} {str(c):<16} {json.dumps(p, ensure_ascii=False):<44} "
                  f"py={py_eta[i]} ts={ts['eta'][i]}")
    else:
        print(f"  computeRemainingMs        {len(CASES)}건 OK")

    n_null = sum(1 for v in py_eta if v is None)
    n_val = len(py_eta) - n_null
    distinct = len({v for v in py_eta if v is not None})
    print(f"    ETA 결과: None {n_null} / 값 {n_val} (서로 다른 값 {distinct}가지)")
    # 케이스 무효 검사 — 전부 None 이면 아무것도 검증 못 한 것이다.
    if n_val == 0:
        fails += 1
        print("    **케이스 무효** — ETA 가 전부 None 이다(cold start?). 분기를 안 태웠다")
    if distinct < 3:
        fails += 1
        print("    **케이스 무효** — 값이 거의 같다. 분기별 차이를 못 본다")

    print()
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
