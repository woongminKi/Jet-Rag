"""mupdf 버전별로 PyMuPDF 와 얼마나 가까운지 **자산 전 범위**로 채점.

## 왜 다시 하는가
Phase 0 S2 는 7 페이지로 재고 `mupdf@1.27.0` 을 골랐다("1.28.0 은 블록을 병합해
어긋난다"). 범위를 넓히니 1.27.0 에서도 블록 경계가 갈린다 — 7 페이지가 좁았다.

기준선 PyMuPDF 1.27.2 는 MuPDF **1.27.2** 인데 npm 에는 1.27.0 다음이 1.28.0 이라
**패치 버전을 맞출 수가 없다.** 그래서 "어느 쪽이 더 가까운가" 를 실측으로 정한다.

## 채점 기준
페이지마다 블록 단위 (type, 텍스트) 목록을 만들어 PyMuPDF 와 비교한다. 텍스트가
일치하는지가 본질이고 블록 묶음 경계도 함께 본다.

사용:
    api/.venv/bin/python api/scripts/spike_pdf_version_compare.py
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

sys.path.insert(0, os.path.join(ROOT, "api"))

VERSIONS = ["1.27.0", "1.28.1"]
ASSETS = [
    "assets/public/law_sample2.pdf",
    "assets/public/law sample3.pdf",
    "assets/public/보건의료_빅데이터_플랫폼_시범사업_추진계획(안).pdf",
    "assets/public/(붙임2) 2025년 데이터센터 산업 활성화 지원 사업 통합_안내서.pdf",
    "assets/public/sample-report.pdf",
    "assets/private/arXiv 영어 학술.pdf",
    "assets/private/[삼성전자]사업보고서(2026.03.10).pdf",
    "assets/private/[SK]사업보고서(2026.03.18).pdf",
]
MAX_PAGES = 40

RUNNER_TS = """
import { STEXT_OPTS, toPageDict } from "file://%SHARED%/pdf_dict.ts";
const mupdf = await import("mupdf") as any;
const jobs = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const out = [];
for (const job of jobs) {
  const doc = mupdf.Document.openDocument(await Deno.readFile(job.path), "application/pdf");
  const pages = [];
  for (let i = 0; i < Math.min(doc.countPages(), job.n); i++) {
    const page = doc.loadPage(i);
    const st = page.toStructuredText(STEXT_OPTS);
    const d = toPageDict(st, page.getBounds());
    pages.push(d.blocks.map((b: any) => [
      b.type,
      (b.lines ?? []).map((l: any) => (l.spans ?? []).map((s: any) => s.text ?? "").join(""))
        .filter((x: string) => x).join("\\n"),
    ]));
    st.destroy?.(); page.destroy?.();
  }
  doc.destroy?.();
  out.push(pages);
}
console.log(JSON.stringify(out));
""".replace("%SHARED%", SHARED)


def py_blocks(page_dict) -> list:
    out = []
    for b in page_dict.get("blocks", []):
        if b.get("type", 0) != 0:
            out.append([1, ""])
            continue
        lines = ["".join(sp.get("text", "") for sp in ln.get("spans", []))
                 for ln in b.get("lines", [])]
        out.append([0, "\n".join(x for x in lines if x)])
    return out


def main() -> None:
    import fitz
    import app.adapters.impl.pymupdf_parser as P

    print(f"  기준선: PyMuPDF {fitz.version[0]} (MuPDF {fitz.version[1]})")
    print(f"  STEXT_OPTS 는 pdf_dict.ts 의 현재 값을 그대로 쓴다")
    print()

    jobs = [{"path": os.path.join(ROOT, a), "n": MAX_PAGES}
            for a in ASSETS if os.path.exists(os.path.join(ROOT, a))]
    names = [os.path.basename(j["path"]) for j in jobs]

    # --- PyMuPDF 기준값 ---
    want = []
    for j in jobs:
        doc = fitz.open(j["path"])
        want.append([py_blocks(P._get_page_dict(doc[i]))
                     for i in range(min(doc.page_count, j["n"]))])
        doc.close()

    results: dict[str, list] = {}
    for ver in VERSIONS:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = os.path.join(tmp, "deno.json")
            with open(cfg, "w") as f:
                json.dump({"imports": {"mupdf": f"npm:mupdf@{ver}"}}, f)
            with open(os.path.join(tmp, "j.json"), "w") as f:
                json.dump([{"path": x["path"], "n": x["n"]} for x in jobs], f)
            with open(os.path.join(tmp, "r.ts"), "w") as f:
                f.write(RUNNER_TS)
            proc = subprocess.run(
                ["deno", "run", "--config", cfg, "--allow-all",
                 os.path.join(tmp, "r.ts"), os.path.join(tmp, "j.json")],
                capture_output=True, text=True, timeout=3000,
            )
        if proc.returncode != 0:
            print(f"  mupdf@{ver} 실행 실패: {proc.stderr.strip().splitlines()[-1][:120]}")
            continue
        results[ver] = json.loads(proc.stdout)

    # --- 채점 ---
    hdr = "  " + "자산".ljust(44) + "  " + "  ".join(f"{v:>18}" for v in results)
    print(hdr)
    print("  " + "-" * (44 + 20 * len(results)))
    totals = {v: [0, 0, 0, 0] for v in results}   # [일치p, 전체p, 블록수차, 텍스트불일치p]
    for ai, name in enumerate(names):
        cells = []
        for v, all_pages in results.items():
            same = bad_txt = dblk = 0
            pages = all_pages[ai]
            for w, g in zip(want[ai], pages):
                if w == g:
                    same += 1
                else:
                    dblk += abs(len(w) - len(g))
                    if [x[1] for x in w if x[0] == 0] != [x[1] for x in g if x[0] == 0]:
                        bad_txt += 1
            n = len(want[ai])
            totals[v][0] += same
            totals[v][1] += n
            totals[v][2] += dblk
            totals[v][3] += bad_txt
            cells.append(f"{same}/{n}p Δblk{dblk} txt✗{bad_txt}".rjust(18))
        print(f"  {name[:44]:<44}  " + "  ".join(cells))

    print()
    for v, (same, n, dblk, badtxt) in totals.items():
        print(f"  mupdf@{v:<8} 블록목록 완전일치 {same}/{n}p ({same / n * 100:.1f}%)  "
              f"블록수 총차이 {dblk}  텍스트 다른 페이지 {badtxt}")

    if len(totals) == 2:
        a, b = list(totals)
        print()
        better = a if totals[a][0] > totals[b][0] else b
        print(f"  → PyMuPDF 에 더 가까운 쪽: **mupdf@{better}**")


if __name__ == "__main__":
    main()
