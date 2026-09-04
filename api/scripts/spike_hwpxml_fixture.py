"""HWPX/HWPML 검증용 fixture 생성기.

실자산(`직제_규정.hwpx`, `한마음생활체육관_내규.hwpx`, `law sample2.hwp`)은 `.gitignore` 의
`/*.hwpx`·`/*.hwp` 대상이라 저장소에 없다. 그 본문을 기준선 JSON 으로 커밋하면 자산을
우회 커밋하는 것과 같으므로, 커밋되는 회귀 세트는 여기서 만든 합성 fixture 로 한다.

현행 파서가 실제로 밟는 경로를 담는다:

HWPX (`hwpx.HwpxDocument.new()` 로 생성 — 라이브러리가 만든 파일이라 구조가 진짜다)
  - style 기반 heading: 기본 스타일에 `개요 1`~`개요 6` 이 있고 정규식 `개요\\s*\\d*` 가 잡는다
  - 텍스트 패턴 heading: `제 3 조 (적용범위)` — 스타일 없이 본문 패턴만으로 판정되는 경로
  - **표 안 중첩 단락**: `hp:tbl > hp:tr > hp:tc > hp:subList > hp:p` 를 XML 로 직접 넣는다.
    `TextExtractor` 가 `.//hp:p` 로 훑기 때문에 중첩 단락도 별도 단락으로 나와야 한다
  - 비BMP 문자(📌) — 코드포인트/UTF-16 계수 차이 회귀

HWPML (최소 XML 직접 작성 — HWPML 은 단일 XML 파일이라 생성 라이브러리가 필요 없다)
  - `HWPML > BODY > SECTION > P > TEXT > CHAR` 트리
  - **중첩 P**: 바깥 P 는 컨테이너로 skip 되고 안쪽만 단락이 돼야 한다
  - `PARAMETERSET` 안의 CHAR: `./TEXT/CHAR` 제한 때문에 **본문에 섞이면 안 된다**
  - UTF-8 BOM — 실자산이 BOM 을 달고 온다

사용:
    api/.venv/bin/python api/scripts/spike_hwpxml_fixture.py
산출물: api/scripts/fixtures/spike_sample.hwpx · spike_sample_hwpml.hwp
"""

from __future__ import annotations

import os
import hwpx
from lxml import etree as ET  # hwpx 라이브러리가 lxml 트리를 쓴다 — 섞으면 append 가 거부된다

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "fixtures")
OUT_HWPX = os.path.join(OUT_DIR, "spike_sample.hwpx")
OUT_HWPML = os.path.join(OUT_DIR, "spike_sample_hwpml.hwp")

HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"

# `개요 1` 스타일의 id — 기본 스타일 표에서 이름으로 찾는다(하드코딩하면 버전 따라 깨진다).
HEADING_STYLE_NAME = "개요 1"


def _table_xml() -> ET.Element:
    """`hp:tbl` 을 직접 만든다. 라이브러리에 표 생성 API 가 없어서 XML 로 넣는다.

    실자산에서 확인한 구조를 그대로 따른다:
        hp:tbl > hp:tr > hp:tc > hp:subList > hp:p > hp:run > hp:t
    """
    tbl = ET.Element(f"{{{HP}}}tbl", {"rowCnt": "2", "colCnt": "2"}, nsmap={"hp": HP})
    cells = [["항목", "결과"], ["유사도", "1.0000 📌"]]
    for row in cells:
        tr = ET.SubElement(tbl, f"{{{HP}}}tr")
        for cell in row:
            tc = ET.SubElement(tr, f"{{{HP}}}tc")
            sub = ET.SubElement(tc, f"{{{HP}}}subList")
            p = ET.SubElement(sub, f"{{{HP}}}p")
            run = ET.SubElement(p, f"{{{HP}}}run")
            t = ET.SubElement(run, f"{{{HP}}}t")
            t.text = cell
    return tbl


def build_hwpx() -> None:
    doc = hwpx.HwpxDocument.new()
    section = doc.sections[0]

    style_id = next(
        (sid for sid, style in doc.styles.items() if getattr(style, "name", "") == HEADING_STYLE_NAME),
        None,
    )
    if style_id is None:
        raise SystemExit(f"기본 스타일에 {HEADING_STYLE_NAME!r} 가 없다 — fixture 전제가 깨졌다")

    section.add_paragraph("제1장 총칙", style_id_ref=style_id)
    section.add_paragraph("스타일 이름으로 heading 이 판정되는 경로다. 📌")
    section.add_paragraph("두 번째 본문 단락 — 앞 heading 이 sticky 로 붙어야 한다.")
    section.add_paragraph("제 3 조 (적용범위)")
    section.add_paragraph("스타일 없이 텍스트 패턴만으로 heading 이 된 뒤의 본문.")

    # 표를 품은 단락 — 바깥 단락 자체의 텍스트와 표 안 중첩 단락이 **따로** 나와야 한다.
    holder = section.add_paragraph("표 앞 안내 문장")
    run = holder.element.find(f"{{{HP}}}run")
    if run is None:
        raise SystemExit("add_paragraph 가 hp:run 을 만들지 않았다 — 라이브러리 동작이 바뀌었다")
    run.append(_table_xml())

    section.add_paragraph("부칙")
    section.add_paragraph("표 뒤 단락 — 문서 순서가 보존되는지 확인용.")

    doc.save(OUT_HWPX)
    print(f"→ {OUT_HWPX} ({os.path.getsize(OUT_HWPX) / 1024:.0f}KB)")


HWPML_TEMPLATE = """<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<HWPML Style="embed" SubVersion="8.0.0.0" Version="2.8">
<HEAD SecCnt="1"><DOCSUMMARY><TITLE>스파이크 fixture</TITLE><AUTHOR>Jet-Rag</AUTHOR></DOCSUMMARY></HEAD>
<BODY>
<SECTION Id="0">
<P Style="0"><TEXT><SECDEF><PARAMETERSET SetId="1"><ITEM Type="Set"><CHAR>메타데이터 CHAR - 본문에 섞이면 안 된다</CHAR></ITEM></PARAMETERSET></SECDEF><CHAR>첫 단락 본문이다. 📌</CHAR></TEXT></P>
<P Style="0"><TEXT><CHAR>제 2 조 (정의)</CHAR></TEXT></P>
<P Style="0"><TEXT><CHAR>텍스트 패턴 heading 뒤의 본문 - sticky 로 붙어야 한다.</CHAR></TEXT></P>
<P Style="0"><TEXT><CHAR>바깥 P 텍스트 - 컨테이너라 skip 되어야 한다</CHAR></TEXT>
  <P Style="0"><TEXT><CHAR>중첩 P 의 본문 - 이쪽만 단락이 된다</CHAR></TEXT></P>
</P>
<P Style="0"><TEXT><CHAR>엔티티 검사: &amp; &lt; &gt; 그리고 줄바꿈 없는 본문</CHAR></TEXT></P>
<P Style="0"><TEXT><CHAR>부칙</CHAR></TEXT></P>
<P Style="0"><TEXT><CHAR>마지막 단락.</CHAR></TEXT></P>
</SECTION>
</BODY>
</HWPML>
"""


def build_hwpml() -> None:
    # 실자산이 UTF-8 BOM 을 달고 온다 — 디코딩 경로를 같이 검증하려면 fixture 도 BOM 을 단다.
    with open(OUT_HWPML, "wb") as f:
        f.write(b"\xef\xbb\xbf" + HWPML_TEMPLATE.encode("utf-8"))
    print(f"→ {OUT_HWPML} ({os.path.getsize(OUT_HWPML) / 1024:.1f}KB)")


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    build_hwpx()
    build_hwpml()


if __name__ == "__main__":
    main()
