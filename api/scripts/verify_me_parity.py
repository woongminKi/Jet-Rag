"""`/me/*` 를 Python 핸들러와 응답 대조.

## rotate 는 부르지 않는다
`POST /me/email-ingest/rotate` 는 **토큰을 재발급해 구 주소를 즉시 무효화**한다. 대조하겠다고
실제로 부르면 사용자의 이메일 주소가 바뀐다. 그래서 이 엔드포인트만은 실행 대신
**요청 URL + 본문을 대조**한다 — 토큰·시각을 주입할 수 있게 만들어 뒀다(난수를 그대로
쓰면 애초에 비교가 불가능하다).

`GET /me/email-ingest` 는 주소가 이미 있으면 읽기만 한다(실측: `email_ingest_addresses` 1행).
`owner_email` 갱신을 피하려고 이메일을 `None` 으로 넘긴다 — 원본도 그때는 안 쓴다.

## 인증
원본은 라우터 레벨에서 익명을 막는다(실측 401 `{"detail":"로그인이 필요합니다."}`).
in-process 대조는 그 계층을 안 거치므로, 게이트 자체는 **배포 후 HTTP** 로 따로 확인한다.

사용:
    api/.venv/bin/python api/scripts/verify_me_parity.py
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
SHARED_DIR = os.path.join(ROOT, "supabase", "functions", "_shared")
DENO_CONFIG = os.path.join(ROOT, "supabase", "functions", "deno.json")

sys.path.insert(0, os.path.join(ROOT, "api"))

# rotate 의 요청을 재현하는 데 쓰는 고정 값 — 실제로 보내지 않는다.
FIXED_TOKEN = "abcdefghijklmnop"
FIXED_NOW_MS = 1789000000000  # 2026-09-05T05:46:40Z

# `pyIsoUtc` 두 분기를 **둘 다** 태운다.
# Python `isoformat()` 은 마이크로초가 0 이면 소수부를 통째로 생략한다. JS `toISOString()`
# 은 늘 `.000` 을 붙이므로, 밀리초가 0 으로 끝나는 순간(천 번에 한 번)에만 갈린다.
# 실제 시각으로 돌리면 그 분기가 사실상 안 태워지므로 고정 값으로 박는다.
PYTIME_CASES = [
    1789000000000,  # 밀리초 0 — 소수부 없음
    1789000000123,  # 밀리초 123 — `.123000`
    1789000000001,  # 밀리초 1 — `.001000` (앞 0 채움)
    0,              # epoch
    1788000000999,
]

# `build_address` 순수 대조 케이스.
ADDRESS_CASES = [
    ("abc123", "in.woong-s.com"),
    ("", "in.woong-s.com"),
    ("x" * 16, "example.test"),
    ("토큰", "한글.도메인"),
]

RUNNER_TS = f"""
import {{ createClient }} from "@supabase/supabase-js";
import {{
  buildAddress, buildRotateQuery, buildRotateRow, generateToken,
}} from "file://{ME_DIR}/email_ingest.ts";
import {{
  buildEmailIngest, buildPlan, buildSubscription,
}} from "file://{ME_DIR}/pipeline.ts";
import {{ utcTodayIso }} from "file://{ME_DIR}/quota.ts";
import {{ pyIsoUtc }} from "file://{SHARED_DIR}/pytime.ts";

const input = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const env: Record<string, string> = input.env;
const client = createClient(env.SUPABASE_URL, env.SUPABASE_SERVICE_ROLE_KEY, {{
  auth: {{ persistSession: false }},
}});
const deps = {{
  client,
  emailIngestDomain: input.domain,
  now: () => input.now_ms,
}};

const plan = await buildPlan(input.user_id, deps);
const subscription = await buildSubscription(input.user_id, deps);
const emailIngest = await buildEmailIngest(input.user_id, null, deps);

// rotate 는 **실행하지 않는다** — 요청 모양만 만든다.
const rotateRow = buildRotateRow(
  input.user_id, null, input.fixed_now_ms, input.fixed_token,
);
// **`buildRotateQuery` 를 직접 부른다.** 조립을 여기 복붙하면 그 함수를 고쳐도
// 안 잡힌다 — 실제로 `on_conflict` 를 지운 음성 대조가 0 건이었다.
const rotateQuery = buildRotateQuery(client, rotateRow).url.searchParams.toString();

const addresses = input.address_cases.map(
  ([t, d]: [string, string]) => buildAddress(t, d),
);
const token = generateToken();

console.log(JSON.stringify({{
  plan, subscription, emailIngest,
  rotate_row: rotateRow, rotate_query: rotateQuery,
  addresses,
  today_iso: utcTodayIso(input.now_ms),
  pytime: input.pytime_cases.map((ms: number) => pyIsoUtc(ms)),
  token_sample: token,
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
        raise SystemExit(f"deno 실행 실패:\n{proc.stderr[:2500]}")
    return json.loads(proc.stdout)


def main() -> None:
    import time
    from datetime import datetime, timezone

    from dotenv import load_dotenv

    load_dotenv(os.path.join(ROOT, ".env"))

    from app.auth.dependencies import CurrentUser
    from app.config import get_settings
    from app.routers.me import me_email_ingest, me_plan, me_subscription
    from app.services import email_ingest as EI

    settings = get_settings()
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
    # 이메일을 넘기지 않는다 — 넘기면 `owner_email` 이 갱신되는 쓰기가 일어난다.
    cu = CurrentUser(user_id=user_id, email=None, is_authenticated=True)

    py = {
        "plan": me_plan(current_user=cu).model_dump(),
        "subscription": me_subscription(current_user=cu).model_dump(),
        "emailIngest": me_email_ingest(current_user=cu, settings=settings).model_dump(),
    }

    env = {k: os.environ[k] for k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")}
    ts = run_deno({
        "user_id": user_id,
        "now_ms": now_ms,
        "fixed_now_ms": FIXED_NOW_MS,
        "fixed_token": FIXED_TOKEN,
        "domain": settings.email_ingest_domain,
        "env": env,
        "address_cases": [list(c) for c in ADDRESS_CASES],
        "pytime_cases": PYTIME_CASES,
    })

    fails = 0

    print(f"대상 사용자: {user_id} / 도메인: {settings.email_ingest_domain}")
    print()
    print("=== 응답 대조 ===")
    for key in ("plan", "subscription", "emailIngest"):
        if py[key] != ts[key]:
            fails += 1
            print(f"  MISMATCH {key}")
            print(f"      py={py[key]}")
            print(f"      ts={ts[key]}")
        else:
            print(f"  {key:<14} OK   {json.dumps(py[key], ensure_ascii=False)[:90]}")

    print()
    print("=== build_address (순수) ===")
    for (token, domain), tv in zip(ADDRESS_CASES, ts["addresses"]):
        pv = EI.build_address(token, domain)
        if pv != tv:
            fails += 1
            print(f"  MISMATCH ({token!r}, {domain!r}): py={pv!r} ts={tv!r}")
    print(f"  {len(ADDRESS_CASES)}건 대조")

    print()
    print("=== isoformat() 형식 (마이크로초 0 분기 포함) ===")
    from datetime import timedelta

    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    zero_frac = 0
    for ms, tv in zip(PYTIME_CASES, ts["pytime"]):
        # **`fromtimestamp(ms/1000)` 을 쓰지 않는다** — 부동소수라 마이크로초가 1 밀린다.
        # timedelta 는 정수 연산이라 검증기와 구현이 다른 메커니즘이 된다.
        pv = (epoch + timedelta(milliseconds=ms)).isoformat()
        if "." not in pv:
            zero_frac += 1
        if pv != tv:
            fails += 1
            print(f"  MISMATCH ms={ms}: py={pv!r} ts={tv!r}")
        else:
            print(f"  OK   ms={ms:<14} {pv}")
    if zero_frac == 0 or zero_frac == len(PYTIME_CASES):
        fails += 1
        print(f"  케이스 무효 — 소수부 생략 분기가 한쪽만 태워졌다 (생략 {zero_frac}건)")

    print()
    print("=== UTC 오늘 날짜 (usage_counters 키) ===")
    py_today = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).date().isoformat()
    if py_today != ts["today_iso"]:
        fails += 1
        print(f"  MISMATCH py={py_today} ts={ts['today_iso']}")
    else:
        print(f"  OK   {py_today}")

    print()
    print("=== 토큰 생성 (알파벳·길이만 — 난수라 값 대조 불가) ===")
    tok = ts["token_sample"]
    if len(tok) != EI._TOKEN_LEN:
        fails += 1
        print(f"  MISMATCH 길이 py={EI._TOKEN_LEN} ts={len(tok)}")
    bad = [ch for ch in tok if ch not in EI._TOKEN_ALPHABET]
    if bad:
        fails += 1
        print(f"  MISMATCH 알파벳 밖 문자: {bad}")
    if not bad and len(tok) == EI._TOKEN_LEN:
        print(f"  OK   길이 {len(tok)} · 알파벳 {len(EI._TOKEN_ALPHABET)}종 안")

    print()
    print("=== rotate — 실행하지 않고 요청만 대조 ===")
    from postgrest import SyncPostgrestClient

    py_row = {
        "user_id": user_id,
        "token": FIXED_TOKEN,
        "owner_email": None,
        "rotated_at": datetime.fromtimestamp(
            FIXED_NOW_MS / 1000, tz=timezone.utc
        ).isoformat(),
    }
    if py_row != ts["rotate_row"]:
        fails += 1
        print("  MISMATCH row")
        print(f"      py={py_row}")
        print(f"      ts={ts['rotate_row']}")
    else:
        print(f"  row  OK   {json.dumps(py_row, ensure_ascii=False)}")

    pg = SyncPostgrestClient("https://example.supabase.co/rest/v1", headers={})
    q = pg.table("email_ingest_addresses").upsert(py_row, on_conflict="user_id")
    py_query = str(q.request.params)
    if py_query != ts["rotate_query"]:
        fails += 1
        print(f"  MISMATCH query\n      py={py_query}\n      ts={ts['rotate_query']}")
    else:
        print(f"  query OK   {py_query}")

    print()
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
