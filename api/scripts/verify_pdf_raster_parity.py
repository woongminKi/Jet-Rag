"""PDF 페이지 래스터화를 Python 원본과 대조.

## 왜 픽셀까지 보는가
vision 은 이 이미지를 그대로 Gemini 에 넘긴다. 렌더가 다르면 **OCR 텍스트가 달라지고**
그건 청크 내용까지 전파된다. 크기만 맞고 내용이 다르면 대조를 통과해도 소용없다.

## 비교 방법
양쪽이 만든 PNG 를 **PyMuPDF 로 다시 열어** 원시 픽셀(RGB)을 비교한다.
PNG 인코더가 달라 바이트가 달라도 픽셀이 같으면 같은 그림이다.

## 미리 아는 차이
MuPDF 버전이 다르다 — PyMuPDF 1.27.2 vs npm mupdf 1.27.0(§23). 픽셀이 완전히 같을
거라 가정하지 않고 **차이를 재서** 보고한다.

사용:
    api/.venv/bin/python api/scripts/verify_pdf_raster_parity.py
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

sys.path.insert(0, os.path.join(ROOT, "api"))

# 성격이 다른 페이지를 고른다 — 텍스트 위주 / 표 / 이미지 많은 것.
TARGETS = [
    ("assets/public/law sample3.pdf", [0, 1]),
    ("assets/public/보건의료_빅데이터_플랫폼_시범사업_추진계획(안).pdf", [0, 5]),
    ("assets/public/sample-report.pdf", [0, 7]),
    ("assets/private/[삼성전자]사업보고서(2026.03.10).pdf", [0, 100]),
]
DPI = 150

RUNNER_TS = f"""
import {{ renderPages }} from "file://{SHARED}/ingest/pdf_raster.ts";

const jobs = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const out = [];
for (const job of jobs.targets) {{
  const bytes = await Deno.readFile(job.path);
  const t0 = performance.now();
  const rendered = await renderPages(bytes, job.pages, jobs.dpi);
  const ms = performance.now() - t0;
  const rows = [];
  for (const idx of job.pages) {{
    const r = rendered.get(idx);
    if (!r) {{ rows.push(null); continue; }}
    const outPath = `${{jobs.outDir}}/${{job.key}}_${{idx}}.png`;
    await Deno.writeFile(outPath, r.png);
    rows.push({{ width: r.width, height: r.height, bytes: r.png.length, path: outPath }});
  }}
  out.push({{ key: job.key, ms, rows }});
}}
console.log(JSON.stringify(out));
"""


def main() -> None:
    import fitz

    targets = [(p, pages) for p, pages in TARGETS
               if os.path.exists(os.path.join(ROOT, p))]
    if not targets:
        raise SystemExit("자산이 없다")

    with tempfile.TemporaryDirectory() as tmp:
        payload = {
            "dpi": DPI,
            "outDir": tmp,
            "targets": [
                {"key": f"a{i}", "path": os.path.join(ROOT, p), "pages": pages}
                for i, (p, pages) in enumerate(targets)
            ],
        }
        cf, rf = os.path.join(tmp, "c.json"), os.path.join(tmp, "r.ts")
        with open(cf, "w", encoding="utf-8") as f:
            json.dump(payload, f)
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
        print(f"  DPI {DPI} (zoom {DPI / 72:.4f})")
        print()
        for i, (rel, pages) in enumerate(targets):
            doc = fitz.open(os.path.join(ROOT, rel))
            name = os.path.basename(rel)[:32]
            for k, idx in enumerate(pages):
                if idx >= doc.page_count:
                    continue
                got = ts[i]["rows"][k]
                if got is None:
                    fails += 1
                    print(f"  **{name} p{idx}: ts 가 렌더 안 함**")
                    continue

                # --- Python 원본 렌더 ---
                pix = doc[idx].get_pixmap(dpi=DPI)
                py_w, py_h = pix.width, pix.height

                # --- ts PNG 를 다시 열어 픽셀 비교 ---
                ts_pix = fitz.Pixmap(got["path"])
                same_size = (py_w == ts_pix.width and py_h == ts_pix.height)

                pixel_note = "-"
                if same_size:
                    a, b = pix.samples, ts_pix.samples
                    if len(a) != len(b):
                        pixel_note = f"**샘플 길이 다름** {len(a)} vs {len(b)}"
                        fails += 1
                    else:
                        diff = sum(1 for x, y in zip(a, b) if x != y)
                        max_d = max((abs(x - y) for x, y in zip(a, b)), default=0)
                        ratio = diff / len(a) if a else 0.0
                        pixel_note = (f"다른 바이트 {diff:,}/{len(a):,} ({ratio:.4%}) "
                                      f"최대차 {max_d}")
                        # 완전 일치가 아니면 그 사실을 드러낸다. 임계는 두지 않는다 —
                        # 얼마나 다른지를 먼저 본다.
                else:
                    fails += 1

                print(f"  {name:<32} p{idx:<4} "
                      f"py {py_w}x{py_h}  ts {ts_pix.width}x{ts_pix.height}  "
                      f"{'크기일치' if same_size else '**크기다름**'}  {pixel_note}")
                ts_pix = None
            doc.close()
            print(f"    렌더 시간(ts, {len(pages)}p): {ts[i]['ms']:.0f}ms")

    print()
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
