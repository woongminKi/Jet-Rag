"""`budget_guard.ts` + `pynum.ts` 신규 헬퍼를 Python 원본과 대조.

## DB 없이 판정 경로 전체를 태운다
비용 SUM 만 스텁으로 갈아끼우면(Python 은 `_sum_*` monkeypatch, TS 는 가짜 client)
한도 비교·분기·**한국어 메시지 문자열**까지 그대로 실행된다. 메시지는 `warnings[]` 로
문서에 남아 사용자에게 보이므로 대조 대상이다.

## 왜 `float()` 와 `.4f` 를 따로 재는가
- `Number("")` 는 0, Python `float("")` 는 예외 — 비용 한 건이 0 으로 둔갑하면 한도
  판정이 조용히 뒤집힌다.
- `toFixed` 는 절반에서 위로 올리고 Python `.4f` 는 짝수 쪽으로 간다.

사용:
    api/.venv/bin/python api/scripts/verify_budget_guard_parity.py
    api/.venv/bin/python api/scripts/verify_budget_guard_parity.py --negative
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SHARED = os.path.join(ROOT, "supabase", "functions", "_shared")
DENO_CONFIG = os.path.join(ROOT, "supabase", "functions", "deno.json")

# --- float() 케이스: Python 과 JS 가 갈리는 것 위주 ---
FLOAT_CASES = [
    1.5, 0, -0.0, True, False, None, [], {}, "1.5", " 1.5 ", "", "   ",
    "1_0", "1_000.5", "_1", "1_", "1__0", "0x10", "0b11", "0o17", "1e5",
    "1E5", ".5", "5.", "+3", "-2.25", "inf", "-inf", "Infinity", "-Infinity",
    "INF", "nan", "NaN", "1,5", "1 5", "abc", "1e", "e5", "1e_5", "1_e5",
    "  \t 2.5 \n ",
    # Python 은 float() 앞에서 유니코드 십진 숫자·공백을 ASCII 로 바꾼다.
    "٣", "٣٤.٥", "１２３", "３.１４", "๗", "৯", " 1 ", " 1.5 ",
    "1", "2", "½", "Ⅲ", "３_４",
]

# --- .4f 케이스: 절반값 위주 ---
FORMAT_CASES = [
    0.0, -0.0, 0.00005, 0.00015, 0.00025, 0.00035, 1.23455, 1.23465,
    0.125, 2.675, 0.1, 1.0 / 3.0, 123456.789012, 1e-9, 5e-5, 0.99995,
    -0.00005, -1.23455, 1e20, 0.0001, 0.30000000000000004,
]

# --- _sum_cost_rows 케이스 ---
SUM_ROW_CASES = [
    [],
    [{"estimated_cost": 0.001}, {"estimated_cost": 0.002}],
    [{"estimated_cost": None}, {"estimated_cost": 0.5}],
    [{"estimated_cost": "0.25"}, {"estimated_cost": "abc"}],
    [{"estimated_cost": ""}, {"estimated_cost": 1.0}],          # JS Number("")=0 함정
    [{"estimated_cost": "0x10"}, {"estimated_cost": 1.0}],      # JS Number("0x10")=16 함정
    [{"estimated_cost": True}, {"estimated_cost": 2}],
    [{"estimated_cost": []}, {"estimated_cost": 3}],
    [{"other": 1}],
    [{"estimated_cost": "1_0"}],
]

# --- page cap 케이스 ---
PAGE_CAP_CASES = [
    (0, 50), (49, 50), (50, 50), (51, 50), (0, 0), (10, 0), (5, -1), (0, 1),
]

# --- 비용 한도 케이스: (스텁 SUM, cap) ---
COST_CASES = [
    (0.0, 1.0), (0.9999, 1.0), (1.0, 1.0), (1.00005, 1.0),
    (0.12345678, 0.05), (None, 1.0), (3.14159, 0.0001),
]

FIXED_NOW_MS = 1_757_251_496_789  # 2026-09-07T13:24:56.789Z

RUNNER_TS = """
import {
  checkCombined, checkDailyBudget, checkDocBudget, check24hSlidingBudget,
  checkDocPageCap, slidingCutoffIso, sumCostRows, utcMidnightIso,
} from "file://%(shared)s/ingest/budget_guard.ts";
import { pyFloat, pyFormatF } from "file://%(shared)s/pynum.ts";

const cfg = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const NEG = cfg.negative === true;
const env: Record<string, string | undefined> = {};

const num = (v: number | null) =>
  v === null ? null : Number.isNaN(v) ? "nan" : v === Infinity ? "inf"
    : v === -Infinity ? "-inf" : Object.is(v, -0) ? "-0.0" : v;

// SUM 만 스텁 — 그 위 판정·메시지는 실제 코드가 만든다.
function fakeClient(rows: unknown) {
  // deno-lint-ignore no-explicit-any
  const q: any = {
    eq: () => q, gte: () => q,
    then: (res: (v: unknown) => void) =>
      res(rows === null ? { data: null, error: new Error("stub 실패") }
                        : { data: rows, error: null }),
  };
  // deno-lint-ignore no-explicit-any
  return { from: () => ({ select: () => q }) } as any;
}
const depsFor = (sum: number | null) => ({
  client: fakeClient(sum === null ? null : [{ estimated_cost: sum }]),
  env, nowMs: cfg.nowMs,
});

const out: Record<string, unknown> = {};
out.floats = cfg.floatCases.map((c: unknown) => num(pyFloat(c)));
out.formats = cfg.formatCases.map((x: number) => pyFormatF(x, 4));
out.sums = cfg.sumRowCases.map((rows: Array<Record<string, unknown>>) =>
  num(sumCostRows(rows)));
out.pageCaps = cfg.pageCapCases.map(([called, cap]: [number, number]) =>
  checkDocPageCap(env, { calledPages: called, pageCap: cap }));
out.midnight = utcMidnightIso(cfg.nowMs);
out.cutoff = slidingCutoffIso(cfg.nowMs);

const costs: unknown[] = [];
for (const [sum, cap] of cfg.costCases) {
  costs.push({
    doc: await checkDocBudget(depsFor(sum), { docId: "d1", capUsd: cap }),
    docNoId: await checkDocBudget(depsFor(sum), { docId: "", capUsd: cap }),
    daily: await checkDailyBudget(depsFor(sum), { capUsd: cap }),
    sliding: await check24hSlidingBudget(depsFor(sum), { capUsd: cap }),
    combined: await checkCombined(depsFor(sum), {
      docId: "d1", docCapUsd: cap, dailyCapUsd: cap, sliding24hCapUsd: cap,
    }),
    combinedNoSliding: await checkCombined(depsFor(sum), {
      docId: "d1", docCapUsd: cap, dailyCapUsd: cap, sliding24hCapUsd: null,
    }),
  });
}
out.costs = costs;

if (NEG) out.formats = (out.formats as string[]).map((s, i) => i === 3 ? s + "0" : s);
console.log(JSON.stringify(out));
""" % {"shared": SHARED}


def main() -> None:
    from app.services import budget_guard as bg

    negative = "--negative" in sys.argv
    fixed_now = datetime.fromtimestamp(FIXED_NOW_MS / 1000, tz=timezone.utc)

    def num(v):
        if v is None:
            return None
        if isinstance(v, float):
            if math.isnan(v):
                return "nan"
            if v == math.inf:
                return "inf"
            if v == -math.inf:
                return "-inf"
            if v == 0.0 and math.copysign(1, v) < 0:
                return "-0.0"
        return v

    # ---- Python 기대값 ----
    py_floats = []
    for c in FLOAT_CASES:
        try:
            py_floats.append(num(float(c)))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            py_floats.append(None)
    py_formats = [f"{x:.4f}" for x in FORMAT_CASES]
    py_sums = [num(bg._sum_cost_rows(rows)) for rows in SUM_ROW_CASES]
    py_page_caps = [
        bg.check_doc_page_cap(called_pages=c, page_cap=p) for c, p in PAGE_CAP_CASES
    ]

    class FixedDT(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: D102
            return fixed_now

    orig_dt = bg.datetime
    bg.datetime = FixedDT  # type: ignore[assignment]
    py_midnight = bg._utc_midnight_iso()
    py_cutoff = bg._sliding_cutoff_iso()
    bg.datetime = orig_dt  # type: ignore[assignment]

    py_costs = []
    orig = (bg._sum_doc_cost, bg._sum_daily_cost, bg._sum_24h_sliding_cost)
    for stub, cap in COST_CASES:
        bg._sum_doc_cost = lambda _d, _s=stub: _s  # type: ignore[assignment]
        bg._sum_daily_cost = lambda _s=stub: _s  # type: ignore[assignment]
        bg._sum_24h_sliding_cost = lambda now=None, _s=stub: _s  # type: ignore[assignment]
        py_costs.append({
            "doc": bg.check_doc_budget(doc_id="d1", cap_usd=cap),
            "docNoId": bg.check_doc_budget(doc_id="", cap_usd=cap),
            "daily": bg.check_daily_budget(cap_usd=cap),
            "sliding": bg.check_24h_sliding_budget(cap_usd=cap),
            "combined": bg.check_combined(
                doc_id="d1", doc_cap_usd=cap, daily_cap_usd=cap,
                sliding_24h_cap_usd=cap),
            "combinedNoSliding": bg.check_combined(
                doc_id="d1", doc_cap_usd=cap, daily_cap_usd=cap,
                sliding_24h_cap_usd=None),
        })
    bg._sum_doc_cost, bg._sum_daily_cost, bg._sum_24h_sliding_cost = orig

    # ---- TS 실행 ----
    with tempfile.TemporaryDirectory() as tmp:
        cf, rf = os.path.join(tmp, "c.json"), os.path.join(tmp, "r.ts")
        with open(cf, "w", encoding="utf-8") as f:
            json.dump({
                "floatCases": FLOAT_CASES, "formatCases": FORMAT_CASES,
                "sumRowCases": SUM_ROW_CASES, "pageCapCases": PAGE_CAP_CASES,
                "costCases": COST_CASES, "nowMs": FIXED_NOW_MS,
                "negative": negative,
            }, f)
        with open(rf, "w", encoding="utf-8") as f:
            f.write(RUNNER_TS)
        proc = subprocess.run(
            ["deno", "run", "--config", DENO_CONFIG, "--allow-all", rf, cf],
            capture_output=True, text=True, timeout=600,
        )
        if proc.returncode != 0:
            raise SystemExit(f"deno 실행 실패:\n{proc.stderr[:4000]}")
        ts = json.loads([l for l in proc.stdout.splitlines() if l.startswith("{")][-1])

    fails: list[str] = []

    def cmp(label: str, a, b) -> None:
        if a != b:
            fails.append(f"{label}: py={a!r} ts={b!r}")

    for c, a, b in zip(FLOAT_CASES, py_floats, ts["floats"]):
        cmp(f"float({c!r})", a, b)
    for c, a, b in zip(FORMAT_CASES, py_formats, ts["formats"]):
        cmp(f"'{{:.4f}}'.format({c!r})", a, b)
    for i, (a, b) in enumerate(zip(py_sums, ts["sums"])):
        cmp(f"sum_cost_rows[{i}]", a, b)
    for (called, cap), a, b in zip(PAGE_CAP_CASES, py_page_caps, ts["pageCaps"]):
        cmp(f"page_cap({called},{cap}).allowed", a.allowed, b["allowed"])
        cmp(f"page_cap({called},{cap}).used", a.used_usd, b["usedUsd"])
        cmp(f"page_cap({called},{cap}).cap", a.cap_usd, b["capUsd"])
        cmp(f"page_cap({called},{cap}).scope", a.scope, b["scope"])
        cmp(f"page_cap({called},{cap}).reason", a.reason, b["reason"])
    cmp("utc_midnight_iso", py_midnight, ts["midnight"])
    # 원본은 마이크로초 6자리, JS 는 밀리초 3자리 — 초까지만 비교하고 나머지는 아래 별도 확인.
    cmp("sliding_cutoff_iso(초까지)", py_cutoff[:19], ts["cutoff"][:19])

    for (stub, cap), pyc, tsc in zip(COST_CASES, py_costs, ts["costs"]):
        for k in ("doc", "docNoId", "daily", "sliding", "combined", "combinedNoSliding"):
            a, b = pyc[k], tsc[k]
            tag = f"{k}(sum={stub},cap={cap})"
            cmp(f"{tag}.allowed", a.allowed, b["allowed"])
            cmp(f"{tag}.used", a.used_usd, b["usedUsd"])
            cmp(f"{tag}.cap", a.cap_usd, b["capUsd"])
            cmp(f"{tag}.scope", a.scope, b["scope"])
            cmp(f"{tag}.reason", a.reason, b["reason"])

    total = (len(FLOAT_CASES) + len(FORMAT_CASES) + len(SUM_ROW_CASES)
             + len(PAGE_CAP_CASES) * 5 + 2 + len(COST_CASES) * 6 * 5)
    for f in fails[:30]:
        print(f"  **{f}**")
    print()
    print(f"  마이크로초 정밀도: py={py_cutoff}  ts={ts['cutoff']}")
    print(f"  비교 {total}건, 불일치 {len(fails)}건")
    if negative:
        print("음성 대조: " + ("검사기 정상 (변조 검출)" if fails else "**검사기가 못 잡는다**"))
        sys.exit(0 if fails else 1)
    print("FAIL 0" if not fails else f"FAIL {len(fails)}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(ROOT, "api"))
    main()
