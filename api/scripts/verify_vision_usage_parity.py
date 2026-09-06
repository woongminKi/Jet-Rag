"""`/stats.vision_usage` 의 DB 경로가 실제 원장과 맞는지 대조.

## 왜 필요한가
`vision_metrics` 는 원래 프로세스 안 카운터로만 사용량을 냈다. `/stats` 가 Supabase Edge 로
넘어가면 isolate 가 휘발성이라 그 값이 **영구히 0** 이 된다. 게다가 vision 호출은 인제스트
경로에서 나는데 그건 아직 Railway 라, 다른 프로세스의 카운터를 읽을 방법이 없다.

지금도 사실상 죽어 있었다 — 운영 실측(2026-09-06): `/stats.vision_usage` 는 전부 0 인데
`vision_usage_log` 에는 2,090 행이 있다. 프로세스 재시작마다 리셋되는 값을 띄우고 있었다.

## 창을 오늘(KST)로 잡은 이유
프론트 카드가 `RPD_CAP = 20`(Gemini 무료 티어 **일일** 요청 한도) 대비 사용률을 그린다.
원래 의도가 "오늘 얼마나 썼나" 인데 구현이 "프로세스 시작 후" 였다 — 둘 다 RPD 와 안 맞았다.
전체 누적을 쓰면 2,090/20 = 10,450% 라 카드가 영구 빨강이 된다.

## 어떻게 재나
`vision_metrics` 를 **거치지 않고 직접** 질의해 기대값을 만들고, 모듈의 DB 경로 결과와
완전 일치로 비교한다. 모듈의 조회 함수를 재사용하면 그 함수에 버그가 있어도 양쪽이 똑같이
틀려서 항상 통과한다 — SLO 채점기에서 실제로 그 실수를 했다.

**집계 방식도 일부러 다르게 했다.** 모듈은 `count="exact"` 질의를 쓰고, 이 스크립트는
행을 **페이지로 전부 받아** 센다. 같은 방식을 쓰면 같은 함정에 같이 빠진다 — 처음엔 둘 다
행을 받아 `len()` 했다가 **PostgREST 의 1,000 행 상한**에 나란히 걸려, 2,090 행짜리 창을
양쪽 다 1,000 으로 세고 "일치" 로 통과했다.

`--since YYYY-MM-DD` 로 창을 넓힐 수 있다. **오늘 호출이 0 건이면 대조가 공허하므로**,
행이 0 이면 그 사실을 크게 찍고 종료 코드를 1 로 둔다(조용히 "통과" 로 읽히지 않게).

사용:
    api/.venv/bin/python api/scripts/verify_vision_usage_parity.py [--since 2026-05-01]
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

sys.path.insert(0, os.path.join(ROOT, "api"))

KST = timezone(timedelta(hours=9))


def expected_from_db(since_iso: str) -> dict:
    """`vision_metrics` 를 거치지 않고 직접 질의해 기대값을 만든다."""
    from supabase import create_client

    c = create_client(
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    )
    # 페이지로 전부 받는다 — 한 번에 받으면 1,000 행에서 잘린다.
    rows: list[dict] = []
    page = 0
    while True:
        chunk = (
            c.table("vision_usage_log")
            .select("called_at, success, quota_exhausted")
            .gte("called_at", since_iso)
            .order("called_at", desc=True)
            .range(page * 1000, page * 1000 + 999)
            .execute()
            .data
            or []
        )
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
        page += 1
    total = len(rows)
    success = sum(1 for r in rows if r.get("success"))
    return {
        "total_calls": total,
        "success_calls": success,
        "error_calls": total - success,
        "last_called_at": rows[0]["called_at"] if rows else None,
        "last_quota_exhausted_at": next(
            (r["called_at"] for r in rows if r.get("quota_exhausted")), None
        ),
    }


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(ROOT, ".env"))

    from app.services import vision_metrics as V

    since = None
    if "--since" in sys.argv:
        since = sys.argv[sys.argv.index("--since") + 1]

    if since:
        # 창을 넓혀 실제 데이터로 재려는 용도 — 모듈의 창 계산만 갈아끼운다.
        bound = datetime.fromisoformat(since).replace(tzinfo=KST)
        V._today_start_kst = lambda: bound  # type: ignore[assignment]
        label = f"{since} 이후 (KST)"
    else:
        bound = V._today_start_kst()
        label = f"오늘 {bound.date()} (KST)"

    os.environ["JETRAG_VISION_USAGE_SOURCE"] = "db"
    got = V.get_usage()
    want = expected_from_db(bound.isoformat())

    print(f"창: {label}")
    print(f"  독립 질의 : {want}")
    print(f"  모듈 출력 : {got}")

    fails = 0
    if got.get("source") != "db":
        fails += 1
        print(f"  MISMATCH source={got.get('source')!r} — DB 경로를 못 탔다")
    for k, v in want.items():
        if got.get(k) != v:
            fails += 1
            print(f"  MISMATCH {k}: 기대={v!r} 실제={got.get(k)!r}")

    print()
    if want["total_calls"] == 0:
        print("**표본 0 건 — 이 실행은 아무것도 검증하지 못했다.**")
        print("`--since` 로 창을 넓혀 다시 돌리거나, 인제스트가 돈 뒤에 다시 잰다.")
        print("(비어 있는 결과를 '통과' 로 읽지 않도록 실패로 처리한다.)")
        sys.exit(1)

    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
