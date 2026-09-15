"""로컬 PDF → extract 섹션 JSON. `chunk_bench.ts` 의 입력을 만든다.

`chunk` 단계 창 분할이 정말 예산 안에 드는지 재려면 **대형 문서 규모의 섹션 배열**이
필요하다. 운영 DB 에서 SK 사업보고서를 끌어오면 재현이 사람 손에 묶이므로, 손에 있는
PDF 를 `--repeat` 로 늘려 같은 규모를 만든다.

섹션은 **운영과 같은 파서**(`PyMuPDFParser`)로 뽑는다. 직접 `page.get_text()` 로
만들면 heading 상속·bbox·블록 분할이 달라져 청크 수가 어긋나고, 그러면 벤치 수치가
실제 부하를 안 나타낸다.

`--repeat N` 은 같은 섹션 묶음을 N 번 잇되 **page 를 문서 페이지 수만큼 밀어 준다**.
page 가 안 밀리면 서로 다른 반복본이 같은 page 로 묶여 병합·창 경계가 실제와 달라진다.

사용:
    cd api && uv run python scripts/make_bench_sections.py <pdf> --repeat 264 \\
        --out /tmp/bench_sections.json

    # 그다음
    cd supabase/functions && deno run --allow-read --allow-env \\
        _shared/ingest/chunk_bench.ts --sections /tmp/bench_sections.json --window 4
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.adapters.impl.pymupdf_parser import PyMuPDFParser  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="PDF → chunk 벤치용 섹션 JSON")
    ap.add_argument("pdf", help="입력 PDF 경로")
    ap.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="같은 문서를 몇 번 이을지. SK 사업보고서(1,513p) 규모를 흉내낼 때 쓴다",
    )
    ap.add_argument("--out", default="bench_sections.json", help="출력 JSON 경로")
    args = ap.parse_args()

    with open(args.pdf, "rb") as f:
        data = f.read()

    result = PyMuPDFParser().parse(data, file_name=os.path.basename(args.pdf))
    base = result.sections
    if not base:
        print("섹션이 0 개다 — 스캔 PDF 이거나 파서가 못 읽은 파일이다", file=sys.stderr)
        return 1

    # page 가 None 인 섹션(PDF 에서는 안 나오지만 계약상 가능)은 밀 수 없다. 그대로 둔다.
    pages = [s.page for s in base if s.page is not None]
    page_span = (max(pages) - min(pages) + 1) if pages else 0

    out: list[dict] = []
    for r in range(max(1, args.repeat)):
        offset = page_span * r
        for s in base:
            out.append(
                {
                    "text": s.text,
                    "page": None if s.page is None else s.page + offset,
                    "section_title": s.section_title,
                    "bbox": list(s.bbox) if s.bbox else None,
                    "metadata": dict(s.metadata),
                }
            )

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)

    chars = sum(len(s["text"]) for s in out)
    print(
        f"sections={len(out)} chars={chars:,} pages≈{page_span * max(1, args.repeat)} "
        f"→ {args.out} ({os.path.getsize(args.out) / 1e6:.1f}MB)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
