"""`vision_incremental.ts` 를 `app/ingest/incremental.py` 와 대조.

`_sections_to_chunks` 와 `_vision_processed_pages` 두 순수 로직이 대상이다.
전자가 만든 `ChunkRecord` 가 그대로 `chunks` 행이 되므로 한 필드만 어긋나도
검색 결과가 달라진다.

## 노린 함정
- `char_range` 의 `len()` 은 **코드포인트** 수다. JS `.length` 는 UTF-16 이라
  이모지·한자 확장에서 갈린다.
- `metadata` 에 `vision_incremental: true` 가 반드시 붙는다. `isVisionDerived` 가 본다.
- caption 이 `None` 이면 키를 **안 넣는다**(빈 문자열은 넣는다).
- `_vision_processed_pages` 의 `if r.get("page")` — `page = 0` 은 falsy 라 빠진다.
- 전체 경로와 달리 **NFC 정규화를 안 한다**(원본에 그 호출이 없다).

사용:
    api/.venv/bin/python api/scripts/verify_vision_incremental_parity.py
    api/.venv/bin/python api/scripts/verify_vision_incremental_parity.py --negative
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

# (text, page, section_title, metadata)
SECTION_CASES = [
    [("본문입니다.", 1, "(vision) p.1 OCR 텍스트", {})],
    [("본문", 2, "(vision) p.2", {"table_caption": "표 캡션"})],
    [("본문", 3, "(vision) p.3", {"figure_caption": "그림 캡션"})],
    [("본문", 4, "(vision) p.4", {"table_caption": "표", "figure_caption": "그림"})],
    # 빈 문자열은 None 이 아니다 — 키가 들어간다.
    [("본문", 5, "(vision) p.5", {"table_caption": "", "figure_caption": ""})],
    # 코드포인트 vs UTF-16 — 이모지·확장한자
    [("가나다 😀 𠮷 라마바", 6, "(vision) p.6", {})],
    [("😀😀😀", 7, "(vision) p.7", {"table_caption": "이모지 표 😀"})],
    # 여러 섹션 → chunk_idx 가 연속으로 붙는다
    [("첫째", 1, "(vision) p.1 이미지 분류: 표", {"table_caption": "T"}),
     ("둘째", 1, "(vision) p.1 OCR 텍스트", {"table_caption": "T"}),
     ("셋째", 2, "(vision) p.2 액션 아이템", {})],
    # page 가 None
    [("본문", None, "(vision) p.9", {"table_caption": "표"})],
    # 200자 넘는 caption (prefix ON 일 때 자른다)
    [("본문", 1, "(vision) p.1", {"table_caption": "가" * 250})],
    [],
]
START_IDXS = [0, 7, -1 + 1, 100]

# `_vision_processed_pages` 케이스 — (page, section_title)
PAGE_ROW_CASES = [
    [],
    [{"page": 1, "section_title": "(vision) p.1 OCR"}],
    [{"page": 1, "section_title": "본문"}],
    [{"page": 0, "section_title": "(vision) p.0 x"}],          # page 0 은 falsy → 제외
    [{"page": None, "section_title": "(vision) p.5 x"}],        # None → 제외
    [{"page": 3, "section_title": "(vision)"}],                 # 접두사 부족 → 제외
    [{"page": 3, "section_title": "(vision) p.3"}],
    [{"page": 2, "section_title": None}],
    [{"page": 2, "section_title": "(vision) p.2 a"},
     {"page": 2, "section_title": "(vision) p.2 b"},
     {"page": 4, "section_title": "(vision) p.4 c"}],
]

RUNNER_TS = """
import { sectionsToChunks, visionProcessedPages }
  from "file://%(shared)s/ingest/vision_incremental.ts";

const cfg = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const NEG = cfg.negative === true;
const out: Record<string, unknown> = {};

out.chunks = [];
for (const captionOn of [false, true]) {
  for (const startIdx of cfg.startIdxs) {
    for (const secs of cfg.sectionCases) {
      // deno-lint-ignore no-explicit-any
      const sections = secs.map((s: any[]) => ({
        text: s[0], page: s[1], section_title: s[2],
        bbox: null, metadata: s[3],
      }));
      (out.chunks as unknown[]).push(sectionsToChunks(sections, {
        docId: "d1",
        startChunkIdx: startIdx,
        env: {
          captionPrefixEnabled: captionOn,
          synonymInjectionEnabled: false,
          synonymLlmEnabled: false,
        },
      }));
    }
  }
}

out.pages = [];
for (const rows of cfg.pageRowCases) {
  // deno-lint-ignore no-explicit-any
  const q: any = {
    eq: () => q,
    then: (res: (v: unknown) => void) => res({ data: rows, error: null }),
  };
  // deno-lint-ignore no-explicit-any
  const client: any = { from: () => ({ select: () => q }) };
  const s = await visionProcessedPages(client, "d1");
  (out.pages as unknown[]).push([...s].sort((a, b) => a - b));
}

if (NEG) (out.pages as number[][])[1] = [];
await Deno.writeTextFile(Deno.args[1], JSON.stringify(out));
""" % {"shared": SHARED}


def main() -> None:
    negative = "--negative" in sys.argv
    os.environ["JETRAG_SYNONYM_INJECTION_ENABLED"] = "false"

    from app.adapters.parser import ExtractedSection
    from app.ingest import incremental as INC

    py_chunks = []
    for caption_on in (False, True):
        os.environ["JETRAG_CAPTION_PREFIX_ENABLED"] = "true" if caption_on else "false"
        for start_idx in START_IDXS:
            for secs in SECTION_CASES:
                sections = [
                    ExtractedSection(text=t, page=p, section_title=st, bbox=None,
                                     metadata=dict(md))
                    for t, p, st, md in secs
                ]
                recs = INC._sections_to_chunks(
                    sections, doc_id="d1", start_chunk_idx=start_idx,
                )
                py_chunks.append([{
                    "doc_id": r.doc_id, "chunk_idx": r.chunk_idx, "text": r.text,
                    "page": r.page, "section_title": r.section_title,
                    "bbox": list(r.bbox) if r.bbox else None,
                    "char_range": list(r.char_range), "metadata": dict(r.metadata),
                } for r in recs])

    class FakeQ:
        def __init__(self, rows):
            self.rows = rows

        def eq(self, *a, **k):
            return self

        def execute(self):
            class R:
                data = self.rows
            return R()

    class FakeClient:
        def __init__(self, rows):
            self.rows = rows

        def table(self, _n):
            outer = self

            class T:
                def select(self, *a, **k):
                    return FakeQ(outer.rows)
            return T()

    py_pages = [
        sorted(INC._vision_processed_pages(FakeClient(rows), "d1"))
        for rows in PAGE_ROW_CASES
    ]

    with tempfile.TemporaryDirectory() as tmp:
        cf, rf = os.path.join(tmp, "c.json"), os.path.join(tmp, "r.ts")
        of = os.path.join(tmp, "out.json")
        with open(cf, "w", encoding="utf-8") as f:
            json.dump({
                "sectionCases": SECTION_CASES, "startIdxs": START_IDXS,
                "pageRowCases": PAGE_ROW_CASES, "negative": negative,
            }, f)
        with open(rf, "w", encoding="utf-8") as f:
            f.write(RUNNER_TS)
        proc = subprocess.run(
            ["deno", "run", "--config", DENO_CONFIG, "--allow-all", rf, cf, of],
            capture_output=True, text=True, timeout=600,
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

    for i, (a, b) in enumerate(zip(py_chunks, ts["chunks"])):
        cmp(f"sectionsToChunks[{i}] 개수", len(a), len(b))
        for j, (ra, rb) in enumerate(zip(a, b)):
            cmp(f"sectionsToChunks[{i}][{j}]", ra, rb)
    for i, (a, b) in enumerate(zip(py_pages, ts["pages"])):
        cmp(f"visionProcessedPages[{i}] {PAGE_ROW_CASES[i]!r}", a, b)

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
