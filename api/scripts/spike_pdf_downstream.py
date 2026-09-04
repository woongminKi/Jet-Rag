"""S2 다운스트림 동등성 검사 — 구조 차이가 **산출물을 바꾸는가**를 잰다.

`spike_pdf_compare.py` 는 block/span 개수를 본다. 하지만 개수 차이 자체는 근거가 아니다.
제품이 실제로 쓰는 값은 두 가지뿐이다:

  1. `pymupdf_parser._extract_dict_blocks` → 섹션(제목/본문/bbox) — 검색·인용에 쓰인다
  2. `vision_need_score.score_page`       → vision 호출 여부 + 점수 — **운영 비용에 직결**

그래서 기준선 dict 와 Edge dict 를 같은 함수에 넣고 출력을 대조한다.
개수가 달라도 이 둘이 같으면 이관에 지장이 없고, 같은 개수라도 여기서 갈리면 문제다.

사용:
    PYTHONPATH=api api/.venv/bin/python api/scripts/spike_pdf_downstream.py <edge_dump_dir>

`<edge_dump_dir>` 는 `spike_pdf_compare.py --dump` 가 남긴 디렉토리.
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "api"))

from app.adapters.impl.pymupdf_parser import _extract_dict_blocks  # noqa: E402
from app.services.vision_need_score import score_page  # noqa: E402

BASELINE = os.path.join(HERE, "spike_pdf_baseline.json")


def to_fitz_dict(blocks: list) -> dict:
    """기준선/Edge 양쪽을 `get_text("dict")` 모양으로 정규화."""
    return {"blocks": blocks}


def sections_of(page_dict: dict, page_num: int) -> list[dict]:
    sections: list = []
    raw: list[str] = []
    _extract_dict_blocks(
        page_dict,
        page_num=page_num,
        current_title=None,
        sections=sections,
        raw_parts=raw,
    )
    return [
        {
            "title": s.section_title,
            "text": s.text,
            "bbox": [round(v, 1) for v in s.bbox] if s.bbox else None,
        }
        for s in sections
    ]


def main() -> None:
    dump_dir = sys.argv[1]
    with open(BASELINE, encoding="utf-8") as f:
        baseline = json.load(f)

    hdr = (
        f"{'샘플':<34}{'섹션 수':>10}{'제목·본문':>10}{'bbox':>7}"
        f"{'need':>12}{'score':>16}{'판정':>7}"
    )
    print(hdr)
    print("-" * 100)
    fails = 0

    for rel, sample in baseline["samples"].items():
        for page_idx, base_page in sample["pages"].items():
            path = os.path.join(dump_dir, rel.replace("/", "_") + f".p{page_idx}.json")
            if not os.path.exists(path):
                print(f"{os.path.basename(rel)[:32]:<34} 덤프 없음: {path}")
                fails += 1
                continue
            with open(path, encoding="utf-8") as f:
                edge = json.load(f)
            if edge.get("error"):
                print(f"{os.path.basename(rel)[:32]:<34} Edge 오류: {edge['error'][:60]}")
                fails += 1
                continue

            page_num = int(page_idx) + 1
            b_dict = to_fitz_dict(base_page["blocks"])
            e_dict = to_fitz_dict(edge["result"]["dict"]["blocks"])
            area_b = base_page["page_area_pt2"]
            area_e = edge["result"]["pageArea"]

            b_sec = sections_of(b_dict, page_num)
            e_sec = sections_of(e_dict, page_num)

            b_score = score_page(b_dict, page_num=page_num, page_area_pt2=area_b)
            e_score = score_page(e_dict, page_num=page_num, page_area_pt2=area_e)

            same_count = len(b_sec) == len(e_sec)
            # 제목·본문은 문자열 그대로 비교. 순서까지 같아야 인용 좌표가 흔들리지 않는다.
            same_text = [(s["title"], s["text"]) for s in b_sec] == [
                (s["title"], s["text"]) for s in e_sec
            ]
            same_bbox = same_count and all(
                (x["bbox"] is None and y["bbox"] is None)
                or (
                    x["bbox"] is not None
                    and y["bbox"] is not None
                    and max(abs(a - c) for a, c in zip(x["bbox"], y["bbox"])) <= 1.0
                )
                for x, y in zip(b_sec, e_sec)
            )
            same_need = b_score.needs_vision == e_score.needs_vision
            score_delta = abs(b_score.composite_score - e_score.composite_score)
            same_trigger = b_score.triggers == e_score.triggers

            ok = same_text and same_bbox and same_need and same_trigger and score_delta < 0.01
            if not ok:
                fails += 1
            print(
                f"{os.path.basename(rel)[:32]:<34}"
                f"{f'{len(b_sec)}/{len(e_sec)}':>10}"
                f"{('O' if same_text else 'X'):>10}"
                f"{('O' if same_bbox else 'X'):>7}"
                f"{f'{b_score.needs_vision}/{e_score.needs_vision}':>12}"
                f"{f'{b_score.composite_score:.4f}/{e_score.composite_score:.4f}':>16}"
                f"{('PASS' if ok else 'FAIL'):>7}"
            )

    print()
    print("기준: 섹션 제목·본문 완전일치 · bboxΔ ≤ 1pt · needs_vision·triggers 동일 · composite Δ < 0.01")
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
