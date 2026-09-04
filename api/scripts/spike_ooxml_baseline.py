"""S4 (DOCX/PPTX Edge 이관) 기준선 생성기.

**프로덕션 파서를 그대로 돌려서** 뜬다 — 임의로 텍스트를 뽑으면 기준선이 아니라 새 구현이 된다.
  * `app/adapters/impl/docx_parser.DocxParser` — paragraph + table 을 XML 순서로 순회,
    `paragraph.style.name` 으로 heading 판정 → section_title sticky propagate
  * `app/adapters/impl/pptx_parser.PptxParser` — slide 단위, title placeholder 우선

PPTX 는 `image_parser=None` 으로 만든다. Vision OCR 이 붙으면 기준선이 외부 API 응답에
의존해 재현되지 않는다 — 순수 텍스트 추출 능력만 비교 대상이다.

사용:
    api/.venv/bin/python api/scripts/spike_ooxml_baseline.py

산출물: api/scripts/spike_ooxml_baseline.json
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "api"))

from app.adapters.impl.docx_parser import DocxParser  # noqa: E402
from app.adapters.impl.pptx_parser import PptxParser  # noqa: E402

OUT = os.path.join(HERE, "spike_ooxml_baseline.json")

# 기본 세트는 **커밋 가능한 합성 fixture** 만 쓴다 (`spike_ooxml_fixture.py` 가 생성).
# 저장소 루트의 실자산(`승인글 템플릿*.docx`, `브랜딩_*.pptx`)은 `.gitignore` 의
# `/*.docx`·`/*.pptx` 대상이라 저장소에 없다 — 그 본문을 기준선 JSON 으로 커밋하면
# 자산을 우회 커밋하는 것과 같다. 로컬에 파일이 있으면 `--include-local` 로 붙인다.
SAMPLES: list[tuple[str, str]] = [
    ("api/scripts/fixtures/spike_sample.docx", "docx"),
    ("api/scripts/fixtures/spike_sample.pptx", "pptx"),
]

# 실자산. `브랜딩_...pptx` 는 텍스트가 0자다(11슬라이드 전부 이미지) — 운영에서는 Vision OCR 경로라
# 텍스트 추출 능력 판정에는 쓸 수 없다. 그래도 "0섹션을 0섹션으로 재현하는가"는 확인 가치가 있다.
LOCAL_SAMPLES: list[tuple[str, str]] = [
    ("승인글 템플릿1.docx", "docx"),
    ("승인글 템플릿3.docx", "docx"),
    ("브랜딩_스튜디오앤드오어.pptx", "pptx"),
]


def run(rel: str, kind: str) -> dict | None:
    path = os.path.join(ROOT, rel)
    if not os.path.exists(path):
        print(f"  MISSING {rel}", file=sys.stderr)
        return None
    with open(path, "rb") as f:
        data = f.read()

    parser = DocxParser() if kind == "docx" else PptxParser(image_parser=None)
    result = parser.parse(data, file_name=os.path.basename(rel))

    sections = [
        {
            "text": s.text,
            "page": s.page,
            "section_title": s.section_title,
        }
        for s in result.sections
    ]
    joined = "\n".join(s["text"] for s in sections)
    print(
        f"  {rel}: source_type={result.source_type} sections={len(sections)}"
        f" chars={len(joined)} warnings={len(result.warnings)}"
    )
    return {
        "source_type": result.source_type,
        "file_bytes": len(data),
        "section_count": len(sections),
        "chars": len(joined),
        "titles": sorted({s["section_title"] for s in sections if s["section_title"]}),
        "text": joined,
        "sections": sections,
        "warnings": result.warnings,
    }


def main() -> None:
    samples = SAMPLES + (LOCAL_SAMPLES if "--include-local" in sys.argv else [])
    out: dict = {"samples": {}}
    for rel, kind in samples:
        got = run(rel, kind)
        if got:
            out["samples"][rel] = got
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n→ {OUT} ({os.path.getsize(OUT) / 1e6:.2f}MB)")


if __name__ == "__main__":
    main()
