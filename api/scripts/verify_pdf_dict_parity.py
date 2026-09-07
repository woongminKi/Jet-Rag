"""`toPageDict` 를 PyMuPDF `page.get_text("dict")` 와 **구조까지** 대조.

## 왜 이게 따로 필요한가
기존 검사기들은 두 부류였다.
- `verify_pdf_extract_parity.py` : PyMuPDF 가 만든 dict 를 **양쪽에 똑같이 먹여** 이후
  로직만 비교 → dict 를 만드는 단계는 안 봤다.
- `verify_pdf_pipeline_baseline.py` : 최종 청크 텍스트만 비교 → span 경계는 텍스트를
  이어 붙이면 사라져서 안 보인다.

그 사이로 실제 결함이 빠져나갔다. `toPageDict` 가 span 을 네이티브 폰트 **포인터**로
갈랐는데, 같은 글꼴의 다른 서브셋 인스턴스(`BCDLEE+` / `BCDEEE+`)가 다른 포인터라
한 줄이 4 조각이 됐다. `vision_need_score` 의 `table_like_score` 는 "span 3 개 이상인
줄" 을 세므로 점수가 0.143 → 0.657 로 부풀었고, **needs_vision 판정이 뒤집혔다**
(sample-report p12). 청크 텍스트는 그대로라 baseline 검사는 통과했다.

## 무엇을 비교하는가
블록 수·타입 → 줄 수 → **줄별 span 수와 span 텍스트·크기**. span 경계가 결과다.

사용:
    api/.venv/bin/python api/scripts/verify_pdf_dict_parity.py
    api/.venv/bin/python api/scripts/verify_pdf_dict_parity.py --negative
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
DENO_CONFIG = os.path.join(ROOT, "supabase", "functions", "deno.json")

# 한글 서브셋 폰트가 섞인 문서를 반드시 포함한다 — 결함이 거기서 났다.
TARGETS = [
    ("assets/public/sample-report.pdf", 30),
    ("assets/public/law sample3.pdf", 4),
    ("assets/public/law_sample2.pdf", 2),
    ("assets/public/(붙임2) 2025년 데이터센터 산업 활성화 지원 사업 통합_안내서.pdf", 25),
    ("assets/public/보건의료_빅데이터_플랫폼_시범사업_추진계획(안).pdf", 20),
    ("assets/private/[삼성전자]사업보고서(2026.03.10).pdf", 25),
    ("assets/private/[SK]사업보고서(2026.03.18).pdf", 25),
    ("assets/private/arXiv 영어 학술.pdf", 20),
]

RUNNER_TS = """
import { pageArea, STEXT_OPTS, toPageDict } from "file://%(shared)s/pdf_dict.ts";
import { scorePage } from "file://%(shared)s/ingest/vision_need_score.ts";

// deno-lint-ignore no-explicit-any
const mupdf = await import("mupdf") as any;
const cfg = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const NEG = cfg.negative === true;

const out = [];
for (const job of cfg.targets) {
  const doc = mupdf.Document.openDocument(await Deno.readFile(job.path), "application/pdf");
  const total = doc.countPages();
  const step = Math.max(1, Math.floor(total / job.sample));
  const pages = [];
  for (let p = 0; p < total && pages.length < job.sample; p += step) {
    const page = doc.loadPage(p);
    const st = page.toStructuredText(STEXT_OPTS);
    const d = toPageDict(st, page.getBounds());
    const sc = scorePage(d, { pageNum: p + 1, pageAreaPt2: pageArea(d) });
    pages.push({
      page: p,
      needsVision: sc.needs_vision,
      triggers: sc.triggers,
      tableLike: sc.table_like_score,
      blocks: d.blocks.map((b) => ({
        type: b.type,
        lines: (b.lines ?? []).map((l) =>
          (l.spans ?? []).map((s) => [s.text, s.size])),
      })),
    });
    st.destroy?.(); page.destroy?.();
  }
  out.push({ path: job.path, pages });
  doc.destroy?.();
}
if (NEG && out[0]?.pages[0]) {
  // span 하나를 둘로 쪼갠다 — 원래 결함과 같은 모양.
  for (const b of out[0].pages[0].blocks) {
    for (const l of b.lines) {
      if (l.length === 1 && l[0][0].length > 3) {
        const [t, sz] = l[0];
        l.splice(0, 1, [t.slice(0, 2), sz], [t.slice(2), sz]);
        break;
      }
    }
  }
}
await Deno.writeTextFile(Deno.args[1], JSON.stringify(out));
""" % {"shared": SHARED}


def main() -> None:
    import fitz

    negative = "--negative" in sys.argv
    jobs = [{"path": os.path.join(ROOT, p), "sample": n}
            for p, n in TARGETS if os.path.exists(os.path.join(ROOT, p))]
    if not jobs:
        raise SystemExit("자산이 없다")

    with tempfile.TemporaryDirectory() as tmp:
        cf, rf = os.path.join(tmp, "c.json"), os.path.join(tmp, "r.ts")
        of = os.path.join(tmp, "out.json")
        with open(cf, "w", encoding="utf-8") as f:
            json.dump({"targets": jobs, "negative": negative}, f)
        with open(rf, "w", encoding="utf-8") as f:
            f.write(RUNNER_TS)
        proc = subprocess.run(
            ["deno", "run", "--config", DENO_CONFIG, "--allow-all", rf, cf, of],
            capture_output=True, text=True, timeout=3600,
        )
        if proc.returncode != 0:
            raise SystemExit(f"deno 실행 실패:\n{proc.stderr[:4000]}")
        with open(of, encoding="utf-8") as f:
            ts = json.load(f)

    from app.services.vision_need_score import score_page

    fails: list[str] = []
    checks = 0
    span_total = 0
    block_diff_pages = 0
    vision_flips = 0
    vision_pages = 0
    flips: list[str] = []

    def cmp(label: str, a, b) -> None:
        nonlocal checks
        checks += 1
        if a != b:
            fails.append(f"{label}\n      py={a!r}\n      ts={b!r}")

    for job, tj in zip(jobs, ts):
        doc = fitz.open(job["path"])
        name = os.path.basename(job["path"])[:30]
        before = len(fails)
        blk = 0
        for tp in tj["pages"]:
            d = doc[tp["page"]].get_text("dict")
            tag = f"{name} p{tp['page'] + 1}"

            # --- 블록 분할은 MuPDF 1.27.0 vs 1.27.2 차이다(§23 에 기록). 여기서는
            #     "다르다" 만 세고 실패로 치지 않는다. 텍스트는 그대로라 최종 청크가
            #     같다는 걸 pdf_known_divergence.json 이 이미 고정해 뒀다.
            # **결론을 정하는 값** — span 경계 차이가 실제로 vision 판정을 뒤집는가.
            page = doc[tp["page"]]
            area = float(page.rect.width) * float(page.rect.height)
            py_sc = score_page(d, page_num=tp["page"] + 1, page_area_pt2=area)
            vision_pages += 1
            if bool(py_sc.needs_vision) != bool(tp["needsVision"]):
                vision_flips += 1
                flips.append(
                    f"{tag} needs_vision py={py_sc.needs_vision} ts={tp['needsVision']} "
                    f"table_like py={py_sc.table_like_score:.3f} ts={tp['tableLike']:.3f} "
                    f"triggers py={py_sc.triggers} ts={tp['triggers']}")

            py_types = [b.get("type", 0) for b in d.get("blocks", [])]
            ts_types = [b["type"] for b in tp["blocks"]]
            if py_types != ts_types:
                blk += 1

            # --- span 경계가 이 검사기의 대상이다. 블록 묶음을 무시하고 줄을 편다.
            #     블록이 합쳐지든 갈라지든 줄의 순서와 내용은 보존되므로 이 비교는
            #     블록 분할 차이에 영향받지 않는다.
            py_lines = [[[sp.get("text", ""), sp.get("size")] for sp in ln.get("spans", [])]
                        for b in d.get("blocks", []) if b.get("type", 0) == 0
                        for ln in b.get("lines", [])]
            ts_lines = [ln for b in tp["blocks"] if b["type"] == 0 for ln in b["lines"]]
            span_total += sum(len(x) for x in py_lines)
            cmp(f"{tag} 줄수", len(py_lines), len(ts_lines))
            for li, (pl, tl) in enumerate(zip(py_lines, ts_lines)):
                cmp(f"{tag} line{li} span수 ({''.join(x[0] for x in pl)[:40]!r})",
                    len(pl), len(tl))
                cmp(f"{tag} line{li} spans", pl, tl)
        doc.close()
        block_diff_pages += blk
        n = len(fails) - before
        print(f"  {name:<32} {len(tj['pages']):>3}p  "
              f"{'span 일치' if n == 0 else f'**span 불일치 {n}건**'}"
              f"{f'  (블록분할 다른 페이지 {blk})' if blk else ''}")

    for f in fails[:6]:
        print(f"  **{f}**")
    print()
    print(f"  비교 {checks}건 (span {span_total:,}개), 불일치 {len(fails)}건")
    print()
    for f in flips[:15]:
        print(f"  ! {f}")

    # --- 기준값 대조 ---
    with open(os.path.join(HERE, "fixtures", "pdf_dict_known_divergence.json"),
              encoding="utf-8") as fh:
        base = json.load(fh)
    got = {"sample_pages": vision_pages, "span_mismatch": len(fails),
           "vision_flips": vision_flips, "block_diff_pages": block_diff_pages}
    print(f"  기준 {{'span':{base['span_mismatch']}, 'flips':{base['vision_flips']}, "
          f"'block':{base['block_diff_pages']}, 'pages':{base['sample_pages']}}}")
    print(f"  실측 {{'span':{got['span_mismatch']}, 'flips':{got['vision_flips']}, "
          f"'block':{got['block_diff_pages']}, 'pages':{got['sample_pages']}}}")
    print(f"  남은 차이의 원인: {base['_원인']}")

    if negative:
        worse = got["span_mismatch"] > base["span_mismatch"]
        print("음성 대조: " + ("검사기 정상 (span 쪼갬 검출)" if worse
                            else "**검사기가 못 잡는다**"))
        sys.exit(0 if worse else 1)

    if got["sample_pages"] != base["sample_pages"]:
        print(f"FAIL — 표본 페이지 수가 바뀌었다({base['sample_pages']} → "
              f"{got['sample_pages']}). 기준값을 다시 재라.")
        sys.exit(1)
    regressed = [k for k in ("span_mismatch", "vision_flips", "block_diff_pages")
                 if got[k] > base[k]]
    if regressed:
        print(f"FAIL — 기준보다 나빠졌다: {', '.join(regressed)}")
        sys.exit(1)
    improved = [k for k in ("span_mismatch", "vision_flips", "block_diff_pages")
                if got[k] < base[k]]
    if improved:
        print(f"개선됨: {', '.join(improved)} — pdf_dict_known_divergence.json 을 갱신하라.")
    print("FAIL 0 (남은 차이는 mupdf.js API 한계로 재현 불가 — 기준값 이내)")
    sys.exit(0)


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(ROOT, "api"))
    main()
