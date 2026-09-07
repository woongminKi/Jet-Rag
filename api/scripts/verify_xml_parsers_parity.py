"""ZIP/XML 파서 4 종(`docx` · `pptx` · `hwpx` · `hwpml`)을 원본 파서와 대조.

Phase 0 이 만든 `ooxml_text.ts` · `hwp_xml_text.ts` 를 인제스트 형태로 이은 뒤,
**실제 파일**로 `ExtractionResult` 전체를 비교한다.

## 무엇을 비교하나
`source_type` · `raw_text` · `warnings` · `metadata` · 섹션 전부
(`text` / `page` / `section_title` / `bbox` / `metadata`).

`section_title` 은 sticky propagate 라 한 곳이 어긋나면 뒤가 전부 밀린다 — 검색에서
"이 청크가 어느 절에 속하는가" 가 통째로 달라지므로 텍스트만 보면 안 된다.

## PPTX 는 Vision 없이 비교한다
원본 `PptxParser` 는 텍스트가 짧은 슬라이드에 Vision OCR 을 돌린다. 그 경로는 아직
안 옮겼으므로 `image_parser=None` 으로 만든 파서와 비교한다 — **텍스트 경로만** 같은지
보는 것이고, Vision 차이는 별도로 기록한다.

사용:
    api/.venv/bin/python api/scripts/verify_xml_parsers_parity.py
    api/.venv/bin/python api/scripts/verify_xml_parsers_parity.py --negative
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
FIX = os.path.join(HERE, "fixtures")

# (경로, 종류)
TARGETS = [
    (os.path.join(FIX, "spike_sample.docx"), "docx"),
    (os.path.join(FIX, "spike_sample.pptx"), "pptx"),
    (os.path.join(FIX, "spike_sample.hwpx"), "hwpx"),
    (os.path.join(FIX, "spike_sample_hwpml.hwp"), "hwpml"),
    (os.path.join(ROOT, "assets/public/직제_규정(2024.4.30.개정).hwpx"), "hwpx"),
    (os.path.join(ROOT, "assets/public/한마음생활체육관_운영_내규(2024.4.30.개정).hwpx"), "hwpx"),
]

RUNNER_TS = """
import {
  extractDocxResult, extractHwpmlResult, extractHwpxResult, extractPptxResult,
} from "file://%(shared)s/ingest/xml_extract.ts";
import { isHwpmlBytes } from "file://%(shared)s/documents/hwpml_sniff.ts";

const cfg = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const NEG = cfg.negative === true;

const out = [];
for (const job of cfg.targets) {
  const bytes = await Deno.readFile(job.path);
  const fn = job.kind === "docx"
    ? extractDocxResult
    : job.kind === "pptx"
    ? extractPptxResult
    : job.kind === "hwpx"
    ? extractHwpxResult
    : extractHwpmlResult;
  try {
    const r = fn(bytes);
    out.push({
      kind: job.kind,
      sniffHwpml: isHwpmlBytes(bytes.subarray(0, 4096)),
      source_type: r.source_type,
      raw_text: r.raw_text,
      warnings: r.warnings,
      metadata: r.metadata,
      sections: r.sections,
    });
  } catch (e) {
    out.push({ kind: job.kind, error: String(e) });
  }
}
if (NEG && out[0]?.sections?.length) out[0].sections[0].section_title = "변조";
await Deno.writeTextFile(Deno.args[1], JSON.stringify(out));
""" % {"shared": SHARED}


def main() -> None:
    negative = "--negative" in sys.argv

    from app.adapters.impl.docx_parser import DocxParser
    from app.adapters.impl.hwpml_parser import HwpmlParser, is_hwpml_bytes
    from app.adapters.impl.hwpx_parser import HwpxParser
    from app.adapters.impl.pptx_parser import PptxParser

    targets = [(p, k) for p, k in TARGETS if os.path.exists(p)]
    if not targets:
        raise SystemExit("대상 파일이 없다")

    parsers = {
        "docx": DocxParser(),
        # Vision 경로는 아직 이식 전 — 텍스트 경로만 비교한다.
        "pptx": PptxParser(image_parser=None),
        "hwpx": HwpxParser(),
        "hwpml": HwpmlParser(),
    }

    py_out = []
    for path, kind in targets:
        with open(path, "rb") as f:
            data = f.read()
        r = parsers[kind].parse(data, file_name=os.path.basename(path))
        py_out.append({
            "kind": kind,
            "sniffHwpml": is_hwpml_bytes(data[:4096]),
            "source_type": r.source_type,
            "raw_text": r.raw_text,
            "warnings": list(r.warnings),
            "metadata": dict(r.metadata or {}),
            "sections": [{
                "text": s.text, "page": s.page, "section_title": s.section_title,
                "bbox": list(s.bbox) if s.bbox else None,
                "metadata": dict(s.metadata),
            } for s in r.sections],
        })

    with tempfile.TemporaryDirectory() as tmp:
        cf, rf = os.path.join(tmp, "c.json"), os.path.join(tmp, "r.ts")
        of = os.path.join(tmp, "out.json")
        with open(cf, "w", encoding="utf-8") as f:
            json.dump({
                "targets": [{"path": p, "kind": k} for p, k in targets],
                "negative": negative,
            }, f)
        with open(rf, "w", encoding="utf-8") as f:
            f.write(RUNNER_TS)
        proc = subprocess.run(
            ["deno", "run", "--config", DENO_CONFIG, "--allow-all", rf, cf, of],
            capture_output=True, text=True, timeout=900,
        )
        if proc.returncode != 0:
            raise SystemExit(f"deno 실행 실패:\n{proc.stderr[:4000]}")
        with open(of, encoding="utf-8") as f:
            ts = json.load(f)

    fails: list[str] = []
    checks = 0

    def cmp(label, a, b):
        nonlocal checks
        checks += 1
        if a != b:
            fails.append(f"{label}\n      py={a!r}\n      ts={b!r}")

    for (path, kind), py, t in zip(targets, py_out, ts):
        name = os.path.basename(path)[:34]
        if "error" in t:
            fails.append(f"{name}: ts 예외 {t['error']}")
            print(f"  {name:<36} **ts 예외**")
            continue
        cmp(f"{name} source_type", py["source_type"], t["source_type"])
        cmp(f"{name} sniff_hwpml", py["sniffHwpml"], t["sniffHwpml"])
        cmp(f"{name} 섹션수", len(py["sections"]), len(t["sections"]))
        for i, (a, b) in enumerate(zip(py["sections"], t["sections"])):
            cmp(f"{name} sections[{i}]", a, b)
        cmp(f"{name} raw_text", py["raw_text"], t["raw_text"])
        cmp(f"{name} warnings", py["warnings"], t["warnings"])
        cmp(f"{name} metadata", py["metadata"], t["metadata"])
        titled = sum(1 for s in py["sections"] if s["section_title"])
        ok = all(f.split("\n")[0].split(" ")[0] != name for f in fails)
        print(f"  {name:<36} {kind:<6} 섹션 py {len(py['sections']):>4} / "
              f"ts {len(t['sections']):>4}  title 있음 {titled:>4}  "
              f"{'일치' if ok else '**불일치**'}")

    for f in fails[:8]:
        print(f"  **{f}**")
    print(f"  비교 {checks}건, 불일치 {len(fails)}건")
    if negative:
        print("음성 대조: " + ("검사기 정상 (변조 검출)" if fails else "**검사기가 못 잡는다**"))
        sys.exit(0 if fails else 1)
    print("FAIL 0" if not fails else f"FAIL {len(fails)}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(ROOT, "api"))
    main()
