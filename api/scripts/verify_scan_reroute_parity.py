"""`vision_scan.ts` 를 `_reroute_pdf_to_image` 와 대조.

## 스캔 PDF 를 만들어서 쓴다
저장소에 텍스트 레이어 없는 PDF 가 없다. `law_sample2.pdf` 를 150 DPI 로 렌더해
그 **이미지만** 넣은 PDF 를 만든다(결정적이라 커밋할 필요가 없다). 실제로
`get_text()` 가 0 자를 뱉는지도 확인한다 — 안 그러면 스캔 경로를 안 탄다.

## 창 분할이 결과를 바꾸지 않는지도 본다
TS 는 태스크당 4 페이지씩 돈다. 한 번에 전부 돌린 것과 창으로 나눠 이어 붙인 것이
같아야 한다.

## 노린 함정 — enrich 와 다른 규칙들
- 섹션 제목이 `p.N` 이다 (`(vision) p.N` 아님)
- `metadata` 를 **버린다** (원본이 `ExtractedSection` 에 안 넘긴다)
- 제목 합성 순서: 원본은 base 를 strip 하지 않고 붙인 뒤 전체를 strip 한다
- sweep·needs_vision·cap·캐시가 전부 없다
- 페이지 상한 5, 임계 50 자

사용:
    api/.venv/bin/python api/scripts/verify_scan_reroute_parity.py
    api/.venv/bin/python api/scripts/verify_scan_reroute_parity.py --negative
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

SOURCE_PDF = "assets/public/law_sample2.pdf"
# 5 페이지 상한을 실제로 넘겨 본다.
SCAN_PAGE_COUNT = 7
PAGES_PER_TASK = 4

# `_is_scan_pdf` 임계 케이스 — 50 자 경계와 유니코드 공백.
THRESHOLD_CASES = [
    "", "   ", "\n\n\t", "  ",
    "가" * 49, "가" * 50, "가" * 51,
    "  " + "가" * 50 + "  ",          # strip 후 50 → 스캔
    "  " + "가" * 51 + "  ",
    "a" * 50, "a" * 51,
    "😀" * 50,                        # 코드포인트 50 (UTF-16 으로는 100)
    "😀" * 51,
    "\x1c" + "가" * 50 + "\x1f",      # Python strip 이 지우는 제어문자
]

RUNNER_TS = """
import { isScanPdf, MAX_SCAN_PAGES, runScanWindow }
  from "file://%(shared)s/ingest/vision_scan.ts";
import type { VisionCaption } from "file://%(shared)s/ingest/vision_caption.ts";
import { countPdfPages } from "file://%(shared)s/ingest/pdf_raster.ts";

const cfg = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const NEG = cfg.negative === true;
const bytes = new Uint8Array(cfg.pdf);

// 캐시·메트릭을 끄고 client 는 안 쓴다.
const env: Record<string, string | undefined> = { JET_RAG_METRICS_PERSIST_ENABLED: "0" };
// deno-lint-ignore no-explicit-any
const client: any = { from: () => ({ insert: () => Promise.resolve({ error: null }) }) };

function fakeCaption(seq: number): VisionCaption {
  return {
    type: (["표", "그림", "문서", "기타"] as const)[seq %% 4],
    ocr_text: seq %% 3 === 0 ? "" : `OCR 본문 ${seq}`,
    caption: `캡션 ${seq}`,
    structured: seq %% 5 === 0 ? { action_items: [`할 일 ${seq}`] } : null,
    usage: null,
    table_caption: seq %% 2 === 0 ? `표 캡션 ${seq}` : null,
    figure_caption: seq %% 7 === 0 ? `그림 캡션 ${seq}` : null,
  };
}

const total = await countPdfPages(bytes);
const processCount = Math.min(total, MAX_SCAN_PAGES);

// ① 한 번에 전부
let seq = 0;
const all = await runScanWindow(
  { client, env, geminiApiKey: "unused", nowMs: 0,
    caption: () => Promise.resolve(fakeCaption(seq++)) },
  { bytes, docId: "d1", fileName: "scan.pdf",
    pages: Array.from({ length: processCount }, (_, i) => i) },
);

// ② 창으로 나눠서
seq = 0;
const win = { sections: [] as unknown[], rawParts: [] as string[], warnings: [] as string[] };
for (let from = 0; from < processCount; from += cfg.pagesPerTask) {
  const pages = [];
  for (let p = from; p < Math.min(processCount, from + cfg.pagesPerTask); p++) pages.push(p);
  const r = await runScanWindow(
    { client, env, geminiApiKey: "unused", nowMs: 0,
      caption: () => Promise.resolve(fakeCaption(seq++)) },
    { bytes, docId: "d1", fileName: "scan.pdf", pages },
  );
  win.sections.push(...r.sections);
  win.rawParts.push(...r.rawParts);
  win.warnings.push(...r.warnings);
}

// 이 경고는 `runScanWindow` 가 아니라 **핸들러**(handlers/scan.ts)가 만든다.
// 여기서는 창 함수만 부르므로 하네스가 같은 자리에 넣어 준다.
const capWarn = total > MAX_SCAN_PAGES
  ? [`스캔 PDF ${total}페이지 중 첫 ${MAX_SCAN_PAGES}페이지만 처리 (Vision API 비용 cap)`]
  : [];

const out = {
  total, processCount,
  all: { sections: all.sections, rawText: all.rawParts.join("\\n\\n"),
         warnings: [...capWarn, ...all.warnings], called: all.calledCount },
  windowed: { sections: win.sections, rawText: win.rawParts.join("\\n\\n"),
              warnings: win.warnings },
  thresholds: cfg.thresholdCases.map((t: string) => isScanPdf(t)),
};
if (NEG) out.all.sections[0].section_title += "!";
await Deno.writeTextFile(Deno.args[1], JSON.stringify(out));
""" % {"shared": SHARED}


def build_scan_pdf(fitz, src_path: str, pages: int) -> bytes:
    """텍스트 레이어가 없는 PDF — 원본 페이지를 이미지로 구워 넣는다."""
    src = fitz.open(src_path)
    out = fitz.open()
    for i in range(pages):
        pix = src[i % len(src)].get_pixmap(dpi=150)
        page = out.new_page(width=pix.width * 72 / 150, height=pix.height * 72 / 150)
        page.insert_image(page.rect, pixmap=pix)
    data = out.tobytes()
    out.close()
    src.close()
    return data


def main() -> None:
    import fitz

    from app.adapters.impl.image_parser import ImageParser
    from app.adapters.vision import VisionCaption
    from app.ingest.stages import extract as EX

    negative = "--negative" in sys.argv
    os.environ["JETRAG_VISION_CACHE_ENABLED"] = "0"
    os.environ["JET_RAG_METRICS_PERSIST_ENABLED"] = "0"

    src = os.path.join(ROOT, SOURCE_PDF)
    if not os.path.exists(src):
        raise SystemExit(f"원본 자산이 없다: {SOURCE_PDF}")
    pdf = build_scan_pdf(fitz, src, SCAN_PAGE_COUNT)

    # **검출기의 입력을 먼저 본다** — 정말 텍스트가 없는가.
    with fitz.open(stream=pdf, filetype="pdf") as d:
        text_len = sum(len(d[i].get_text().strip()) for i in range(len(d)))
    print(f"  만든 스캔 PDF: {SCAN_PAGE_COUNT}페이지, 추출 텍스트 {text_len}자 "
          f"({'스캔으로 판정됨' if text_len <= 50 else '**텍스트가 남아 있다**'})")
    if text_len > 50:
        raise SystemExit("스캔 PDF 를 못 만들었다 — 대조가 무의미하다")

    counter = {"n": 0}

    class FakeCaptioner:
        def caption(self, image_bytes: bytes, *, mime_type: str) -> VisionCaption:
            seq = counter["n"]
            counter["n"] += 1
            types_ = ["표", "그림", "문서", "기타"]
            return VisionCaption(
                type=types_[seq % 4],
                ocr_text="" if seq % 3 == 0 else f"OCR 본문 {seq}",
                caption=f"캡션 {seq}",
                structured={"action_items": [f"할 일 {seq}"]} if seq % 5 == 0 else None,
                usage=None,
                table_caption=f"표 캡션 {seq}" if seq % 2 == 0 else None,
                figure_caption=f"그림 캡션 {seq}" if seq % 7 == 0 else None,
            )

    py = EX._reroute_pdf_to_image(
        pdf,
        file_name="scan.pdf",
        image_parser=ImageParser(captioner=FakeCaptioner()),
        doc_id="d1",
    )
    py_sections = [{
        "text": s.text, "page": s.page, "section_title": s.section_title,
        "bbox": list(s.bbox) if s.bbox else None, "metadata": dict(s.metadata),
    } for s in py.sections]
    py_thresholds = [EX._is_scan_pdf(
        type("R", (), {"raw_text": t})()  # `_is_scan_pdf` 는 raw_text 만 본다
    ) for t in THRESHOLD_CASES]

    with tempfile.TemporaryDirectory() as tmp:
        cf, rf = os.path.join(tmp, "c.json"), os.path.join(tmp, "r.ts")
        of = os.path.join(tmp, "out.json")
        with open(cf, "w", encoding="utf-8") as f:
            json.dump({
                "pdf": list(pdf), "pagesPerTask": PAGES_PER_TASK,
                "thresholdCases": THRESHOLD_CASES, "negative": negative,
            }, f)
        with open(rf, "w", encoding="utf-8") as f:
            f.write(RUNNER_TS)
        proc = subprocess.run(
            ["deno", "run", "--config", DENO_CONFIG, "--allow-all", rf, cf, of],
            capture_output=True, text=True, timeout=1200,
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

    cmp("처리 페이지 수 (상한 5)", 5, ts["processCount"])
    cmp("섹션 수", len(py_sections), len(ts["all"]["sections"]))
    for i, (a, b) in enumerate(zip(py_sections, ts["all"]["sections"])):
        cmp(f"sections[{i}]", a, b)
    cmp("raw_text", py.raw_text, ts["all"]["rawText"])
    cmp("warnings", list(py.warnings), ts["all"]["warnings"])
    # 창으로 나눠도 같아야 한다.
    cmp("창 분할 — 섹션", ts["all"]["sections"], ts["windowed"]["sections"])
    cmp("창 분할 — raw_text", ts["all"]["rawText"], ts["windowed"]["rawText"])
    for t, a, b in zip(THRESHOLD_CASES, py_thresholds, ts["thresholds"]):
        cmp(f"is_scan_pdf({t[:12]!r}… len={len(t)})", a, b)

    print(f"  섹션 py {len(py_sections)} / ts {len(ts['all']['sections'])}  "
          f"vision 호출 {ts['all']['called']}  경고 {len(ts['all']['warnings'])}")
    for f in fails[:10]:
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
