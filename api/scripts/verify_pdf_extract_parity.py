"""`pymupdf_parser.py` 의 heading 판정·블록 변환을 Python 원본과 대조.

## 왜 이게 제일 중요한가
운영 chunk 36,818 / 37,080 (**99.3%**) 이 PDF 에서 나온다(2026-09-07 실측).

## 두 층으로 본다
1. **합성 케이스** — Python↔JS 가 갈리는 지점을 일부러 태운다
   (`median` 짝수, `\\s`·`\\d` 집합, Python `$` 의 끝-개행, `IGNORECASE`, 80자 경계,
   블랙리스트 우선순위)
2. **실자산** — 실제 PDF 의 `page.get_text("dict")` 를 그대로 TS 에 먹인다.
   `pdf_dict.ts` 변환 품질과 **무관하게 파서 로직만** 대조된다
   (변환 자체는 Phase 0 S2 에서 block 7/7 일치로 이미 검증).

사용:
    api/.venv/bin/python api/scripts/verify_pdf_extract_parity.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SHARED = os.path.join(ROOT, "supabase", "functions", "_shared")
DENO_CONFIG = os.path.join(ROOT, "supabase", "functions", "spike", "deno.json")

sys.path.insert(0, os.path.join(ROOT, "api"))

# 실자산 — 공개 3건 + 운영 대형 2건(private, 내용은 커밋하지 않는다).
ASSETS = [
    "assets/public/law sample3.pdf",
    "assets/public/보건의료_빅데이터_플랫폼_시범사업_추진계획(안).pdf",
    "assets/public/(붙임2) 2025년 데이터센터 산업 활성화 지원 사업 통합_안내서.pdf",
    "assets/public/sample-report.pdf",
    "assets/private/[삼성전자]사업보고서(2026.03.10).pdf",
    "assets/private/[SK]사업보고서(2026.03.18).pdf",
    "assets/private/arXiv 영어 학술.pdf",
]
MAX_PAGES_PER_ASSET = 30   # 고르게 표본. 전 페이지는 너무 느리다.

# --- median ---
MEDIAN_CASES = [
    [], [1.0], [1.0, 2.0], [1.0, 2.0, 3.0], [1.0, 2.0, 3.0, 4.0],
    [9.0, 10.0],                 # 짝수 → 9.5 (문자열 정렬이면 10 이 앞에 온다)
    [10.0, 9.0, 100.0],          # 정렬 필요
    [0.1, 0.2],                  # 부동소수 평균
    [3.0, 3.0, 3.0],
    [1.5, 2.5, 3.5, 4.5],        # 짝수 평균 3.0
]

# --- heading 판정 ---
# (block_max, page_median, text)
HEADING_CASES = [
    # font ratio 경계 — 1.15 배
    (11.5, 10.0, "본문입니다"),        # 정확히 1.15 → True
    (11.4, 10.0, "본문입니다"),        # 미만 → False
    (100.0, 0.0, "본문입니다"),        # page_median 0 → ratio 분기 안 탐
    # 한국어 조문
    (10.0, 10.0, "제1조 목적"),
    (10.0, 10.0, "제 12 조"),
    (10.0, 10.0, "제１２조"),           # **전각 숫자** — Python `\d` 는 잡는다
    (10.0, 10.0, "제1조"),  # **U+001C** — Python `\s` 만 공백
    (10.0, 10.0, "제﻿1﻿조"),  # **U+FEFF** — JS `\s` 만 공백
    (10.0, 10.0, "제1조"),
    (10.0, 10.0, "부칙"),
    (10.0, 10.0, "별표 3"),
    (10.0, 10.0, "별첨"),
    (10.0, 10.0, "【제목】"),
    (10.0, 10.0, "[제목]"),
    (10.0, 10.0, "【" + "가" * 31 + "】"),   # 30자 초과 → 매칭 안 됨
    # 영어 학술
    (10.0, 10.0, "1. Introduction"),
    (10.0, 10.0, "2.1 Related Work"),
    (10.0, 10.0, "3.4.1 Detailed Method"),
    (10.0, 10.0, "1.2.3.4.5 Too Deep"),      # {0,3} 초과
    (10.0, 10.0, "Abstract"),
    (10.0, 10.0, "References"),
    (10.0, 10.0, "Acknowledgements"),
    (10.0, 10.0, "Acknowledgment"),
    (10.0, 10.0, "Related  Work"),           # `\s+` 복수 공백
    (10.0, 10.0, "Appendix A"),
    (10.0, 10.0, "Chapter 5"),
    (10.0, 10.0, "Section"),
    (10.0, 10.0, "abstract"),                # 소문자 → 패턴은 IGNORECASE 아님
    # 블랙리스트 — **font size 가 커도 heading 이 아니어야 한다**
    (100.0, 10.0, "12"),
    (100.0, 10.0, "1234"),
    (100.0, 10.0, "12345"),                  # {1,4} 초과 → 블랙리스트 미적용
    (100.0, 10.0, "Page 3"),
    (100.0, 10.0, "page 3"),                 # **IGNORECASE**
    (100.0, 10.0, "PAGE 3"),
    (100.0, 10.0, "- 4 -"),
    (100.0, 10.0, "arXiv:2601.00442v1 [hep-th] 1 Jan 2026"),
    (100.0, 10.0, "ARXIV:2601.00442"),       # IGNORECASE
    (100.0, 10.0, "１２"),                    # 전각 페이지 번호
    (100.0, 10.0, "12 "),                    # 끝 공백 → `\s*$`
    (100.0, 10.0, "12\n"),                   # **Python `$` 는 끝 개행 앞에서도 매칭**
    (100.0, 10.0, "12\n34"),                 # 중간 개행 → 매칭 안 됨
    # 80자 경계 — 길면 텍스트 패턴·블랙리스트 둘 다 적용 안 함
    (10.0, 10.0, "제1조 " + "가" * 75),        # 80자
    (10.0, 10.0, "제1조 " + "가" * 76),        # 81자 → 패턴 미적용
    (100.0, 10.0, "12" + " " * 79),           # 81자 블랙리스트 미적용 → ratio 로 True
    (10.0, 10.0, "🙂" * 80),                   # 코드포인트 80
    (10.0, 10.0, "🙂" * 81),
    (10.0, 10.0, ""),
    # **코드포인트 vs UTF-16** — 위 이모지 케이스는 어차피 패턴에 안 걸려 양쪽 다 False 라
    # 길이 세는 법이 달라도 결과가 같았다. 경계를 넘나들면서 **패턴에도 걸려야** 갈린다.
    (10.0, 10.0, "제1조 " + "🙂" * 75),         # 코드포인트 80 (UTF-16 155) → 패턴 적용
    (10.0, 10.0, "제1조 " + "🙂" * 76),         # 코드포인트 81 → 패턴 미적용
    (100.0, 10.0, "12" + "🙂" * 78),           # 블랙리스트 쪽 경계
    # **Python `$` 는 끝 개행 하나 앞에서도 매칭** — JS `$` 는 아니다.
    # 블랙리스트(`\s*$`)로는 못 태운다. `\s*` 가 개행을 먹어 양쪽이 같아진다.
    # heading 패턴의 `([\s(].*)?$` 는 `.` 가 개행을 안 먹어서 갈린다.
    (10.0, 10.0, "제1조 목적\n"),
    (10.0, 10.0, "1. Introduction\n"),
    (10.0, 10.0, "제1조 목적\n뒤에 더"),         # 중간 개행 → 양쪽 다 실패
]

# --- 블록 → 섹션 ---
def _sp(text, size=10.0, bbox=None):
    return {"text": text, "size": size, "bbox": bbox or [0.0, 0.0, 1.0, 1.0]}


def _ln(spans, bbox=None):
    return {"bbox": bbox or [0.0, 0.0, 1.0, 1.0], "spans": spans}


def _bl(lines, *, type=0, bbox=None):
    b = {"type": type, "bbox": bbox or [1.0, 2.0, 3.0, 4.0]}
    if lines is not None:
        b["lines"] = lines
    return b


BLOCK_CASES = [
    {"width": 100.0, "height": 200.0, "blocks": []},
    # 일반 본문 + heading sticky
    {"width": 100.0, "height": 200.0, "blocks": [
        _bl([_ln([_sp("제1조 목적", 20.0)])]),
        _bl([_ln([_sp("본문 하나", 10.0)])]),
        _bl([_ln([_sp("본문 둘", 10.0)])]),
    ]},
    # 이미지 블록은 건너뛴다 (type=1)
    {"width": 100.0, "height": 200.0, "blocks": [
        _bl(None, type=1),
        _bl([_ln([_sp("본문", 10.0)])]),
    ]},
    # 빈 텍스트 블록 → skip
    {"width": 100.0, "height": 200.0, "blocks": [
        _bl([_ln([_sp("   ", 10.0)])]),
        _bl([_ln([_sp("본문", 10.0)])]),
    ]},
    # **U+001C 만 있는 블록** — Python strip 은 지워서 skip, JS trim 은 남긴다
    {"width": 100.0, "height": 200.0, "blocks": [
        _bl([_ln([_sp("", 10.0)])]),
        _bl([_ln([_sp("본문", 10.0)])]),
    ]},
    # **U+FEFF 만 있는 블록** — 반대
    {"width": 100.0, "height": 200.0, "blocks": [
        _bl([_ln([_sp("﻿", 10.0)])]),
        _bl([_ln([_sp("본문", 10.0)])]),
    ]},
    # 여러 line → `\n` join
    {"width": 100.0, "height": 200.0, "blocks": [
        _bl([_ln([_sp("첫 줄", 10.0)]), _ln([_sp("둘째 줄", 10.0)])]),
    ]},
    # 빈 line 은 버린다
    {"width": 100.0, "height": 200.0, "blocks": [
        _bl([_ln([_sp("", 10.0)]), _ln([_sp("있음", 10.0)])]),
    ]},
    # span 여러 개 → 이어붙임, max size 로 heading 판정
    {"width": 100.0, "height": 200.0, "blocks": [
        _bl([_ln([_sp("작게", 8.0), _sp("크게", 20.0)])]),
        _bl([_ln([_sp("본문", 10.0)])]),
    ]},
    # size 0/음수/누락 → median·max 에서 제외
    {"width": 100.0, "height": 200.0, "blocks": [
        _bl([_ln([_sp("영", 0.0), _sp("정상", 10.0)])]),
        _bl([_ln([_sp("본문", 10.0)])]),
    ]},
    # **median 짝수** — 두 중간값 평균이라야 맞다
    {"width": 100.0, "height": 200.0, "blocks": [
        _bl([_ln([_sp("a", 9.0), _sp("b", 10.0)])]),
        _bl([_ln([_sp("c", 11.4), _sp("d", 11.4)])]),   # 9.5*1.15=10.925 → 11.4 heading
    ]},
    # bbox 없는 블록
    {"width": 100.0, "height": 200.0, "blocks": [
        {"type": 0, "lines": [_ln([_sp("bbox 없음", 10.0)])]},
    ]},
    # 블랙리스트가 큰 글꼴을 이긴다
    {"width": 100.0, "height": 200.0, "blocks": [
        _bl([_ln([_sp("제1조 목적", 20.0)])]),
        _bl([_ln([_sp("12", 60.0)])]),       # 페이지 번호 — title 바뀌면 안 됨
        _bl([_ln([_sp("본문", 10.0)])]),
    ]},
    # **빈 line 이 중간**에 있어야 join 결과가 갈린다.
    # 앞/뒤의 빈 line 은 `strip()` 이 지워 버려 유지하든 버리든 같아진다.
    {"width": 100.0, "height": 200.0, "blocks": [
        _bl([_ln([_sp("첫 줄", 10.0)]), _ln([_sp("", 10.0)]), _ln([_sp("셋째 줄", 10.0)])]),
    ]},
    # span 이 없는 line
    {"width": 100.0, "height": 200.0, "blocks": [
        _bl([_ln([_sp("앞", 10.0)]), _ln([]), _ln([_sp("뒤", 10.0)])]),
    ]},
    # **image block 인데 lines 가 있는** 경우 — type 검사가 유일한 방어선임을 확인한다.
    # (실제 PyMuPDF image block 에는 lines 가 없지만, 그러면 type 검사를 빼도 결과가
    #  같아져 "검사가 필요한지" 를 대조로 확인할 수 없다.)
    {"width": 100.0, "height": 200.0, "blocks": [
        _bl([_ln([_sp("이미지 안 텍스트", 30.0)])], type=1),
        _bl([_ln([_sp("본문", 10.0)])]),
    ]},
    # **음수 size 만 있는 블록** — `size > 0` 필터가 median 을 좌우한다.
    # 필터가 있으면 median 은 [10, 10] → 10, 없으면 [-5, 10, 10] → 10 이 아니라 분포가 밀린다.
    {"width": 100.0, "height": 200.0, "blocks": [
        _bl([_ln([_sp("정상1", 10.0)])]),
        _bl([_ln([_sp("정상2", 10.0)])]),
        _bl([_ln([_sp("음수", -5.0), _sp("음수2", -7.0)])]),
        _bl([_ln([_sp("후보", 11.4)])]),      # median 10 → 11.5 미만 → heading 아님
    ]},
    # **글자 수 가중 median 이 코드포인트인지 UTF-16 인지** 갈리는 지점.
    #
    # 처음엔 이모지 2개 + `abc` 로 잡았다가 실패했다 — **판정 대상 블록의 글자도
    # median 에 들어간다**는 걸 빼고 계산했다. 표를 전부 세서 다시 잡았다:
    #   코드포인트: 20pt×10, 10pt×15, 12pt×4 = 29표 → 정렬 시 가운데(idx14)가 10
    #   UTF-16   : 20pt×20, 10pt×15, 12pt×4 = 39표 → 가운데(idx19)가 20
    # 판정 블록은 12pt —  median 10 이면 12 ≥ 11.5 로 heading, median 20 이면 아니다.
    {"width": 100.0, "height": 200.0, "blocks": [
        _bl([_ln([_sp("🙂" * 10, 20.0)])]),    # 코드포인트 10 / UTF-16 20
        _bl([_ln([_sp("가" * 15, 10.0)])]),
        _bl([_ln([_sp("판정대상", 12.0)])]),
    ]},
    # size 필드 누락 / 문자열
    {"width": 100.0, "height": 200.0, "blocks": [
        _bl([_ln([{"text": "size 없음", "bbox": [0.0, 0.0, 1.0, 1.0]}])]),
        _bl([_ln([{"text": "size 문자열", "size": "12", "bbox": [0.0, 0.0, 1.0, 1.0]}])]),
        _bl([_ln([_sp("본문", 10.0)])]),
    ]},
]

RUNNER_TS = f"""
import {{
  blockMaxSize, blockText, extractDictBlocks, isHeadingBlock, median, pageMedianSize,
}} from "file://{SHARED}/ingest/pdf_extract.ts";

const input = JSON.parse(await Deno.readTextFile(Deno.args[0]));
console.log(JSON.stringify({{
  median: input.median.map((xs: number[]) => median(xs)),
  heading: input.heading.map(([bm, pm, t]: [number, number, string]) =>
    isHeadingBlock(bm, pm, t)),
  blocks: input.blocks.map((d: unknown) => {{
    const r = extractDictBlocks(d as never, {{ pageNum: 7, currentTitle: "이전제목" }});
    return {{
      sections: r.sections, rawParts: r.rawParts, nextTitle: r.nextTitle,
      pageMedian: pageMedianSize(d as never),
    }};
  }}),
  assets: input.assets.map((pages: unknown[]) => {{
    let title: string | null = null;
    const out = [];
    for (let i = 0; i < pages.length; i++) {{
      const r = extractDictBlocks(pages[i] as never,
        {{ pageNum: i + 1, currentTitle: title }});
      title = r.nextTitle;
      out.push({{ sections: r.sections, rawParts: r.rawParts, nextTitle: r.nextTitle }});
    }}
    return out;
  }}),
}}));
"""


def run_deno(payload: dict, timeout: int = 1200) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        cf, rf = os.path.join(tmp, "c.json"), os.path.join(tmp, "r.ts")
        with open(cf, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        with open(rf, "w", encoding="utf-8") as f:
            f.write(RUNNER_TS)
        proc = subprocess.run(
            ["deno", "run", "--config", DENO_CONFIG, "--allow-all", rf, cf],
            capture_output=True, text=True, timeout=timeout,
        )
    if proc.returncode != 0:
        raise SystemExit(f"deno 실행 실패:\n{proc.stderr[:4000]}")
    return json.loads(proc.stdout)


def jsonable(o):
    """PyMuPDF dict 를 JSON 으로 넘기기 위한 최소 손질.

    image block 의 `image` 는 원본 바이트라 직렬화가 안 된다. **구조는 그대로 두고**
    bytes 만 길이 표기로 바꾼다 — 파서가 쓰는 필드(`type`/`bbox`/`lines`/`spans`/
    `size`/`text`)를 골라내는 방식은 "파서가 뭘 쓰는지" 에 대한 내 가정을 대조에
    끼워 넣게 되므로 피한다.
    """
    if isinstance(o, bytes):
        return f"<bytes:{len(o)}>"
    if isinstance(o, dict):
        return {k: jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    return o


def sec_to_dict(s):
    return {"text": s.text, "page": s.page, "section_title": s.section_title,
            "bbox": list(s.bbox) if s.bbox else None, "metadata": s.metadata}


def main() -> None:
    from statistics import median as py_median

    import fitz

    import app.adapters.impl.pymupdf_parser as P

    # --- 실자산 dict 수집 ---
    asset_pages: list[list[dict]] = []
    asset_names: list[str] = []
    for rel in ASSETS:
        full = os.path.join(ROOT, rel)
        if not os.path.exists(full):
            print(f"  (자산 없음, 건너뜀) {rel}")
            continue
        doc = fitz.open(full)
        n = doc.page_count
        idxs = (list(range(n)) if n <= MAX_PAGES_PER_ASSET
                else [round(k * (n - 1) / (MAX_PAGES_PER_ASSET - 1))
                      for k in range(MAX_PAGES_PER_ASSET)])
        pages = [jsonable(P._get_page_dict(doc[i])) for i in idxs]
        doc.close()
        asset_pages.append(pages)
        asset_names.append(f"{os.path.basename(rel)} ({len(pages)}/{n}p)")

    ts = run_deno({
        "median": MEDIAN_CASES,
        "heading": HEADING_CASES,
        "blocks": BLOCK_CASES,
        "assets": asset_pages,
    })

    fails = 0

    # --- median ---
    want = [float(py_median(xs)) if xs else 0.0 for xs in MEDIAN_CASES]
    bad = [i for i, (a, b) in enumerate(zip(want, ts["median"])) if a != b]
    if bad:
        fails += 1
        print(f"  **median {len(bad)}건 불일치** {[(MEDIAN_CASES[i], want[i], ts['median'][i]) for i in bad[:3]]}")
    else:
        print(f"  median                    {len(MEDIAN_CASES)}건 OK")

    # --- heading ---
    want = [P._is_heading_block(bm, pm, t) for bm, pm, t in HEADING_CASES]
    bad = [i for i, (a, b) in enumerate(zip(want, ts["heading"])) if a != b]
    if bad:
        fails += 1
        print(f"  **isHeadingBlock {len(bad)}건 불일치** 인덱스 {bad[:8]}")
        for i in bad[:6]:
            bm, pm, t = HEADING_CASES[i]
            print(f"    [{i}] max={bm} med={pm} {t!r:<44} py={want[i]} ts={ts['heading'][i]}")
    else:
        print(f"  isHeadingBlock            {len(HEADING_CASES)}건 OK  "
              f"(True {sum(want)} / False {len(want) - sum(want)})")
    if not (0 < sum(want) < len(want)):
        fails += 1
        print("    **케이스 무효** — True/False 한쪽만 나왔다")

    # --- 블록 → 섹션 ---
    n_bad = 0
    for i, d in enumerate(BLOCK_CASES):
        secs: list = []
        raws: list = []
        nt = P._extract_dict_blocks(d, page_num=7, current_title="이전제목",
                                    sections=secs, raw_parts=raws)
        w = {"sections": [sec_to_dict(s) for s in secs], "rawParts": raws,
             "nextTitle": nt, "pageMedian": P._page_median_size(d)}
        g = ts["blocks"][i]
        if w != g:
            n_bad += 1
            fails += 1
            print(f"  **extractDictBlocks [{i}] 불일치**")
            for k in w:
                if w[k] != g.get(k):
                    print(f"      {k:<12} py={json.dumps(w[k], ensure_ascii=False)[:170]}")
                    print(f"      {'':<12} ts={json.dumps(g.get(k), ensure_ascii=False)[:170]}")
    if n_bad == 0:
        print(f"  extractDictBlocks         {len(BLOCK_CASES)}건 OK")

    # --- 실자산 ---
    print()
    print("  === 실자산 (PyMuPDF dict 를 그대로 양쪽에 먹임) ===")
    tot_sec = tot_head = 0
    for ai, pages in enumerate(asset_pages):
        title = None
        bad_pages = []
        n_sec = n_head = 0
        for pi, d in enumerate(pages):
            secs: list = []
            raws: list = []
            title = P._extract_dict_blocks(d, page_num=pi + 1, current_title=title,
                                           sections=secs, raw_parts=raws)
            w = {"sections": [sec_to_dict(s) for s in secs], "rawParts": raws,
                 "nextTitle": title}
            g = ts["assets"][ai][pi]
            n_sec += len(secs)
            n_head += sum(1 for s in secs if s.section_title)
            if w != g:
                bad_pages.append(pi)
                if len(bad_pages) <= 2:
                    for k in w:
                        if w[k] != g.get(k):
                            print(f"    **{asset_names[ai]} p{pi + 1} {k} 불일치**")
                            print(f"      py={json.dumps(w[k], ensure_ascii=False)[:200]}")
                            print(f"      ts={json.dumps(g.get(k), ensure_ascii=False)[:200]}")
        tot_sec += n_sec
        tot_head += n_head
        mark = "OK" if not bad_pages else f"**{len(bad_pages)}p 불일치**"
        if bad_pages:
            fails += 1
        print(f"    {asset_names[ai]:<52} 섹션 {n_sec:>5}  title 있음 {n_head:>5}  {mark}")
    print(f"    합계 섹션 {tot_sec:,} / title 부여 {tot_head:,}")
    if tot_sec == 0 or tot_head == 0:
        fails += 1
        print("    **케이스 무효** — 섹션 또는 heading 이 하나도 안 나왔다")

    print()
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
