"""`/stats` · `/stats/trend` 를 Python 핸들러와 응답 대조.

`/search` 때와 같이 **양쪽 in-process** 로 돌린다 — HTTP 를 태우면 인증·네트워크 변수가
섞이고, 인증은 Phase 1 에서 이미 따로 검증했다.

## 시각에 의존하는 필드를 어떻게 다루나
`generated_at` 은 "지금" 이라 두 실행이 같을 수 없다 — **형식만** 본다
(`YYYY-MM-DDTHH:MM:SS.ffffff+00:00`). 값 비교에서는 뺀다.

`added_this_month`·`added_last_7d`·`vision_usage` 는 **현재 시각에 의존**한다. 두 호출
사이에 자정이 지나면 값이 갈릴 수 있으므로, TS 쪽에는 Python 이 쓴 시각을 주입해
같은 기준으로 계산하게 한다. 주입이 없으면 "가끔 실패하는 검사기" 가 된다.

## FastAPI 계층의 422 는 따로 잰다
Python 핸들러를 직접 부르면 **FastAPI 의 `Literal` 검증을 안 거친다.** 그래서 잘못된
`range`/`mode`/`metric` 은 in-process 대조로 확인할 수 없다 — 실제로 그 사각지대 때문에
"마지막만 `or`, 나머지는 쉼표" 라는 pydantic 문구 규칙을 놓쳤고, 배포 후 HTTP 로 재고서야
드러났다. 지금은 **pydantic 으로 직접 기대값을 만들어** 대조한다(독립 생성).

## 이 검사기가 못 재는 것
`/stats` 는 호출 사이에 DB 가 바뀌면 값이 달라진다(인제스트가 돌면 문서 수가 는다).
운영 트래픽이 적어 실제로는 안정적이지만, 불일치가 나면 **먼저 그 가능성을 의심**해야
한다 — 같은 스크립트를 한 번 더 돌려 재현되는지 본다.

사용:
    api/.venv/bin/python api/scripts/verify_stats_parity.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
STATS_DIR = os.path.join(ROOT, "supabase", "functions", "_shared", "stats")
DENO_CONFIG = os.path.join(ROOT, "supabase", "functions", "deno.json")

sys.path.insert(0, os.path.join(ROOT, "api"))

TREND_CASES: list[tuple[str, dict]] = [
    ("기본", {}),
    ("search 24h", {"metric": "search", "range": "24h"}),
    ("search 30d hybrid", {"metric": "search", "range": "30d", "mode": "hybrid"}),
    ("search dense", {"metric": "search", "mode": "dense"}),
    ("search sparse", {"metric": "search", "mode": "sparse"}),
    ("vision 7d", {"metric": "vision", "range": "7d"}),
    ("vision 24h (mode 무시)", {"metric": "vision", "range": "24h", "mode": "dense"}),
    ("vision 30d", {"metric": "vision", "range": "30d"}),
    ("range 잘못", {"range": "1y"}),
    ("mode 잘못", {"mode": "bogus"}),
    ("metric 잘못", {"metric": "bogus"}),
    ("여러 개 동시에 잘못", {"range": "1y", "mode": "bogus", "metric": "bogus"}),
]

RUNNER_TS = f"""
import {{ createClient }} from "@supabase/supabase-js";
import {{
  buildStats, buildTrend, utcIsoLikePython, validateTrendParams,
}} from "file://{STATS_DIR}/pipeline.ts";

const input = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const env: Record<string, string> = input.env;
const client = createClient(env.SUPABASE_URL, env.SUPABASE_SERVICE_ROLE_KEY, {{
  auth: {{ persistSession: false }},
}});
// Python 이 쓴 시각을 그대로 쓴다 — 자정·창 경계에서 갈리지 않게.
const now = () => input.now_ms;

const stats = await buildStats(input.user_id, {{ client, now }});

const trends: unknown[] = [];
for (const qs of input.trend as Record<string, string>[]) {{
  const v = validateTrendParams(new URLSearchParams(qs));
  if (!v.ok) {{
    trends.push({{ status: 422, detail: v.detail }});
    continue;
  }}
  trends.push({{ status: 200, body: await buildTrend(v.params, {{ client, now }}) }});
}}

console.log(JSON.stringify({{ stats, trends, iso_sample: utcIsoLikePython(input.now_ms) }}));
"""


def run_deno(payload: dict, timeout: int = 600) -> dict:
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


def diff(a, b, path="") -> list[str]:
    """파싱된 값 기준 깊은 비교. `1.0` 과 `1` 은 같다고 본다."""
    out: list[str] = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{path}.{k}: 한쪽에만 있음 (py={k in a} ts={k in b})")
            else:
                out += diff(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: 길이 py={len(a)} ts={len(b)}")
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                out += diff(x, y, f"{path}[{i}]")
    elif isinstance(a, bool) != isinstance(b, bool):
        out.append(f"{path}: py={a!r} ts={b!r}")
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if a != b:
            out.append(f"{path}: py={a!r} ts={b!r}")
    elif a != b:
        out.append(f"{path}: py={a!r} ts={b!r}")
    return out


_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+00:00$")


def literal_errors(field: str, allowed: tuple, raw: str) -> list[dict]:
    """FastAPI 가 낼 422 항목을 **pydantic 으로 직접** 만든다.

    핸들러를 거치지 않으므로 TS 구현과 독립적이다 — 같은 규칙을 두 번 적으면
    양쪽이 똑같이 틀려도 통과한다.
    """
    from typing import Literal

    from pydantic import TypeAdapter, ValidationError

    ta = TypeAdapter(Literal[allowed])  # type: ignore[valid-type]
    try:
        ta.validate_python(raw)
    except ValidationError as e:
        out = []
        for err in e.errors():
            out.append({
                "type": err["type"],
                "loc": ["query", field],
                "msg": err["msg"],
                "input": raw,
                "ctx": {"expected": err["ctx"]["expected"]},
            })
        return out
    return []


def main() -> None:
    import time
    from datetime import datetime, timezone

    from dotenv import load_dotenv

    load_dotenv(os.path.join(ROOT, ".env"))
    # 두 지표 모두 DB 기준으로 — Edge 에는 in-memory 가 없다.
    os.environ["JETRAG_SLO_SOURCE"] = "db"
    os.environ["JETRAG_VISION_USAGE_SOURCE"] = "db"

    from app.auth.dependencies import CurrentUser
    from app.routers.stats import stats as py_stats
    from app.routers.stats import stats_trend as py_trend

    user_id = os.environ.get("OWNER_USER_ID")
    if not user_id:
        from supabase import create_client
        c = create_client(
            os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"]
        )
        rows = (
            c.table("documents").select("user_id").is_("deleted_at", "null")
            .limit(1).execute().data
        )
        if not rows:
            raise SystemExit("문서를 가진 사용자를 못 찾았다.")
        user_id = rows[0]["user_id"]

    now_ms = int(time.time() * 1000)
    cu = CurrentUser(user_id=user_id, email=None, is_authenticated=True)

    py_stats_body = py_stats(current_user=cu).model_dump()
    py_trends = []
    for _, qs in TREND_CASES:
        try:
            r = py_trend(
                range=qs.get("range", "7d"),
                mode=qs.get("mode", "all"),
                metric=qs.get("metric", "search"),
            )
            py_trends.append({"status": 200, "body": r.model_dump()})
        except Exception as exc:  # noqa: BLE001
            py_trends.append({"status": "ERR", "detail": str(exc)})

    env = {k: os.environ[k] for k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")}
    ts = run_deno({
        "user_id": user_id,
        "now_ms": now_ms,
        "env": env,
        "trend": [qs for _, qs in TREND_CASES],
    })

    fails = 0

    print(f"대상 사용자: {user_id}")
    print()
    print("=== /stats 응답 대조 (generated_at 값 제외) ===")
    a = {k: v for k, v in py_stats_body.items() if k != "generated_at"}
    b = {k: v for k, v in ts["stats"].items() if k != "generated_at"}
    d = diff(a, b)
    if d:
        fails += len(d)
        for line in d[:20]:
            print(f"  MISMATCH {line}")
        if len(d) > 20:
            print(f"  ... 외 {len(d) - 20}건")
    else:
        doc = a["documents"]
        print(f"  OK   문서 {doc['total']}건 · 청크 {a['chunks_total']} · "
              f"잡 {a['jobs']['total']} · SLO 표본 {a['search_slo']['sample_count']}")

    print()
    print("=== generated_at 형식 ===")
    for name, val in (("py", py_stats_body["generated_at"]), ("ts", ts["iso_sample"])):
        if not _ISO_RE.match(val):
            fails += 1
            print(f"  MISMATCH {name} 형식이 다르다: {val!r}")
    print(f"  py={py_stats_body['generated_at']}")
    print(f"  ts={ts['iso_sample']}")

    print()
    print("=== /stats/trend 대조 ===")
    for (name, qs), pv, tv in zip(TREND_CASES, py_trends, ts["trends"]):
        # Python 은 잘못된 Literal 을 FastAPI 계층에서 422 로 막는다. 핸들러를 직접
        # 부르면 그 검증을 안 거치므로, 여기서는 **TS 의 422 만** 형태로 확인한다.
        if tv.get("status") == 422:
            if pv.get("status") == 200:
                print(f"  {name:<24} TS 422 (원본은 FastAPI 계층에서 422 — 별도 확인)")
            continue
        if pv.get("status") != 200:
            fails += 1
            print(f"  {name:<24} py 오류: {pv.get('detail')}")
            continue
        pa = {k: v for k, v in pv["body"].items() if k != "generated_at"}
        tb = {k: v for k, v in tv["body"].items() if k != "generated_at"}
        d = diff(pa, tb)
        if d:
            fails += 1
            print(f"  {name:<24} MISMATCH ({len(d)}건)")
            for line in d[:4]:
                print(f"      {line}")
        else:
            print(f"  {name:<24} OK   buckets={len(pa['buckets'])} "
                  f"error_code={pa['error_code']}")

    print()
    print("=== trend 422 문구 (pydantic 직접 생성과 대조) ===")
    from app.routers.stats import _VALID_METRICS, _VALID_MODES, _VALID_RANGES

    ts_422 = {}
    for (name, qs), tv in zip(TREND_CASES, ts["trends"]):
        if tv.get("status") == 422:
            ts_422[name] = tv["detail"]
    literal_cases = [
        ("range 잘못", "range", tuple(_VALID_RANGES), "1y"),
        ("mode 잘못", "mode", tuple(_VALID_MODES), "bogus"),
        ("metric 잘못", "metric", tuple(_VALID_METRICS), "bogus"),
    ]
    for name, field, allowed, raw in literal_cases:
        want = literal_errors(field, allowed, raw)
        got = ts_422.get(name)
        if got != want:
            fails += 1
            print(f"  MISMATCH {name}")
            print(f"      pydantic={want}")
            print(f"      ts      ={got}")
        else:
            print(f"  {name:<16} OK   {want[0]['msg']}")
    print(f"  {len(literal_cases)}건 대조")

    print()
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
