"""vision 에 실제로 들어가는 바이트를 원본과 대조.

원본 경로:
    `page.get_pixmap(dpi=150)` → `.tobytes("png")` → `ImageParser._normalize`
    (PIL 디코드 → EXIF transpose → LANCZOS 단변 1024 → JPEG q85 optimize)

포팅 경로:
    `renderPageForVision` — mupdf 렌더 → `resizeRgbLanczos` → `Pixmap#asJPEG(85)`

## 무엇이 같아야 하고 무엇이 다를 수밖에 없는가
- **크기·mime**: 같아야 한다. Gemini 의 타일링·토큰 비용이 여기서 정해진다.
- **픽셀**: 축소 결과는 이미 바이트 일치를 확인했다(`verify_image_normalize_parity.py`).
  남는 차이는 **JPEG 인코더**뿐이다 — Pillow 는 libjpeg 를 `optimize=True` 로,
  mupdf 는 자체 기본값으로 부른다. 같은 q85 라도 바이트는 다르다.
- 그래서 여기서는 **디코드 후 픽셀**을 비교한다. 인코더가 달라도 q85 양자화가 같으면
  디코드 결과도 같다. 다르면 얼마나 다른지를 수치로 남긴다.

사용:
    api/.venv/bin/python api/scripts/verify_vision_input_parity.py
"""

from __future__ import annotations

import io
import json
import math
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SHARED = os.path.join(ROOT, "supabase", "functions", "_shared")
DENO_CONFIG = os.path.join(ROOT, "supabase", "functions", "deno.json")

TARGETS = [
    ("assets/public/law sample3.pdf", [0, 1]),
    ("assets/public/sample-report.pdf", [0, 4]),
    ("assets/public/(붙임2) 2025년 데이터센터 산업 활성화 지원 사업 통합_안내서.pdf", [0, 3]),
    ("assets/private/arXiv 영어 학술.pdf", [0, 2]),
    ("assets/private/[삼성전자]사업보고서(2026.03.10).pdf", [0, 50]),
]

RUNNER_TS = """
import { renderPageForVision } from "file://%(shared)s/ingest/pdf_raster.ts";

// deno-lint-ignore no-explicit-any
const mupdf = await import("mupdf") as any;
const cfg = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const out = [];
for (const job of cfg.targets) {
  const doc = mupdf.Document.openDocument(await Deno.readFile(job.path), "application/pdf");
  for (const p of job.pages) {
    if (p >= doc.countPages()) continue;
    const t0 = performance.now();
    const r = renderPageForVision(mupdf, doc, p, 150);
    const ms = performance.now() - t0;
    const key = `${job.key}_${p}`;
    await Deno.writeFile(`${cfg.outDir}/${key}.jpg`, r.jpeg);
    out.push({ key, w: r.width, h: r.height, mime: r.mimeType, bytes: r.jpeg.length, ms });
  }
  doc.destroy?.();
}
console.log(JSON.stringify(out));
""" % {"shared": SHARED}


def main() -> None:
    import fitz
    from app.adapters.impl.image_parser import _normalize

    jobs, flat = [], []
    for i, (rel, pages) in enumerate(TARGETS):
        if not os.path.exists(os.path.join(ROOT, rel)):
            continue
        jobs.append({"key": f"k{i}", "path": os.path.join(ROOT, rel), "pages": pages})
        flat.extend((rel, p, f"k{i}_{p}") for p in pages)
    if not jobs:
        raise SystemExit("자산이 없다")

    with tempfile.TemporaryDirectory() as tmp:
        cf, rf = os.path.join(tmp, "c.json"), os.path.join(tmp, "r.ts")
        with open(cf, "w", encoding="utf-8") as f:
            json.dump({"targets": jobs, "outDir": tmp}, f)
        with open(rf, "w", encoding="utf-8") as f:
            f.write(RUNNER_TS)
        proc = subprocess.run(
            ["deno", "run", "--config", DENO_CONFIG, "--allow-all", rf, cf],
            capture_output=True, text=True, timeout=1800,
        )
        if proc.returncode != 0:
            raise SystemExit(f"deno 실행 실패:\n{proc.stderr[:4000]}")
        rows = {r["key"]: r
                for r in json.loads(
                    [l for l in proc.stdout.splitlines() if l.startswith("[")][-1])}

        from PIL import Image
        fails = 0
        for rel, page_no, key in flat:
            r = rows.get(key)
            if r is None:
                continue
            doc = fitz.open(os.path.join(ROOT, rel))
            png = doc[page_no].get_pixmap(dpi=150).tobytes("png")
            doc.close()
            # 원본 함수를 그대로 호출한다 — 재구현하면 그게 곧 대조 대상이 아니다.
            py_bytes, py_mime, _w = _normalize(png, "image/png")

            py_img = Image.open(io.BytesIO(py_bytes)).convert("RGB")
            with open(os.path.join(tmp, key + ".jpg"), "rb") as f:
                ts_img = Image.open(io.BytesIO(f.read())).convert("RGB")

            size_ok = (py_img.width, py_img.height) == (ts_img.width, ts_img.height)
            mime_ok = py_mime == r["mime"]
            if not (size_ok and mime_ok):
                fails += 1

            note = "-"
            if size_ok:
                a, b = py_img.tobytes(), ts_img.tobytes()
                diff = sum(1 for x, y in zip(a, b) if x != y)
                se = sum((x - y) ** 2 for x, y in zip(a, b))
                mse = se / len(a)
                psnr = float("inf") if mse == 0 else 10 * math.log10(255 * 255 / mse)
                note = (f"디코드 후 다른 바이트 {diff:,}/{len(a):,} ({diff/len(a):.2%}) "
                        f"PSNR {psnr:.1f}dB")

            print(f"  {os.path.basename(rel)[:26]:<28} p{page_no:<4} "
                  f"py {py_img.width}x{py_img.height} {len(py_bytes)//1024}KB {py_mime} | "
                  f"ts {ts_img.width}x{ts_img.height} {r['bytes']//1024}KB {r['mime']} "
                  f"{r['ms']:.0f}ms")
            print(f"      {'크기·mime 일치' if size_ok and mime_ok else '**크기/mime 불일치**'}  {note}")

    print()
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(ROOT, "api"))
    main()
