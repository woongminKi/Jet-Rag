"""`image_normalize.ts` 의 LANCZOS 축소를 Pillow 와 대조.

## 축소기만 격리한다
TS 가 mupdf 로 150 DPI 렌더한 **원시 RGB 를 그대로 덤프**하고, Python 은 그 파일을
읽어 Pillow 로 축소한다. 양쪽 입력이 바이트 단위로 같으므로 차이가 나면 그건
**축소기의 차이**다 — 렌더러 차이가 섞이지 않는다.

## 왜 픽셀 단위인가
Gemini Vision 은 비결정적이라 "이미지가 조금 달라도 캡션이 같은가" 를 사후 측정할 수
없다. 모델 입력을 원본과 같게 만들어 그 질문을 없애는 게 목적이므로, 완전 일치가
기준이다.

## 음성 대조
`--negative` 로 TS 계수를 일부러 틀어 이 검사가 실제로 잡는지 확인한다. 안 잡으면
검사기를 믿을 수 없다.

사용:
    api/.venv/bin/python api/scripts/verify_image_normalize_parity.py
    api/.venv/bin/python api/scripts/verify_image_normalize_parity.py --negative
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SHARED = os.path.join(ROOT, "supabase", "functions", "_shared")
DENO_CONFIG = os.path.join(ROOT, "supabase", "functions", "deno.json")

# 세로가 긴 것 / 가로가 긴 것(회전 페이지) / 단변이 이미 1024 이하인 것을 섞는다.
TARGETS = [
    ("assets/public/law sample3.pdf", [0, 1]),
    ("assets/public/sample-report.pdf", [0, 4]),
    ("assets/public/(붙임2) 2025년 데이터센터 산업 활성화 지원 사업 통합_안내서.pdf", [0, 3]),
    ("assets/private/arXiv 영어 학술.pdf", [0, 2]),
    ("assets/private/[삼성전자]사업보고서(2026.03.10).pdf", [0, 50]),
    ("assets/private/[SK]사업보고서(2026.03.18).pdf", [0]),
]
# 축소가 없는 경로(단변 ≤ 1024)도 한 번 태운다 — 72 DPI 로 렌더하면 단변이 595px.
LOW_DPI_CASE = ("assets/public/sample-report.pdf", 0, 72)

RUNNER_TS = """
import { renderPageToPng } from "file://%(shared)s/ingest/pdf_raster.ts";
import { normalizeTarget, resizeRgbLanczos }
  from "file://%(shared)s/ingest/image_normalize.ts";

// deno-lint-ignore no-explicit-any
const mupdf = await import("mupdf") as any;
const cfg = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const NEG = cfg.negative === true;

const out = [];
for (const job of cfg.targets) {
  const doc = mupdf.Document.openDocument(await Deno.readFile(job.path), "application/pdf");
  for (const p of job.pages) {
    if (p >= doc.countPages()) continue;
    const page = doc.loadPage(p);
    const zoom = job.dpi / 72;
    const pix = page.toPixmap(
      mupdf.Matrix.scale(zoom, zoom), mupdf.ColorSpace.DeviceRGB, false, true,
    );
    const w = pix.getWidth(), h = pix.getHeight(), stride = pix.getStride();
    const src = pix.getPixels();
    const t = normalizeTarget(w, h);

    let dst: Uint8Array;
    let ms = 0;
    if (t.resize) {
      const t0 = performance.now();
      dst = resizeRgbLanczos(src, w, h, stride, t.width, t.height);
      ms = performance.now() - t0;
    } else {
      // 축소 없음 — 원본 픽셀을 stride 제거해 그대로 담는다.
      dst = new Uint8Array(w * h * 3);
      for (let y = 0; y < h; y++) {
        dst.set(src.subarray(y * stride, y * stride + w * 3), y * w * 3);
      }
    }
    if (NEG) dst[dst.length >> 1] = (dst[dst.length >> 1] + 7) & 0xff;  // 음성 대조

    // 원본 픽셀도 stride 제거해 덤프 — Python 이 같은 입력을 받게 한다.
    const srcFlat = new Uint8Array(w * h * 3);
    for (let y = 0; y < h; y++) {
      srcFlat.set(src.subarray(y * stride, y * stride + w * 3), y * w * 3);
    }
    const key = `${job.key}_${p}_${job.dpi}`;
    await Deno.writeFile(`${cfg.outDir}/${key}.src`, srcFlat);
    await Deno.writeFile(`${cfg.outDir}/${key}.dst`, dst);
    out.push({ key, src: [w, h], dst: [t.width, t.height], resize: t.resize, ms });
    pix.destroy?.(); page.destroy?.();
  }
  doc.destroy?.();
}
console.log(JSON.stringify(out));
""" % {"shared": SHARED}


def main() -> None:
    from PIL import Image

    negative = "--negative" in sys.argv

    jobs = []
    for i, (rel, pages) in enumerate(TARGETS):
        if os.path.exists(os.path.join(ROOT, rel)):
            jobs.append({"key": f"k{i}", "path": os.path.join(ROOT, rel),
                         "pages": pages, "dpi": 150})
    rel, pg, dpi = LOW_DPI_CASE
    if os.path.exists(os.path.join(ROOT, rel)):
        jobs.append({"key": "low", "path": os.path.join(ROOT, rel),
                     "pages": [pg], "dpi": dpi})
    if not jobs:
        raise SystemExit("자산이 없다")

    with tempfile.TemporaryDirectory() as tmp:
        cfg = {"targets": jobs, "outDir": tmp, "negative": negative}
        cf, rf = os.path.join(tmp, "c.json"), os.path.join(tmp, "r.ts")
        with open(cf, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        with open(rf, "w", encoding="utf-8") as f:
            f.write(RUNNER_TS)
        proc = subprocess.run(
            ["deno", "run", "--config", DENO_CONFIG, "--allow-all", rf, cf],
            capture_output=True, text=True, timeout=1800,
        )
        if proc.returncode != 0:
            raise SystemExit(f"deno 실행 실패:\n{proc.stderr[:4000]}")
        rows = json.loads([l for l in proc.stdout.splitlines() if l.startswith("[")][-1])

        fails = 0
        for r in rows:
            sw, sh = r["src"]
            dw, dh = r["dst"]
            with open(os.path.join(tmp, r["key"] + ".src"), "rb") as f:
                src_raw = f.read()
            with open(os.path.join(tmp, r["key"] + ".dst"), "rb") as f:
                ts_raw = f.read()

            img = Image.frombytes("RGB", (sw, sh), src_raw)
            if r["resize"]:
                py = img.resize((dw, dh), Image.Resampling.LANCZOS)
            else:
                py = img
            py_raw = py.tobytes()

            if len(py_raw) != len(ts_raw):
                fails += 1
                print(f"  **{r['key']}: 길이 다름 py {len(py_raw)} ts {len(ts_raw)}**")
                continue
            diff = sum(1 for a, b in zip(py_raw, ts_raw) if a != b)
            maxd = max((abs(a - b) for a, b in zip(py_raw, ts_raw)), default=0)
            ok = diff == 0
            if not ok:
                fails += 1
            print(f"  {r['key']:<12} {sw}x{sh} → {dw}x{dh} "
                  f"{'축소' if r['resize'] else '원본유지'}  "
                  f"{'일치' if ok else f'**다름 {diff:,}/{len(py_raw):,} 최대차 {maxd}**'}"
                  f"  {r['ms']:.0f}ms")

    print()
    if negative:
        print(f"음성 대조: {fails}/{len(rows)} 케이스에서 차이 검출"
              f" — {'검사기 정상' if fails == len(rows) else '**검사기가 못 잡는다**'}")
        sys.exit(0 if fails == len(rows) else 1)
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
