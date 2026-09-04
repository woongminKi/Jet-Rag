"""S4 채점기 — Edge 의 `?kind=docx|pptx` 응답을 프로덕션 파서 기준선과 대조한다.

기준선은 `spike_ooxml_baseline.py` 가 실제 `DocxParser`/`PptxParser` 로 뜬 값이다.
텍스트 유사도만 보지 않는다 — `section_title` sticky propagate 가 검색 품질을 좌우하므로
**섹션 단위 (text, section_title) 쌍이 그대로인지**까지 본다.

사용:
    python3 api/scripts/spike_ooxml_compare.py [--dump DIR]
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BASELINE = os.path.join(HERE, "spike_ooxml_baseline.json")
FUNCTION_URL = "https://mpmtydudhojpukuuadrd.supabase.co/functions/v1/spike"

SIM_PASS = 0.95


def anon_key() -> str:
    key = os.environ.get("SUPABASE_ANON_KEY")
    if key:
        return key
    with open(os.path.join(ROOT, ".env"), encoding="utf-8") as f:
        for line in f:
            if line.startswith("NEXT_PUBLIC_SUPABASE_ANON_KEY="):
                return line.split("=", 1)[1].strip().strip("\"'")
    raise SystemExit("SUPABASE_ANON_KEY 를 찾지 못했다")


def call_edge(path: str, kind: str, key: str) -> dict:
    with open(path, "rb") as f:
        body = f.read()
    req = urllib.request.Request(
        f"{FUNCTION_URL}?kind={kind}",
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/octet-stream"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return json.loads(raw)
        except Exception:
            return {"error": f"HTTP {e.code}: {raw[:300]!r}", "result": None, "cpuMs": 0}


def similarity(a: str, b: str) -> float:
    """공백 무시 문자 유사도 — S1/S2 와 같은 방식."""
    return difflib.SequenceMatcher(None, "".join(a.split()), "".join(b.split())).ratio()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", help="Edge 원본 응답 저장 디렉토리")
    args = ap.parse_args()
    if args.dump:
        os.makedirs(args.dump, exist_ok=True)

    key = anon_key()
    with open(BASELINE, encoding="utf-8") as f:
        baseline = json.load(f)

    hdr = f"{'샘플':<34}{'섹션':>12}{'문자':>14}{'쌍일치':>9}{'제목':>7}{'유사도':>9}{'cpuMs':>8}  판정"
    print(hdr)
    print("-" * 100)
    fails = 0

    for rel, base in baseline["samples"].items():
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            print(f"{os.path.basename(rel)[:32]:<34} 파일 없음")
            fails += 1
            continue
        kind = base["source_type"]
        edge = call_edge(path, kind, key)
        if args.dump:
            with open(os.path.join(args.dump, rel.replace("/", "_") + ".json"), "w", encoding="utf-8") as f:
                json.dump(edge, f, ensure_ascii=False)
        if edge.get("error"):
            print(f"{os.path.basename(rel)[:32]:<34} Edge 오류: {str(edge['error'])[:50]}")
            fails += 1
            continue

        r = edge["result"]
        e_secs = r["sections"]
        b_secs = base["sections"]
        e_text = "\n".join(s["text"] for s in e_secs)

        pairs_match = sum(
            1
            for x, y in zip(b_secs, e_secs)
            if x["text"] == y["text"] and x["section_title"] == y["sectionTitle"]
        )
        titles_match = sorted(base["titles"]) == sorted(r["titles"])
        sim = similarity(base["text"], e_text)

        ok = (
            len(b_secs) == len(e_secs)
            and pairs_match == len(b_secs)
            and titles_match
            and sim >= SIM_PASS
            and edge["cpuMs"] < 2000
        )
        if not ok:
            fails += 1

        secs = f"{len(b_secs)}/{len(e_secs)}"
        chars = f"{base['chars']}/{r['chars']}"
        print(
            f"{os.path.basename(rel)[:32]:<34}{secs:>12}{chars:>14}"
            f"{f'{pairs_match}/{len(b_secs)}':>9}{('O' if titles_match else 'X'):>7}"
            f"{sim:>9.4f}{edge['cpuMs']:>8.1f}  {'PASS' if ok else 'FAIL'}"
        )

    print()
    print(f"기준: 섹션 수·(text, title) 쌍 완전일치 · 제목 집합 동일 · 유사도 ≥ {SIM_PASS} · cpuMs < 2000")
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
