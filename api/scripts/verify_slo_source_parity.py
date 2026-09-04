"""`/stats.search_slo` 의 DB 표본 경로가 기존 ring 경로와 같은 수를 내는지 대조.

## 왜 필요한가
`search_metrics` 는 원래 프로세스 안 ring buffer(최근 500 건)로만 SLO 를 냈다.
`/search` 가 Supabase Edge 로 넘어가면 **이 프로세스의 ring 에는 아무것도 안 쌓인다.**
그런데 `/stats` 는 아직 Railway 에서 돌아서, ring 을 읽으면 검색이 정상인데도 SLO 가
0 으로 보인다 — 매일 02:00 UTC cron(`monitor-search-slo.yml`)이 보는 값이 그것이다.
그래서 표본 출처를 `search_metrics_log` 로 옮겼다.

## 어떻게 재나
바꾼 게 "출처" 뿐이고 계산식은 그대로라, **같은 표본을 두 경로에 넣으면 같은 값이
나와야 한다.**

1. **이 스크립트가 직접** Supabase 에 질의해 최근 500 행을 가져와 ring 에 넣고 계산한다.
2. `search_metrics` 의 DB 경로로 계산한다.
3. `source` 를 뺀 나머지 전체(백분위·평균·fallback 분포·by_mode)를 **완전 일치**로 비교한다.

1) 에서 `_fetch_recent_from_db()` 를 재사용하지 않는 게 핵심이다. 처음엔 그걸 써서 ring 을
채웠는데, 그러면 그 함수에 버그가 있어도 **양쪽이 똑같이 틀려서 항상 통과한다.**
실제로 음성 대조 5 종을 걸었을 때 이 스크립트는 한 건도 못 잡았다 — 자기 자신과 비교하는
검사기였다. 질의를 독립시켜서 고쳤다.

`JET_RAG_METRICS_PERSIST_ENABLED=0` 으로 돌려 ring 에 넣는 동안 DB 에는 아무것도 안 쓴다.

## 이 스크립트가 못 재는 것
출처 라벨(`db`/`ring`/`ring_fallback`)과 기본값 규칙은 값에 안 나타나므로 여기서 못 잡는다.
그건 `tests/test_search_metrics.py` 의 `SloSourceTest` 가 가짜 클라이언트로 덮는다.

사용:
    api/.venv/bin/python api/scripts/verify_slo_source_parity.py
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

sys.path.insert(0, os.path.join(ROOT, "api"))


def fetch_rows_independently(limit: int) -> list[dict]:
    """`search_metrics` 를 거치지 않고 직접 질의한다 — 대조의 독립성을 위해서다."""
    from supabase import create_client

    client = create_client(
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    )
    resp = (
        client.table("search_metrics_log")
        .select(
            "took_ms, dense_hits, sparse_hits, fused, has_dense, "
            "fallback_reason, embed_cache_hit, mode"
        )
        .order("recorded_at", desc=True)
        .limit(limit)
        .execute()
    )
    return resp.data or []


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(ROOT, ".env"))
    # ring 에 표본을 넣는 동안 DB 로 write-through 되지 않게 막는다.
    os.environ["JET_RAG_METRICS_PERSIST_ENABLED"] = "0"
    os.environ["JET_RAG_METRICS_PERSIST_ASYNC"] = "0"

    from app.services import search_metrics as sm

    os.environ["JETRAG_SLO_SOURCE"] = "db"
    db_slo = sm.get_search_slo()
    if db_slo["source"] != "db":
        raise SystemExit(
            f"DB 경로를 못 탔다 (source={db_slo['source']!r}) — 자격증명·마이그레이션 확인 필요."
        )

    rows = fetch_rows_independently(sm._RING_MAXLEN)
    print(f"독립 질의로 가져온 표본 {len(rows)}행 (ring 최대 {sm._RING_MAXLEN})")
    if not rows:
        raise SystemExit("표본이 0 행이라 대조할 수 없다.")

    sm.reset()
    for r in rows:
        sm.record_search(
            took_ms=int(r["took_ms"]), dense_hits=int(r["dense_hits"]),
            sparse_hits=int(r["sparse_hits"]), fused=int(r["fused"]),
            has_dense=bool(r["has_dense"]), fallback_reason=r.get("fallback_reason"),
            embed_cache_hit=bool(r.get("embed_cache_hit")),
            mode=r.get("mode") or "hybrid",
        )
    os.environ["JETRAG_SLO_SOURCE"] = "ring"
    ring_slo = sm.get_search_slo()

    a = {k: v for k, v in db_slo.items() if k != "source"}
    b = {k: v for k, v in ring_slo.items() if k != "source"}

    print()
    print(f"  DB   source={db_slo['source']:<14} p50={db_slo['p50_ms']} p95={db_slo['p95_ms']} "
          f"n={db_slo['sample_count']}")
    print(f"  ring source={ring_slo['source']:<14} p50={ring_slo['p50_ms']} "
          f"p95={ring_slo['p95_ms']} n={ring_slo['sample_count']}")

    diffs = [k for k in set(a) | set(b) if a.get(k) != b.get(k)]
    print()
    if diffs:
        for k in sorted(diffs):
            print(f"  MISMATCH {k}: db={a.get(k)!r} ring={b.get(k)!r}")
        print(f"FAIL {len(diffs)}")
        sys.exit(1)

    print("  " + json.dumps({k: v for k, v in a.items() if k != "by_mode"}, ensure_ascii=False))
    print("  by_mode 표본: " + json.dumps(
        {m: v["sample_count"] for m, v in a["by_mode"].items()}, ensure_ascii=False))
    print()
    print("FAIL 0")


if __name__ == "__main__":
    main()
