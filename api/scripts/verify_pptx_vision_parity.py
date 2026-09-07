"""PPTX Vision 보강(`pptx_vision.ts`)을 원본 `PptxParser` 와 대조.

## Gemini 를 부르지 않고, **정규화도 건너뛴다**
Vision 응답은 비결정적이라 그대로 부르면 대조가 성립하지 않는다. 양쪽에 같은 가짜
파서를 주입해 "어떤 그림을 골랐는가 · 어떤 순서로 붙였는가 · 상한을 어떻게 셌는가"만 본다.

**캡셔너가 아니라 `ImageParser.parse` 자리를 갈아끼운다.** 캡셔너만 바꾸면 그것이
**정규화된** 바이트를 보게 되어 이미지 정규화 차이(Pillow vs mupdf 재인코딩)가 섞인다 —
처음에 그렇게 재서 5건이 전부 불일치로 나왔다. 그 차이는 `verify_image_decode_parity.py`
가 163건으로 따로 대조한다. 여기서는 **원본 blob 의 sha256 앞 12자**를 텍스트로 돌려
"같은 그림을 골랐는가" 만 본다.

## 노린 함정
- **가장 큰 그림 고르기** — 동점이면 Python `max()` 는 **앞의 것**이다. `>=` 로 쓰면 갈린다.
- **상한 5는 "시도" 기준** — 성공만 세면 실패할 때마다 다음 슬라이드로 넘어간다.
- **rerouting vs augment** — 텍스트 0 이면 대체, 1~49 면 기존 텍스트 **뒤에** 붙인다.
- **제목** — rerouting 이고 제목이 없을 때만 `p.N (Vision OCR)`.
- **quota 감지 후 즉시 중단** — 남은 슬라이드는 호출조차 안 한다.

사용:
    api/.venv/bin/python api/scripts/verify_pptx_vision_parity.py
    api/.venv/bin/python api/scripts/verify_pptx_vision_parity.py --negative
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
DENO_CONFIG = os.path.join(ROOT, "supabase", "functions", "deno.json")

# 실제 그림이 든 파일이 있어야 이 경로가 돈다. 없으면 건너뛴다.
CANDIDATES = [
    os.path.join(ROOT, "브랜딩_스튜디오앤드오어.pptx"),
    os.path.join(HERE, "fixtures", "spike_sample.pptx"),
]

RUNNER_TS = """
import { extractPptxWithVision } from "file://%(shared)s/ingest/pptx_vision.ts";

const cfg = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const out = [];
for (const job of cfg.jobs) {
  const bytes = await Deno.readFile(job.path);
  let calls = 0;
  // **원본 blob** 을 본다 — 정규화 전이다.
  const parseImageFn = async (blob: Uint8Array, _n: string, _st: string) => {
    calls++;
    if (cfg.quotaAfter && calls > cfg.quotaAfter) {
      throw new Error("RESOURCE_EXHAUSTED: quota");
    }
    const d = await crypto.subtle.digest("SHA-256", blob.slice().buffer as ArrayBuffer);
    const hex = [...new Uint8Array(d)].map((x) => x.toString(16).padStart(2, "0"))
      .join("").slice(0, 12);
    return { result: { raw_text: `FAKE ${hex} ${blob.length}` }, metricErrors: [] };
  };
  const deps = job.vision
    ? {
      // DB 는 안 쓴다 — recordCall 이 조용히 실패해도 결과에 영향이 없다.
      // deno-lint-ignore no-explicit-any
      client: { from: () => ({ insert: () => Promise.resolve({ error: null }) }) } as any,
      env: {},
      geminiApiKey: "fake",
      nowMs: 0,
      docId: null,
      parseImageFn,
    }
    : null;
  const r = await extractPptxWithVision(bytes, job.fileName, deps);
  out.push({
    name: job.name,
    attempted: r.attempted,
    succeeded: r.succeeded,
    warnings: r.warnings,
    sections: r.sections.map((s) => ({
      text: s.text, page: s.page, section_title: s.section_title,
    })),
    raw_text: r.rawParts.join("\\n\\n"),
  });
}
if (cfg.negative && out[0]?.sections?.length) out[0].sections[0].text = "변조";
await Deno.writeTextFile(Deno.args[1], JSON.stringify(out));
""" % {"shared": SHARED}


class FakeImageParser:
    """`ImageParser.parse` 자리. **정규화 전 blob** 을 보고 sha256 을 돌려준다."""

    def __init__(self, quota_after: int | None) -> None:
        self.calls = 0
        self.quota_after = quota_after

    def parse(self, data: bytes, *, file_name: str, source_type: str | None = None,
              **_kw):
        from app.adapters.parser import ExtractionResult

        self.calls += 1
        if self.quota_after is not None and self.calls > self.quota_after:
            raise RuntimeError("RESOURCE_EXHAUSTED: quota")
        hex12 = hashlib.sha256(data).hexdigest()[:12]
        return ExtractionResult(
            source_type="image",
            sections=[],
            raw_text=f"FAKE {hex12} {len(data)}",
            warnings=[],
        )


def main() -> None:
    negative = "--negative" in sys.argv
    sys.path.insert(0, os.path.join(ROOT, "api"))

    path = next((p for p in CANDIDATES if os.path.exists(p)), None)
    if path is None:
        print("  그림이 든 PPTX 가 없다 — 건너뛴다")
        sys.exit(0)
    file_name = os.path.basename(path)

    from app.adapters.impl.pptx_parser import PptxParser

    jobs = [
        {"name": "vision 켜짐", "vision": True, "quotaAfter": None},
        {"name": "vision 꺼짐 (image_parser=None)", "vision": False, "quotaAfter": None},
        {"name": "2번째 호출부터 quota", "vision": True, "quotaAfter": 1},
    ]

    py_rows = []
    for job in jobs:
        parser = PptxParser(
            image_parser=FakeImageParser(job["quotaAfter"]) if job["vision"] else None
        )
        r = parser.parse(open(path, "rb").read(), file_name=file_name)
        py_rows.append({
            "name": job["name"],
            "warnings": list(r.warnings),
            "sections": [
                {"text": s.text, "page": s.page, "section_title": s.section_title}
                for s in r.sections
            ],
            "raw_text": r.raw_text,
        })

    ts_rows = []
    with tempfile.TemporaryDirectory() as tmp:
        for job in jobs:
            cf = os.path.join(tmp, f"c_{job['vision']}_{job['quotaAfter']}.json")
            rf, of = os.path.join(tmp, "r.ts"), os.path.join(tmp, "o.json")
            with open(cf, "w", encoding="utf-8") as f:
                json.dump({
                    "jobs": [{
                        "name": job["name"], "path": path,
                        "fileName": file_name, "vision": job["vision"],
                    }],
                    "quotaAfter": job["quotaAfter"],
                    "negative": negative and job["vision"] and job["quotaAfter"] is None,
                }, f)
            with open(rf, "w", encoding="utf-8") as f:
                f.write(RUNNER_TS)
            proc = subprocess.run(
                ["deno", "run", "--config", DENO_CONFIG, "--allow-all", rf, cf, of],
                capture_output=True, text=True, timeout=1800,
            )
            if proc.returncode != 0:
                raise SystemExit(f"deno 실행 실패:\n{proc.stderr[:4000]}")
            with open(of, encoding="utf-8") as f:
                ts_rows.extend(json.load(f))

    fails: list[str] = []
    checks = 0

    def cmp(label, a, b):
        nonlocal checks
        checks += 1
        if a != b:
            fails.append(f"{label}\n      py={a!r}\n      ts={b!r}")

    print(f"  대상: {file_name}")
    for py, ts in zip(py_rows, ts_rows):
        before = len(fails)
        cmp(f"[{py['name']}] 섹션 수", len(py["sections"]), len(ts["sections"]))
        for i, (a, b) in enumerate(zip(py["sections"], ts["sections"])):
            cmp(f"[{py['name']}] sections[{i}]", a, b)
        cmp(f"[{py['name']}] raw_text", py["raw_text"], ts["raw_text"])
        cmp(f"[{py['name']}] warnings", py["warnings"], ts["warnings"])
        ok = len(fails) == before
        print(f"    {py['name']:<32} 섹션 py {len(py['sections']):>3} / ts "
              f"{len(ts['sections']):>3}  {'일치' if ok else '**불일치**'}")

    for f in fails[:6]:
        print(f"  **{f}**")
    print(f"  비교 {checks}건, 불일치 {len(fails)}건")
    if negative:
        print("음성 대조: " + ("검사기 정상 (변조 검출)" if fails else "**검사기가 못 잡는다**"))
        sys.exit(0 if fails else 1)
    print("FAIL 0" if not fails else f"FAIL {len(fails)}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
