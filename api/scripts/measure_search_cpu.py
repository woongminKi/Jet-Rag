"""검색 경로의 **CPU 몫**을 잰다 — Phase 2 진입 판정용.

## 왜 이걸 먼저 재나
Edge Functions 의 제한은 **요청당 CPU 2초**다. wall clock 이 아니다.
`search_metrics_log` 에 남은 값(중앙 182ms / p90 818ms / p99 3,089ms / 최대 42s)은 전부
**wall** 이라 그대로 읽으면 안 된다 — 임베딩 API·DB RPC 왕복이 섞여 있어 42초짜리는
외부 지연일 가능성이 높다. 지금 계기로는 분해가 안 되므로 여기서 분해한다.

`time.process_time()` 은 프로세스가 실제로 CPU 를 쓴 시간만 센다(I/O 대기 제외).
따라서 `cpu / wall` 이 곧 "Edge 예산에 부딪히는 비율" 의 근사다.

## 한계 — 그대로 Deno 수치는 아니다
Python 의 CPU 시간이 Deno 의 CPU 시간과 같지는 않다. 다만 **어느 규모인지**는 이걸로 갈린다.
CPU 몫이 wall 의 5% 라면 2초 예산은 문제가 아니고, 80% 라면 포팅 설계를 바꿔야 한다.
정확한 Deno 수치는 실제 포팅 후 `spike` 하네스로 다시 잰다.

사용:
    api/.venv/bin/python api/scripts/measure_search_cpu.py [--profile]
"""

from __future__ import annotations

import cProfile
import os
import pstats
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "api"))


def load_env() -> None:
    """`.env` 를 읽어 환경에 올린다 — 실제 DB·임베딩 API 를 타야 의미 있는 수치가 나온다."""
    path = os.path.join(ROOT, ".env")
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


load_env()

# 운영에서 문서를 실제로 가진 사용자. default_user_id 로 돌리면 결과가 0건이라
# 후처리(융합·스니펫) CPU 가 거의 안 잡힌다.
OWNER = "2af8fca5-03ab-421b-94b8-53d4fe9d8046"

QUERIES = [
    ("짧은 질의", "세무"),
    ("자연어 질의", "데이터센터 지원 사업의 신청 자격"),
    ("긴 자연어 질의", "이 문서에서 정기결제 해지와 환불 절차가 어떻게 되는지 알려줘"),
    ("문서유형어 혼합", "삼성 사업보고서 매출"),
    ("영문 질의", "vision need score threshold"),
]


def run_once(q: str, limit: int = 10):
    from fastapi import Response

    from app.auth.dependencies import CurrentUser
    from app.routers.search import search

    return search(
        q=q,
        limit=limit,
        offset=0,
        tags=None,
        doc_type=None,
        from_date=None,
        to_date=None,
        doc_id=None,
        mode="hybrid",
        response=Response(),
        current_user=CurrentUser(user_id=OWNER, email=None, is_authenticated=True),
    )


def measure(label: str, q: str) -> dict:
    w0, c0 = time.monotonic(), time.process_time()
    result = run_once(q)
    wall = (time.monotonic() - w0) * 1000
    cpu = (time.process_time() - c0) * 1000
    # 응답 필드는 `items` 다. 처음에 `results` 로 읽어 전부 0건으로 나왔고, 그대로였으면
    # "후처리 CPU 가 거의 없다" 는 잘못된 결론을 낼 뻔했다.
    d = result.model_dump()
    qp = d.get("query_parsed") or {}
    return {
        "label": label,
        "q": q,
        "wall": wall,
        "cpu": cpu,
        "hits": len(d.get("items") or []),
        "dense": qp.get("dense_hits"),
        "sparse": qp.get("sparse_hits"),
        "fused": qp.get("fused"),
    }


def main() -> None:
    profile = "--profile" in sys.argv

    print("워밍업 (임포트·연결 수립 비용 제외)...")
    try:
        run_once("워밍업")
    except Exception as e:  # noqa: BLE001
        print(f"  워밍업 실패: {type(e).__name__}: {e}")
        print("  .env 의 SUPABASE / HF / DEEPINFRA 설정을 확인하세요.")
        raise SystemExit(1)

    rows = []
    print()
    print(f"{'질의':<20}{'결과':>5}{'dense':>7}{'sparse':>7}{'fused':>7}{'wall(ms)':>11}{'CPU(ms)':>10}{'CPU 비중':>10}")
    print("-" * 82)
    for label, q in QUERIES:
        # 회차 편차가 있으므로 2회 재고 빠른 쪽을 쓴다(느린 쪽은 외부 API 지연이 섞인다).
        a = measure(label, q)
        b = measure(label, q)
        r = a if a["wall"] <= b["wall"] else b
        rows.append(r)
        share = r["cpu"] / r["wall"] * 100 if r["wall"] else 0
        print(
            f"{label:<20}{r['hits']:>5}{r['dense'] or 0:>7}{r['sparse'] or 0:>7}{r['fused'] or 0:>7}"
            f"{r['wall']:>11.1f}{r['cpu']:>10.1f}{share:>9.1f}%"
        )

    max_cpu = max(r["cpu"] for r in rows)
    max_wall = max(r["wall"] for r in rows)
    print()
    print(f"CPU 최대 {max_cpu:.1f}ms / wall 최대 {max_wall:.1f}ms")
    print(f"Edge CPU 예산 2,000ms 대비 여유: 약 {2000 / max_cpu:.0f}배" if max_cpu else "")
    print()
    print("※ Python CPU 시간이다. Deno 수치와 같지 않지만 규모 판정에는 쓸 수 있다.")
    print("※ wall 의 나머지는 I/O 대기(임베딩 API · DB RPC)로 Edge CPU 예산에 안 잡힌다.")

    if profile:
        print()
        print("=== CPU 상위 함수 (누적 시간) ===")
        pr = cProfile.Profile()
        pr.enable()
        for _, q in QUERIES:
            run_once(q)
        pr.disable()
        st = pstats.Stats(pr)
        st.sort_stats("cumulative")
        st.print_stats(18)


if __name__ == "__main__":
    main()
