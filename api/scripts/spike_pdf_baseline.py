"""S2 (PDF Edge 이관) 기준선 생성기.

목적: Deno/Edge 후보 파서가 **반드시 재현해야 하는 값**을 PyMuPDF 로 먼저 떠둔다.
비교 대상은 "텍스트가 비슷한가"가 아니다 — 현행 코드가 실제로 읽는 필드다:

  * `app/adapters/impl/pymupdf_parser.py`
      - `page.get_text("dict")` → `blocks[].bbox`, `blocks[].lines[].spans[].{text,size}`
      - block 내 max span size 로 heading 판정 (`_HEADING_FONT_RATIO`)
  * `app/services/vision_need_score.py`
      - `blocks[].type` (0=text, 1=image)
      - `lines[].spans[]` 개수 → table-like 휴리스틱
      - `spans[0].bbox` → 첫 span x 좌표 cluster
      - image block `bbox` → `image_area_ratio`
      - `page.rect.width * page.rect.height` → `page_area_pt2`

사용:
    python3 api/scripts/spike_pdf_baseline.py                 # 기본 샘플 세트
    python3 api/scripts/spike_pdf_baseline.py <pdf> <page…>   # 임의 파일/페이지

산출물: api/scripts/spike_pdf_baseline.json
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import fitz  # PyMuPDF

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spike_pdf_baseline.json")

# (경로, 판정에 쓸 페이지 인덱스들, 선정 이유)
SAMPLES: list[tuple[str, list[int], str]] = [
    ("assets/public/law sample3.pdf", [0, 1], "기존 기준선(5,395자/34섹션)과 같은 문서 — 텍스트 대조용"),
    (
        "(붙임2) 2025년 데이터센터 산업 활성화 지원 사업 통합_안내서.pdf",
        [0, 39],
        "vision_need_score 주석이 지목한 표 페이지(p.40 = index 39) 포함",
    ),
    ("assets/public/sample-report.pdf", [0], "9MB/93p — image block 이 있는지 확인"),
]

# `assets/private/` 는 저장소에 없다(.gitignore `/assets/*`). 여기서 뽑은 본문을 커밋하면
# private 자산 내용이 저장소로 새므로 **기본 세트에서 뺀다.** 로컬에 파일이 있으면
# `--include-private` 로 붙여 7페이지 전체를 재현할 수 있다.
PRIVATE_SAMPLES: list[tuple[str, list[int], str]] = [
    ("assets/private/[삼성전자]사업보고서(2026.03.10).pdf", [0, 100], "573p 대용량 — 페이지 단위 처리 대조"),
]


def dump_page(page: fitz.Page) -> dict[str, Any]:
    """현행 코드가 읽는 필드만 골라 뜬다. 여기 없는 값은 이관 시 재현 의무가 없다."""
    d = page.get_text("dict")
    blocks_out: list[dict[str, Any]] = []
    span_count = 0
    text_block_count = 0
    image_block_count = 0

    for b in d.get("blocks", []):
        btype = b.get("type", 0)
        entry: dict[str, Any] = {"type": btype, "bbox": [round(float(v), 2) for v in b.get("bbox", [])]}
        if btype == 0:
            text_block_count += 1
            lines_out = []
            for line in b.get("lines", []):
                spans_out = []
                for s in line.get("spans", []):
                    span_count += 1
                    spans_out.append(
                        {
                            "text": s.get("text", ""),
                            "size": round(float(s.get("size", 0.0)), 2),
                            "bbox": [round(float(v), 2) for v in s.get("bbox", [])],
                            "font": s.get("font"),
                            "flags": s.get("flags"),
                        }
                    )
                lines_out.append({"bbox": [round(float(v), 2) for v in line.get("bbox", [])], "spans": spans_out})
            entry["lines"] = lines_out
        else:
            image_block_count += 1
        blocks_out.append(entry)

    rect = page.rect
    plain = page.get_text()
    return {
        "page_index": page.number,
        "page_width": round(float(rect.width), 2),
        "page_height": round(float(rect.height), 2),
        "page_area_pt2": round(float(rect.width * rect.height), 2),
        "block_count": len(blocks_out),
        "text_block_count": text_block_count,
        "image_block_count": image_block_count,
        "span_count": span_count,
        "text_chars": len(plain),
        "text": plain,
        "blocks": blocks_out,
    }


def run(path_rel: str, pages: list[int], why: str) -> dict[str, Any] | None:
    path = os.path.join(ROOT, path_rel)
    if not os.path.exists(path):
        print(f"  MISSING {path_rel}", file=sys.stderr)
        return None
    doc = fitz.open(path)
    out: dict[str, Any] = {
        "why": why,
        "page_count": doc.page_count,
        "file_bytes": os.path.getsize(path),
        "pages": {},
    }
    for idx in pages:
        if idx >= doc.page_count:
            continue
        p = dump_page(doc.load_page(idx))
        out["pages"][str(idx)] = p
        print(
            f"  p{idx}: chars={p['text_chars']} blocks={p['block_count']}"
            f"(text {p['text_block_count']}/img {p['image_block_count']}) spans={p['span_count']}"
            f" area={p['page_area_pt2']}"
        )
    doc.close()
    return out


def main() -> None:
    args = sys.argv[1:]
    include_private = "--include-private" in args
    args = [a for a in args if a != "--include-private"]

    if args:
        rel = os.path.relpath(os.path.abspath(args[0]), ROOT)
        pages = [int(a) for a in args[1:]] or [0]
        samples = [(rel, pages, "CLI 지정")]
    else:
        samples = SAMPLES + (PRIVATE_SAMPLES if include_private else [])

    result: dict[str, Any] = {"mupdf_version": fitz.__doc__.splitlines()[0], "samples": {}}
    for rel, pages, why in samples:
        print(rel)
        got = run(rel, pages, why)
        if got:
            result["samples"][rel] = got

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(f"\n→ {OUT} ({os.path.getsize(OUT) / 1e6:.2f}MB)")


if __name__ == "__main__":
    main()
