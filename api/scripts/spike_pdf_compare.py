"""S2 채점기 — Edge 의 `?kind=pdf-dict` 응답을 PyMuPDF 기준선과 필드 단위로 대조한다.

"텍스트가 비슷하다"로는 이관 가능 판정이 안 된다. `vision_need_score.py` 는 span 개수와
좌표를 읽어 vision 호출 여부를 정하므로, **구조와 수치가 맞아야** 로직을 그대로 옮길 수 있다.

사용:
    export SUPABASE_ANON_KEY=...          # 없으면 저장소 루트 .env 에서 읽는다
    python3 api/scripts/spike_pdf_compare.py            # 기준선 전 샘플
    python3 api/scripts/spike_pdf_compare.py --dump DIR # Edge 원본 응답도 저장

기준선은 `spike_pdf_baseline.py` 가 만든 `spike_pdf_baseline.json`.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERE = os.path.dirname(os.path.abspath(__file__))
BASELINE = os.path.join(HERE, "spike_pdf_baseline.json")
FUNCTION_URL = "https://mpmtydudhojpukuuadrd.supabase.co/functions/v1/spike"

# 판정 임계 — 근거는 work-log 2026-09-04 참조.
SIM_PASS = 0.95  # S1 과 동일 기준
BBOX_TOL = 1.0  # mupdf 의 asJSON 은 bbox 를 정수로 낸다. 1pt 이내면 동일로 본다.


def anon_key() -> str:
    key = os.environ.get("SUPABASE_ANON_KEY")
    if key:
        return key
    with open(os.path.join(ROOT, ".env"), encoding="utf-8") as f:
        for line in f:
            if line.startswith("NEXT_PUBLIC_SUPABASE_ANON_KEY="):
                return line.split("=", 1)[1].strip().strip("\"'")
    raise SystemExit("SUPABASE_ANON_KEY 를 찾지 못했다")


def call_edge(path: str, page: int, key: str) -> dict:
    with open(path, "rb") as f:
        body = f.read()
    req = urllib.request.Request(
        f"{FUNCTION_URL}?kind=pdf-dict&page={page}",
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/pdf"},
        method="POST",
    )
    # 스파이크는 실패도 결과다 — 500 의 본문(에러 전문)이 판정 근거이므로 예외로 버리지 않는다.
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return json.loads(raw)
        except Exception:
            return {"error": f"HTTP {e.code}: {raw[:500]!r}", "cpuMs": 0, "result": None}


def page_text(dict_: dict) -> str:
    """블록→라인→span 을 순회해 평문을 만든다. 기준선 `page.get_text()` 와 대조용."""
    out = []
    for b in dict_["blocks"]:
        if b["type"] != 0:
            continue
        for line in b.get("lines", []):
            out.append("".join(s["text"] for s in line["spans"]))
    return "\n".join(out)


def similarity(a: str, b: str) -> float:
    """공백을 무시한 문자 유사도 — S1(`spike_hwp_similarity.py`)과 같은 방식."""
    na = "".join(a.split())
    nb = "".join(b.split())
    return difflib.SequenceMatcher(None, na, nb).ratio()


def bbox_delta(base_blocks: list, edge_blocks: list) -> tuple[float, int]:
    """블록 bbox 최대 절대 편차와 비교한 블록 수."""
    worst = 0.0
    n = 0
    for bb, eb in zip(base_blocks, edge_blocks):
        for x, y in zip(bb["bbox"], eb["bbox"]):
            worst = max(worst, abs(float(x) - float(y)))
        n += 1
    return worst, n


def compare(base_page: dict, edge: dict) -> dict:
    r = edge["result"]
    d = r["dict"]
    base_blocks = base_page["blocks"]
    edge_blocks = d["blocks"]

    worst_bbox, compared = bbox_delta(base_blocks, edge_blocks)
    txt = page_text(d)
    sim = similarity(base_page["text"], txt)

    # block 별 span 개수 시퀀스 — 표 휴리스틱이 직접 읽는 값이라 개수가 아니라 배열로 본다.
    def span_seq(blocks, is_base):
        seq = []
        for b in blocks:
            if (b["type"] != 0) if is_base else (b["type"] != 0):
                continue
            seq.append(sum(len(line["spans"]) for line in b.get("lines", [])))
        return seq

    base_seq = span_seq(base_blocks, True)
    edge_seq = span_seq(edge_blocks, False)

    return {
        "blocks": (base_page["block_count"], r["blockCount"]),
        "text_blocks": (base_page["text_block_count"], r["textBlocks"]),
        "image_blocks": (base_page["image_block_count"], r["imageBlocks"]),
        "spans": (base_page["span_count"], r["spanCount"]),
        "area": (base_page["page_area_pt2"], round(r["pageArea"], 2)),
        "span_seq_match": base_seq == edge_seq,
        "worst_bbox_delta": round(worst_bbox, 3),
        "bbox_compared": compared,
        "similarity": round(sim, 4),
        "cpuMs": round(edge["cpuMs"], 1),
        "chars": (base_page["text_chars"], len(txt)),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", help="Edge 원본 응답을 저장할 디렉토리 (사후 분석용)")
    args = ap.parse_args()
    if args.dump:
        os.makedirs(args.dump, exist_ok=True)

    key = anon_key()
    with open(BASELINE, encoding="utf-8") as f:
        baseline = json.load(f)

    fails = 0
    rows = []
    for rel, sample in baseline["samples"].items():
        path = os.path.join(ROOT, rel)
        for page_idx, base_page in sample["pages"].items():
            edge = call_edge(path, int(page_idx), key)
            if args.dump:
                safe = rel.replace("/", "_")
                with open(os.path.join(args.dump, f"{safe}.p{page_idx}.json"), "w", encoding="utf-8") as f:
                    json.dump(edge, f, ensure_ascii=False)
            if edge.get("error"):
                print(f"FAIL {rel} p{page_idx}: {edge['error'][:200]}")
                fails += 1
                continue
            c = compare(base_page, edge)
            ok = (
                c["blocks"][0] == c["blocks"][1]
                and c["text_blocks"][0] == c["text_blocks"][1]
                and c["image_blocks"][0] == c["image_blocks"][1]
                and c["spans"][0] == c["spans"][1]
                and c["span_seq_match"]
                and c["worst_bbox_delta"] <= BBOX_TOL
                and c["similarity"] >= SIM_PASS
                and c["cpuMs"] < 2000
            )
            if not ok:
                fails += 1
            rows.append((f"{os.path.basename(rel)[:26]} p{page_idx}", c, ok))

    hdr = f"{'샘플':<32}{'block':>12}{'span':>12}{'seq':>5}{'bboxΔ':>8}{'유사도':>9}{'cpuMs':>8}  판정"
    print(hdr)
    print("-" * len(hdr))
    for name, c, ok in rows:
        blocks = "{}/{}".format(*c["blocks"])
        spans = "{}/{}".format(*c["spans"])
        seq = "O" if c["span_seq_match"] else "X"
        verdict = "PASS" if ok else "FAIL"
        print(
            f"{name:<32}{blocks:>12}{spans:>12}{seq:>5}"
            f"{c['worst_bbox_delta']:>8}{c['similarity']:>9}{c['cpuMs']:>8}  {verdict}"
        )
    print()
    print(f"기준: block/span 완전일치 · span 시퀀스 일치 · bboxΔ ≤ {BBOX_TOL}pt · 유사도 ≥ {SIM_PASS} · cpuMs < 2000")
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
