#!/usr/bin/env python3
"""Phase 0 / S1 — WASM 추출 결과를 파이썬 파서 기준선과 대조해 채점한다.

`scripts/spike_hwp_extract.ts` 가 떨군 산출물을 읽어 `spike_baseline.json` 의
기준선 텍스트와 비교한다. 추출과 채점을 분리해 둔 이유는 추출기를 고칠 때마다
같은 잣대로 재현 가능한 수치를 얻기 위해서다.

판정 기준(플랜): 유사도 >= 0.95 면 S1 텍스트 품질 PASS.

유사도는 **공백을 전부 제거하고** SequenceMatcher 로 잰다. HWP 는 문단·셀 경계의
공백 처리가 파서마다 달라, 공백을 세면 실제 내용 손실이 아닌 차이가 점수를 좌우한다.

사용:
    python3 api/scripts/spike_hwp_similarity.py <추출디렉토리> [--baseline-key KEY]

예:
    python3 api/scripts/spike_hwp_similarity.py /tmp/hwpout \
        --baseline-key "assets/public/law_sample1.hwp"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = REPO_ROOT / "api" / "scripts" / "spike_baseline.json"
PASS_THRESHOLD = 0.95

WS = re.compile(r"\s+")
TAG = re.compile(r"<[^>]+>")


def strip_ws(s: str) -> str:
    return WS.sub("", s)


def html_to_text(html: str) -> str:
    """대조군용 거친 변환. toHtml 이 표 셀을 누락한다는 사실을 재확인하는 용도라 이 정도면 된다."""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = TAG.sub("\n", text)
    return (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )


def similarity(baseline: str, candidate: str) -> float:
    return SequenceMatcher(None, strip_ws(baseline), strip_ws(candidate)).ratio()


def missing_samples(baseline: str, candidate: str, limit: int = 12) -> list[str]:
    """기준선에는 있고 후보에는 없는 토큰 — 격차의 정체를 눈으로 확인하기 위한 것."""
    cand = strip_ws(candidate)
    out: list[str] = []
    for tok in dict.fromkeys(t for t in re.split(r"\s+", baseline) if len(t) >= 2):
        if strip_ws(tok) and strip_ws(tok) not in cand:
            out.append(tok)
            if len(out) >= limit:
                break
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir", help="spike_hwp_extract.ts 산출 디렉토리")
    ap.add_argument("--baseline-key", default="assets/public/law_sample1.hwp")
    ap.add_argument("--baseline", default=str(BASELINE_PATH))
    args = ap.parse_args()

    baseline_all = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    if args.baseline_key not in baseline_all:
        print(f"기준선에 키 없음: {args.baseline_key}", file=sys.stderr)
        print(f"가능한 키: {list(baseline_all)}", file=sys.stderr)
        return 2
    baseline = baseline_all[args.baseline_key]["text"]

    outdir = Path(args.outdir)
    candidates: dict[str, str] = {}

    extracted = outdir / "extracted.txt"
    if extracted.exists():
        candidates["extracted(toJson)"] = extracted.read_text(encoding="utf-8")

    html = outdir / "toHtml.html"
    if html.exists():
        candidates["toHtml"] = html_to_text(html.read_text(encoding="utf-8"))

    if not candidates:
        print(f"채점할 산출물이 없다: {outdir}", file=sys.stderr)
        return 2

    print(f"기준선: {args.baseline_key} ({len(baseline)}자, 공백제거 {len(strip_ws(baseline))}자)\n")
    print(f"{'후보':<20} {'chars':>8} {'유사도':>8}  판정")
    print("-" * 52)

    best = 0.0
    for name, text in candidates.items():
        ratio = similarity(baseline, text)
        best = max(best, ratio)
        verdict = "PASS" if ratio >= PASS_THRESHOLD else "FAIL"
        print(f"{name:<20} {len(text):>8} {ratio:>8.4f}  {verdict}")

    for name, text in candidates.items():
        miss = missing_samples(baseline, text)
        if miss:
            print(f"\n{name} 누락 토큰(기준선 대비): {', '.join(miss)}")

    print(f"\n최고 유사도 {best:.4f} / 기준 {PASS_THRESHOLD} → {'PASS' if best >= PASS_THRESHOLD else 'FAIL'}")
    return 0 if best >= PASS_THRESHOLD else 1


if __name__ == "__main__":
    raise SystemExit(main())
