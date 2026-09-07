"""PDF **페이지 분할이 결과를 바꾸지 않는지** 실자산으로 확인.

## 이게 분할 설계의 성립 조건이다
워커는 대형 PDF 를 `PDF_PAGES_PER_TASK` 페이지씩 나눠 처리한다. 나눠서 처리한 결과가
한 번에 처리한 것과 **완전히 같아야** 한다. 다르면 문서 크기에 따라 청크가 달라진다.

가장 깨지기 쉬운 것은 `current_title` 이다. 문서 전체 sticky 라서, 범위 경계에서
직전 제목을 안 넘기면 그 뒤 섹션의 제목이 통째로 어긋난다. 그래서 분할 크기를 여러 개
써서 **경계 위치를 옮겨 가며** 확인한다 — 한 가지 크기로만 재면 우연히 맞을 수 있다.

## 음성 대조도 같이 돈다
`carryTitle` 을 끊었을 때 실제로 깨지는지 본다. 안 깨지면 이 검사가 아무것도
증명하지 못한다는 뜻이다.

사용:
    api/.venv/bin/python api/scripts/verify_pdf_page_split.py
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

# 경계를 여러 곳에 두려고 서로 배수가 아닌 값을 섞는다.
SPLITS = [1, 3, 10, 7]
MAX_PAGES = 60

ASSETS = [
    "assets/public/law_sample2.pdf",
    "assets/public/law sample3.pdf",
    "assets/public/보건의료_빅데이터_플랫폼_시범사업_추진계획(안).pdf",
    "assets/public/sample-report.pdf",
    "assets/private/arXiv 영어 학술.pdf",
    "assets/private/[SK]사업보고서(2026.03.18).pdf",
]

RUNNER_TS = f"""
import {{ extractPdfRange }} from "file://{SHARED}/ingest/pdf_open.ts";
import {{ runChunkStage }} from "file://{SHARED}/ingest/chunk_records.ts";

const ENV = {{
  captionPrefixEnabled: false, synonymInjectionEnabled: false, synonymLlmEnabled: false,
}};
const enc = new TextEncoder();
async function sha16(t: string) {{
  const h = await crypto.subtle.digest("SHA-256", enc.encode(t));
  return [...new Uint8Array(h)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 16);
}}

/** 워커가 하듯 `step` 페이지씩 순차로 돌린다. `carry=false` 면 title 인계를 끊는다. */
async function run(bytes: Uint8Array, maxPages: number, step: number, carry: boolean) {{
  const sections = [];
  let title: string | null = null;
  let from = 0;
  let total = Infinity;
  let tasks = 0;
  while (from < Math.min(total, maxPages)) {{
    // **`maxPages` 로 잘라야 한다.** step 만 주면 통합 경로(step=전체)가 maxPages 를
    // 넘겨 읽어 분할 경로와 비교 대상이 달라진다(처음에 이렇게 짜서 SK 가 1,513p 대
    // 60p 로 붙었다).
    const count = Math.min(step, maxPages - from);
    const r = await extractPdfRange(bytes, {{
      from, count, carryTitle: carry ? title : null,
    }});
    total = r.totalPages;
    if (r.processed === 0) break;
    title = r.nextTitle;
    sections.push(...r.sections);
    from += r.processed;
    tasks++;
  }}
  const recs = runChunkStage({{ docId: "DOC", sections: sections as never, env: ENV }});
  const rows = [];
  for (const r of recs) {{
    rows.push([
      r.chunk_idx, r.page, await sha16(r.text),
      r.section_title ? await sha16(r.section_title) : null,
    ]);
  }}
  return {{ tasks, sections: sections.length, chunks: rows.length,
           digest: await sha16(JSON.stringify(rows)) }};
}}

const inp = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const out = [];
for (const job of inp.jobs) {{
  const bytes = await Deno.readFile(job.path);
  const whole = await run(bytes, job.maxPages, 1e9, true);
  const splits: Record<string, unknown> = {{}};
  for (const step of inp.splits) splits[String(step)] = await run(bytes, job.maxPages, step, true);
  // 음성 대조 — title 인계를 끊는다. 여기서 깨져야 이 검사가 의미를 갖는다.
  const noCarry = await run(bytes, job.maxPages, inp.splits[0], false);
  out.push({{ whole, splits, noCarry }});
}}
console.log(JSON.stringify(out));
"""


def main() -> None:
    jobs = []
    for rel in ASSETS:
        full = os.path.join(ROOT, rel)
        if os.path.exists(full):
            jobs.append({"path": full, "maxPages": MAX_PAGES, "rel": rel})
        else:
            print(f"  (자산 없음) {rel}")

    with tempfile.TemporaryDirectory() as tmp:
        jf, rf = os.path.join(tmp, "j.json"), os.path.join(tmp, "r.ts")
        with open(jf, "w", encoding="utf-8") as f:
            json.dump({"jobs": [{"path": j["path"], "maxPages": j["maxPages"]} for j in jobs],
                       "splits": SPLITS}, f)
        with open(rf, "w", encoding="utf-8") as f:
            f.write(RUNNER_TS)
        proc = subprocess.run(
            ["deno", "run", "--config", DENO_CONFIG, "--allow-all", rf, jf],
            capture_output=True, text=True, timeout=3000,
        )
    if proc.returncode != 0:
        raise SystemExit(f"deno 실행 실패:\n{proc.stderr[:4000]}")
    res = json.loads(proc.stdout)

    fails = 0
    broke = 0
    print(f"  분할 크기 {SPLITS} 를 통합 처리와 대조한다")
    print()
    print(f"  {'자산':<40} {'청크':>6} {'통합 digest':>18}  {'분할 결과':>26}")
    print("  " + "-" * 96)
    for j, r in zip(jobs, res):
        w = r["whole"]
        cells = []
        for step in SPLITS:
            s = r["splits"][str(step)]
            ok = s["digest"] == w["digest"] and s["chunks"] == w["chunks"]
            if not ok:
                fails += 1
            cells.append(f"{step}p:{'=' if ok else '**X**'}({s['tasks']})")
        print(f"  {os.path.basename(j['rel'])[:40]:<40} {w['chunks']:>6} {w['digest']:>18}  "
              + " ".join(cells))
        for step in SPLITS:
            s = r["splits"][str(step)]
            if s["digest"] != w["digest"]:
                print(f"      **{step}p 분할 불일치** 청크 {w['chunks']} vs {s['chunks']}  "
                      f"섹션 {w['sections']} vs {s['sections']}  digest {s['digest']}")

        # 음성 대조
        if r["noCarry"]["digest"] != w["digest"]:
            broke += 1
        else:
            print(f"      **음성 대조 무효** — title 인계를 끊어도 결과가 같다 "
                  f"(이 문서엔 sticky title 이 없다는 뜻)")

    print()
    print(f"  음성 대조: title 인계를 끊으면 {broke}/{len(jobs)} 문서가 깨진다")
    if broke == 0:
        fails += 1
        print("    **케이스 무효** — 어느 문서도 안 깨지면 이 검사는 아무것도 증명하지 못한다")

    print()
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
