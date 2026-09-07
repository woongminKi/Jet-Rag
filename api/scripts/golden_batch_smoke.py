"""W4 Day 5 — golden 20건 batch 라이브 smoke (W3 baseline 비교).
W21 Day 1 — mode 인자 + threshold 검증 + exit code (회귀 보호 강화).

목적
- W3 Day 5 마감 5/5 top-1 hit baseline → W4 후 회귀 측정
- top-3 hit 율 + p95 latency + cache_hit 효과 측정
- W21+: mode=all 시 hybrid/dense/sparse ablation 비교 — KPI '하이브리드 +5pp 우세'
- exit code 1: --require-top1-min 미달 시 (CI 통합 가능)

사용
    cd api && uv run python scripts/golden_batch_smoke.py
        # → stdout markdown (mode=hybrid)
    cd api && uv run python scripts/golden_batch_smoke.py --mode all --output ../work-log/...md
        # → 3 mode ablation
    cd api && uv run python scripts/golden_batch_smoke.py --require-top1-min 0.7
        # → top-1 hit 율 < 70% 시 exit 1
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
import urllib.parse
import urllib.request
from pathlib import Path

# 두 이름을 다 받는다. `monitor_search_slo.py` 는 `JET_RAG_API_BASE` 를 쓰는데 여기만
# `JETRAG_API_BASE_URL` 이라, 한쪽 이름으로 넘기면 조용히 localhost 로 떨어져 **20건
# 전부 err** 이 된다(실측으로 한 번 헛돌았다). 이름 하나 때문에 측정을 못 하면 안 된다.
_BASE = (
    os.environ.get("JETRAG_API_BASE_URL")
    or os.environ.get("JET_RAG_API_BASE")
    or "http://localhost:8000"
).rstrip("/")

# 2026-09-04 — Cloudflare 프록시 전환(Phase 1 Task 1.7) 이후 `Python-urllib/*` UA 가
# 403(Cloudflare error 1010)으로 차단된다. 실측: urllib 만 막히고 requests·httpx·Go·
# node-fetch·Deno 는 통과한다. 도메인이 오렌지 구름으로 바뀌며 Cloudflare 기본 보호가
# 걸린 결과다. 클라이언트 이름을 명시해 우회한다 — 어차피 자기 트래픽을 밝히는 게 맞다.
_USER_AGENT = "Jet-Rag-Ops/1.0 (+https://jetrag.woong-s.com)"


def _open(url: str, timeout: int):
    """UA 를 붙여서 연다. 맨 urlopen 을 쓰면 Cloudflare 가 막는다."""
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    return urllib.request.urlopen(req, timeout=timeout)


# ---------------------------------------------------------------------------
# 골든셋은 **CSV 에서 읽는다** — 예전에는 여기 v0.1 20 건이 인라인으로 박혀 있었다.
#
# 2026-09-07 실측: 그 20 건이 기대하던 문서 **5 개가 하나도 존재하지 않았다**(문서
# 재업로드로 id 변경, 쏘나타 문서는 아예 없음). 그래서 `top-1 hit 0/20` 이 나오는데
# 그건 검색 품질이 아니라 **기대값이 죽은 것**이다 — 이 상태로는 회귀를 못 잡는다.
#
# `evals/golden_v2.csv` 는 132 행 전부 살아 있다(실측). 그걸 기본으로 쓴다.
# 다른 세트를 보려면 `--goldenset evals/golden_v1.csv`.
#
# **죽은 문서를 참조하는 행은 조용히 버리지 않고 센다.** golden_v1 은 157 행 중 40 행이
# 사라진 문서 4 개를 가리키는데, 러너가 조용히 걸러 왔다 — 그래서 아무도 몰랐다.
# ---------------------------------------------------------------------------
_DEFAULT_GOLDEN = Path(__file__).resolve().parents[2] / "evals" / "golden_v2.csv"


def _load_golden(path: Path, live_doc_ids: set[str] | None) -> tuple[list[dict], list[str]]:
    """CSV → 이 스크립트가 쓰는 dict. 죽은 문서 행은 빼고 **목록으로 돌려준다.**"""
    rows: list[dict] = []
    dropped: list[str] = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            doc_id = (r.get("doc_id") or "").strip()
            rid = (r.get("id") or "").strip()
            if not doc_id or not (r.get("query") or "").strip():
                continue
            if live_doc_ids is not None and doc_id not in live_doc_ids:
                dropped.append(f"{rid}({doc_id[:8]})")
                continue
            rows.append({
                "id": rid,
                "type": (r.get("query_type") or "-").strip(),
                "q": r["query"].strip(),
                # 응답의 doc_id 는 전체 UUID 라 앞 8 자로 맞춘다(원래 방식과 동일).
                "expect": doc_id[:8],
                "filters": {},
            })
    return rows, dropped


def _live_doc_ids() -> set[str] | None:
    """현재 DB 의 문서 id. 자격증명이 없으면 `None` — 그때는 거르지 않는다."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        return None
    try:
        from supabase import create_client

        client = create_client(url, key)
        return {
            r["id"] for r in client.table("documents").select("id").execute().data
        }
    except Exception as exc:  # noqa: BLE001 — 못 읽으면 거르지 않고 그대로 돈다
        print(f"[warn] 현재 문서 목록을 못 읽었다 ({exc}) — 죽은 행을 거르지 않는다",
              file=sys.stderr)
        return None


GOLDEN: list[dict] = []  # main() 에서 채운다.


def _fetch_search(q: str, filters: dict, limit: int = 10, mode: str = "hybrid") -> dict:
    params: dict = {"q": q, "limit": str(limit)}
    params.update(filters)
    if mode != "hybrid":
        params["mode"] = mode
    qs = urllib.parse.urlencode(params)
    url = f"{_BASE}/search?{qs}"
    with _open(url, 30) as resp:
        return json.load(resp)


def _is_match(doc_id: str, short: str) -> bool:
    """doc_id full UUID 의 첫 8자가 short 와 동일한지."""
    return doc_id.lower().startswith(short.lower())


def _run_mode(mode: str) -> list[dict]:
    """mode 별 golden batch 1회 실행 — results 리스트 반환."""
    results: list[dict] = []
    for g in GOLDEN:
        try:
            r = _fetch_search(g["q"], g["filters"], limit=10, mode=mode)
        except Exception as exc:  # noqa: BLE001
            print(f"[ERROR] {g['id']} mode={mode} {exc}", file=sys.stderr)
            results.append({**g, "mode": mode, "error": str(exc)})
            continue
        items = r.get("items", [])
        top_doc_ids = [it.get("doc_id", "") for it in items[:3]]
        top1 = _is_match(top_doc_ids[0], g["expect"]) if top_doc_ids else False
        top3 = any(_is_match(d, g["expect"]) for d in top_doc_ids)
        results.append({
            "id": g["id"],
            "type": g["type"],
            "q": g["q"],
            "expect": g["expect"],
            "mode": mode,
            "top1": top1,
            "top3": top3,
            "took_ms": r.get("took_ms"),
            "total": r.get("total"),
            "query_parsed": r.get("query_parsed"),
            "top_doc_ids": top_doc_ids,
        })
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", "-o", help="markdown 출력 경로")
    parser.add_argument(
        "--mode",
        choices=["hybrid", "dense", "sparse", "all"],
        default="hybrid",
        help="검색 mode (all 시 hybrid/dense/sparse 3 mode ablation)",
    )
    parser.add_argument(
        "--goldenset",
        default=str(_DEFAULT_GOLDEN),
        help="골든셋 CSV 경로 (기본: evals/golden_v2.csv)",
    )
    parser.add_argument(
        "--require-top1-min",
        type=float,
        default=None,
        help="top-1 hit 비율 최소 임계값 (0.0~1.0). 미달 시 exit 1 (CI gate).",
    )
    args = parser.parse_args()

    global GOLDEN
    GOLDEN, dropped = _load_golden(Path(args.goldenset), _live_doc_ids())
    print(f"골든셋 {Path(args.goldenset).name} — {len(GOLDEN)}행", file=sys.stderr)
    if dropped:
        # **조용히 버리지 않는다.** 기대 문서가 사라진 행은 회귀를 못 잡는다.
        print(
            f"[warn] 사라진 문서를 가리켜 뺀 행 {len(dropped)}건: "
            f"{', '.join(dropped[:8])}{' …' if len(dropped) > 8 else ''}",
            file=sys.stderr,
        )
    if not GOLDEN:
        print("[ERROR] 돌릴 행이 없다 — 골든셋이 통째로 낡았다.", file=sys.stderr)
        return 1

    if args.mode == "all":
        modes = ["hybrid", "dense", "sparse"]
        results: list[dict] = []
        for m in modes:
            results.extend(_run_mode(m))
    else:
        results = _run_mode(args.mode)

    # mode 별 집계 — args.mode='all' 시 3 mode, 그 외 1 mode
    by_mode: dict[str, list[dict]] = {}
    for r in results:
        by_mode.setdefault(r.get("mode", args.mode), []).append(r)

    lines: list[str] = []
    lines.append(f"# golden {len(GOLDEN)}건 batch — 라이브 smoke (mode={args.mode})")
    lines.append("")

    # mode 별 요약 — ablation 비교
    if args.mode == "all":
        lines.append("## mode 별 ablation 비교")
        lines.append("")
        lines.append("| mode | top-1 | top-3 | avg ms |")
        lines.append("|---|---:|---:|---:|")
        for m in ("hybrid", "dense", "sparse"):
            ms = [r for r in by_mode.get(m, []) if "error" not in r]
            if not ms:
                continue
            t1 = sum(1 for r in ms if r["top1"])
            t3 = sum(1 for r in ms if r["top3"])
            avg = statistics.mean(r["took_ms"] for r in ms if r["took_ms"])
            lines.append(f"| {m} | {t1}/{len(ms)} ({t1/len(ms)*100:.0f}%) | {t3}/{len(ms)} | {avg:.0f} |")
        lines.append("")

    # 종합 (단일 mode 또는 mode='all' 종합)
    successful = [r for r in results if "error" not in r]
    top1_count = sum(1 for r in successful if r["top1"])
    top3_count = sum(1 for r in successful if r["top3"])
    took_ms_list = [r["took_ms"] for r in successful if r["took_ms"]]

    lines.append("## 종합")
    lines.append("")
    lines.append(f"- 총 {len(results)} 건 — 성공 {len(successful)} / 에러 {len(results) - len(successful)}")
    if successful:
        top1_pct = top1_count / len(successful) * 100
        top3_pct = top3_count / len(successful) * 100
        lines.append(f"- top-1 hit: **{top1_count}/{len(successful)}** ({top1_pct:.1f}%)")
        lines.append(f"- top-3 hit: **{top3_count}/{len(successful)}** ({top3_pct:.1f}%)")
    if took_ms_list:
        lines.append(
            f"- latency: avg {statistics.mean(took_ms_list):.0f}ms · "
            f"p50 {statistics.median(took_ms_list):.0f}ms · "
            f"p95 {sorted(took_ms_list)[int(len(took_ms_list) * 0.95)]:.0f}ms · "
            f"max {max(took_ms_list):.0f}ms"
        )
    lines.append("")

    by_type: dict[str, list[dict]] = {}
    for r in successful:
        by_type.setdefault(r["type"], []).append(r)
    lines.append("## 카테고리별 (종합)")
    lines.append("")
    lines.append("| type | top-1 | top-3 | avg ms |")
    lines.append("|---|---:|---:|---:|")
    for t, rs in by_type.items():
        t1 = sum(1 for r in rs if r["top1"])
        t3 = sum(1 for r in rs if r["top3"])
        avg = statistics.mean(r["took_ms"] for r in rs if r["took_ms"])
        lines.append(f"| {t} | {t1}/{len(rs)} | {t3}/{len(rs)} | {avg:.0f} |")
    lines.append("")

    lines.append("## 상세")
    lines.append("")
    lines.append("| mode | id | type | query | expected | top1 | top3 | took_ms | total | top doc |")
    lines.append("|---|---|---|---|---|:---:|:---:|---:|---:|---|")
    for r in results:
        m = r.get("mode", args.mode)
        if "error" in r:
            lines.append(f"| {m} | {r['id']} | {r['type']} | `{r['q']}` | {r['expect']} | ⚠️ | ⚠️ | err | - | - |")
            continue
        t1 = "✓" if r["top1"] else "✗"
        t3 = "✓" if r["top3"] else "✗"
        top_short = r["top_doc_ids"][0][:8] if r["top_doc_ids"] else "(none)"
        lines.append(
            f"| {m} | {r['id']} | {r['type']} | `{r['q']}` | {r['expect']} | {t1} | {t3} | "
            f"{r['took_ms']} | {r['total']} | {top_short} |"
        )

    out = "\n".join(lines)
    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
        print(f"[OK] {args.output}", file=sys.stderr)
    else:
        print(out)

    # W21 Day 1 — threshold gate (CI 통합 가능)
    if args.require_top1_min is not None and successful:
        top1_rate = top1_count / len(successful)
        if top1_rate < args.require_top1_min:
            print(
                f"[FAIL] top-1 hit 율 {top1_rate:.2%} < 임계 {args.require_top1_min:.2%} "
                f"({top1_count}/{len(successful)})",
                file=sys.stderr,
            )
            return 1
        print(
            f"[OK] top-1 hit 율 {top1_rate:.2%} ≥ 임계 {args.require_top1_min:.2%}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
