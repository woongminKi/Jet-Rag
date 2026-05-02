"""W7 Day 2 — /stats.search_slo 모니터링 + 운영 가시성.

배경
- W6 Day 2 (DE-65) 후 chunks 555 → 1256 (×2.3) — HNSW 인덱스 부담 ↑.
- W4-Q-3 embedding cache 효과 (cache hit p95 159~169ms) 가 누적 자료에서도 유지되는지 추적 필요.
- search_metrics ring buffer 는 in-memory (재시작 시 reset, W3 P3 F-4) — 본 스크립트는 임시 snapshot
  + work-log 기록 패턴.

사용
    cd api && uv run python scripts/monitor_search_slo.py            # 1회 snapshot
    cd api && uv run python scripts/monitor_search_slo.py --warmup   # golden batch 로 ring buffer warming 후 snapshot
    cd api && uv run python scripts/monitor_search_slo.py --output ../work-log/...md
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

_BASE = "http://localhost:8000"


def _fetch_stats() -> dict:
    with urllib.request.urlopen(f"{_BASE}/stats", timeout=15) as resp:
        return json.load(resp)


def _warm_ring_buffer(queries: list[str], limit: int = 5) -> list[int]:
    """golden 일부 쿼리로 ring buffer 를 warm — search_slo 가 의미 있는 sample 갖도록."""
    took_ms_list: list[int] = []
    for q in queries:
        try:
            qs = urllib.parse.urlencode({"q": q, "limit": str(limit)})
            with urllib.request.urlopen(f"{_BASE}/search?{qs}", timeout=20) as resp:
                d = json.load(resp)
            took_ms_list.append(int(d.get("took_ms", 0)))
        except Exception as exc:  # noqa: BLE001
            print(f"[warmup] {q}: {exc}", file=sys.stderr)
    return took_ms_list


def _render_markdown(slo: dict, warm_took_ms: list[int] | None) -> str:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines: list[str] = []
    lines.append("# search_slo monitoring snapshot")
    lines.append("")
    lines.append(f"- 측정 시각 (UTC): `{now}`")
    lines.append(f"- API base: `{_BASE}`")
    lines.append("")
    lines.append("## search_slo (in-memory ring buffer)")
    lines.append("")
    lines.append(f"- sample count: **{slo.get('sample_count', 0)}**")
    lines.append(f"- p50: **{slo.get('p50_ms', 0)}ms**")
    lines.append(f"- p95: **{slo.get('p95_ms', 0)}ms**")
    lines.append(
        f"- avg dense_hits: {slo.get('avg_dense_hits', 0):.1f} · "
        f"sparse_hits: {slo.get('avg_sparse_hits', 0):.2f} · "
        f"fused: {slo.get('avg_fused', 0):.1f}"
    )
    lines.append(
        f"- fallback breakdown: {json.dumps(slo.get('fallback_breakdown', {}), ensure_ascii=False)}"
    )
    lines.append(
        f"- **cache hit rate**: {slo.get('cache_hit_rate', 0):.3f} "
        f"({slo.get('cache_hit_count', 0)} / {slo.get('sample_count', 0)})"
    )
    lines.append("")

    if warm_took_ms:
        lines.append("## warmup batch (cache miss + cache hit 혼합)")
        lines.append("")
        lines.append(f"- 실행 query 수: {len(warm_took_ms)}")
        lines.append(f"- avg: {statistics.mean(warm_took_ms):.0f}ms")
        lines.append(f"- p50: {statistics.median(warm_took_ms):.0f}ms")
        lines.append(
            f"- p95: {sorted(warm_took_ms)[max(0, int(len(warm_took_ms) * 0.95) - 1)]:.0f}ms"
        )
        lines.append(f"- max: {max(warm_took_ms)}ms")
        lines.append("")

    lines.append("## 평가 가이드")
    lines.append("")
    lines.append("| 지표 | 정상 (W6 cache hit) | 경고 임계 | 위험 임계 |")
    lines.append("|---|---|---|---|")
    lines.append("| p95 | < 200ms | 200~500ms | > 500ms (KPI §13.1 위협) |")
    lines.append("| cache_hit_rate | > 0.4 (반복 쿼리 환경) | 0.2~0.4 | < 0.2 (cache 효과 ↓, 자료 다양성 ↑) |")
    lines.append("| fallback_count | 0 | 1~5/500 sample | ≥ 5/500 sample (HF API 안정성 ↓) |")
    lines.append("")
    lines.append("## 알려진 한계")
    lines.append("")
    lines.append("- in-memory ring buffer (maxlen=500) — uvicorn 재시작 시 reset (W3 P3 F-4)")
    lines.append("- 누적 자료 (50+ doc) 시 HNSW 인덱스 부담 ↑ → p95 추적 필요")
    lines.append("- 본 스크립트는 단일 snapshot — 트렌드 추적은 W4-Q-16 (DB 영속화) 후")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="search_slo 모니터링 snapshot")
    parser.add_argument("--output", "-o", help="markdown 출력 경로 (기본: stdout)")
    parser.add_argument(
        "--warmup",
        action="store_true",
        help="golden 일부 쿼리로 ring buffer 를 warm 후 snapshot",
    )
    args = parser.parse_args()

    warm_took_ms = None
    if args.warmup:
        # golden v0.1 의 키워드 5건 + 자연어 3건 — sample N=8 보장
        warm_queries = [
            "휴관일", "이사장", "대법원 판결", "쏘나타", "2.2%",
            "체육관 이용료 정책 정리해줘",
            "민법상 변제충당 순서 어떻게 되나",
            "내년 반도체 시장 전망 어떻게 봐",
        ]
        # 1차 (cache miss) → 1초 대기 → 2차 (cache hit) — 캐시 효과 측정 가능
        warm_took_ms = _warm_ring_buffer(warm_queries)
        time.sleep(1)
        warm_took_ms.extend(_warm_ring_buffer(warm_queries))

    stats = _fetch_stats()
    slo = stats.get("search_slo") or {}
    out = _render_markdown(slo, warm_took_ms)

    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
        print(f"[OK] {args.output}", file=sys.stderr)
    else:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
