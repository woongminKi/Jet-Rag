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
import json
import os
import statistics
import sys
import urllib.parse
import urllib.request
from pathlib import Path

_BASE = os.environ.get("JETRAG_API_BASE_URL", "http://localhost:8000").rstrip("/")

# golden v0.1 의 20건 — query / expected_doc_id (단축) / meta_filters
GOLDEN: list[dict] = [
    {"id": "G-001", "type": "자연어", "q": "체육관 휴관일이 언제예요", "expect": "b758eec4", "filters": {}},
    {"id": "G-002", "type": "자연어", "q": "체육관 이용료 정책 정리해줘", "expect": "b758eec4", "filters": {}},
    {"id": "G-003", "type": "자연어", "q": "회원카드 발급 절차 어떻게 되더라", "expect": "b758eec4", "filters": {}},
    {"id": "G-004", "type": "자연어", "q": "이사장 책임 범위 어디까지", "expect": "dd8c1fb0", "filters": {}},
    {"id": "G-005", "type": "자연어", "q": "경영본부랑 기술본부 차이가 뭐예요", "expect": "dd8c1fb0", "filters": {}},
    {"id": "G-006", "type": "자연어", "q": "민법상 변제충당 순서 어떻게 되나", "expect": "49ef8d01", "filters": {}},
    {"id": "G-007", "type": "자연어", "q": "소멸시효 지난 채무 어떻게 처리해야 해", "expect": "49ef8d01", "filters": {}},
    {"id": "G-008", "type": "자연어", "q": "이 보고서 핵심 3줄로 요약하면", "expect": "3970feab", "filters": {}},
    {"id": "G-009", "type": "자연어", "q": "내년 반도체 시장 전망 어떻게 봐", "expect": "3970feab", "filters": {}},
    {"id": "G-010", "type": "자연어", "q": "쏘나타 신규 옵션 핵심만", "expect": "6004fd65", "filters": {}},
    {"id": "G-011", "type": "키워드", "q": "휴관일", "expect": "b758eec4", "filters": {}},
    {"id": "G-012", "type": "키워드", "q": "이사장", "expect": "dd8c1fb0", "filters": {}},
    {"id": "G-013", "type": "키워드", "q": "대법원 판결", "expect": "49ef8d01", "filters": {}},
    {"id": "G-014", "type": "키워드", "q": "쏘나타", "expect": "6004fd65", "filters": {}},
    {"id": "G-015", "type": "키워드", "q": "2.2%", "expect": "3970feab", "filters": {}},
    {"id": "G-016", "type": "메타혼합", "q": "체육관 운영", "expect": "b758eec4", "filters": {"doc_type": "hwpx"}},
    {"id": "G-017", "type": "메타혼합", "q": "직제", "expect": "dd8c1fb0", "filters": {"doc_type": "hwpx"}},
    {"id": "G-018", "type": "메타혼합", "q": "변제충당", "expect": "49ef8d01", "filters": {"doc_type": "pdf"}},
    {"id": "G-019", "type": "메타혼합", "q": "AI 투자", "expect": "3970feab", "filters": {"doc_type": "pdf"}},
    # G-020 — v0.2 → v0.3 추가 정정: 4d0ea2c4 (jet_rag_day4_sample) 의 chunks 가
    # 손상 추출 ("···" dot 만) → 의미 있는 텍스트 매칭 불가. expected 를 sample-report 로
    # 변경 + query 를 회귀 안전망 의도 유지하면서 자연어로.
    {"id": "G-020", "type": "메타혼합", "q": "샘플 보고서", "expect": "3970feab", "filters": {"doc_type": "pdf"}},
    # G-021 ~ G-025 (DOCX) — 사용자 DOCX 자료 업로드 후 expect 채워서 활성. 현재 placeholder.
    # (golden 평가셋 v0.2 §3.2 참조)
]


def _fetch_search(q: str, filters: dict, limit: int = 10, mode: str = "hybrid") -> dict:
    params: dict = {"q": q, "limit": str(limit)}
    params.update(filters)
    if mode != "hybrid":
        params["mode"] = mode
    qs = urllib.parse.urlencode(params)
    url = f"{_BASE}/search?{qs}"
    with urllib.request.urlopen(url, timeout=30) as resp:
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
        "--require-top1-min",
        type=float,
        default=None,
        help="top-1 hit 비율 최소 임계값 (0.0~1.0). 미달 시 exit 1 (CI gate).",
    )
    args = parser.parse_args()

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
