"""HWPX/HWPML 기준선 생성기 — 프로덕션 파서를 그대로 돌린다.

S1 에서 `@rhwp/core` 는 **HWP 5.x(OLE2) 전용**으로 확정됐다. 기준선 6샘플 중 HWPX 2건 +
HWPML 1건은 그 경로로 처리할 수 없어 별도 판정이 필요했다 — 이 스크립트가 그 기준선이다.

  * `app/adapters/impl/hwpx_parser.HwpxParser` — ZIP + `Contents/section*.xml`,
    `styleIDRef` → `header.xml` 의 style 이름으로 heading 판정 + sticky propagate
  * `app/adapters/impl/hwpml_parser.HwpmlParser` — `BODY > SECTION > P > TEXT > CHAR`,
    heading 은 텍스트 패턴만, `section_title` 은 `current_title or "section {Id}"`

실자산은 `.gitignore` 의 `/*.hwp`·`/*.hwpx` 대상이라 저장소에 없다. 그래서 커밋되는 기본
세트는 **합성 fixture** 뿐이고(`spike_hwpxml_fixture.py` 생성), 실자산은 `--include-local`
로 붙인다. S2/S4 와 같은 정책이다.

사용:
    api/.venv/bin/python api/scripts/spike_hwpxml_baseline.py [--include-local]

산출물: api/scripts/spike_hwpxml_baseline.json
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "api"))

from app.adapters.impl.hwpml_parser import HwpmlParser  # noqa: E402
from app.adapters.impl.hwpx_parser import HwpxParser  # noqa: E402

OUT = os.path.join(HERE, "spike_hwpxml_baseline.json")

SAMPLES: list[tuple[str, str]] = [
    ("api/scripts/fixtures/spike_sample.hwpx", "hwpx"),
    ("api/scripts/fixtures/spike_sample_hwpml.hwp", "hwpml"),
]

# 실자산. `law sample2.hwp` 는 이름만 `.hwp` 이고 실제로는 UTF-8 BOM XML(HWPML) 이다 —
# 확장자를 믿으면 안 된다는 S1 의 발견이 그대로 적용된다.
LOCAL_SAMPLES: list[tuple[str, str]] = [
    ("직제_규정(2024.4.30.개정).hwpx", "hwpx"),
    ("한마음생활체육관_운영_내규(2024.4.30.개정).hwpx", "hwpx"),
    ("law sample2.hwp", "hwpml"),
]


def run(rel: str, kind: str) -> dict | None:
    path = os.path.join(ROOT, rel)
    if not os.path.exists(path):
        print(f"  MISSING {rel}", file=sys.stderr)
        return None
    with open(path, "rb") as f:
        data = f.read()

    parser = HwpxParser() if kind == "hwpx" else HwpmlParser()
    result = parser.parse(data, file_name=os.path.basename(rel))

    sections = [
        {"text": s.text, "page": s.page, "section_title": s.section_title}
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
