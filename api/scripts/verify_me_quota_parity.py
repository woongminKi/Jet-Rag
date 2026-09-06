"""`/me/*` 의 플랜·사용량 분기를 **합성 데이터**로 대조.

## 왜 따로 필요한가
`verify_me_parity.py` 는 실제 DB 로 응답을 대조한다. 그런데 운영 상태가 한 점에 고정돼
있어서 분기가 대부분 실행되지 않는다 (2026-09-06 실측):

```
구독 = pro/active   플랜 조회 성공   문서 카운트 성공   UTC 와 KST 가 같은 날짜
```

음성 대조를 걸어 보니 "past_due 를 유효에서 제외", "documents_count 의 null→0 접기 제거",
"usage_counters 날짜를 KST 로", "pro 판정을 plan!=null 로" 네 가지가 **한 건도 안 잡혔다.**
전부 지금 데이터로는 결과가 같아서다.

## 어떻게 재나
양쪽에 **가짜 DB 클라이언트**를 꽂아 같은 행을 먹인다. Python 은 `get_supabase_client` 를
갈아끼우고, TS 는 클라이언트를 인자로 받으므로 그대로 넣는다.

날짜 경계는 `now` 를 주입한다 — UTC 와 KST 가 **다른 날**인 시각을 반드시 하나 넣는다.
안 그러면 "UTC vs KST" 결함이 안 잡힌다(위와 같은 이유).

사용:
    api/.venv/bin/python api/scripts/verify_me_quota_parity.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
ME_DIR = os.path.join(ROOT, "supabase", "functions", "_shared", "me")
DENO_CONFIG = os.path.join(ROOT, "supabase", "functions", "deno.json")

sys.path.insert(0, os.path.join(ROOT, "api"))

USER = "u-test"
DOMAIN = "in.example.test"


def _ms(iso: str) -> int:
    from datetime import datetime

    return int(datetime.fromisoformat(iso).timestamp() * 1000)


# UTC 와 KST 가 **다른 날**인 시각 — 이게 없으면 날짜 기준 결함이 안 잡힌다.
NOW_SAME_DAY = _ms("2026-09-06T02:00:00+00:00")   # UTC 9/6 · KST 9/6
NOW_DIFF_DAY = _ms("2026-09-06T15:30:00+00:00")   # UTC 9/6 · KST 9/7

# (이름, 테이블별 행, now, 기대 plan_code)
#
# **행에 필터 키를 반드시 넣는다.** 처음엔 `subscriptions` 행에 `user_id` 를 빼먹어서
# `eq("user_id", ...)` 에 전부 탈락했고, 양쪽 다 "구독 없음 → free" 로 떨어져 일치했다 —
# `past_due` 분기가 통째로 안 태워졌는데 초록이었다. 그래서 기대 코드를 케이스에 적어
# **분기를 실제로 태웠는지 검사기가 스스로 확인**한다.
def sub(plan_code: str, status: str, end=None) -> dict:
    return {"user_id": USER, "plan_code": plan_code, "status": status,
            "current_period_end": end}


def plan_row(code: str, max_docs: int, per_day: int) -> dict:
    return {"code": code, "max_documents": max_docs, "answers_per_day": per_day}


def doc(i: int) -> dict:
    return {"id": str(i), "user_id": USER, "deleted_at": None}


def usage(count: int, date: str, metric: str = "answers") -> dict:
    return {"user_key": USER, "metric": metric, "period_date": date, "count": count}


FREE = plan_row("free", 5, 3)
PRO = plan_row("pro", 200, 50)

CASES: list[tuple[str, dict, int, str | None]] = [
    ("구독 없음 → free", {
        "subscriptions": [], "plans": [FREE], "documents": [doc(1)], "usage_counters": [],
    }, NOW_SAME_DAY, "free"),
    ("active pro", {
        "subscriptions": [sub("pro", "active", "2026-10-01T00:00:00+00:00")],
        "plans": [FREE, PRO], "documents": [doc(1), doc(2)],
        "usage_counters": [usage(7, "2026-09-06")],
    }, NOW_SAME_DAY, "pro"),
    # `past_due` 는 유예 기간이라 플랜을 **유지**한다 — 갈리면 유료 사용자가 free 로 떨어진다.
    ("past_due 는 플랜 유지", {
        "subscriptions": [sub("pro", "past_due")],
        "plans": [FREE, PRO], "documents": [], "usage_counters": [],
    }, NOW_SAME_DAY, "pro"),
    ("canceled 는 free 로", {
        "subscriptions": [sub("pro", "canceled", "2026-09-01T00:00:00+00:00")],
        "plans": [FREE, PRO], "documents": [], "usage_counters": [],
    }, NOW_SAME_DAY, "free"),
    ("알 수 없는 status 는 free 로", {
        "subscriptions": [sub("pro", "trialing")],
        "plans": [FREE, PRO], "documents": [], "usage_counters": [],
    }, NOW_SAME_DAY, "free"),
    ("다른 사용자의 구독은 안 본다", {
        "subscriptions": [{"user_id": "other", "plan_code": "pro", "status": "active",
                           "current_period_end": None}],
        "plans": [FREE, PRO], "documents": [], "usage_counters": [],
    }, NOW_SAME_DAY, "free"),
    # 날짜 기준 — UTC 와 KST 가 다른 날.
    ("usage_counters 는 UTC 날짜 (KST 로는 다음 날)", {
        "subscriptions": [], "plans": [FREE], "documents": [],
        "usage_counters": [
            usage(11, "2026-09-06"),   # UTC 기준 오늘
            usage(99, "2026-09-07"),   # KST 기준 오늘 — 이게 나오면 틀렸다
        ],
    }, NOW_DIFF_DAY, "free"),
    ("다른 metric 은 안 센다", {
        "subscriptions": [], "plans": [FREE], "documents": [],
        "usage_counters": [usage(42, "2026-09-06", metric="documents")],
    }, NOW_SAME_DAY, "free"),
    ("문서 수는 본인 것만", {
        "subscriptions": [], "plans": [FREE],
        "documents": [doc(1), doc(2), {"id": "3", "user_id": "other", "deleted_at": None}],
        "usage_counters": [],
    }, NOW_SAME_DAY, "free"),
]

# 플랜 조회가 실패하는 경우 — `/me/plan` 은 503.
PLAN_MISSING = {
    "subscriptions": [], "plans": [],  # plans 에 free 가 없다
    "documents": [], "usage_counters": [],
}

RUNNER_TS = f"""
import {{ buildPlan, buildSubscription, MeHttpError }} from "file://{ME_DIR}/pipeline.ts";
import {{ getEffectivePlan }} from "file://{ME_DIR}/quota.ts";

const input = JSON.parse(await Deno.readTextFile(Deno.args[0]));

/** 테이블별 행 배열을 먹는 최소 클라이언트. `eq` 필터만 지원한다. */
// deno-lint-ignore no-explicit-any
function fakeClient(tables: Record<string, any[]>): any {{
  return {{
    from(name: string) {{
      const rows = tables[name] ?? [];
      // deno-lint-ignore no-explicit-any
      const q: any = {{
        _eq: {{}} as Record<string, unknown>,
        _count: false,
        _limit: undefined as number | undefined,
        // deno-lint-ignore no-explicit-any
        select(_cols: string, opts?: any) {{
          if (opts?.count === "exact") q._count = true;
          return q;
        }},
        eq(k: string, v: unknown) {{ q._eq[k] = v; return q; }},
        is(_k: string, _v: unknown) {{ return q; }},
        limit(n: number) {{ q._limit = n; return q; }},
        // deno-lint-ignore no-explicit-any
        then(resolve: any) {{
          const m = rows.filter((r) =>
            Object.entries(q._eq).every(([k, v]) => r[k] === v)
          );
          resolve({{
            data: m.slice(0, q._limit ?? m.length),
            count: q._count ? m.length : null,
            error: null,
          }});
        }},
      }};
      return q;
    }},
  }};
}}

const out: unknown[] = [];
// deno-lint-ignore no-explicit-any
for (const c of input.cases as any[]) {{
  const deps = {{
    client: fakeClient(c.tables),
    emailIngestDomain: input.domain,
    now: () => c.now_ms,
  }};
  let plan: unknown;
  try {{
    plan = await buildPlan(input.user, deps);
  }} catch (e) {{
    plan = e instanceof MeHttpError ? {{ status: e.status, detail: e.detail }} : String(e);
  }}
  out.push({{
    plan,
    subscription: await buildSubscription(input.user, deps),
    effective: await getEffectivePlan(deps.client, input.user),
  }});
}}
console.log(JSON.stringify(out));
"""


def run_deno(payload: dict) -> list:
    with tempfile.TemporaryDirectory() as tmp:
        cf, rf = os.path.join(tmp, "cases.json"), os.path.join(tmp, "runner.ts")
        with open(cf, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        with open(rf, "w", encoding="utf-8") as f:
            f.write(RUNNER_TS)
        proc = subprocess.run(
            ["deno", "run", "--config", DENO_CONFIG, "--allow-all", rf, cf],
            capture_output=True, text=True, timeout=300,
        )
    if proc.returncode != 0:
        raise SystemExit(f"deno 실행 실패:\n{proc.stderr[:2500]}")
    return json.loads(proc.stdout)


class _FakeQuery:
    def __init__(self, rows):
        self._rows, self._eq, self._count, self._limit = rows, {}, False, None

    def select(self, _cols, count=None):
        if count == "exact":
            self._count = True
        return self

    def eq(self, k, v):
        self._eq[k] = v
        return self

    def is_(self, _k, _v):
        return self

    def limit(self, n):
        self._limit = n
        return self

    def execute(self):
        m = [r for r in self._rows if all(r.get(k) == v for k, v in self._eq.items())]
        return type("R", (), {
            "data": m[: self._limit] if self._limit else m,
            "count": len(m) if self._count else None,
        })()


class _FakeClient:
    def __init__(self, tables):
        self._tables = tables

    def table(self, name):
        return _FakeQuery(self._tables.get(name, []))


def main() -> None:
    from datetime import datetime, timezone
    from unittest.mock import patch

    from app.auth.dependencies import CurrentUser
    from app.config import get_settings
    from app.routers import me as ME
    from app.services import quota as Q

    all_cases = [
        *CASES,
        ("plans 에 코드 없음 → /me/plan 503", PLAN_MISSING, NOW_SAME_DAY, None),
    ]

    ts = run_deno({
        "user": USER,
        "domain": DOMAIN,
        "cases": [{"tables": t, "now_ms": n} for _, t, n, _ in all_cases],
    })

    fails = 0
    print("=== 플랜·구독 분기 (합성 DB) ===")
    for (name, tables, now_ms, want_code), tv in zip(all_cases, ts):
        client = _FakeClient(tables)
        frozen = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)

        class _DT(datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen if tz else frozen.replace(tzinfo=None)

        with patch.object(Q, "get_supabase_client", lambda: client), \
                patch.object(Q, "datetime", _DT):
            cu = CurrentUser(user_id=USER, email=None, is_authenticated=True)
            try:
                plan = ME.me_plan(current_user=cu).model_dump()
            except Exception as exc:  # noqa: BLE001
                plan = {"status": getattr(exc, "status_code", "ERR"),
                        "detail": getattr(exc, "detail", str(exc))}
            sub = ME.me_subscription(current_user=cu).model_dump()
            eff = Q.get_effective_plan(USER)
            eff_d = None if eff is None else {
                "code": eff.code, "max_documents": eff.max_documents,
                "answers_per_day": eff.answers_per_day,
            }

        # **케이스가 의도한 분기를 실제로 태웠는지** 먼저 본다. 필터 키를 빠뜨리면
        # 양쪽이 나란히 엉뚱한 가지로 가서 "일치" 로 통과한다.
        got_code = eff_d["code"] if eff_d else None
        if got_code != want_code:
            fails += 1
            print(f"  케이스 무효 {name} — 기대 plan_code={want_code!r} 인데 {got_code!r}")
            continue

        want = {"plan": plan, "subscription": sub, "effective": eff_d}
        if want != tv:
            fails += 1
            print(f"  MISMATCH {name}")
            print(f"      py={want}")
            print(f"      ts={tv}")
        else:
            print(f"  {name:<38} OK")
    print(f"  {len(all_cases)}건 대조")

    print()
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
