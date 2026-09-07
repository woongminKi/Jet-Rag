"""PDF 인제스트 파이프라인 전체(mupdf → dict → extract → chunk)를 대조.

`verify_pdf_extract_parity.py` 는 **PyMuPDF dict 를 양쪽에 먹여** 파서 로직만 봤다.
여기서는 TS 쪽이 **mupdf 로 직접 PDF 를 읽는다** — `pdf_dict.ts` 변환까지 사슬 전체가
들어간다. Phase 0 S2 는 7 페이지로 검증했고, 여기서는 실자산 전 범위로 넓힌다.

## 기준선의 vision 을 조심할 것
`fixtures/ingest_baselines/law_sample2.pdf.json` 은 `vision_calls: 2` 다. 즉
`_enrich_pdf_with_vision` 이 섹션을 보탠 뒤의 결과라 **순수 파서 경로와 다를 수 있다.**
vision 은 LLM 호출이라 이번 이식 범위가 아니다. 그래서 세 값을 다 찍고 차이를 드러낸다:

    기준선(vision 포함)  ·  Python vision-OFF  ·  TypeScript

`Python vision-OFF == TypeScript` 가 이번 이식의 합격 조건이고,
기준선과의 차이는 vision 몫으로 분리해 보고한다.

사용:
    api/.venv/bin/python api/scripts/verify_pdf_pipeline_baseline.py
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SHARED = os.path.join(ROOT, "supabase", "functions", "_shared")
DENO_CONFIG = os.path.join(ROOT, "supabase", "functions", "spike", "deno.json")
BASELINE = os.path.join(HERE, "fixtures", "ingest_baselines", "law_sample2.pdf.json")
# 맞출 수 없는 차이의 스냅샷 — 값이 변하면 실패시켜 악화를 잡는다.
KNOWN = os.path.join(HERE, "fixtures", "pdf_known_divergence.json")

# 기준선이 있는 자산 + 대형 자산. 대형은 기준선이 없으니 py↔ts 만 본다.
ASSETS = [
    ("assets/public/law_sample2.pdf", BASELINE),
    ("assets/public/law sample3.pdf", None),
    ("assets/public/보건의료_빅데이터_플랫폼_시범사업_추진계획(안).pdf", None),
    ("assets/public/(붙임2) 2025년 데이터센터 산업 활성화 지원 사업 통합_안내서.pdf", None),
    ("assets/public/sample-report.pdf", None),
    ("assets/private/arXiv 영어 학술.pdf", None),
    ("assets/private/[삼성전자]사업보고서(2026.03.10).pdf", None),
    ("assets/private/[SK]사업보고서(2026.03.18).pdf", None),
]
# 대형 PDF 를 전 페이지 돌리면 대조가 몇 분씩 걸린다. 앞에서부터 자른다 —
# `current_title` 이 문서 전체 sticky 라 **연속 구간**이어야 의미가 있다(표본 추출 불가).
MAX_PAGES = 60

RUNNER_TS = f"""
import {{ STEXT_OPTS, toPageDict }} from "file://{SHARED}/pdf_dict.ts";
import {{ extractDictBlocks }} from "file://{SHARED}/ingest/pdf_extract.ts";
import {{ runChunkStage }} from "file://{SHARED}/ingest/chunk_records.ts";

const mupdf = await import("mupdf") as any;
const jobs = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const enc = new TextEncoder();

async function sha16(t: string) {{
  const h = await crypto.subtle.digest("SHA-256", enc.encode(t));
  return [...new Uint8Array(h)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 16);
}}

const out = [];
for (const job of jobs) {{
  const bytes = await Deno.readFile(job.path);
  const doc = mupdf.Document.openDocument(bytes, "application/pdf");
  const n = Math.min(doc.countPages(), job.maxPages);

  const sections = [];
  const rawParts: string[] = [];
  let title: string | null = null;
  for (let i = 0; i < n; i++) {{
    const page = doc.loadPage(i);
    const st = page.toStructuredText(STEXT_OPTS);
    const dict = toPageDict(st, page.getBounds());
    const r = extractDictBlocks(dict, {{ pageNum: i + 1, currentTitle: title }});
    title = r.nextTitle;
    sections.push(...r.sections);
    rawParts.push(...r.rawParts);
    st.destroy?.();
    page.destroy?.();
  }}
  doc.destroy?.();

  const recs = runChunkStage({{
    docId: "DOC", sections,
    env: {{ captionPrefixEnabled: false, synonymInjectionEnabled: false, synonymLlmEnabled: false }},
  }});
  const rows = [];
  for (const r of recs) {{
    rows.push({{
      chunk_idx: r.chunk_idx, page: r.page,
      has_section_title: Boolean(r.section_title),
      // **title 문자열까지 본다.** bool 만 비교하면 "제목이 있다" 는 같고 내용이 다른
      // 경우를 놓친다 — 실제로 arXiv 가 그렇게 통과했었다.
      title_sha16: r.section_title ? await sha16(r.section_title) : null,
      text_sha16: await sha16(r.text), text_len: [...r.text].length,
      meta_keys: Object.keys(r.metadata).sort(),
      has_bbox: r.bbox !== null, has_char_range: r.char_range != null,
    }});
  }}
  out.push({{
    pages: n, section_count: sections.length,
    raw_text: rawParts.join("\\n\\n"), rows,
    digest: await sha16(rows.map((r) => r.text_sha16).join("|")),
  }});
}}
console.log(JSON.stringify(out));
"""


def sha16(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()[:16]


def main() -> None:
    sys.path.insert(0, os.path.join(ROOT, "api"))
    import fitz

    import app.ingest.stages.chunk as C
    from app.adapters.parser import ExtractedSection, ExtractionResult
    import app.adapters.impl.pymupdf_parser as P

    jobs = []
    for rel, base in ASSETS:
        full = os.path.join(ROOT, rel)
        if os.path.exists(full):
            jobs.append({"path": full, "maxPages": MAX_PAGES, "rel": rel, "base": base})
        else:
            print(f"  (자산 없음) {rel}")

    with tempfile.TemporaryDirectory() as tmp:
        jf, rf = os.path.join(tmp, "j.json"), os.path.join(tmp, "r.ts")
        with open(jf, "w", encoding="utf-8") as f:
            json.dump([{"path": j["path"], "maxPages": j["maxPages"]} for j in jobs], f)
        with open(rf, "w", encoding="utf-8") as f:
            f.write(RUNNER_TS)
        proc = subprocess.run(
            ["deno", "run", "--config", DENO_CONFIG, "--allow-all", rf, jf],
            capture_output=True, text=True, timeout=3000,
        )
    if proc.returncode != 0:
        raise SystemExit(f"deno 실행 실패:\n{proc.stderr[:4000]}")
    ts_all = json.loads(proc.stdout)

    keys = ["chunk_idx", "page", "has_section_title", "title_sha16",
            "text_sha16", "text_len", "meta_keys", "has_bbox", "has_char_range"]
    fails = 0
    observed: dict = {}

    for j, ts in zip(jobs, ts_all):
        # --- Python: vision 없이 순수 파서 → chunk ---
        doc = fitz.open(j["path"])
        n = min(doc.page_count, j["maxPages"])
        sections: list = []
        raw_parts: list = []
        title = None
        for i in range(n):
            title = P._extract_dict_blocks(
                P._get_page_dict(doc[i]), page_num=i + 1, current_title=title,
                sections=sections, raw_parts=raw_parts)
        doc.close()
        py_recs = C._to_chunk_records(
            doc_id="DOC",
            sections=C._merge_short_sections(C._split_long_sections(sections)))
        py_rows = [{
            "chunk_idx": r.chunk_idx, "page": r.page,
            "has_section_title": bool(r.section_title),
            "title_sha16": sha16(r.section_title) if r.section_title else None,
            "text_sha16": sha16(r.text), "text_len": len(r.text),
            "meta_keys": sorted(r.metadata.keys()),
            "has_bbox": r.bbox is not None,
            "has_char_range": r.char_range is not None,
        } for r in py_recs]
        py_digest = sha16("|".join(r["text_sha16"] for r in py_rows))

        name = os.path.basename(j["rel"])
        total_pages = fitz.open(j["path"]).page_count
        bad = [i for i, (a, b) in enumerate(zip(py_rows, ts["rows"]))
               if any(a[k] != b.get(k) for k in keys)]
        len_ok = len(py_rows) == len(ts["rows"])
        ok = len_ok and not bad
        observed[name] = {"pages": n, "py": len(py_rows), "ts": len(ts["rows"]),
                          "bad_rows": len(bad),
                          "sections_py": len(sections), "sections_ts": ts["section_count"]}
        mark = "일치" if ok else f"**불일치** ({len(py_rows)} vs {len(ts['rows'])}청크, {len(bad)}행)"
        print(f"  {name[:46]:<46} {n}/{total_pages}p  "
              f"섹션 py {len(sections):>5} ts {ts['section_count']:>5}  "
              f"청크 {len(py_rows):>4}  {mark}")
        if not ok:
            for i in (bad[:2] or [min(len(py_rows), len(ts["rows"]))- 1]):
                if i < len(py_rows) and i < len(ts["rows"]):
                    a, b = py_rows[i], ts["rows"][i]
                    for k in keys:
                        if a[k] != b.get(k):
                            print(f"      [{i}] {k:<18} py={a[k]!r}  ts={b.get(k)!r}")

        # raw_text 는 공백 차이만 허용 (HWP 와 같은 정책)
        py_raw = "\n\n".join(raw_parts)
        if "".join(py_raw.split()) != "".join(ts["raw_text"].split()):
            observed[name]["raw_text_chars_differ"] = True
            print(f"      **raw_text 문자 내용 불일치** py {len(py_raw)}자 / ts {len(ts['raw_text'])}자")

        # --- 기준선이 있으면 3방향 ---
        if j["base"] and os.path.exists(j["base"]):
            with open(j["base"], encoding="utf-8") as f:
                bd = json.load(f)["documents"][0]
            det = bd["deterministic"]
            vision = bd["nondeterministic"].get("vision_calls", 0)
            print(f"      기준선  청크 {det['chunk_count']} digest {det['chunks_digest']}"
                  f"  (vision_calls={vision})")
            print(f"      python  청크 {len(py_rows)} digest {py_digest}")
            print(f"      deno    청크 {len(ts['rows'])} digest {ts['digest']}")
            if det["chunks_digest"] != py_digest:
                print(f"      → 기준선과 다르다. vision_calls={vision} 이므로 "
                      f"**vision 이 보탠 섹션 차이**로 본다(이번 이식 범위 밖).")

    # --- 알려진 차이와 대조 ---
    # 3 개 문서는 MuPDF 1.27.0(npm 최신 1.27.x) 과 PyMuPDF 가 쓰는 MuPDF 1.27.2 의
    # **블록 분할 패치 차이** 때문에 갈린다. npm 에 1.27.2 가 없어 맞출 수 없다.
    # 텍스트 손실은 0 이다(공백 무시하면 전체 텍스트 동일, 경계만 이동).
    #
    # 늘 FAIL 을 내면 이 검사는 곧 무시된다. **알려진 값과 같으면 통과, 달라지면 실패**로
    # 바꿔 악화를 잡는다. 줄어들었을 때도 실패시켜 스냅샷 갱신을 강제한다.
    if os.environ.get("UPDATE_KNOWN") == "1":
        with open(KNOWN, "w", encoding="utf-8") as f:
            json.dump(observed, f, ensure_ascii=False, indent=1, sort_keys=True)
        print(f"  알려진 차이 스냅샷 갱신: {os.path.relpath(KNOWN, ROOT)}")
        return

    print()
    print("  === 알려진 차이 대조 ===")
    known = {}
    if os.path.exists(KNOWN):
        with open(KNOWN, encoding="utf-8") as f:
            known = json.load(f)
    drift = 0
    for name in sorted(set(known) | set(observed)):
        w, g = known.get(name), observed.get(name)
        if w == g:
            continue
        drift += 1
        print(f"    **{name}** 알려진 값 {json.dumps(w, ensure_ascii=False)}")
        print(f"    {'':<{len(name) + 6}}현재      {json.dumps(g, ensure_ascii=False)}")
    if drift == 0:
        n_div = sum(1 for v in observed.values() if v["bad_rows"] or v["py"] != v["ts"])
        print(f"    {len(observed)}건 전부 알려진 값과 동일 "
              f"(그중 차이 있는 문서 {n_div}건 — MuPDF 1.27.0 vs 1.27.2 블록 분할)")
    else:
        print("    → 새 차이거나 줄어든 것이다. 원인을 확인하고 "
              "`UPDATE_KNOWN=1` 로 스냅샷을 갱신할 것.")

    print()
    print("FAIL 0" if drift == 0 else f"FAIL {drift}")
    sys.exit(1 if drift else 0)


if __name__ == "__main__":
    main()
