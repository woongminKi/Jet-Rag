"""결제·정기결제 이식(`_shared/billing/*`)을 원본과 대조.

## 돈이 걸린 코드라 대조 기준이 다르다
검색 결과가 조금 달라지는 것과 **이중 청구**는 무게가 다르다. 그래서 응답만 보지 않고
**DB 에 어떤 write 가 어떤 순서로 나갔는지**까지 비교한다. 양쪽 DB 를 같은 스텁으로
갈고, 호출 로그를 통째로 맞춘다.

## 서비스 함수는 진짜를 돌린다
`billing.py` 의 로직을 harness 안에 다시 구현하면 대조가 아무것도 증명하지 않는다.
DB(supabase client)와 KakaoPay(HTTP)만 스텁으로 바꾸고 나머지는 원본 코드를 태운다.

## 노린 것 — 실패 종류마다 처리가 다르다
| 시나리오 | 기대 |
|---|---|
| `billing_key` 없음 | `past_due` + `charge_failed` 이력 |
| SID 복호화 실패 | **skip only** — `past_due` 아님(grace clock 보존) |
| 결제 거절 | `past_due`, 이미 `past_due_since` 가 있으면 **덮어쓰지 않음** |
| 이번 주기 이미 결제됨 | 결제 호출 0회, 기간만 갱신 |
| 결제 성공 후 갱신 실패 | 멱등 마커가 남아 다음 배치가 재청구 안 함 |
| 말일(1/31) | 2/28 로 clamp |

**운영 키·운영 SID 를 쓰지 않는다** — Fernet 키는 매번 새로 만들고, SID 는 가짜다.

사용:
    api/.venv/bin/python api/scripts/verify_billing_parity.py
    api/.venv/bin/python api/scripts/verify_billing_parity.py --negative
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

from cryptography.fernet import Fernet

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SHARED = os.path.join(ROOT, "supabase", "functions", "_shared")
DENO_CONFIG = os.path.join(ROOT, "supabase", "functions", "deno.json")

NOW_MS = 1_772_000_000_000  # 2026-02-25T07:33:20+00:00 — 고정 시각
NOW_ISO = datetime.fromtimestamp(NOW_MS / 1000, tz=timezone.utc).isoformat()

U1 = "11111111-1111-1111-1111-111111111111"
U2 = "22222222-2222-2222-2222-222222222222"

RUNNER_TS = """
import {
  approveSubscription, cancelSubscription, chargeDueSubscriptions,
  startSubscription, SubscriptionNotPendingError, sweepPastDue,
} from "file://%(shared)s/billing/billing.ts";
import { addOneMonth, formatIso, parseIso, utcParts } from "file://%(shared)s/billing/pydate.ts";
import { KakaoPayClient, PaymentError } from "file://%(shared)s/billing/kakaopay.ts";
import { fernetEncrypt } from "file://%(shared)s/billing/fernet.ts";

const cfg = JSON.parse(await Deno.readTextFile(Deno.args[0]));

// ---- DB 스텁: write 를 순서대로 기록한다 ----
function makeClient(state: Record<string, any>, calls: unknown[]) {
  const q = (rows: unknown) => {
    const o: any = {
      eq: () => o, in: () => o, lte: () => o, is: () => o, limit: () => o,
      select: () => o,
      then: (res: (v: unknown) => void) => res({ data: rows, error: null }),
    };
    return o;
  };
  return {
    from(table: string) {
      return {
        select: () => {
          if (table === "subscriptions") return q(state.subscriptions ?? []);
          if (table === "payment_history") return q(state.history ?? []);
          return q([]);
        },
        insert(row: Record<string, unknown>) {
          calls.push({ op: "insert", table, row });
          if (state.insertFails?.[table]) {
            return { then: (r: (v: unknown) => void) => r({ data: null, error: { message: "insert 실패" } }) };
          }
          return q(null);
        },
        update(row: Record<string, unknown>) {
          calls.push({ op: "update", table, row });
          const fail = state.updateFails?.[table];
          return {
            eq: () => ({
              then: (r: (v: unknown) => void) =>
                r(fail ? { data: null, error: { message: "update 실패" } } : { data: null, error: null }),
            }),
          };
        },
      };
    },
  } as any;
}

// ---- KakaoPay 스텁: HTTP 를 가로챈다 ----
function makeProvider(behavior: Record<string, any>, calls: unknown[]) {
  const fetchFn = ((url: string | URL, init?: RequestInit) => {
    const path = String(url).replace("https://open-api.kakaopay.com", "");
    calls.push({ op: "kakaopay", path, body: JSON.parse(String(init?.body ?? "{}")) });
    const b = behavior[path];
    if (b?.throw) return Promise.reject(new TypeError("network down"));
    return Promise.resolve(
      new Response(JSON.stringify(b?.json ?? {}), { status: b?.status ?? 200 }),
    );
  }) as unknown as typeof fetch;
  return new KakaoPayClient({ secretKey: "sk_test", cid: "TCSUBSCRIP", fetchFn });
}

const out: Record<string, unknown> = {};

// ---- 1. 날짜 연산 ----
out.dates = cfg.dateCases.map((s: string) => formatIso(addOneMonth(parseIso(s)!)));

// ---- 2. 시나리오 ----
const scenarios: unknown[] = [];
for (const sc of cfg.scenarios) {
  const calls: unknown[] = [];
  const state = JSON.parse(JSON.stringify(sc.state));
  // 암호문은 TS 쪽에서 만든다 — 양쪽이 서로의 것을 풀 수 있음은 fernet 대조가 이미 증명했다.
  for (const row of state.subscriptions ?? []) {
    if (row.__sid) row.billing_key = await fernetEncrypt(cfg.key, row.__sid);
    if (row.__badKey) row.billing_key = "gAAAAABm-깨진-암호문";
  }
  const deps = {
    client: makeClient(state, calls),
    provider: makeProvider(sc.kakaopay ?? {}, calls),
    encryptionKey: cfg.key,
    billingRedirectBase: cfg.redirectBase,
    nowMs: cfg.nowMs,
  };
  let result: unknown = null;
  let error: string | null = null;
  try {
    if (sc.fn === "charge") result = await chargeDueSubscriptions(deps);
    else if (sc.fn === "sweep") result = await sweepPastDue(deps);
    else if (sc.fn === "start") result = await startSubscription(deps, sc.userId);
    else if (sc.fn === "approve") { await approveSubscription(deps, sc.userId, sc.pgToken); result = "ok"; }
    else if (sc.fn === "cancel") { await cancelSubscription(deps, sc.userId); result = "ok"; }
  } catch (e) {
    error = e instanceof SubscriptionNotPendingError
      ? "SubscriptionNotPendingError"
      : e instanceof PaymentError ? `PaymentError` : e!.constructor.name;
  }
  scenarios.push({ name: sc.name, result, error, calls });
}
out.scenarios = scenarios;

if (cfg.negative) (out.dates as string[])[0] = "변조";
await Deno.writeTextFile(Deno.args[1], JSON.stringify(out));
""" % {"shared": SHARED}

DATE_CASES = [
    "2026-01-31T12:00:00+00:00",   # 말일 clamp → 2/28
    "2024-01-31T12:00:00+00:00",   # 윤년 → 2/29
    "2026-12-15T09:30:00+00:00",   # 연도 넘김
    "2026-11-30T23:59:59+00:00",
    "2026-03-31T00:00:00+00:00",   # → 4/30
    "2026-05-15T10:20:30.123456+00:00",  # 마이크로초 보존
    "2026-05-15T10:20:30.789+00:00",     # 3자리 → 6자리로 늘어난다
    "2026-08-31T00:00:00+00:00",   # → 9/30
    "2024-02-29T00:00:00+00:00",   # 윤일 → 3/29
]


def scenarios() -> list[dict]:
    """`__sid` 는 harness 가 암호화해 넣을 자리 표시자다."""
    ready_ok = {"/online/v1/payment/ready": {
        "json": {"tid": "T1234", "next_redirect_pc_url": "https://kakao/pc"}}}
    return [
        # ---- charge ----
        {"name": "billing_key 없음 → past_due", "fn": "charge",
         "state": {"subscriptions": [
             {"user_id": U1, "billing_key": None, "status": "active",
              "current_period_end": "2026-02-01T00:00:00+00:00", "past_due_since": None}]}},
        {"name": "정상 결제", "fn": "charge",
         "state": {"subscriptions": [
             {"user_id": U1, "__sid": "SID-AAA", "status": "active",
              "current_period_end": "2026-02-01T00:00:00+00:00", "past_due_since": None}]},
         "kakaopay": {}},
        {"name": "결제 거절 → past_due", "fn": "charge",
         "state": {"subscriptions": [
             {"user_id": U1, "__sid": "SID-AAA", "status": "active",
              "current_period_end": "2026-02-01T00:00:00+00:00", "past_due_since": None}]},
         "kakaopay": {"/online/v1/payment/subscription": {"status": 400, "json": {"msg": "declined"}}}},
        {"name": "이미 past_due — since 안 덮어씀", "fn": "charge",
         "state": {"subscriptions": [
             {"user_id": U1, "__sid": "SID-AAA", "status": "past_due",
              "current_period_end": "2026-02-01T00:00:00+00:00",
              "past_due_since": "2026-02-20T00:00:00+00:00"}]},
         "kakaopay": {"/online/v1/payment/subscription": {"status": 400, "json": {"msg": "declined"}}}},
        {"name": "SID 복호화 실패 → skip only", "fn": "charge",
         "state": {"subscriptions": [
             {"user_id": U1, "__badKey": True, "status": "active",
              "current_period_end": "2026-02-01T00:00:00+00:00", "past_due_since": None}]}},
        {"name": "이번 주기 이미 결제됨 → 결제 호출 0", "fn": "charge",
         "state": {"subscriptions": [
             {"user_id": U1, "__sid": "SID-AAA", "status": "active",
              "current_period_end": "2026-02-01T00:00:00+00:00", "past_due_since": None}],
             "history": [{"id": 1}]}},
        {"name": "기간 갱신 실패해도 charged", "fn": "charge",
         "state": {"subscriptions": [
             {"user_id": U1, "__sid": "SID-AAA", "status": "active",
              "current_period_end": "2026-02-01T00:00:00+00:00", "past_due_since": None}],
             "updateFails": {"subscriptions": True}}},
        {"name": "대상 없음", "fn": "charge", "state": {"subscriptions": []}},
        # ---- sweep ----
        {"name": "grace 초과 → canceled", "fn": "sweep",
         "state": {"subscriptions": [
             {"user_id": U1, "__sid": "SID-AAA",
              "past_due_since": "2026-02-01T00:00:00+00:00"}]}},
        {"name": "billing_key 없이 sweep", "fn": "sweep",
         "state": {"subscriptions": [
             {"user_id": U1, "billing_key": None,
              "past_due_since": "2026-02-01T00:00:00+00:00"}]}},
        {"name": "inactive 실패해도 로컬 해지", "fn": "sweep",
         "state": {"subscriptions": [
             {"user_id": U1, "__sid": "SID-AAA",
              "past_due_since": "2026-02-01T00:00:00+00:00"}]},
         "kakaopay": {"/online/v1/payment/manage/subscription/inactive": {"throw": True}}},
        # ---- start ----
        {"name": "신규 유저 → placeholder insert", "fn": "start", "userId": U1,
         "state": {"subscriptions": []}, "kakaopay": ready_ok},
        {"name": "기존 구독자 재클릭 → pending_tid 만", "fn": "start", "userId": U1,
         "state": {"subscriptions": [{"status": "active"}]}, "kakaopay": ready_ok},
        {"name": "ready 응답 불완전", "fn": "start", "userId": U1,
         "state": {"subscriptions": []},
         "kakaopay": {"/online/v1/payment/ready": {"json": {"tid": "T1"}}}},
        {"name": "PC URL 없으면 모바일", "fn": "start", "userId": U1,
         "state": {"subscriptions": []},
         "kakaopay": {"/online/v1/payment/ready": {
             "json": {"tid": "T1", "next_redirect_pc_url": "",
                      "next_redirect_mobile_url": "https://kakao/m"}}}},
        # ---- approve ----
        {"name": "pending_tid 없음 → 409", "fn": "approve", "userId": U1, "pgToken": "PG",
         "state": {"subscriptions": [{"pending_tid": None}]}},
        {"name": "approve 성공", "fn": "approve", "userId": U1, "pgToken": "PG",
         "state": {"subscriptions": [{"pending_tid": "T1234"}]},
         "kakaopay": {"/online/v1/payment/approve": {"json": {"sid": "SID-NEW"}}}},
        {"name": "approve 응답에 sid 없음", "fn": "approve", "userId": U1, "pgToken": "PG",
         "state": {"subscriptions": [{"pending_tid": "T1234"}]},
         "kakaopay": {"/online/v1/payment/approve": {"json": {"tid": "T1234"}}}},
        # ---- cancel ----
        {"name": "cancel — SID 있음", "fn": "cancel", "userId": U1,
         "state": {"subscriptions": [{"user_id": U1, "__sid": "SID-AAA"}]}},
        {"name": "cancel — SID 없음", "fn": "cancel", "userId": U1,
         "state": {"subscriptions": [{"user_id": U1, "billing_key": None}]}},
    ]


REDIRECT_BASE = "https://jetrag.example.test"


def main() -> None:
    negative = "--negative" in sys.argv
    sys.path.insert(0, os.path.join(ROOT, "api"))

    import types

    import httpx

    from app.adapters import payment_factory
    from app.adapters.impl import kakaopay as kakaopay_mod
    from app.adapters.payment import PaymentError
    from app.services import billing as B
    from app.services import billing_crypto as BC

    # **매번 새 키.** 운영 키를 쓰지 않는다.
    key = Fernet.generate_key().decode()
    fixed = datetime.fromtimestamp(NOW_MS / 1000, tz=timezone.utc)
    B._now = lambda _n=None: fixed  # noqa: SLF001 — 시각 고정
    BC.get_settings = lambda: types.SimpleNamespace(billing_key_encryption_key=key)
    B.get_settings = lambda: types.SimpleNamespace(
        billing_redirect_base=REDIRECT_BASE, billing_key_encryption_key=key,
    )

    real_httpx_client = httpx.Client

    class Table:
        def __init__(self, rows, calls, table, state):
            self._rows, self._calls, self._t, self._state = rows, calls, table, state
            self._mode = "sel"

        def select(self, *a, **k):
            self._mode = "sel"
            return self

        def insert(self, row, **k):
            self._mode = "ins"
            self._calls.append({"op": "insert", "table": self._t, "row": row})
            return self

        def update(self, row, **k):
            self._mode = "upd"
            self._calls.append({"op": "update", "table": self._t, "row": row})
            return self

        def eq(self, *a, **k):
            return self

        def in_(self, *a, **k):
            return self

        def lte(self, *a, **k):
            return self

        def is_(self, *a, **k):
            return self

        def limit(self, *a, **k):
            return self

        def execute(self):
            if self._mode == "ins" and (self._state.get("insertFails") or {}).get(self._t):
                raise RuntimeError("insert 실패")
            if self._mode == "upd" and (self._state.get("updateFails") or {}).get(self._t):
                raise RuntimeError("update 실패")
            return types.SimpleNamespace(data=self._rows if self._mode == "sel" else None)

    class FakeClient:
        def __init__(self, state, calls):
            self.state, self.calls = state, calls

        def table(self, name):
            rows = (self.state.get("subscriptions") if name == "subscriptions"
                    else self.state.get("history") if name == "payment_history" else [])
            return Table(rows or [], self.calls, name, self.state)

    def run_scenario(sc: dict) -> dict:
        calls: list = []
        state = json.loads(json.dumps(sc["state"]))
        for row in state.get("subscriptions") or []:
            if row.get("__sid"):
                row["billing_key"] = Fernet(key.encode()).encrypt(
                    row["__sid"].encode()).decode()
            if row.get("__badKey"):
                row["billing_key"] = "gAAAAABm-깨진-암호문"

        client = FakeClient(state, calls)
        B.get_supabase_client = lambda _c=client: _c
        behavior = sc.get("kakaopay") or {}

        def handler(request: httpx.Request) -> httpx.Response:
            path = str(request.url).replace(kakaopay_mod._BASE_URL, "")
            calls.append({"op": "kakaopay", "path": path,
                          "body": json.loads(request.content.decode() or "{}")})
            b = behavior.get(path) or {}
            if b.get("throw"):
                raise httpx.ConnectError("network down")
            return httpx.Response(b.get("status", 200), json=b.get("json") or {})

        def fake_client(**kw):
            return real_httpx_client(transport=httpx.MockTransport(handler), **kw)

        kakaopay_mod.httpx = types.SimpleNamespace(
            Client=fake_client, HTTPError=httpx.HTTPError)
        provider = kakaopay_mod.KakaoPayImpl(secret_key="sk_test", cid="TCSUBSCRIP")
        B.get_payment_provider = lambda _p=provider: _p
        payment_factory.get_payment_provider = lambda _p=provider: _p

        result, error = None, None
        try:
            fn = sc["fn"]
            if fn == "charge":
                r = B.charge_due_subscriptions()
                result = {"charged": r.charged, "failed": r.failed,
                          "user_ids_charged": r.user_ids_charged,
                          "user_ids_failed": r.user_ids_failed}
            elif fn == "sweep":
                r = B.sweep_past_due()
                result = {"canceled": r.canceled, "user_ids": r.user_ids}
            elif fn == "start":
                r = B.start_subscription(sc["userId"])
                result = {"tid": r.tid, "redirect_url": r.redirect_url}
            elif fn == "approve":
                B.approve_subscription(sc["userId"], sc["pgToken"])
                result = "ok"
            elif fn == "cancel":
                B.cancel_subscription(sc["userId"])
                result = "ok"
        except B.SubscriptionNotPendingError:
            error = "SubscriptionNotPendingError"
        except PaymentError:
            error = "PaymentError"
        except Exception as exc:  # noqa: BLE001
            error = type(exc).__name__
        return {"name": sc["name"], "result": result, "error": error, "calls": calls}

    cases = scenarios()
    py_rows = [run_scenario(sc) for sc in cases]
    py_dates = [B._add_one_month(B._parse_ts(s)).isoformat() for s in DATE_CASES]

    with tempfile.TemporaryDirectory() as tmp:
        cf, rf = os.path.join(tmp, "c.json"), os.path.join(tmp, "r.ts")
        of = os.path.join(tmp, "out.json")
        with open(cf, "w", encoding="utf-8") as f:
            json.dump({"key": key, "nowMs": NOW_MS, "redirectBase": REDIRECT_BASE,
                       "dateCases": DATE_CASES, "scenarios": cases,
                       "negative": negative}, f)
        with open(rf, "w", encoding="utf-8") as f:
            f.write(RUNNER_TS)
        proc = subprocess.run(
            ["deno", "run", "--config", DENO_CONFIG, "--allow-all", rf, cf, of],
            capture_output=True, text=True, timeout=900)
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

    def norm(calls):
        out = []
        for c in calls:
            c = dict(c)
            row = c.get("row")
            if isinstance(row, dict) and "billing_key" in row:
                # IV 가 난수라 암호문은 매번 다르다 — 존재 여부만 본다.
                c["row"] = {**row, "billing_key": "<암호문>"}
            out.append(c)
        return out

    print("  --- 날짜 (말일 clamp · 마이크로초 표기) ---")
    for s, a, b in zip(DATE_CASES, py_dates, ts["dates"]):
        cmp(f"add_one_month({s})", a, b)
        print(f"    {s:<36} → {a:<36}{'일치' if a == b else '**불일치**'}")

    print("  --- 시나리오 (응답 + DB write 순서까지) ---")
    for a, b in zip(py_rows, ts["scenarios"]):
        before = len(fails)
        cmp(f"[{a['name']}] result", a["result"], b["result"])
        cmp(f"[{a['name']}] error", a["error"], b["error"])
        cmp(f"[{a['name']}] 호출", norm(a["calls"]), norm(b["calls"]))
        ok = len(fails) == before
        print(f"    {a['name']:<36} 호출 {len(a['calls'])}건  "
              f"{'일치' if ok else '**불일치**'}")

    for f in fails[:6]:
        print(f"  **{f}**")
    print(f"  비교 {checks}건, 불일치 {len(fails)}건")
    if negative:
        print("음성 대조: " + ("검사기 정상 (변조 검출)" if fails else "**검사기가 못 잡는다**"))
        sys.exit(0 if fails else 1)
    print("FAIL 0" if not fails else f"FAIL {len(fails)}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
