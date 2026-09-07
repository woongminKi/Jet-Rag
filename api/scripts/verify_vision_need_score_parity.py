"""`vision_need_score` 를 Python 원본과 대조.

## 왜 이게 중요한가
이 판정이 갈리면 **vision 을 태울 페이지가 달라진다.** 그건 곧 비용(Gemini 호출)과
청크 내용이 달라진다는 뜻이고, 검색 결과까지 전파된다.

## 대조 방식
`verify_pdf_extract_parity.py` 와 같다 — 실제 PDF 의 `page.get_text("dict")` 를
**양쪽에 그대로 먹여** `pdf_dict.ts` 변환 품질과 분리하고 판정 로직만 본다.
합성 케이스로 각 신호의 경계도 태운다.

사용:
    api/.venv/bin/python api/scripts/verify_vision_need_score_parity.py
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SHARED = os.path.join(ROOT, "supabase", "functions", "_shared")
DENO_CONFIG = os.path.join(ROOT, "supabase", "functions", "deno.json")

sys.path.insert(0, os.path.join(ROOT, "api"))

ASSETS = [
    "assets/public/law sample3.pdf",
    "assets/public/보건의료_빅데이터_플랫폼_시범사업_추진계획(안).pdf",
    "assets/public/(붙임2) 2025년 데이터센터 산업 활성화 지원 사업 통합_안내서.pdf",
    "assets/public/sample-report.pdf",
    "assets/private/arXiv 영어 학술.pdf",
    "assets/private/[삼성전자]사업보고서(2026.03.10).pdf",
    "assets/private/[SK]사업보고서(2026.03.18).pdf",
]
MAX_PAGES = 25


def sp(text, bbox=None):
    return {"text": text, "bbox": bbox or [0.0, 0.0, 10.0, 10.0], "size": 10.0}


def ln(spans, bbox=None):
    return {"bbox": bbox or [0.0, 0.0, 10.0, 10.0], "spans": spans}


def bl(lines, type=0, bbox=None):
    b = {"type": type, "bbox": bbox or [0.0, 0.0, 10.0, 10.0]}
    if lines is not None:
        b["lines"] = lines
    return b


# (설명, page_dict, page_area_pt2)
SYNTH: list[tuple[str, dict, float]] = [
    ("빈 페이지", {"blocks": []}, 1000.0),
    ("면적 0", {"blocks": [bl([ln([sp("본문")])])]}, 0.0),
    ("면적 음수", {"blocks": [bl([ln([sp("본문")])])]}, -5.0),
    # --- density 경계: chars/area == 1e-3 ---
    ("density 정확히 1e-3", {"blocks": [bl([ln([sp("가" * 10)])])]}, 10000.0),
    ("density 1e-3 미만", {"blocks": [bl([ln([sp("가" * 9)])])]}, 10000.0),
    ("density 충분", {"blocks": [bl([ln([sp("가" * 500)])])]}, 10000.0),
    # --- table_like: span 3+ ---
    ("span 3개 = 열", {"blocks": [bl([ln([sp("a"), sp("b"), sp("c")])])]}, 100.0),
    ("span 2개 = 열 아님", {"blocks": [bl([ln([sp("a"), sp("b")])])]}, 100.0),
    # --- v2 fallback: single span 다중 공백 ---
    ("공백 2칸 분리 3열", {"blocks": [bl([ln([sp("가  나  다")])])]}, 100.0),
    ("공백 1칸 = 미분리", {"blocks": [bl([ln([sp("가 나 다")])])]}, 100.0),
    ("탭 분리", {"blocks": [bl([ln([sp("가\t나\t다")])])]}, 100.0),
    ("NBSP 2칸", {"blocks": [bl([ln([sp("가  나  다")])])]}, 100.0),
    ("U+001C 2칸 (Python 만 공백)",
     {"blocks": [bl([ln([sp("가나다")])])]}, 100.0),
    ("U+FEFF 2칸 (JS 만 공백)",
     {"blocks": [bl([ln([sp("가﻿﻿나﻿﻿다")])])]}, 100.0),
    # --- v3 block align: line 3+ & x bucket 3+ ---
    ("block align 3버킷", {"blocks": [bl([
        ln([sp("a", [0.0, 0, 5, 5])]), ln([sp("b", [50.0, 0, 55, 5])]),
        ln([sp("c", [100.0, 0, 105, 5])]),
    ])]}, 100.0),
    ("block align 2버킷", {"blocks": [bl([
        ln([sp("a", [0.0, 0, 5, 5])]), ln([sp("b", [0.0, 0, 5, 5])]),
        ln([sp("c", [50.0, 0, 55, 5])]),
    ])]}, 100.0),
    ("x tol 4pt 경계 (차 4.0)", {"blocks": [bl([
        ln([sp("a", [0.0, 0, 5, 5])]), ln([sp("b", [4.0, 0, 9, 5])]),
        ln([sp("c", [8.0, 0, 13, 5])]),
    ])]}, 100.0),
    ("x tol 초과 (차 4.1)", {"blocks": [bl([
        ln([sp("a", [0.0, 0, 5, 5])]), ln([sp("b", [4.1, 0, 9, 5])]),
        ln([sp("c", [8.2, 0, 13, 5])]),
    ])]}, 100.0),
    ("bbox 없는 line 섞임", {"blocks": [bl([
        ln([{"text": "a"}]), ln([sp("b", [50.0, 0, 55, 5])]),
        ln([sp("c", [100.0, 0, 105, 5])]),
    ])]}, 100.0),
    # --- caption ---
    ("캡션 [표 1]", {"blocks": [bl([ln([sp("[표 1] 매출 추이")])])]}, 100.0),
    ("캡션 <그림 2>", {"blocks": [bl([ln([sp("<그림 2>")])])]}, 100.0),
    ("캡션 Figure 3", {"blocks": [bl([ln([sp("Figure 3")])])]}, 100.0),
    ("캡션 Fig. 4", {"blocks": [bl([ln([sp("Fig. 4")])])]}, 100.0),
    ("캡션 표 1-2", {"blocks": [bl([ln([sp("표 1-2")])])]}, 100.0),
    ("캡션 도 1 (P1 fix)", {"blocks": [bl([ln([sp("도 1")])])]}, 100.0),
    ("캡션 사진 1", {"blocks": [bl([ln([sp("사진 1")])])]}, 100.0),
    ("캡션 Photo 1", {"blocks": [bl([ln([sp("Photo 1")])])]}, 100.0),
    ("오탐 '그림 좋다'", {"blocks": [bl([ln([sp("그림 좋다")])])]}, 100.0),
    ("오탐 '표면 처리'", {"blocks": [bl([ln([sp("표면 처리")])])]}, 100.0),
    ("오탐 '사진작가'", {"blocks": [bl([ln([sp("사진작가")])])]}, 100.0),
    ("캡션 80자 경계", {"blocks": [bl([ln([sp("표 1 " + "가" * 75)])])]}, 100.0),
    ("캡션 81자 초과", {"blocks": [bl([ln([sp("표 1 " + "가" * 76)])])]}, 100.0),
    ("캡션 이모지 80코드포인트", {"blocks": [bl([ln([sp("표 1 " + "🙂" * 75)])])]}, 100.0),
    ("대소문자 figure 3", {"blocks": [bl([ln([sp("figure 3")])])]}, 100.0),
    # --- image_area ---
    ("이미지 30% 정확히",
     {"blocks": [bl(None, type=1, bbox=[0.0, 0.0, 30.0, 10.0]), bl([ln([sp("본문")])])]}, 1000.0),
    ("이미지 29%",
     {"blocks": [bl(None, type=1, bbox=[0.0, 0.0, 29.0, 10.0]), bl([ln([sp("본문")])])]}, 1000.0),
    ("이미지 bbox 3개 (무효)",
     {"blocks": [{"type": 1, "bbox": [0.0, 0.0, 30.0]}, bl([ln([sp("본문")])])]}, 1000.0),
    ("이미지 음수 크기",
     {"blocks": [bl(None, type=1, bbox=[30.0, 10.0, 0.0, 0.0]), bl([ln([sp("본문")])])]}, 1000.0),
    # --- text_quality ---
    ("정상 한글", {"blocks": [bl([ln([sp("가" * 100)])])]}, 100.0),
    ("PUA 깨짐 100%", {"blocks": [bl([ln([sp("" * 100)])])]}, 100.0),
    ("PUA 60% (경계 아래)",
     {"blocks": [bl([ln([sp("" * 60 + "가" * 40)])])]}, 100.0),
    ("PUA 정확히 60%",
     {"blocks": [bl([ln([sp("" * 6 + "가" * 4)])])]}, 100.0),
    ("공백만", {"blocks": [bl([ln([sp("   ")])])]}, 100.0),
    ("한자", {"blocks": [bl([ln([sp("漢字" * 50)])])]}, 100.0),
    ("라틴 보충", {"blocks": [bl([ln([sp("éàü" * 30)])])]}, 100.0),
    ("한국어 문장부호", {"blocks": [bl([ln([sp("「가」·나、" * 20)])])]}, 100.0),
    # --- entity (판정엔 안 쓰이지만 composite 에 영향) ---
    ("엔티티 [표 1]", {"blocks": [bl([ln([sp("[표 1]" + "가" * 200)])])]}, 100.0),
    ("엔티티 Eq. (3)", {"blocks": [bl([ln([sp("Eq. (3)" + "가" * 200)])])]}, 100.0),
    ("엔티티 식 (5)", {"blocks": [bl([ln([sp("식 (5)" + "가" * 200)])])]}, 100.0),
    # --- 빈 span / 공백 span ---
    ("빈 span 만", {"blocks": [bl([ln([sp("")])])]}, 100.0),
    ("공백 span 만", {"blocks": [bl([ln([sp("  ")])])]}, 100.0),
    ("lines 없는 text block", {"blocks": [bl(None)]}, 100.0),
    ("spans 없는 line", {"blocks": [bl([{"bbox": [0, 0, 1, 1]}])]}, 100.0),
]

RUNNER_TS = f"""
import {{ scorePage }} from "file://{SHARED}/ingest/vision_need_score.ts";

const input = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const out = {{
  synth: input.synth.map(([, dict, area]: [string, unknown, number], i: number) =>
    scorePage(dict as never, {{ pageNum: i + 1, pageAreaPt2: area }})),
  assets: input.assets.map((pages: [unknown, number][]) =>
    pages.map(([dict, area], i) =>
      scorePage(dict as never, {{ pageNum: i + 1, pageAreaPt2: area }}))),
}};
console.log(JSON.stringify(out));
"""


def to_dict(ps) -> dict:
    return {
        "page": ps.page, "text_chars": ps.text_chars, "page_area_pt2": ps.page_area_pt2,
        "text_density": ps.text_density, "entity_hits": ps.entity_hits,
        "table_like_score": ps.table_like_score, "needs_vision": ps.needs_vision,
        "image_area_ratio": ps.image_area_ratio, "text_quality": ps.text_quality,
        "caption_score": ps.caption_score, "composite_score": ps.composite_score,
        "triggers": list(ps.triggers),
    }


def close(a, b, tol=1e-9) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        return abs(float(a) - float(b)) <= tol
    return a == b


def main() -> None:
    import fitz

    from app.services.vision_need_score import score_page
    import app.adapters.impl.pymupdf_parser as P

    sys.path.insert(0, HERE)
    from verify_pdf_extract_parity import jsonable

    # --- 실자산 dict 수집 ---
    asset_pages: list[list] = []
    names: list[str] = []
    for rel in ASSETS:
        full = os.path.join(ROOT, rel)
        if not os.path.exists(full):
            continue
        doc = fitz.open(full)
        n = min(doc.page_count, MAX_PAGES)
        pages = []
        for i in range(n):
            page = doc[i]
            area = float(page.rect.width * page.rect.height)
            pages.append([jsonable(P._get_page_dict(page)), area])
        doc.close()
        asset_pages.append(pages)
        names.append(f"{os.path.basename(rel)[:34]} ({n}p)")

    with tempfile.TemporaryDirectory() as tmp:
        cf, rf = os.path.join(tmp, "c.json"), os.path.join(tmp, "r.ts")
        with open(cf, "w", encoding="utf-8") as f:
            json.dump({"synth": SYNTH, "assets": asset_pages}, f)
        with open(rf, "w", encoding="utf-8") as f:
            f.write(RUNNER_TS)
        proc = subprocess.run(
            ["deno", "run", "--config", DENO_CONFIG, "--allow-all", rf, cf],
            capture_output=True, text=True, timeout=1200,
        )
    if proc.returncode != 0:
        raise SystemExit(f"deno 실행 실패:\n{proc.stderr[:4000]}")
    ts = json.loads(proc.stdout)

    fails = 0
    keys = ["text_chars", "text_density", "entity_hits", "table_like_score",
            "needs_vision", "image_area_ratio", "text_quality", "caption_score",
            "composite_score", "triggers"]

    # --- 합성 ---
    bad = []
    py_synth = []
    for i, (label, dict_, area) in enumerate(SYNTH):
        want = to_dict(score_page(dict_, page_num=i + 1, page_area_pt2=area))
        py_synth.append(want)
        got = ts["synth"][i]
        diff = {k: (want[k], got.get(k)) for k in keys if not close(want[k], got.get(k))}
        if diff:
            bad.append((label, diff))
    if bad:
        fails += 1
        print(f"  **합성 {len(bad)}건 불일치**")
        for label, diff in bad[:8]:
            print(f"    {label}")
            for k, (a, b) in diff.items():
                print(f"      {k:<20} py={a!r}  ts={b!r}")
    else:
        n_need = sum(1 for w in py_synth if w["needs_vision"])
        print(f"  합성 케이스                {len(SYNTH)}건 OK  "
              f"(needs_vision True {n_need} / False {len(SYNTH) - n_need})")

    # 케이스 무효 검사 — 각 trigger 가 최소 한 번은 켜져야 한다.
    seen = set()
    for w in py_synth:
        seen.update(w["triggers"])
    for t in ("low_density", "table_like", "image_area", "text_quality_low", "caption"):
        if t not in seen:
            fails += 1
            print(f"    **케이스 무효** — trigger `{t}` 를 한 번도 안 태웠다")

    # --- 실자산 ---
    print()
    print("  === 실자산 (PyMuPDF dict 를 그대로 양쪽에 먹임) ===")
    tot_pages = tot_need = 0
    for ai, pages in enumerate(asset_pages):
        n_bad = 0
        n_need = 0
        for pi, (d, area) in enumerate(pages):
            want = to_dict(score_page(d, page_num=pi + 1, page_area_pt2=area))
            got = ts["assets"][ai][pi]
            if want["needs_vision"]:
                n_need += 1
            diff = {k: (want[k], got.get(k)) for k in keys if not close(want[k], got.get(k))}
            if diff:
                n_bad += 1
                if n_bad <= 2:
                    print(f"    **{names[ai]} p{pi + 1} 불일치**")
                    for k, (a, b) in diff.items():
                        print(f"      {k:<20} py={a!r}  ts={b!r}")
        tot_pages += len(pages)
        tot_need += n_need
        if n_bad:
            fails += 1
        print(f"    {names[ai]:<42} {len(pages):>3}p  vision 필요 {n_need:>3}  "
              f"{'OK' if not n_bad else f'**{n_bad}p 불일치**'}")
    print(f"    합계 {tot_pages}p / vision 필요 {tot_need}p")
    if tot_need == 0 or tot_need == tot_pages:
        fails += 1
        print("    **케이스 무효** — 실자산에서 판정이 한쪽으로만 나왔다")

    print()
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
