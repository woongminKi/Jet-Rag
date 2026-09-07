"""`vision_enrich.ts` 결선을 `_enrich_pdf_with_vision` 과 대조.

## 무엇을 스텁으로 두는가
**Gemini 호출만** 양쪽에 같은 함수를 물린다 — 캡션을 페이지 번호에서 결정적으로 만든다.
이미지 바이트로 만들면 안 된다(크로마 서브샘플링 때문에 양쪽 JPEG 이 다르다).

나머지는 전부 실제 코드가 돈다:
- `needs_vision` OR 규칙 (실제 PDF 페이지를 읽어 판정)
- 캐시 조회·적재, 메트릭 적재 (ENV 로 끈다 — 원본과 같은 토글)
- 비용/페이지 cap 판정과 **한국어 메시지**
- 섹션 제목 합성 `(vision) p.N ...`, 페이지 번호, metadata
- `raw_text` 이어붙이기 순서

## 창 분할이 결과를 바꾸지 않는지가 핵심이다
원본은 문서를 한 번에 돌고 포팅은 4 페이지씩 돈다. TS 쪽은 창을 반복 호출해 이어 붙인
뒤 원본과 **같은 섹션 목록이 나오는지**를 본다. 여기가 어긋나면 창 분할 설계가 틀린 것이다.

사용:
    api/.venv/bin/python api/scripts/verify_vision_enrich_parity.py
    api/.venv/bin/python api/scripts/verify_vision_enrich_parity.py --negative
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

# (자산, page cap) — cap 0 은 무한. 작은 cap 은 cap 도달 메시지 경로를 태운다.
CASES = [
    ("assets/public/law sample3.pdf", 0),
    ("assets/public/sample-report.pdf", 0),
    ("assets/public/(붙임2) 2025년 데이터센터 산업 활성화 지원 사업 통합_안내서.pdf", 0),
    ("assets/private/arXiv 영어 학술.pdf", 0),
    ("assets/public/sample-report.pdf", 3),   # page cap 도달
    ("assets/public/sample-report.pdf", 1),   # 첫 페이지에서 즉시 도달
]
MAX_PAGES = 12       # 원본 50 을 줄여 대조를 빠르게 — 창 경계(4)를 여러 번 넘긴다
PAGES_PER_TASK = 4

RUNNER_TS = """
import { emptyCarry, runVisionWindow, type VisionEnv }
  from "file://%(shared)s/ingest/vision_enrich.ts";
import type { VisionCaption } from "file://%(shared)s/ingest/vision_caption.ts";
import { countPdfPages } from "file://%(shared)s/ingest/pdf_raster.ts";

const cfg = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const NEG = cfg.negative === true;

// 캐시·메트릭을 끈다(원본과 같은 토글). 예산 가드는 살려 두고 SUM 만 0 으로 만든다.
const env: Record<string, string | undefined> = {
  JETRAG_VISION_CACHE_ENABLED: "0",
  JET_RAG_METRICS_PERSIST_ENABLED: "0",
};

// 비용 SUM 이 0 인 가짜 client — 판정·메시지는 실제 코드가 만든다.
// deno-lint-ignore no-explicit-any
const q: any = {
  eq: () => q, gte: () => q, in: () => q, limit: () => q, order: () => q,
  then: (res: (v: unknown) => void) => res({ data: [], error: null }),
};
// deno-lint-ignore no-explicit-any
const client: any = {
  from: () => ({ select: () => q, update: () => q, upsert: () => q, insert: () => q }),
};

function fakeCaption(page: number): VisionCaption {
  return {
    // 페이지에서 결정적으로 만든다 — 이미지 바이트를 쓰면 양쪽이 갈린다.
    type: (["표", "그림", "문서", "기타"] as const)[page %% 4],
    ocr_text: page %% 3 === 0 ? "" : `OCR 본문 p${page}`,
    caption: `캡션 p${page}`,
    structured: page %% 5 === 0 ? { action_items: [`할 일 ${page}`] } : null,
    usage: null,
    table_caption: page %% 2 === 0 ? `표 캡션 ${page}` : null,
    figure_caption: page %% 7 === 0 ? `그림 캡션 ${page}` : null,
  };
}

const out = [];
for (const c of cfg.cases) {
  const bytes = await Deno.readFile(c.path);
  const totalPages = await countPdfPages(bytes);
  const processCount = Math.min(totalPages, cfg.maxPages);

  const ve: VisionEnv = {
    enabled: true, maxPages: cfg.maxPages, maxSweeps: 2,
    budgetRecheckEveryNPages: 5, needScoreEnabled: true,
    pageCapPerDoc: c.pageCap, docBudgetUsd: 0.1, dailyBudgetUsd: 0.5,
    sliding24hBudgetUsd: 0.5, geminiApiKey: "unused",
  };

  let carry = emptyCarry();
  const sections = [], rawParts = [];
  // 이 경고는 `runVisionWindow` 가 아니라 **핸들러**(handlers/vision.ts) 가 만든다.
  // 여기서는 창 함수만 부르므로 하네스가 같은 자리에 넣어 준다.
  const warnings = totalPages > cfg.maxPages
    ? [`vision_enrich: ${totalPages}페이지 중 첫 ${cfg.maxPages}페이지만 처리 (paid tier RPM/latency 보호)`]
    : [];
  // Python 쪽 FakeCaptioner 와 같은 규칙 — **호출마다** 증가한다(페이지 번호가 아니다).
  // needs_vision skip 이 있어 페이지 번호와 어긋나는 게 정상이다.
  let callSeq = 0;
  for (let from = 0; from < processCount; from += cfg.pagesPerTask) {
    const r = await runVisionWindow(
      { client, env, visionEnv: ve, nowMs: 0,
        caption: () => Promise.resolve(fakeCaption(callSeq++)) },
      { bytes, jobId: "j", docId: "d", fileName: c.path.split("/").pop(),
        sha256: null,
        pages: Array.from(
          { length: Math.min(cfg.pagesPerTask, processCount - from) }, (_, i) => from + i),
        pendingTotal: processCount, pendingIndexBase: from,
        pageCap: c.pageCap, carry, progressTotal: processCount },
    );
    sections.push(...r.sections);
    rawParts.push(...r.rawParts);
    warnings.push(...r.warnings);
    carry = r.carry;
    if (r.stopped) break;
  }
  out.push({
    path: c.path, pageCap: c.pageCap, sections, rawParts, warnings,
    called: carry.calledCount, completed: carry.completed,
    skipped: carry.skippedByNeedScore,
    budgetExceeded: carry.budgetExceeded, pageCapExceeded: carry.pageCapExceeded,
  });
}

if (NEG) {
  // 첫 케이스는 전 페이지가 needs_vision=false 라 섹션이 0 개다 — 섹션이 있는 걸 고른다.
  const victim = out.find((o) => o.sections.length > 0);
  if (!victim) throw new Error("변조할 섹션이 없다 — 음성 대조가 의미 없다");
  victim.sections[0].section_title += "!";
}
await Deno.writeTextFile(Deno.args[1], JSON.stringify(out));
""" % {"shared": SHARED}


def main() -> None:
    negative = "--negative" in sys.argv

    os.environ["JETRAG_VISION_CACHE_ENABLED"] = "0"
    os.environ["JET_RAG_METRICS_PERSIST_ENABLED"] = "0"
    os.environ["JETRAG_PDF_VISION_ENRICH_MAX_PAGES"] = str(MAX_PAGES)

    from app.adapters.impl.image_parser import ImageParser
    from app.adapters.parser import ExtractionResult
    from app.adapters.vision import VisionCaption
    from app.ingest.stages import extract as ex
    from app.services import budget_guard as bg

    # 원본 상수를 대조 조건에 맞춘다 (ENV 는 import 시점에 읽혀 이미 늦었다).
    ex._VISION_ENRICH_MAX_PAGES = MAX_PAGES

    # 비용 SUM 을 0 으로 — 판정·메시지는 실제 코드가 만든다.
    bg._sum_doc_cost = lambda _d: 0.0            # type: ignore[assignment]
    bg._sum_daily_cost = lambda: 0.0             # type: ignore[assignment]
    bg._sum_24h_sliding_cost = lambda now=None: 0.0  # type: ignore[assignment]

    # 페이지 순서대로 결정적 캡션을 준다 — TS 의 fakeCaption 과 같은 규칙.
    counter = {"n": 0}

    class FakeCaptioner:
        def caption(self, image_bytes: bytes, *, mime_type: str) -> VisionCaption:
            page = counter["n"]
            counter["n"] += 1
            types_ = ["표", "그림", "문서", "기타"]
            return VisionCaption(
                type=types_[page % 4],
                ocr_text="" if page % 3 == 0 else f"OCR 본문 p{page}",
                caption=f"캡션 p{page}",
                structured={"action_items": [f"할 일 {page}"]} if page % 5 == 0 else None,
                usage=None,
                table_caption=f"표 캡션 {page}" if page % 2 == 0 else None,
                figure_caption=f"그림 캡션 {page}" if page % 7 == 0 else None,
            )

    py_out = []
    cases = [(p, cap) for p, cap in CASES if os.path.exists(os.path.join(ROOT, p))]
    if not cases:
        raise SystemExit("자산이 없다")

    for rel, page_cap in cases:
        counter["n"] = 0
        with open(os.path.join(ROOT, rel), "rb") as f:
            data = f.read()
        base = ExtractionResult(source_type="pdf", sections=[], raw_text="", warnings=[])
        result = ex._enrich_pdf_with_vision(
            data,
            base_result=base,
            file_name=os.path.basename(rel),
            image_parser=ImageParser(captioner=FakeCaptioner()),
            job_id=None,          # 진행 표시 skip
            doc_id="d",
            sha256=None,
            client=object(),      # flags 마킹 경로는 여기서 안 탄다(cap 도달 시만)
            page_cap_override=page_cap if page_cap else 0,
        )
        py_out.append({
            "sections": [{
                "text": s.text, "page": s.page, "section_title": s.section_title,
                "bbox": list(s.bbox) if s.bbox else None, "metadata": dict(s.metadata),
            } for s in result.sections],
            "raw_text": result.raw_text,
            "warnings": list(result.warnings),
        })

    with tempfile.TemporaryDirectory() as tmp:
        cf, rf = os.path.join(tmp, "c.json"), os.path.join(tmp, "r.ts")
        of = os.path.join(tmp, "out.json")
        with open(cf, "w", encoding="utf-8") as f:
            json.dump({
                "cases": [{"path": os.path.join(ROOT, p), "pageCap": c} for p, c in cases],
                "maxPages": MAX_PAGES, "pagesPerTask": PAGES_PER_TASK,
                "negative": negative,
            }, f)
        with open(rf, "w", encoding="utf-8") as f:
            f.write(RUNNER_TS)
        proc = subprocess.run(
            ["deno", "run", "--config", DENO_CONFIG, "--allow-all", rf, cf, of],
            capture_output=True, text=True, timeout=3600,
        )
        if proc.returncode != 0:
            raise SystemExit(f"deno 실행 실패:\n{proc.stderr[:5000]}")
        with open(of, encoding="utf-8") as f:
            ts_out = json.load(f)

    fails: list[str] = []
    checks = 0

    def cmp(label: str, a, b) -> None:
        nonlocal checks
        checks += 1
        if a != b:
            fails.append(f"{label}\n      py={a!r}\n      ts={b!r}")

    for (rel, cap), py, ts in zip(cases, py_out, ts_out):
        tag = f"{os.path.basename(rel)[:24]} cap={cap}"
        ts_raw = "\n\n".join(ts["rawParts"])
        cmp(f"{tag} 섹션수", len(py["sections"]), len(ts["sections"]))
        py_pages = sorted({x["page"] for x in py["sections"]})
        ts_pages = sorted({x["page"] for x in ts["sections"]})
        if py_pages != ts_pages:
            print(f"    페이지 집합 다름 py={py_pages} ts={ts_pages} "
                  f"(py만={sorted(set(py_pages)-set(ts_pages))} "
                  f"ts만={sorted(set(ts_pages)-set(py_pages))})")
        for j, (sa, sb) in enumerate(zip(py["sections"], ts["sections"])):
            cmp(f"{tag} sections[{j}]", sa, sb)
        cmp(f"{tag} raw_text", py["raw_text"], ts_raw)
        cmp(f"{tag} warnings", py["warnings"], ts["warnings"])
        print(f"  {tag:<34} 섹션 py {len(py['sections'])} / ts {len(ts['sections'])}  "
              f"called {ts['called']}  skip {len(ts['skipped'])}  "
              f"warn {len(ts['warnings'])}")

    for f in fails[:12]:
        print(f"  **{f}**")
    print()
    print(f"  비교 {checks}건, 불일치 {len(fails)}건")
    if negative:
        print("음성 대조: " + ("검사기 정상 (변조 검출)" if fails else "**검사기가 못 잡는다**"))
        sys.exit(0 if fails else 1)
    print("FAIL 0" if not fails else f"FAIL {len(fails)}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(ROOT, "api"))
    main()
